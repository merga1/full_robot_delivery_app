"""
missions.py — sequential multi-waypoint missions.

Old problems fixed
  * a mission was reported "completed" as soon as the nav subprocess exited,
    even if Nav2 never moved the robot.  Now a step only completes when Nav2
    reports SUCCEEDED.
  * the API did not return `steps` / `progress`, which crashed the Flutter
    mission screen.  Both are returned now.
  * missions could not be cancelled.  POST /missions/{id}/cancel added.
  * blocking calls ran inside the event loop.  They now run in a worker thread.

Mission status : pending | in_progress | completed | failed | cancelled
Step status    : pending | in_progress | completed | failed | cancelled
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from . import database, ros_hub
from .auth import require_operator

log = logging.getLogger("robot.missions")
router = APIRouter(prefix="/missions", tags=["missions"])

NAV_TIMEOUT_S = float(os.getenv("ROBOT_NAV_TIMEOUT_S", "300"))   # per waypoint
DWELL_S = float(os.getenv("ROBOT_MISSION_DWELL_S", "0"))         # pause at each stop

_state: dict[str, Any] = {"id": None, "cancel": False, "task": None}


class MissionRequest(BaseModel):
    destinations: list[str]


class MissionError(RuntimeError):
    """A mission could not be started.  Carries the HTTP status to report."""

    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def current_mission_id() -> Optional[int]:
    return _state["id"]


def cancel_running() -> bool:
    """Request cancellation of the running mission (if any)."""
    if _state["id"] is None:
        return False
    _state["cancel"] = True
    ros_hub.hub.cancel_nav_goal()
    return True


def start_mission(destinations: list[str]) -> int:
    """Create and launch a mission, return its id.

    Raises [MissionError] when the mission cannot be started.  Must be called
    from an async handler (it schedules the runner task on the running loop).
    """
    if not destinations:
        raise MissionError("A mission needs at least one destination", 422)
    if _state["id"] is not None:
        raise MissionError(
            f"Mission #{_state['id']} is still running — cancel it first", 409)
    poses = []
    for name in destinations:
        d = database.get_destination(name)
        if d is None:
            raise MissionError(f"Destination '{name}' not found", 404)
        poses.append(d)
    steps = [{"order": i, "destination": p["name"], "status": "pending", "error": None}
             for i, p in enumerate(poses)]
    mission_id = database.create_mission([p["name"] for p in poses], steps)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        raise MissionError("Missions can only be started from an async request", 500)
    _state.update(id=mission_id, cancel=False)
    _state["task"] = asyncio.create_task(_run(mission_id, poses, steps))
    return mission_id


@router.post("")
async def create_mission(body: MissionRequest, _auth=Depends(require_operator)):
    try:
        mission_id = start_mission(body.destinations)
    except MissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return database.get_mission(mission_id)


@router.get("")
async def list_missions(_auth=Depends(require_operator)):
    return {"missions": database.list_missions()}


@router.get("/{mission_id}")
async def get_mission(mission_id: int, _auth=Depends(require_operator)):
    m = database.get_mission(mission_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return m


@router.post("/{mission_id}/cancel")
async def cancel_mission(mission_id: int, _auth=Depends(require_operator)):
    if _state["id"] != mission_id:
        m = database.get_mission(mission_id)
        if m is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        return {"ok": True, "status": m["status"], "message": "Mission is not running"}
    cancel_running()
    return {"ok": True, "status": "cancelling"}


async def _run(mission_id: int, poses: list[dict], steps: list[dict]) -> None:
    def save(**kw: Any) -> None:
        database.update_mission(mission_id, steps=steps, **kw)

    def finish(status: str, error: Optional[str] = None) -> None:
        save(status=status, error=error, finished=True)
        log.info("mission %s %s %s", mission_id, status, error or "")
        # A mission can be a delivery for a customer order — tell the order
        # about it so the customer sees "delivered" in the app / web page.
        try:
            from . import ordering
            ordering.on_mission_finished(mission_id, status, error)
        except Exception:                       # never break the mission runner
            log.exception("could not update the orders of mission %s", mission_id)

    try:
        save(status="in_progress")
        for i, pose in enumerate(poses):
            if _state["cancel"]:
                for s in steps[i:]:
                    s["status"] = "cancelled"
                return finish("cancelled")
            steps[i]["status"] = "in_progress"
            save()
            reply = await run_in_threadpool(
                ros_hub.hub.send_nav_goal, pose["name"], pose["x"], pose["y"], pose["theta"])
            if not reply["ok"]:
                err = reply.get("error") or f"Goal not accepted ({reply['state']})"
                steps[i].update(status="failed", error=err)
                for s in steps[i + 1:]:
                    s["status"] = "cancelled"
                return finish("failed", f"Step {i + 1} ({pose['name']}): {err}")

            gid, t0 = reply["goal_id"], time.monotonic()
            outcome, err = "running", None
            while outcome == "running":
                await asyncio.sleep(0.5)
                snap = ros_hub.hub.nav.snapshot()
                if _state["cancel"]:
                    outcome = "cancelled"
                elif snap["goal_id"] != gid:
                    outcome, err = "failed", "Goal was replaced by another command"
                elif snap["state"] == "succeeded":
                    outcome = "completed"
                elif snap["state"] in ("failed", "rejected", "unavailable"):
                    outcome, err = "failed", snap["error"] or snap["state"]
                elif snap["state"] == "canceled":
                    outcome = "cancelled"
                elif time.monotonic() - t0 > NAV_TIMEOUT_S:
                    ros_hub.hub.cancel_nav_goal()
                    outcome, err = "failed", f"Timed out after {NAV_TIMEOUT_S:.0f} s"

            if outcome == "completed":
                steps[i]["status"] = "completed"
                save()
                if DWELL_S > 0 and i < len(poses) - 1:
                    await asyncio.sleep(DWELL_S)
                continue
            if outcome == "cancelled":
                for s in steps[i:]:
                    s["status"] = "cancelled"
                return finish("cancelled")
            steps[i].update(status="failed", error=err)
            for s in steps[i + 1:]:
                s["status"] = "cancelled"
            return finish("failed", f"Step {i + 1} ({pose['name']}): {err}")
        finish("completed")
    except Exception as exc:                      # never leave a mission "in_progress"
        log.exception("mission %s crashed", mission_id)
        finish("failed", f"Internal error: {exc}")
    finally:
        _state.update(id=None, cancel=False, task=None)
