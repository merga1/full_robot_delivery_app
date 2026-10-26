"""Robot and mission repositories."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models.robot import MissionEvent, Robot, RobotMission


async def get_robot(session: AsyncSession, robot_id: int) -> Optional[Robot]:
    return await session.get(Robot, robot_id)


async def get_robot_by_robot_id(session: AsyncSession, robot_id: str) -> Optional[Robot]:
    stmt = select(Robot).where(Robot.robot_id == (robot_id or "").strip())
    return (await session.execute(stmt)).scalars().first()


async def list_robots(session: AsyncSession) -> list[Robot]:
    stmt = select(Robot).order_by(Robot.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_mission(session: AsyncSession, mission_id: int) -> Optional[RobotMission]:
    stmt = (
        select(RobotMission)
        .options(selectinload(RobotMission.events))
        .where(RobotMission.id == mission_id)
    )
    return (await session.execute(stmt)).scalars().unique().first()


async def get_mission_by_uid(session: AsyncSession, mission_uid: str) -> Optional[RobotMission]:
    stmt = (
        select(RobotMission)
        .options(selectinload(RobotMission.events))
        .where(RobotMission.mission_uid == (mission_uid or "").strip())
    )
    return (await session.execute(stmt)).scalars().unique().first()


async def list_missions(
    session: AsyncSession,
    robot_id: Optional[int] = None,
    order_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> list[RobotMission]:
    stmt = select(RobotMission)
    if robot_id is not None:
        stmt = stmt.where(RobotMission.robot_id == robot_id)
    if order_id is not None:
        stmt = stmt.where(RobotMission.order_id == order_id)
    if status:
        stmt = stmt.where(RobotMission.status == status)
    stmt = stmt.order_by(RobotMission.id.desc()).limit(max(1, min(limit, 500)))
    return list((await session.execute(stmt)).scalars().unique().all())


async def active_mission_for_robot(
    session: AsyncSession, robot_id: int
) -> Optional[RobotMission]:
    """The mission a robot is currently working on, if any."""
    from ..models.enums import MissionStatus
    from ..state_machines import mission as mission_sm

    stmt = select(RobotMission).where(RobotMission.robot_id == robot_id)
    rows = list((await session.execute(stmt)).scalars().unique().all())
    for row in rows:
        if row.status in mission_sm.OPEN_STATUSES:
            return row
    return None


async def active_mission_for_order(
    session: AsyncSession, order_id: int
) -> Optional[RobotMission]:
    stmt = (
        select(RobotMission)
        .options(selectinload(RobotMission.events))
        .where(RobotMission.order_id == order_id)
        .order_by(RobotMission.id.desc())
    )
    return (await session.execute(stmt)).scalars().unique().first()


async def add_mission_event(
    session: AsyncSession,
    mission: RobotMission,
    event_uid: str,
    event_type: str,
    source: str = "gateway",
    payload: Optional[dict] = None,
) -> MissionEvent:
    import json

    from ..db.base import utcnow

    row = MissionEvent(
        mission_id=mission.id,
        event_uid=event_uid,
        event_type=event_type,
        source=source,
        payload_json=json.dumps(payload or {}, default=str),
        created_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def event_exists(session: AsyncSession, event_uid: str) -> bool:
    stmt = select(MissionEvent.id).where(MissionEvent.event_uid == event_uid).limit(1)
    return (await session.execute(stmt)).first() is not None


async def touch_heartbeat(
    session: AsyncSession, robot: Robot, *, connected: bool = False
) -> None:
    now = datetime.now(timezone.utc)
    robot.last_heartbeat_at = now
    if connected:
        robot.connected_at = now
    await session.flush()
