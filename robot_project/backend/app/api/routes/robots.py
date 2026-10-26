"""Robot and mission endpoints.

The backend never drives the robot directly.  Every command in this module is
sent to the robot's gateway over the authenticated WebSocket and the answer
reports what the **robot** acknowledged — never an optimistic "done".
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.session import get_session
from ...core.security import create_token
from ...models.enums import MissionStatus
from ...schemas.robots import (
    MissionOut,
    ReturnHomeBody,
    RobotDetail,
    RobotOut,
)
from ...schemas.orders import OrderDetail
from ...services import mission_service, robot_service
from ...services.mission_service import MissionError
from ..dependencies import Principal, require_admin, require_dispatch, require_robot_screen, require_staff

log = logging.getLogger("robot.api.robots")

router = APIRouter(prefix="/api/v1", tags=["robots"])


class RobotScreenAction(BaseModel):
    """A touchscreen action bound to one active mission."""

    robot_id: str = Field(min_length=1, max_length=64)
    mission_uid: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=280)


# ── robots ───────────────────────────────────────────────────────────────────

@router.get("/robots", response_model=list[RobotOut], summary="List the robots")
async def list_robots(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> list[RobotOut]:
    robots = await robot_service.list_robots(session)
    return [RobotOut.model_validate(r) for r in robots]


@router.post("/robots/{robot_id}/gateway-token", summary="Create a robot gateway token")
async def gateway_token(
    robot_id: str,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> dict:
    """Issue a robot-only token for the gateway WebSocket.

    This endpoint is deliberately admin-only and returns the token once. Store
    it in the robot gateway environment, never in a browser or staff profile.
    """
    robot = await robot_service.get_or_create_robot(session, robot_id)
    token = create_token(
        robot.robot_id,
        "gateway",
        token_type="robot",
        extra_claims={"robot_id": robot.robot_id},
    )
    await session.commit()
    return {"ok": True, "robot_id": robot.robot_id, "token": token, "token_type": "robot"}


@router.get("/robots/{robot_id}", response_model=RobotDetail, summary="Robot details")
async def get_robot(
    robot_id: int,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> RobotDetail:
    robot = await robot_service.get_robot(session, robot_id)
    if robot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Robot not found")
    detail = RobotDetail(**RobotOut.model_validate(robot).model_dump())
    from ...repositories import robots as robots_repo

    missions = await robots_repo.list_missions(session, robot_id=robot.id, limit=10)
    detail.recent_missions = [MissionOut.model_validate(m) for m in missions]
    return detail


@router.get("/robots/{robot_id}/missions", response_model=list[MissionOut],
            summary="The robot's missions")
async def robot_missions(
    robot_id: int,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> list[MissionOut]:
    from ...repositories import robots as robots_repo

    robot = await robot_service.get_robot(session, robot_id)
    if robot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Robot not found")
    missions = await robots_repo.list_missions(session, robot_id=robot.id, limit=limit)
    return [MissionOut.model_validate(m) for m in missions]


@router.post("/robots/{robot_id}/return-home", summary="Send a robot back to its home position")
async def return_home(
    robot_id: int,
    payload: ReturnHomeBody,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_dispatch),
) -> dict:
    """Request a manual return home (Flutter / admin dashboard).

    The answer states whether the robot **acknowledged** the command — it is
    never reported as "returned home" until the gateway confirms the arrival.
    """
    robot = await robot_service.get_robot(session, robot_id)
    if robot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Robot not found")
    try:
        mission = await mission_service.request_return_home(
            session, robot=robot, actor=principal.role, actor_id=None,
            auto=False, command_id=payload.command_id,
        )
    except MissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await session.commit()
    return {
        "ok": True,
        "status": "ACKNOWLEDGED",
        "message": "The robot accepted the return command; it is on its way home.",
        "mission_id": mission.id,
        "mission_uid": mission.mission_uid,
        "mission_status": mission.status,
    }


# ── robot screen (the touchscreen on the robot) ──────────────────────────────

@router.get("/robot-screen/mission", summary="The mission the robot is currently running")
async def robot_screen_mission(
    robot: str = Query(..., description="The robot id, e.g. robot_01"),
    session: AsyncSession = Depends(get_session),
    _screen=Depends(require_robot_screen),
) -> dict:
    """Read-only view for the robot's touchscreen.

    The screen authenticates with the robot's own token; the mission data it
    shows is always the backend's authoritative state.
    """
    from ...repositories import robots as robots_repo

    robot_row = await robot_service.get_robot_by_robot_id(session, robot)
    if robot_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown robot")
    mission = await robots_repo.active_mission_for_robot(session, robot_row.id)
    payload = {
        "robot_id": robot_row.robot_id,
        "name": robot_row.name,
        "connection_status": robot_row.connection_status,
        "availability": robot_row.availability,
        "battery_percent": robot_row.battery_percent,
        "fault_code": robot_row.fault_code,
        "fault_detail": robot_row.fault_detail,
        "mission": None,
    }
    if mission is not None:
        payload["mission"] = MissionOut.model_validate(mission).model_dump()
        if mission.order_id:
            from ...services import order_service

            order = await order_service.load_order(session, mission.order_id)
            if order is not None:
                payload["order"] = {
                    "id": order.id,
                    "code": order.code,
                    "status": order.status,
                    "table_label": order.table_label,
                    "items": order.item_payload,
                    "note": order.special_instructions,
                }
    return payload


@router.post("/robot-screen/confirm-collection", response_model=OrderDetail,
             summary="Confirm collection from the robot touchscreen")
async def robot_screen_confirm_collection(
    payload: RobotScreenAction,
    session: AsyncSession = Depends(get_session),
    _screen=Depends(require_robot_screen),
) -> OrderDetail:
    """Allow only the configured robot screen to confirm its active mission.

    The screen token is deliberately separate from staff JWTs.  The mission and
    robot ids are checked together so a screen cannot confirm another robot's
    order by changing a request parameter.
    """
    from ...repositories import robots as robots_repo

    robot = await robot_service.get_robot_by_robot_id(session, payload.robot_id)
    mission = await robots_repo.get_mission_by_uid(session, payload.mission_uid)
    if robot is None or mission is None or mission.robot_id != robot.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Unknown robot mission")
    if mission.order_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This mission has no food order")

    from ...services import order_service

    order = await order_service.load_order(session, mission.order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    try:
        await order_service.confirm_collection(
            session, order, actor="robot_screen", actor_name=payload.robot_id,
            note=payload.note or "Confirmed on robot touchscreen",
        )
    except Exception as exc:
        from ...services.mission_service import MissionError
        from ...services.order_service import OrderError
        if isinstance(exc, (OrderError, MissionError)):
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        raise
    await session.commit()
    await session.refresh(order)
    from .orders import _detail
    return _detail(order)


@router.post("/robot-screen/request-assistance",
             summary="Request assistance from the robot touchscreen")
async def robot_screen_request_assistance(
    payload: RobotScreenAction,
    session: AsyncSession = Depends(get_session),
    _screen=Depends(require_robot_screen),
) -> dict:
    from ...repositories import robots as robots_repo

    robot = await robot_service.get_robot_by_robot_id(session, payload.robot_id)
    mission = await robots_repo.get_mission_by_uid(session, payload.mission_uid)
    if robot is None or mission is None or mission.robot_id != robot.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Unknown robot mission")
    try:
        ack = await mission_service.send_mission_command(
            session, robot=robot, mission=mission,
            command="REQUEST_ASSISTANCE", actor="robot_screen",
        )
    except Exception as exc:
        from ...services.mission_service import MissionError
        if isinstance(exc, MissionError):
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        raise
    await session.commit()
    return {"ok": True, "ack": ack, "mission_uid": mission.mission_uid}
