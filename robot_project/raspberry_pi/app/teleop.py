"""
teleop.py — joystick endpoints.

The hub (ros_hub.py) owns publishing: while the joystick is armed it publishes
the latest command at 20 Hz and falls back to zero velocity if the app stops
sending for 0.5 s (Wi-Fi drop, app killed, phone locked).  These endpoints just
update that command and never touch ROS directly, so they answer in ~1 ms.

Safety rules
  * commands are ignored (and reported as armed=false) until /teleop/enable
  * out-of-order commands are dropped using the app's `seq` counter, so a late
    "go" can never overtake a "stop"
  * arming the joystick cancels any running mission / navigation goal
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from . import missions, robot_link, ros_hub
from .auth import require_operator

router = APIRouter(prefix="/teleop", tags=["teleop"])


class EnableBody(BaseModel):
    enabled: bool


class CmdVelBody(BaseModel):
    linear: float = 0.0
    angular: float = 0.0
    seq: Optional[int] = None


def _chain() -> dict[str, Any]:
    bridge = robot_link.get_bridge_status()
    hub = ros_hub.hub
    return {
        "hub_ready": hub.ready,
        "bridge_ok": bridge.get("ok", False),
        "cmd_vel_subscriber": bridge.get("cmd_vel_subscriber", False),
        "bridge_subscriptions": bridge.get("bridge_subscriptions", []),
        "cmd_vel_topic": ros_hub.CMD_VEL_TOPIC,
        # The hub node runs inside the API process, so "relay alive" == hub alive.
        "relay_alive": hub.ready,
        # The hub publishes RELIABLE, which is compatible with RELIABLE and
        # BEST_EFFORT subscribers alike.
        "qos_compatible": True,
        "hint": bridge.get("hint", "") if not bridge.get("ok") else "",
    }


@router.post("/enable")
def enable_teleop(body: EnableBody, _auth=Depends(require_operator)):
    if body.enabled:
        missions.cancel_running()
        ros_hub.hub.cancel_nav_goal()
    ros_hub.hub.teleop_arm(body.enabled and ros_hub.hub.ready)
    return {"ok": ros_hub.hub.ready, "teleop_armed": ros_hub.hub.teleop_armed(), **_chain()}


@router.post("/cmd_vel")
def send_cmd_vel(body: CmdVelBody, _auth=Depends(require_operator)):
    res = ros_hub.hub.teleop_cmd(body.linear, body.angular, body.seq)
    snap = ros_hub.hub.teleop_snapshot()
    return {"ok": True, "armed": res["armed"], "applied": res["applied"],
            "reason": res.get("reason"), "linear": snap["lin"], "angular": snap["ang"],
            "publish_count": snap["publish_count"]}


@router.post("/stop")
def stop_teleop(_auth=Depends(require_operator)):
    ros_hub.hub.teleop_cmd(0.0, 0.0)
    return {"ok": True, "armed": ros_hub.hub.teleop_armed()}


@router.post("/test-drive")
def test_drive(_auth=Depends(require_operator)):
    """Creep forward 0.1 m/s for 0.5 s. Requires the joystick to be armed."""
    hub = ros_hub.hub
    chain = _chain()
    if not hub.teleop_armed():
        return {**chain, "ok": False, "hint": "Arm the joystick first (switch to Joystick mode)."}
    if not chain["cmd_vel_subscriber"]:
        return {**chain, "ok": False, "hint": "Nothing is subscribed to " + ros_hub.CMD_VEL_TOPIC
                + " — is the robot stack running?"}
    hub.teleop_cmd(0.1, 0.0)
    time.sleep(0.5)
    hub.teleop_cmd(0.0, 0.0)
    return {**chain, "ok": True, "hint": "Test drive sent (0.1 m/s for 0.5 s)."}


@router.get("/debug")
def teleop_debug(_auth=Depends(require_operator)):
    snap = ros_hub.hub.teleop_snapshot()
    chain = _chain()
    return {
        "ok": snap["armed"] and chain["bridge_ok"],
        "teleop_armed": snap["armed"],
        "linear": snap["lin"],
        "angular": snap["ang"],
        "publish_count": snap["publish_count"],
        "bridge_listening": chain["cmd_vel_subscriber"],
        **chain,
    }


@router.get("/connection")
def teleop_connection(_auth=Depends(require_operator)):
    chain = _chain()
    warnings: list[str] = []
    if not chain["hub_ready"]:
        warnings.append("ROS hub not ready — check the API log")
    if not chain["cmd_vel_subscriber"]:
        warnings.append(f"No subscriber on {ros_hub.CMD_VEL_TOPIC} — is the robot stack running?")
    return {
        "chain_ok": chain["hub_ready"] and chain["cmd_vel_subscriber"],
        "hub_ready": chain["hub_ready"],
        "bridge": robot_link.get_bridge_status(),
        "warnings": warnings,
        "hint": "; ".join(warnings) if warnings else "All good",
    }
