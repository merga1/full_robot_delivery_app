"""Robot registry: identity, heartbeat, connection state and availability."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..events.outbox import enqueue_event
from ..models.enums import EventType, RobotAvailability, RobotConnectionStatus
from ..models.robot import Robot
from ..repositories import robots as repo

log = logging.getLogger("robot.robots")


class RobotError(ValueError):
    pass


async def list_robots(session: AsyncSession) -> list[Robot]:
    return await repo.list_robots(session)


async def get_robot(session: AsyncSession, robot_id: int) -> Optional[Robot]:
    return await repo.get_robot(session, robot_id)


async def get_robot_by_robot_id(session: AsyncSession, robot_id: str) -> Optional[Robot]:
    return await repo.get_robot_by_robot_id(session, robot_id)


async def get_or_create_robot(
    session: AsyncSession, robot_id: str, name: Optional[str] = None
) -> Robot:
    """Return the robot row for ``robot_id``, creating it on first contact.

    The gateway authenticates first; this function only records the identity
    the gateway *proved* it owns.
    """
    robot = await repo.get_robot_by_robot_id(session, robot_id)
    if robot is not None:
        return robot
    robot = Robot(robot_id=robot_id, name=name or robot_id)
    session.add(robot)
    await session.flush()
    log.info("registered new robot %s", robot_id)
    return robot


async def mark_connected(
    session: AsyncSession,
    robot: Robot,
    *,
    gateway_version: Optional[str] = None,
    ros_distro: Optional[str] = None,
    navigation_action: Optional[str] = None,
    home_destination_id: Optional[str] = None,
) -> Robot:
    now = datetime.now(timezone.utc)
    robot.connection_status = RobotConnectionStatus.ONLINE.value
    robot.connected_at = now
    robot.last_heartbeat_at = now
    if gateway_version:
        robot.gateway_version = gateway_version
    if ros_distro:
        robot.ros_distro = ros_distro
    if navigation_action:
        robot.navigation_action = navigation_action
    if home_destination_id:
        robot.home_destination_id = home_destination_id
    if not robot.availability:
        robot.availability = RobotAvailability.AVAILABLE.value
    await session.flush()
    await enqueue_event(
        session, EventType.ROBOT_STATUS_UPDATED,
        data=_status_payload(robot), channel="admin",
    )
    return robot


async def record_heartbeat(
    session: AsyncSession,
    robot: Robot,
    *,
    battery_percent: Optional[float] = None,
    battery_voltage: Optional[float] = None,
    position: Optional[dict] = None,
    fault_code: Optional[str] = None,
    fault_detail: Optional[str] = None,
) -> Robot:
    now = datetime.now(timezone.utc)
    robot.last_heartbeat_at = now
    robot.connection_status = RobotConnectionStatus.ONLINE.value
    if battery_percent is not None:
        robot.battery_percent = float(battery_percent)
    if battery_voltage is not None:
        robot.battery_voltage = float(battery_voltage)
    if position:
        robot.pose_x = _maybe_float(position.get("x"))
        robot.pose_y = _maybe_float(position.get("y"))
        robot.pose_yaw = _maybe_float(position.get("yaw"))
        robot.pose_frame = position.get("frame_id") or robot.pose_frame
    if fault_code:
        robot.fault_code = fault_code
        robot.fault_detail = fault_detail
        robot.availability = RobotAvailability.FAULTED.value
    elif robot.availability == RobotAvailability.FAULTED.value:
        robot.availability = RobotAvailability.AVAILABLE.value
        robot.fault_code = None
        robot.fault_detail = None
    await session.flush()

    await enqueue_event(
        session, EventType.ROBOT_STATUS_UPDATED,
        data=_status_payload(robot), channel="admin",
    )

    threshold = settings.LOW_BATTERY_THRESHOLD_PERCENT
    if (
        threshold
        and robot.battery_percent is not None
        and robot.battery_percent <= threshold
        and not robot.fault_code
    ):
        from . import notification_service

        await notification_service.notify(
            session,
            notification_type="LOW_BATTERY",
            title=f"{robot.name} battery low",
            body=f"Battery at {robot.battery_percent:.0f}% (threshold {threshold}%)",
            channels=["admin", "flutter", "robot"],
            payload={"robot_id": robot.robot_id, "battery_percent": robot.battery_percent},
            event_uid=f"lowbattery-{robot.robot_id}-{int(now.timestamp()) // 300}",
        )
    return robot


async def mark_disconnected(session: AsyncSession, robot: Robot, reason: str = "") -> Robot:
    robot.connection_status = RobotConnectionStatus.OFFLINE.value
    robot.fault_code = robot.fault_code or "GATEWAY_DISCONNECTED"
    robot.fault_detail = reason or robot.fault_detail
    if robot.availability != RobotAvailability.CHARGING.value:
        robot.availability = RobotAvailability.FAULTED.value
    await session.flush()
    await enqueue_event(
        session, EventType.ROBOT_OFFLINE,
        data={**_status_payload(robot), "reason": reason}, channel="admin",
    )
    from . import notification_service

    await notification_service.notify(
        session,
        notification_type="ROBOT_OFFLINE",
        title=f"{robot.name} went offline",
        body=reason or "The robot gateway stopped sending heartbeats.",
        channels=["admin", "flutter"],
        payload={"robot_id": robot.robot_id, "reason": reason},
        event_uid=f"offline-{robot.robot_id}-{int(datetime.now(timezone.utc).timestamp())}",
    )
    return robot


async def sweep_stale_robots(session: AsyncSession) -> list[Robot]:
    """Mark robots whose heartbeat timed out as offline.

    A disconnected robot is **never** reported as available.
    """
    timeout = settings.ROBOT_GATEWAY_HEARTBEAT_TIMEOUT_SECONDS
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout)
    stmt = select(Robot).where(
        Robot.connection_status != RobotConnectionStatus.OFFLINE.value
    )
    stale: list[Robot] = []
    for robot in (await session.execute(stmt)).scalars().all():
        if robot.last_heartbeat_at is None or robot.last_heartbeat_at < cutoff:
            await mark_disconnected(
                session, robot,
                reason=f"No heartbeat for more than {timeout} s",
            )
            stale.append(robot)
    return stale


async def set_availability(
    session: AsyncSession, robot: Robot, availability: str
) -> Robot:
    robot.availability = availability
    await session.flush()
    await enqueue_event(
        session, EventType.ROBOT_STATUS_UPDATED,
        data=_status_payload(robot), channel="admin",
    )
    return robot


def _status_payload(robot: Robot) -> dict:
    return {
        "robot_id": robot.robot_id,
        "id": robot.id,
        "name": robot.name,
        "connection_status": robot.connection_status,
        "availability": robot.availability,
        "battery_percent": robot.battery_percent,
        "battery_voltage": robot.battery_voltage,
        "fault_code": robot.fault_code,
        "fault_detail": robot.fault_detail,
        "home_destination_id": robot.home_destination_id,
        "current_mission_id": robot.current_mission_id,
        "position": {
            "x": robot.pose_x, "y": robot.pose_y, "yaw": robot.pose_yaw,
            "frame_id": robot.pose_frame,
        },
        "last_heartbeat_at": robot.last_heartbeat_at.isoformat() if robot.last_heartbeat_at else None,
    }


def _maybe_float(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
