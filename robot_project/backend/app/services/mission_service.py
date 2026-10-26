"""Mission lifecycle and the bridge between the robot gateway and the database.

The rules this service enforces:

* A mission is **created** when an order is assigned to a robot.
* A mission only becomes ``DISPATCH_REQUESTED`` after the gateway **acknowledged**
  the dispatch command.
* A mission only reaches ``ARRIVED_AT_DESTINATION`` after the gateway reported a
  successful Nav2 result (``NAVIGATION_SUCCEEDED``) — never on a timer.
* An order is only ``DELIVERED`` after a collection confirmation.
* A mission only becomes ``COMPLETED`` after the robot reported it is home
  (``HOME_REACHED``).

Every gateway frame is stored as a :class:`MissionEvent` before it is applied, so
the evidence trail survives even if the state machine rejects the transition.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..events.outbox import enqueue_event
from ..events.schemas import (
    GatewayCommandAck,
    GatewayFrame,
    GatewayMissionEvent,
    new_id,
)
from ..models.enums import (
    EventType,
    MissionEventType,
    MissionStatus,
    RobotAvailability,
    RobotConnectionStatus,
)
from ..models.robot import Robot, RobotMission
from ..repositories import robots as robot_repo
from ..state_machines import mission as mission_sm
from . import notification_service, robot_service
from .gateway_registry import (
    CommandAckTimeout,
    CommandRejected,
    GatewayNotConnected,
    build_command,
    registry,
)

log = logging.getLogger("robot.missions")


class MissionError(RuntimeError):
    """A mission operation was refused."""

    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


# ── helpers ──────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _mission_payload(mission: RobotMission) -> dict[str, Any]:
    robot = None
    if mission.robot_id:
        robot = await robot_repo.get_robot(mission.robot_id)  # type: ignore[arg-type]
    return {
        "mission_id": mission.id,
        "mission_uid": mission.mission_uid,
        "order_id": mission.order_id,
        "status": mission.status,
        "mission_kind": mission.mission_kind,
        "destination_id": mission.destination_id,
        "destination_label": mission.destination_label,
        "robot_id": robot.robot_id if robot else None,
        "robot_name": robot.name if robot else None,
        "progress_percent": mission.progress_percent,
        "distance_remaining": mission.distance_remaining,
        "failure_reason": mission.failure_reason,
    }


async def _publish_mission(
    session: AsyncSession, mission: RobotMission, event_type: EventType = EventType.MISSION_UPDATED
) -> None:
    await enqueue_event(
        session, event_type, data=await _mission_payload(mission), channel="all",
    )


async def _transition_mission(
    session: AsyncSession,
    mission: RobotMission,
    target: str,
    actor: str,
    *,
    reason: Optional[str] = None,
    raise_on_error: bool = True,
) -> bool:
    current = mission.status
    if current == target:
        return True
    try:
        mission_sm.require_transition(current, target, actor)
    except mission_sm.MissionTransitionError as exc:
        if raise_on_error:
            raise MissionError(str(exc), 409) from exc
        log.warning("mission %s: refusing %s -> %s (%s)", mission.mission_uid, current, target, exc)
        return False
    mission.status = target
    await session.flush()
    await _publish_mission(session, mission)
    return True


# ── mission creation ─────────────────────────────────────────────────────────

async def create_mission(
    session: AsyncSession,
    *,
    order: Any,
    robot: Robot,
    destination_id: str,
    destination_label: str,
    actor: str,
    actor_id: Optional[int] = None,
    mission_kind: str = "delivery",
) -> RobotMission:
    """Create a mission and move it to ``ASSIGNED``."""
    mission = RobotMission(
        mission_uid=new_id("msn"),
        robot_id=robot.id,
        order_id=getattr(order, "id", None),
        destination_id=destination_id,
        destination_label=destination_label,
        status=MissionStatus.CREATED.value,
        mission_kind=mission_kind,
        requested_by_id=actor_id,
        requested_at=_now(),
    )
    session.add(mission)
    await session.flush()

    await _transition_mission(session, mission, MissionStatus.ASSIGNED.value, actor)
    robot.current_mission_id = mission.id
    robot.availability = RobotAvailability.BUSY.value
    await session.flush()
    return mission


async def notify_gateway_of_assignment(
    session: AsyncSession, mission: RobotMission, robot: Robot
) -> dict[str, Any]:
    """Send ``ASSIGN_MISSION`` and wait for the gateway's acknowledgement.

    The mission stays in ``ASSIGNED`` when the gateway does not acknowledge —
    the backend never pretends the robot accepted the work.
    """
    command = build_command(
        "ASSIGN_MISSION",
        robot.robot_id,
        mission_uid=mission.mission_uid,
        destination_id=mission.destination_id,
        order_code=getattr(mission.order, "code", None) if mission.order else None,
        payload={
            "destination_label": mission.destination_label,
            "mission_kind": mission.mission_kind,
        },
    )
    try:
        ack = await registry.send_command(robot.robot_id, command)
    except (GatewayNotConnected, CommandAckTimeout, CommandRejected) as exc:
        log.warning("ASSIGN_MISSION for robot %s failed: %s", robot.robot_id, exc)
        await robot_repo.add_mission_event(
            session, mission, command.event_uid, "ASSIGN_FAILED", "backend",
            {"command_id": command.command_id, "error": str(exc)},
        )
        raise MissionError(f"The robot did not accept the mission: {exc}", 503) from exc

    mission.acknowledged_at = _now()
    await session.flush()
    await robot_repo.add_mission_event(
        session, mission, command.event_uid, MissionEventType.MISSION_ACCEPTED.value,
        "gateway", {"command_id": command.command_id, "ack": ack},
    )
    await _publish_mission(session, mission)
    return ack


# ── inbound gateway frames ───────────────────────────────────────────────────

async def apply_gateway_frame(
    session: AsyncSession, frame: GatewayFrame, robot: Robot
) -> dict[str, Any]:
    """Apply a frame received from the gateway.

    Returns a small dict describing what happened (used by the WebSocket route
    and by the tests).
    """
    if isinstance(frame, GatewayCommandAck):
        return {"handled": "command_ack", "status": frame.status}

    if isinstance(frame, GatewayMissionEvent):
        return await apply_mission_event(session, frame, robot)

    return {"handled": "ignored", "frame": getattr(frame, "frame", "unknown")}


async def apply_mission_event(
    session: AsyncSession, frame: GatewayMissionEvent, robot: Robot
) -> dict[str, Any]:
    event_type = frame.event_type
    mission = await robot_repo.get_mission_by_uid(session, frame.mission_uid)
    if mission is None:
        log.warning("mission event for unknown mission %s", frame.mission_uid)
        return {"handled": "unknown_mission"}

    # Idempotency: the same event_uid must never be applied twice.
    if await robot_repo.event_exists(session, frame.event_uid):
        log.info("ignoring duplicate mission event %s", frame.event_uid)
        return {"handled": "duplicate"}

    await robot_repo.add_mission_event(
        session, mission, frame.event_uid, event_type, "gateway",
        {"message": frame.message, "position": frame.position, **frame.data},
    )
    await session.flush()

    if event_type == MissionEventType.MISSION_ACCEPTED.value:
        mission.acknowledged_at = _now()
        await session.flush()
        return {"handled": "mission_accepted"}

    if event_type == MissionEventType.NAVIGATION_STARTED.value:
        mission.started_at = mission.started_at or _now()
        mission.progress_percent = 0.0
        if mission.mission_kind == "return_home":
            await _transition_mission(
                session, mission, MissionStatus.RETURNING_HOME.value, "system")
        else:
            await _transition_mission(
                session, mission, MissionStatus.NAVIGATING_TO_TABLE.value, "system")
            await _order_moved_to_delivering(session, mission)
        await session.flush()
        return {"handled": "navigation_started"}

    if event_type == MissionEventType.NAVIGATION_PROGRESS.value:
        distance = frame.data.get("distance_remaining")
        percent = frame.data.get("progress_percent")
        if distance is not None:
            try:
                mission.distance_remaining = float(distance)
            except (TypeError, ValueError):
                pass
        if percent is not None:
            try:
                mission.progress_percent = max(0.0, min(100.0, float(percent)))
            except (TypeError, ValueError):
                pass
        await session.flush()
        await _publish_mission(session, mission)
        return {"handled": "navigation_progress"}

    if event_type == MissionEventType.NAVIGATION_SUCCEEDED.value:
        mission.arrived_at = _now()
        mission.progress_percent = 100.0
        mission.distance_remaining = 0.0
        if mission.mission_kind == "return_home":
            await _transition_mission(
                session, mission, MissionStatus.DOCKED_OR_HOME.value, "system")
            await _transition_mission(
                session, mission, MissionStatus.COMPLETED.value, "system")
            mission.completed_at = _now()
            mission.result = "HOME_REACHED"
            robot.current_mission_id = None
            robot.availability = RobotAvailability.AVAILABLE.value
            await session.flush()
            await _publish_mission(session, mission, EventType.ROBOT_HOME_REACHED)
            await notification_service.notify(
                session,
                notification_type="HOME_REACHED",
                title=f"{robot.name} is back at the home position",
                body="The robot completed the return and is available again.",
                channels=["admin", "flutter", "robot"],
                payload={"robot_id": robot.robot_id, "mission_uid": mission.mission_uid},
                event_uid=f"home-{mission.mission_uid}",
            )
            return {"handled": "navigation_succeeded_home"}

        await _transition_mission(
            session, mission, MissionStatus.ARRIVED_AT_DESTINATION.value, "system")
        await _transition_mission(
            session, mission, MissionStatus.AWAITING_COLLECTION.value, "system")
        await session.flush()
        await _order_arrived(session, mission)
        await notification_service.notify(
            session,
            notification_type="ROBOT_ARRIVED",
            title=f"{robot.name} arrived at {mission.destination_label}",
            body="The robot is waiting for the customer to collect the order.",
            channels=["kitchen", "admin", "flutter", "robot", "customer"],
            payload={
                "robot_id": robot.robot_id,
                "mission_uid": mission.mission_uid,
                "order_id": mission.order_id,
                "code": getattr(mission.order, "code", None) if mission.order else None,
            },
            event_uid=f"arrived-{mission.mission_uid}",
        )
        return {"handled": "navigation_succeeded"}

    if event_type == MissionEventType.NAVIGATION_FAILED.value:
        mission.failure_reason = frame.message or "Navigation failed"
        await _transition_mission(
            session, mission, MissionStatus.NAVIGATION_FAILED.value, "system",
            raise_on_error=False,
        )
        await _transition_mission(
            session, mission, MissionStatus.RECOVERY_REQUIRED.value, "system",
            raise_on_error=False,
        )
        await session.flush()
        await _order_delivery_failed(session, mission, mission.failure_reason)
        await notification_service.notify(
            session,
            notification_type="NAVIGATION_FAILED",
            title=f"{robot.name} could not reach {mission.destination_label}",
            body=mission.failure_reason or "Navigation failed.",
            channels=["kitchen", "admin", "flutter", "robot", "customer"],
            payload={
                "robot_id": robot.robot_id,
                "mission_uid": mission.mission_uid,
                "order_id": mission.order_id,
                "code": getattr(mission.order, "code", None) if mission.order else None,
            },
            event_uid=f"navfail-{frame.event_uid}",
        )
        return {"handled": "navigation_failed"}

    if event_type == MissionEventType.MISSION_CANCELLED.value:
        mission.failure_reason = frame.message or "Cancelled by the robot"
        await _transition_mission(
            session, mission, MissionStatus.CANCELLED.value, "system", raise_on_error=False)
        await session.flush()
        await _publish_mission(session, mission)
        return {"handled": "mission_cancelled"}

    if event_type == MissionEventType.ROBOT_BLOCKED.value:
        await _transition_mission(
            session, mission, MissionStatus.BLOCKED.value, "system", raise_on_error=False)
        await session.flush()
        await notification_service.notify(
            session,
            notification_type="ROBOT_BLOCKED",
            title=f"{robot.name} is blocked",
            body=frame.message or "The robot reports it is blocked.",
            channels=["admin", "flutter", "robot"],
            payload={"robot_id": robot.robot_id, "mission_uid": mission.mission_uid},
            event_uid=f"blocked-{frame.event_uid}",
        )
        return {"handled": "robot_blocked"}

    if event_type == MissionEventType.ROBOT_OFFLINE.value:
        await _transition_mission(
            session, mission, MissionStatus.ROBOT_OFFLINE.value, "system", raise_on_error=False)
        await session.flush()
        return {"handled": "robot_offline"}

    if event_type == MissionEventType.ROBOT_FAULT.value:
        robot.fault_code = frame.data.get("fault_code") or "ROBOT_FAULT"
        robot.fault_detail = frame.message
        robot.availability = RobotAvailability.FAULTED.value
        await session.flush()
        await _transition_mission(
            session, mission, MissionStatus.RECOVERY_REQUIRED.value, "system",
            raise_on_error=False)
        await notification_service.notify(
            session,
            notification_type="ROBOT_FAULT",
            title=f"{robot.name} reported a fault",
            body=frame.message or "The robot needs assistance.",
            channels=["admin", "flutter", "robot", "customer"],
            payload={"robot_id": robot.robot_id, "mission_uid": mission.mission_uid},
            event_uid=f"fault-{frame.event_uid}",
        )
        return {"handled": "robot_fault"}

    if event_type == MissionEventType.ASSISTANCE_REQUIRED.value:
        await _transition_mission(
            session, mission, MissionStatus.RECOVERY_REQUIRED.value, "system",
            raise_on_error=False)
        await notification_service.notify(
            session,
            notification_type="ASSISTANCE_REQUIRED",
            title=f"{robot.name} needs assistance",
            body=frame.message or "A staff member has to check the robot.",
            channels=["admin", "flutter", "robot"],
            payload={"robot_id": robot.robot_id, "mission_uid": mission.mission_uid},
            event_uid=f"assist-{frame.event_uid}",
        )
        return {"handled": "assistance_required"}

    if event_type == MissionEventType.LOAD_CONFIRMED.value:
        await _transition_mission(
            session, mission, MissionStatus.READY_TO_DISPATCH.value, "system",
            raise_on_error=False)
        await session.flush()
        return {"handled": "load_confirmed"}

    if event_type == MissionEventType.COLLECTION_CONFIRMED.value:
        await _confirm_collection(session, mission, robot, actor="gateway")
        return {"handled": "collection_confirmed"}

    if event_type == MissionEventType.HOME_REACHED.value:
        mission.arrived_at = _now()
        await _transition_mission(
            session, mission, MissionStatus.DOCKED_OR_HOME.value, "system", raise_on_error=False)
        await _transition_mission(
            session, mission, MissionStatus.COMPLETED.value, "system", raise_on_error=False)
        mission.completed_at = _now()
        robot.current_mission_id = None
        robot.availability = RobotAvailability.AVAILABLE.value
        await session.flush()
        await _publish_mission(session, mission, EventType.ROBOT_HOME_REACHED)
        return {"handled": "home_reached"}

    return {"handled": "ignored", "event_type": event_type}


# ── order side effects of gateway events ─────────────────────────────────────

async def _order_moved_to_delivering(session: AsyncSession, mission: RobotMission) -> None:
    from . import order_service

    order = await order_service.load_order(session, mission.order_id)
    if order is None:
        return
    await order_service.transition(
        session, order, order_service.OrderStatus.OUT_FOR_DELIVERY.value,
        actor="system", reason="The robot started navigating",
    )


async def _order_arrived(session: AsyncSession, mission: RobotMission) -> None:
    from . import order_service

    order = await order_service.load_order(session, mission.order_id)
    if order is None:
        return
    await order_service.transition(
        session, order, order_service.OrderStatus.ARRIVED_AT_TABLE.value,
        actor="system", reason="The robot reported arrival at the table",
    )


async def _order_delivery_failed(
    session: AsyncSession, mission: RobotMission, reason: str
) -> None:
    from . import order_service

    order = await order_service.load_order(session, mission.order_id)
    if order is None:
        return
    try:
        await order_service.transition(
            session, order, order_service.OrderStatus.DELIVERY_FAILED.value,
            actor="system", reason=reason,
        )
    except Exception as exc:
        log.warning("could not mark order %s as failed: %s", mission.order_id, exc)


# ── collection + return home ─────────────────────────────────────────────────

async def _confirm_collection(
    session: AsyncSession, mission: RobotMission, robot: Robot, actor: str
) -> None:
    from . import order_service

    order = await order_service.load_order(session, mission.order_id)
    if order is not None:
        await order_service.transition(
            session, order, order_service.OrderStatus.COLLECTION_CONFIRMED.value,
            actor=actor, reason="Collection confirmed",
        )
        await order_service.transition(
            session, order, order_service.OrderStatus.DELIVERED.value,
            actor="system", reason="Collection confirmed — order completed",
        )
    await _transition_mission(
        session, mission, MissionStatus.COLLECTION_CONFIRMED.value, "system",
        raise_on_error=False,
    )
    await session.flush()

    await notification_service.notify(
        session,
        notification_type="COLLECTION_CONFIRMED",
        title=f"Order {getattr(order, 'code', '')} collected",
        body="The customer collected the order.",
        channels=["kitchen", "admin", "flutter", "robot", "customer"],
        payload={
            "order_id": getattr(order, "id", None),
            "code": getattr(order, "code", None),
            "mission_uid": mission.mission_uid,
            "robot_id": robot.robot_id,
        },
        event_uid=f"collected-{mission.mission_uid}",
    )

    if settings.AUTO_RETURN_HOME:
        await request_return_home(
            session, robot=robot, mission=mission, actor="system", auto=True,
        )


async def request_return_home(
    session: AsyncSession,
    *,
    robot: Robot,
    mission: Optional[RobotMission] = None,
    actor: str,
    actor_id: Optional[int] = None,
    auto: bool = False,
    command_id: Optional[str] = None,
) -> RobotMission:
    """Ask the robot to go home.

    * With ``mission`` — the delivery mission continues into its return phase.
    * Without ``mission`` — a standalone return mission is created (manual return).
    """
    if robot.connection_status != RobotConnectionStatus.ONLINE.value:
        raise MissionError("The robot is not connected — it cannot return home", 409)
    if robot.fault_code:
        raise MissionError(
            f"The robot reports a fault ({robot.fault_code}) — it cannot return home", 409)
    if robot.availability == RobotAvailability.CHARGING.value:
        raise MissionError("The robot is charging", 409)

    home = robot.home_destination_id or settings.ROBOT_DEFAULT_HOME_DESTINATION
    if not home:
        raise MissionError("The robot has no home destination configured", 409)

    if mission is not None:
        if mission.status not in (
            MissionStatus.COLLECTION_CONFIRMED.value,
            MissionStatus.RETURN_REQUESTED.value,
        ):
            raise MissionError(
                f"The order has not been collected yet (mission is {mission.status})", 409)
        await _transition_mission(
            session, mission, MissionStatus.RETURN_REQUESTED.value, "system")
        target = mission
    else:
        existing = await robot_repo.active_mission_for_robot(session, robot.id)
        if existing is not None and existing.status not in (
            MissionStatus.COLLECTION_CONFIRMED.value,
            MissionStatus.RETURN_REQUESTED.value,
            MissionStatus.RETURNING_HOME.value,
            MissionStatus.DOCKED_OR_HOME.value,
        ):
            raise MissionError(
                f"Robot '{robot.name}' is busy with mission #{existing.id} "
                f"({existing.status}). Cancel it first.", 409)
        target = await create_mission(
            session,
            order=None,
            robot=robot,
            destination_id=home,
            destination_label="Home position",
            actor=actor,
            actor_id=actor_id,
            mission_kind="return_home",
        )
        await _transition_mission(
            session, target, MissionStatus.READY_TO_DISPATCH.value, "system")

    command = build_command(
        "RETURN_HOME",
        robot.robot_id,
        mission_uid=target.mission_uid,
        destination_id=home,
        payload={"home_destination_id": home, "auto": auto},
    )
    if command_id:
        command.command_id = command_id

    try:
        ack = await registry.send_command(robot.robot_id, command)
    except (GatewayNotConnected, CommandAckTimeout, CommandRejected) as exc:
        await robot_repo.add_mission_event(
            session, target, command.event_uid, "RETURN_FAILED", "backend",
            {"command_id": command.command_id, "error": str(exc)},
        )
        await notification_service.notify(
            session,
            notification_type="RETURN_FAILED",
            title=f"{robot.name} could not start the return",
            body=str(exc),
            channels=["admin", "flutter", "robot"],
            payload={"robot_id": robot.robot_id, "mission_uid": target.mission_uid},
            event_uid=f"returnfail-{command.event_uid}",
        )
        raise MissionError(f"The robot did not accept the return command: {exc}", 503) from exc

    await robot_repo.add_mission_event(
        session, target, command.event_uid, "RETURN_COMMAND_ACKNOWLEDGED", "gateway",
        {"command_id": command.command_id, "ack": ack},
    )
    if mission is None:
        await _transition_mission(
            session, target, MissionStatus.DISPATCH_REQUESTED.value, "system")
    await session.flush()
    await _publish_mission(session, target, EventType.ROBOT_RETURN_STARTED)
    await notification_service.notify(
        session,
        notification_type="RETURN_STARTED",
        title=f"{robot.name} is returning home",
        body="The robot is driving back to the home position.",
        channels=["admin", "flutter", "robot", "customer"],
        payload={"robot_id": robot.robot_id, "mission_uid": target.mission_uid},
        event_uid=f"return-{target.mission_uid}",
    )
    return target


# ── operator commands ────────────────────────────────────────────────────────

async def send_mission_command(
    session: AsyncSession,
    robot: Robot,
    command: str,
    mission: Optional[RobotMission] = None,
    actor: str = "admin",
    wait: bool = True,
) -> dict[str, Any]:
    """Send a pause / resume / cancel / assistance command to the gateway."""
    if robot.connection_status != RobotConnectionStatus.ONLINE.value:
        raise MissionError("The robot is not connected", 409)
    cmd = build_command(
        command,
        robot.robot_id,
        mission_uid=mission.mission_uid if mission else None,
        destination_id=mission.destination_id if mission else None,
    )
    try:
        ack = await registry.send_command(robot.robot_id, cmd, wait_for_ack=wait)
    except (GatewayNotConnected, CommandAckTimeout, CommandRejected) as exc:
        raise MissionError(str(exc), 503) from exc
    if mission is not None:
        await robot_repo.add_mission_event(
            session, mission, cmd.event_uid, f"{command}_ACKNOWLEDGED", "gateway",
            {"command_id": cmd.command_id, "ack": ack},
        )
        await session.flush()
    return ack
