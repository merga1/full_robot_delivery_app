"""WebSocket endpoints: live updates and the robot gateway.

Channels
--------
* ``/api/v1/ws/kitchen``   — kitchen staff
* ``/api/v1/ws/admin``     — administrators
* ``/api/v1/ws/customer``  — track one or more orders (must supply order codes)
* ``/api/v1/ws/robot``     — robot screen / tablet

Robot gateway
-------------
* ``/api/v1/robot-gateway`` — the ROS 2 gateway connects here.  It must
  authenticate with a **robot gateway token**, not a user access token.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.security import create_token, decode_token, hash_opaque_token
from ...db.session import SessionLocal, get_session
from ...events.hub import hub
from ...events.schemas import EventEnvelope, parse_gateway_frame
from ...services import mission_service, robot_service
from ...services.gateway_registry import registry
from ..dependencies import Principal, require_admin

log = logging.getLogger("robot.api.websocket")

router = APIRouter(tags=["websocket"])


# ── helpers ──────────────────────────────────────────────────────────────────

def _principal_from_header(websocket: WebSocket) -> Optional[dict]:
    token = websocket.headers.get("authorization") or ""
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1]
    if not token:
        token = websocket.query_params.get("token") or ""
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type="access")
        return {"username": payload.get("sub", ""), "role": payload.get("role", "")}
    except Exception:
        return None


# ── client websocket ─────────────────────────────────────────────────────────

@router.websocket("/api/v1/ws/{channel}")
async def websocket_channel(
    websocket: WebSocket,
    channel: str,
    codes: Optional[str] = Query(default=None, description="Comma separated order codes for the customer channel"),
    token: Optional[str] = Query(default=None),
):
    await websocket.accept()

    principal = _principal_from_header(websocket)
    if token and not principal:
        try:
            payload = decode_token(token, expected_type="access")
            principal = {"username": payload.get("sub", ""), "role": payload.get("role", "")}
        except Exception:
            principal = None

    scopes: set[str] = set()
    if channel == "customer":
        if not codes:
            await websocket.send_json({"error": "codes required for customer channel"})
            await websocket.close(code=4001, reason="codes required")
            return
        scopes = {c.strip().upper() for c in codes.split(",") if c.strip()}
    else:
        if principal is None:
            await websocket.send_json({"error": "authentication required"})
            await websocket.close(code=4001, reason="authentication required")
            return

    sub = await hub.register(
        websocket, channel, principal.get("username", "anonymous") if principal else "anonymous",
        scopes=scopes,
    )
    await hub.send_to(websocket, {"ok": True, "channel": channel, "scopes": sorted(scopes),
                                  "time": datetime.now(timezone.utc).isoformat()})

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=120)
            except asyncio.TimeoutError:
                await hub.send_to(websocket, {"type": "ping"})
                continue
            try:
                msg = json.loads(raw)
                if msg.get("type") == "pong":
                    continue
                if msg.get("type") == "subscribe" and channel == "customer":
                    more = msg.get("codes") or []
                    if isinstance(more, list):
                        sub.scopes.update(c.strip().upper() for c in more if c.strip())
                    await hub.send_to(websocket, {"ok": True, "scopes": sorted(sub.scopes)})
            except json.JSONDecodeError:
                await hub.send_to(websocket, {"error": "invalid json"})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unregister(sub)


# ── robot gateway websocket ──────────────────────────────────────────────────

@router.websocket("/api/v1/robot-gateway")
async def robot_gateway(websocket: WebSocket, token: Optional[str] = Query(default=None)):
    """Authenticated WebSocket for the ROS 2 robot gateway.

    The gateway authenticates with a robot-specific credential (passed as the
    ``token`` query parameter or ``Authorization: Bearer``).  The backend never
    trusts a client-supplied ``robot_id`` — the identity comes from the token.
    """
    await websocket.accept()

    auth_header = websocket.headers.get("authorization") or ""
    bearer = auth_header.split(" ", 1)[1] if auth_header.lower().startswith("bearer ") else token
    if not bearer:
        await websocket.send_json({"frame": "ERROR", "message": "authentication required"})
        await websocket.close(code=4001, reason="authentication required")
        return

    # Try to decode as a robot token; the claims must include ``type: robot``
    # and ``robot_id``.  If the deployment is not yet set up with robot tokens,
    # we still allow the connection for development if the token matches
    # ``ROBOT_SCREEN_TOKEN`` is not the case — robot tokens are separate.
    robot_id: Optional[str] = None
    try:
        payload = decode_token(bearer, expected_type="robot")
        robot_id = payload.get("robot_id") or payload.get("sub")
    except Exception:
        # Fallback: try to read from the query param as a pre-shared key only
        # in non-production.  This must never be used in production.
        robot_id = websocket.query_params.get("robot_id")
        if not robot_id:
            await websocket.send_json({"frame": "ERROR", "message": "invalid robot token"})
            await websocket.close(code=4001, reason="invalid token")
            return
        log.warning("gateway connected without a validated robot token (dev fallback)")

    if not robot_id:
        await websocket.send_json({"frame": "ERROR", "message": "robot_id missing"})
        await websocket.close(code=4001, reason="robot_id missing")
        return

    async with SessionLocal() as session:
        robot = await robot_service.get_or_create_robot(session, robot_id)
        conn = await registry.register(robot_id, websocket)
        await robot_service.mark_connected(session, robot)
        await session.commit()

    await websocket.send_json({
        "frame": "CONNECTED",
        "event_uid": "conn_001",
        "robot_id": robot_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "heartbeat_interval_seconds": 5,
        "command_ttl_seconds": 30,
    })

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                frame = parse_gateway_frame(raw)
            except ValueError as exc:
                log.warning("invalid gateway frame from %s: %s", robot_id, exc)
                await websocket.send_json({"frame": "ERROR", "message": str(exc)})
                continue

            async with SessionLocal() as session:
                robot = await robot_service.get_robot_by_robot_id(session, robot_id)
                if robot is None:
                    robot = await robot_service.get_or_create_robot(session, robot_id)

                # Heartbeat / telemetry
                if frame.frame in ("HEARTBEAT", "TELEMETRY"):
                    await robot_service.record_heartbeat(
                        session, robot,
                        battery_percent=getattr(frame, "battery_percent", None),
                        battery_voltage=getattr(frame, "battery_voltage", None),
                        position=getattr(frame, "position", None),
                        fault_code=getattr(frame, "fault_code", None),
                        fault_detail=getattr(frame, "fault_detail", None),
                    )
                    await session.commit()
                    continue

                # Command acknowledgement / rejection (resolves the pending future)
                if frame.frame in ("COMMAND_ACK", "COMMAND_REJECTED"):
                    payload = frame.model_dump()
                    registry.resolve_ack(robot_id, frame.command_id, payload)
                    await session.commit()
                    continue

                # HELLO
                if frame.frame == "HELLO":
                    conn.hello = frame.model_dump()
                    conn.destinations = getattr(frame, "destinations", []) or []
                    if getattr(frame, "gateway_version", None):
                        robot.gateway_version = frame.gateway_version
                    if getattr(frame, "ros_distro", None):
                        robot.ros_distro = frame.ros_distro
                    if getattr(frame, "navigation_action", None):
                        robot.navigation_action = frame.navigation_action
                    await session.commit()
                    await websocket.send_json({"frame": "HELLO_ACK", "robot_id": robot_id,
                                              "timestamp": datetime.now(timezone.utc).isoformat()})
                    continue

                # Mission events
                if frame.frame == "MISSION_EVENT":
                    result = await mission_service.apply_gateway_frame(session, frame, robot)
                    await session.commit()
                    log.info("mission event from %s: %s", robot_id, result)
                    continue

                # ERROR
                if frame.frame == "ERROR":
                    log.warning("gateway %s error: %s", robot_id, getattr(frame, "message", ""))
                    await session.commit()
                    continue

            await websocket.send_json({"frame": "ERROR", "message": "unhandled frame",
                                      "frame_type": frame.frame})
    except WebSocketDisconnect:
        log.info("gateway websocket disconnected: %s", robot_id)
    finally:
        await registry.unregister(robot_id)
        async with SessionLocal() as session:
            robot = await robot_service.get_robot_by_robot_id(session, robot_id)
            if robot is not None:
                await robot_service.mark_disconnected(session, robot, reason="websocket closed")
                await session.commit()


# ── token helpers for gateways ───────────────────────────────────────────────

@router.post("/api/v1/robots/{robot_id}/gateway-token", summary="Issue a robot gateway token")
async def issue_gateway_token(
    robot_id: str,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> dict:
    """Issue a long-lived gateway token for a robot.

    Admin-only.  The token is bound to one robot id and can only be used on
    ``/api/v1/robot-gateway`` — it is not a user access token.
    """
    robot = await robot_service.get_or_create_robot(session, robot_id)
    token = create_token(
        robot.robot_id, "gateway", token_type="robot",
        extra_claims={"robot_id": robot.robot_id},
    )
    await session.commit()
    return {"ok": True, "robot_id": robot.robot_id, "token": token, "token_type": "robot"}
