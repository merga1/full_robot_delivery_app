"""Mission endpoints.

A mission is the backend's record of one robot job.  Commands sent here are
forwarded to the robot's gateway; the response reports what the **robot**
acknowledged, never an optimistic success.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.session import get_session
from ...models.enums import MissionStatus
from ...schemas.robots import (
    MissionCommandBody,
    MissionDetail,
    MissionEventOut,
    MissionOut,
)
from ...services import mission_service, robot_service
from ...services.mission_service import MissionError
from ..dependencies import Principal, require_dispatch, require_staff

log = logging.getLogger("robot.api.missions")

router = APIRouter(prefix="/api/v1/missions", tags=["missions"])


async def _load_mission(session: AsyncSession, mission_id: int):
    from ...repositories import robots as robots_repo

    mission = await robots_repo.get_mission(session, mission_id)
    if mission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
    return mission


async def _robot_of_mission(session: AsyncSession, mission):
    if mission.robot_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="This mission has no robot")
    robot = await robot_service.get_robot(session, mission.robot_id)
    if robot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="The robot of this mission no longer exists")
    return robot


@router.get("", response_model=list[MissionOut], summary="List the missions")
async def list_missions(
    status_filter: Optional[str] = None,
    robot_id: Optional[int] = None,
    order_id: Optional[int] = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> list[MissionOut]:
    from ...repositories import robots as robots_repo

    missions = await robots_repo.list_missions(
        session, robot_id=robot_id, order_id=order_id, status=status_filter, limit=limit
    )
    return [MissionOut.model_validate(m) for m in missions]


@router.get("/{mission_id}", response_model=MissionDetail, summary="Mission details")
async def get_mission(
    mission_id: int,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> MissionDetail:
    mission = await _load_mission(session, mission_id)
    detail = MissionDetail(**MissionOut.model_validate(mission).model_dump())
    detail.events = [MissionEventOut.model_validate(e) for e in mission.events]
    return detail


@router.post("/{mission_id}/pause", summary="Pause a mission")
async def pause_mission(
    mission_id: int,
    payload: MissionCommandBody,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_dispatch),
) -> dict:
    mission = await _load_mission(session, mission_id)
    robot = await _robot_of_mission(session, mission)
    try:
        ack = await mission_service.send_mission_command(
            session, robot=robot, command="PAUSE_MISSION", mission=mission,
            actor=principal.username,
        )
    except MissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await mission_service._transition_mission(
        session, mission, MissionStatus.PAUSED.value, "admin", raise_on_error=False)
    await session.commit()
    return {"ok": True, "status": "ACKNOWLEDGED", "ack": ack,
            "mission_status": mission.status}


@router.post("/{mission_id}/resume", summary="Resume a mission")
async def resume_mission(
    mission_id: int,
    payload: MissionCommandBody,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_dispatch),
) -> dict:
    mission = await _load_mission(session, mission_id)
    robot = await _robot_of_mission(session, mission)
    try:
        ack = await mission_service.send_mission_command(
            session, robot=robot, command="RESUME_MISSION", mission=mission,
            actor=principal.username,
        )
    except MissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if mission.status == MissionStatus.PAUSED.value:
        target = (MissionStatus.NAVIGATING_TO_TABLE.value
                  if mission.mission_kind != "return_home"
                  else MissionStatus.RETURNING_HOME.value)
        await mission_service._transition_mission(
            session, mission, target, "admin", raise_on_error=False)
    await session.commit()
    return {"ok": True, "status": "ACKNOWLEDGED", "ack": ack,
            "mission_status": mission.status}


@router.post("/{mission_id}/cancel", summary="Cancel a mission")
async def cancel_mission(
    mission_id: int,
    payload: MissionCommandBody,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_dispatch),
) -> dict:
    mission = await _load_mission(session, mission_id)
    robot = await _robot_of_mission(session, mission)
    try:
        await mission_service.send_mission_command(
            session, robot=robot, command="CANCEL_MISSION", mission=mission,
            actor=principal.username,
        )
    except MissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await mission_service._transition_mission(
        session, mission, MissionStatus.CANCEL_REQUESTED.value, "admin", raise_on_error=False)
    await session.commit()
    return {"ok": True, "status": "REQUESTED", "mission_status": mission.status}


@router.post("/{mission_id}/request-assistance", summary="Ask for assistance")
async def request_assistance(
    mission_id: int,
    payload: MissionCommandBody,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> dict:
    mission = await _load_mission(session, mission_id)
    robot = await _robot_of_mission(session, mission)
    try:
        ack = await mission_service.send_mission_command(
            session, robot=robot, command="REQUEST_ASSISTANCE", mission=mission,
            actor=principal.username,
        )
    except MissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await session.commit()
    return {"ok": True, "status": "ACKNOWLEDGED", "ack": ack}
