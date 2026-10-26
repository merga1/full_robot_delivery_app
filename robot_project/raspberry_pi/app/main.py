"""
main.py — FastAPI application for the robot Pi backend.

Routes
  /auth/*         login / JWT                         (auth.py)
  /robot/*        start/stop, status, bridge          (robot_link.py)
  /teleop/*       joystick                            (teleop.py)
  /destinations   named map poses                     (database.py)
  /navigation/*   map, goal, cancel, status, initial pose   (ros_hub.py)
  /missions/*     multi-stop missions                 (missions.py)
  /customer/*     menu, orders, "call the robot"      (customer_api.py)
  /kitchen/api/*  order queue, menu & table admin     (kitchen_api.py)
  /o/<table>      customer order page (table QR)      (web.py)
  /kitchen        kitchen dashboard                   (web.py)
  /logs           robot launch log
  /health         unauthenticated liveness probe (used by app discovery)

Run it with run_server_ros.sh so ROS is sourced.  Nothing in here blocks the
event loop: every endpoint that does I/O is a plain `def` (FastAPI runs those in
a worker thread) and ROS state is read from the hub's cache.
"""

from __future__ import annotations

import logging
import math
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import database, missions, robot_link, ros_hub
from .auth import require_operator, require_staff
from .auth import router as auth_router
from .customer_api import router as customer_router
from .kitchen_api import router as kitchen_router
from .teleop import router as teleop_router
from . import web as web_pages

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("robot.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    database.init()
    ros_hub.hub.start()          # raises with a clear message if ROS is missing
    log.info("Robot API ready (ros=%s, sim=%s)", ros_hub.ROS_AVAILABLE, ros_hub.hub.sim)
    yield
    robot_link.shutdown()
    ros_hub.hub.stop()


app = FastAPI(title="Robot Controller API", version="3.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(auth_router)
app.include_router(teleop_router)
app.include_router(missions.router)
app.include_router(customer_router)
app.include_router(kitchen_router)
app.include_router(web_pages.router)
web_pages.mount_static(app)


# ── Robot lifecycle ───────────────────────────────────────────────────────────

@app.post("/robot/start")
def start_robot(_auth=Depends(require_operator)):
    return robot_link.start_robot()


@app.post("/robot/stop")
def stop_robot(_auth=Depends(require_operator)):
    missions.cancel_running()
    return robot_link.stop_robot()


@app.post("/robot/ready")
def set_ready(_auth=Depends(require_operator)):
    return robot_link.set_ready()


@app.get("/robot/status")
def get_robot_status(_auth=Depends(require_operator)):
    return robot_link.get_status()


@app.get("/robot/link-check")
def link_check(_auth=Depends(require_operator)):
    return robot_link.get_link_check()


@app.get("/robot/bridge-status")
def bridge_status(_auth=Depends(require_operator)):
    return robot_link.get_bridge_status()


@app.post("/robot/enable-motors")
def enable_motors(_auth=Depends(require_operator)):
    return robot_link.enable_motors()


# ── Destinations ──────────────────────────────────────────────────────────────

class DestinationBody(BaseModel):
    name: str
    x: float = 0.0
    y: float = 0.0
    theta: float = 0.0


@app.get("/destinations")
def list_destinations(_auth=Depends(require_operator)):
    return {"destinations": database.get_destinations()}


@app.post("/destinations")
def upsert_destination(body: DestinationBody, _auth=Depends(require_operator)):
    try:
        database.upsert_destination(body.name, body.x, body.y, body.theta)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True, "name": body.name.strip(), "x": body.x, "y": body.y, "theta": body.theta}


@app.delete("/destinations/{name}")
def delete_destination(name: str, _auth=Depends(require_operator)):
    if not database.delete_destination(name):
        raise HTTPException(status_code=404, detail=f"Destination '{name}' not found")
    return {"ok": True, "name": name}


# ── Paired devices (the robot screen / admin phones) ──────────────────────────
# The kiosk on the robot and the admin phones register themselves so the
# dashboard can show which device is connected.  Registration needs a staff
# login, the heartbeat does not.

class DeviceBody(BaseModel):
    device_id: str
    name: str = ""
    kind: str = "admin"          # admin | kiosk | phone


@app.post("/devices/register")
def register_device(body: DeviceBody, _auth=Depends(require_staff)):
    if not body.device_id.strip():
        raise HTTPException(status_code=422, detail="device_id is required")
    device = database.register_device(body.device_id.strip(), body.name, body.kind)
    return {"ok": True, "device": device}


@app.post("/devices/heartbeat")
def device_heartbeat(body: DeviceBody):
    """Called by the app every few seconds — no login needed."""
    if not body.device_id.strip():
        raise HTTPException(status_code=422, detail="device_id is required")
    database.touch_device(body.device_id.strip())
    return {"ok": True}


@app.get("/devices")
def list_devices(_auth=Depends(require_staff)):
    return {"devices": database.list_devices()}


@app.delete("/devices/{device_id}")
def delete_device(device_id: str, _auth=Depends(require_staff)):
    if not database.delete_device(device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    return {"ok": True}


# ── Map ───────────────────────────────────────────────────────────────────────
# Preferred source: the live /map topic from Nav2 — it is by definition the map
# the robot localizes in, so goals can never land on a different map.
# Fallback: the map YAML on disk (ROBOT_MAP_YAML or REAL1_WS/.../my_map.yaml).

_png_cache: dict[tuple, bytes] = {}


def _find_map_yaml() -> Optional[Path]:
    override = os.getenv("ROBOT_MAP_YAML", "")
    if override and Path(override).exists():
        return Path(override)
    ws = os.getenv("REAL1_WS", "")
    if ws:
        for name in ("my_map.yaml", "my_map1.yaml"):
            cand = Path(ws) / "src/articubot_one/maps" / name
            if cand.exists():
                return cand
    return None


def _yaml_image_path(yaml_path: Path) -> Path:
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    image = Path(data.get("image", "my_map.pgm"))
    return image if image.is_absolute() else yaml_path.parent / image


def _parse_map_yaml(path: Path) -> dict[str, Any]:
    from PIL import Image
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    origin = data.get("origin", [0.0, 0.0, 0.0])
    with Image.open(_yaml_image_path(path)) as img:
        width, height = img.size
    return {
        "image_url": "/navigation/map/image",
        "resolution": float(data.get("resolution", 0.05)),
        "origin": {"x": float(origin[0]), "y": float(origin[1]), "yaw": float(origin[2])},
        "width": width, "height": height, "frame_id": "map",
        "negate": int(data.get("negate", 0)),
    }


def _same_map(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (a["width"] == b["width"] and a["height"] == b["height"]
            and math.isclose(a["resolution"], b["resolution"], rel_tol=1e-3)
            and abs(a["origin"]["x"] - b["origin"]["x"]) < 0.02
            and abs(a["origin"]["y"] - b["origin"]["y"]) < 0.02)


@app.get("/navigation/map")
def get_map_meta(_auth=Depends(require_operator)):
    live = ros_hub.hub.live_map_meta()
    yaml_path = _find_map_yaml()
    saved = _parse_map_yaml(yaml_path) if yaml_path else None
    if live is None and saved is None:
        raise HTTPException(
            status_code=404,
            detail="No map available: Nav2 is not publishing /map yet and no map YAML was "
                   "found. Start the robot, or set REAL1_WS / ROBOT_MAP_YAML.")
    if live is not None:
        meta = dict(live, source="live")
        if saved is not None and not _same_map(live, saved):
            meta["warning"] = (f"The saved map file ({yaml_path.name}) differs from the map "
                               "Nav2 is using. Showing Nav2's live map.")
        return meta
    return dict(saved, source="file")  # type: ignore[arg-type]


@app.get("/navigation/map/image")
def get_map_image(_auth=Depends(require_operator)):
    """Always returns a PNG (small + natively decodable by Flutter)."""
    png = ros_hub.hub.live_map_png()
    if png is not None:
        return Response(content=png, media_type="image/png")
    yaml_path = _find_map_yaml()
    if yaml_path is None:
        raise HTTPException(status_code=404, detail="Map not configured")
    img_path = _yaml_image_path(yaml_path)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"Map image not found: {img_path}")
    key = (str(img_path), img_path.stat().st_mtime_ns)
    if key not in _png_cache:
        import io
        from PIL import Image, ImageOps
        negate = int((yaml.safe_load(yaml_path.read_text()) or {}).get("negate", 0))
        with Image.open(img_path) as img:
            img = img.convert("L")
            if negate:
                img = ImageOps.invert(img)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
        _png_cache.clear()
        _png_cache[key] = buf.getvalue()
    return Response(content=_png_cache[key], media_type="image/png")


# ── Navigation ────────────────────────────────────────────────────────────────

class GoalBody(BaseModel):
    destination: str


class InitialPoseBody(BaseModel):
    x: float
    y: float
    theta: float = 0.0     # degrees


@app.post("/navigation/goal")
def send_goal(body: GoalBody, _auth=Depends(require_operator)):
    dest = database.get_destination(body.destination)
    if dest is None:
        raise HTTPException(status_code=404, detail=f"Destination '{body.destination}' not found")
    if missions.current_mission_id() is not None:
        raise HTTPException(status_code=409,
                            detail="A mission is running — cancel it before sending a goal")
    reply = ros_hub.hub.send_nav_goal(body.destination, dest["x"], dest["y"], dest["theta"])
    if not reply["ok"]:
        code = 503 if reply["state"] == "unavailable" else 409
        raise HTTPException(status_code=code, detail=reply.get("error") or reply["state"])
    return {"ok": True, "destination": body.destination, "x": dest["x"], "y": dest["y"],
            "theta": dest["theta"], "state": reply["state"], "goal_id": reply["goal_id"]}


@app.post("/navigation/cancel")
def cancel_navigation(_auth=Depends(require_operator)):
    missions.cancel_running()
    cancelled = ros_hub.hub.cancel_nav_goal()
    return {"ok": True, "cancelled": cancelled}


@app.get("/navigation/status")
def navigation_status(_auth=Depends(require_operator)):
    return {**ros_hub.hub.nav.snapshot(), "nav_ready": ros_hub.hub.nav_ready(),
            "localization": ros_hub.hub.localization(), "pose": ros_hub.hub.get_pose()}


@app.post("/navigation/initial-pose")
def set_initial_pose(body: InitialPoseBody, _auth=Depends(require_operator)):
    """Tell AMCL where the robot is (same as 2D Pose Estimate in RViz)."""
    res = ros_hub.hub.set_initial_pose(body.x, body.y, body.theta)
    if not res["ok"]:
        raise HTTPException(status_code=422, detail=res["error"])
    return res


# ── Logs / health ─────────────────────────────────────────────────────────────

@app.get("/logs")
def get_logs(severity: Optional[str] = None, limit: int = 100, _auth=Depends(require_operator)):
    logs = robot_link.get_logs(severity=severity, limit=limit)
    return {"logs": logs, "count": len(logs)}


@app.get("/health")
async def health():
    return {"ok": True, "service": "robot-control", "api": "robot-api",
            "host": socket.gethostname(), "user": os.getenv("USER", ""),
            "ros": ros_hub.ROS_AVAILABLE, "sim": ros_hub.hub.sim}
