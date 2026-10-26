"""Order lifecycle: creation, kitchen workflow, robot assignment and collection.

All business rules live here — the API routes only translate HTTP into calls on
this module.  Prices are always computed from the database menu, never from the
client payload.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..events.outbox import enqueue_event
from ..models.enums import EventType, NotificationType, OrderKind, OrderStatus
from ..models.order import Order
from ..models.table import RestaurantTable
from ..repositories import menu as menu_repo
from ..repositories import notifications as notif_repo
from ..repositories import orders as order_repo
from ..state_machines import order as order_sm
from . import audit_service, mission_service, notification_service
from .mission_service import MissionError

log = logging.getLogger("robot.orders")

#: unambiguous alphabet for the human readable order code (no 0/O, 1/I/l)
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
MAX_QTY = 50


class OrderError(ValueError):
    """Business rule violation for an order."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))


def _new_tracking_token() -> str:
    return secrets.token_urlsafe(24)


# ── loading ──────────────────────────────────────────────────────────────────

async def load_order(session: AsyncSession, order_id: Optional[int]) -> Optional[Order]:
    if order_id is None:
        return None
    return await order_repo.get_order(session, order_id)


async def load_order_by_code(session: AsyncSession, code: str) -> Optional[Order]:
    return await order_repo.get_order_by_code(session, code)


async def list_orders(session: AsyncSession, **kwargs) -> list[Order]:
    return await order_repo.list_orders(session, **kwargs)


async def get_history(session: AsyncSession, order_id: int) -> list:
    return await order_repo.get_history(session, order_id)


# ── serialisation for events ─────────────────────────────────────────────────

def order_payload(order: Order) -> dict[str, Any]:
    return {
        "id": order.id,
        "code": order.code,
        "kind": order.kind,
        "status": order.status,
        "payment_status": order.payment_status,
        "table_label": order.table_label,
        "table_id": order.table_id,
        "total": order.total,
        "items": order.item_payload,
        "note": order.special_instructions,
        "source": order.source,
        "mission_id": order.mission_id,
        "assigned_robot_id": order.assigned_robot_id,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
    }


# ── transitions ──────────────────────────────────────────────────────────────

#: order status -> notification type raised by that transition
_NOTIFY_FOR_STATUS: dict[str, str] = {
    OrderStatus.ACCEPTED.value: NotificationType.ORDER_ACCEPTED.value,
    OrderStatus.REJECTED.value: NotificationType.ORDER_REJECTED.value,
    OrderStatus.PREPARING.value: NotificationType.ORDER_PREPARING.value,
    OrderStatus.READY_FOR_PICKUP.value: NotificationType.ORDER_READY.value,
    OrderStatus.READY_FOR_DISPATCH.value: NotificationType.ORDER_READY.value,
    OrderStatus.LOADED.value: NotificationType.ORDER_LOADED.value,
    OrderStatus.OUT_FOR_DELIVERY.value: NotificationType.ORDER_DISPATCHED.value,
    OrderStatus.ARRIVED_AT_TABLE.value: NotificationType.ROBOT_ARRIVED.value,
    OrderStatus.COLLECTION_CONFIRMED.value: NotificationType.COLLECTION_CONFIRMED.value,
    OrderStatus.DELIVERED.value: NotificationType.ORDER_DELIVERED.value,
    OrderStatus.CANCELLED.value: NotificationType.ORDER_REJECTED.value,
    OrderStatus.DELIVERY_FAILED.value: NotificationType.NAVIGATION_FAILED.value,
}


async def transition(
    session: AsyncSession,
    order: Order,
    target: str,
    *,
    actor: str,
    actor_name: Optional[str] = None,
    actor_id: Optional[int] = None,
    reason: Optional[str] = None,
    metadata: Optional[dict] = None,
    extra_payload: Optional[dict] = None,
) -> Order:
    """Move an order to ``target``, recording history, events and notifications."""
    current = order.status
    try:
        order_sm.require_transition(current, target, actor)
    except order_sm.OrderTransitionError as exc:
        raise OrderError(str(exc), 409) from exc

    order.status = target
    if target == OrderStatus.DELIVERED.value:
        from datetime import datetime, timezone

        order.delivered_at = datetime.now(timezone.utc)
    await session.flush()

    await order_repo.add_history(
        session, order, current, target,
        actor=actor_name or actor, actor_id=actor_id, reason=reason,
        metadata=metadata,
    )

    payload = {**order_payload(order), **(extra_payload or {})}
    await enqueue_event(
        session, _EVENT_FOR_STATUS.get(target, EventType.ORDER_UPDATED),
        data=payload, channel="all",
    )

    notification_type = _NOTIFY_FOR_STATUS.get(target)
    if notification_type:
        await notification_service.notify(
            session,
            notification_type=notification_type,
            title=_notification_title(order, target),
            body=_notification_body(order, target),
            payload=payload,
            event_uid=f"order-{order.code}-{target.lower()}",
            correlation_id=order.code,
        )
    return order


_EVENT_FOR_STATUS: dict[str, EventType] = {
    OrderStatus.ACCEPTED.value: EventType.ORDER_ACCEPTED,
    OrderStatus.REJECTED.value: EventType.ORDER_REJECTED,
    OrderStatus.PREPARING.value: EventType.ORDER_PREPARING,
    OrderStatus.READY_FOR_PICKUP.value: EventType.ORDER_READY,
    OrderStatus.READY_FOR_DISPATCH.value: EventType.ORDER_READY,
    OrderStatus.LOADED.value: EventType.ORDER_LOADED,
    OrderStatus.OUT_FOR_DELIVERY.value: EventType.ORDER_DISPATCHED,
    OrderStatus.ARRIVED_AT_TABLE.value: EventType.ROBOT_ARRIVED,
    OrderStatus.COLLECTION_CONFIRMED.value: EventType.ORDER_COLLECTION_CONFIRMED,
    OrderStatus.DELIVERED.value: EventType.ORDER_DELIVERED,
    OrderStatus.CANCELLED.value: EventType.ORDER_CANCELLED,
    OrderStatus.DELIVERY_FAILED.value: EventType.ORDER_DELIVERY_FAILED,
}


def _notification_title(order: Order, target: str) -> str:
    return {
        OrderStatus.ACCEPTED.value: f"Order {order.code} accepted",
        OrderStatus.REJECTED.value: f"Order {order.code} rejected",
        OrderStatus.PREPARING.value: f"Order {order.code} is being prepared",
        OrderStatus.READY_FOR_PICKUP.value: f"Order {order.code} is ready",
        OrderStatus.READY_FOR_DISPATCH.value: f"Order {order.code} is ready for dispatch",
        OrderStatus.LOADED.value: f"Order {order.code} loaded on the robot",
        OrderStatus.OUT_FOR_DELIVERY.value: f"Order {order.code} is on the way",
        OrderStatus.ARRIVED_AT_TABLE.value: f"Order {order.code} arrived at {order.table_label}",
        OrderStatus.COLLECTION_CONFIRMED.value: f"Order {order.code} collected",
        OrderStatus.DELIVERED.value: f"Order {order.code} delivered",
        OrderStatus.CANCELLED.value: f"Order {order.code} cancelled",
        OrderStatus.DELIVERY_FAILED.value: f"Delivery failed for order {order.code}",
    }.get(target, f"Order {order.code} updated")


def _notification_body(order: Order, target: str) -> str:
    items = ", ".join(f"{i['qty']}× {i['name']}" for i in order.item_payload)
    base = f"Table {order.table_label}" if order.table_label else "Table unknown"
    if target in (OrderStatus.DELIVERED.value, OrderStatus.COLLECTION_CONFIRMED.value):
        return base
    return f"{base} — {items}" if items else base


# ── creation ─────────────────────────────────────────────────────────────────

async def _resolve_table(
    session: AsyncSession, table_code: Optional[str], table_id: Optional[int]
) -> RestaurantTable:
    table: Optional[RestaurantTable] = None
    if table_code:
        table = await menu_repo.get_table_by_code(session, table_code)
        if table is None:
            table = await menu_repo.get_table_by_number(session, table_code)
    if table is None and table_id is not None:
        table = await menu_repo.get_table(session, table_id)
    if table is None:
        raise OrderError("Unknown table — scan the QR code on your table", 404)
    if not table.is_active:
        raise OrderError("This table is not in service", 409)
    return table


async def _price_items(session: AsyncSession, raw_items: list) -> tuple[list[dict], float]:
    if not raw_items:
        raise OrderError("Your order is empty")
    resolved: list[dict] = []
    total = 0.0
    for raw in raw_items:
        item = await menu_repo.get_menu_item(session, int(raw.menu_id))
        if item is None:
            raise OrderError("One of the items is no longer on the menu")
        if not item.available:
            raise OrderError(f"'{item.name}' is sold out")
        qty = int(raw.qty)
        if qty < 1 or qty > MAX_QTY:
            raise OrderError(f"At most {MAX_QTY} of one item please")
        line_total = round(float(item.price) * qty, 2)
        total += line_total
        resolved.append({
            "menu_item_id": item.id,
            "name": item.name,
            "unit_price": round(float(item.price), 2),
            "quantity": qty,
            "line_total": line_total,
            "customizations": raw.customizations or "",
            "preparation_note": raw.preparation_note or item.preparation_note,
        })
    return resolved, round(total, 2)


async def create_order(
    session: AsyncSession,
    *,
    table_code: Optional[str] = None,
    table_id: Optional[int] = None,
    items: Optional[list] = None,
    note: str = "",
    kind: str = OrderKind.ORDER.value,
    source: str = "web",
    customer_id: Optional[int] = None,
    customer_name: Optional[str] = None,
    customer_phone: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    actor: str = "customer",
    actor_name: str = "customer",
) -> Order:
    """Create an order (or a robot call)."""
    if kind not in (OrderKind.ORDER.value, OrderKind.CALL.value):
        raise OrderError(f"Unknown order kind '{kind}'")

    if idempotency_key:
        duplicate = (
            await session.execute(
                select(Order).where(Order.idempotency_key == idempotency_key)
            )
        ).scalars().first()
        if duplicate is not None:
            return duplicate

    table = await _resolve_table(session, table_code, table_id)

    if kind == OrderKind.CALL.value:
        resolved: list[dict] = []
        subtotal = 0.0
    else:
        resolved, subtotal = await _price_items(session, items or [])

    tax = round(subtotal * float(settings.TAX_RATE), 2)
    fees = round(float(settings.DELIVERY_FEE), 2) if kind == OrderKind.ORDER.value else 0.0
    total = round(subtotal + tax + fees, 2)

    order = Order(
        code=_new_code(),
        tracking_token=_new_tracking_token(),
        customer_id=customer_id,
        table_id=table.id,
        table_label=table.display_name,
        kind=kind,
        status=OrderStatus.PENDING.value,
        subtotal=subtotal,
        tax=tax,
        fees=fees,
        total=total,
        special_instructions=(note or "").strip()[:280],
        source=source,
        idempotency_key=idempotency_key,
    )
    session.add(order)
    await session.flush()

    from ..models.order import OrderItem

    for line in resolved:
        session.add(OrderItem(order_id=order.id, **line))
    await session.flush()

    await order_repo.add_history(
        session, order, None, OrderStatus.PENDING.value,
        actor=actor_name, actor_id=None, reason="Order placed",
    )

    await audit_service.record(
        session, actor=actor_name, action="order.create",
        resource_type="order", resource_id=order.id,
        metadata={"code": order.code, "kind": kind, "total": total,
                  "table": table.display_name},
    )

    title = (f"Call request from {table.display_name}" if kind == OrderKind.CALL.value
             else f"New order {order.code} — {table.display_name}")
    body = (f"Table {table.display_name} called the robot."
            if kind == OrderKind.CALL.value else
            ", ".join(f"{l['quantity']}× {l['name']}" for l in resolved) or "Robot call")
    await notification_service.notify(
        session,
        notification_type=NotificationType.NEW_ORDER.value,
        title=title,
        body=body,
        payload={**order_payload(order)},
        event_uid=f"neworder-{order.code}",
        correlation_id=order.code,
    )
    await enqueue_event(
        session, EventType.ORDER_CREATED,
        data=order_payload(order), channel="all",
    )
    log.info("order %s created (kind=%s total=%.2f source=%s)",
             order.code, kind, total, source)
    return order


# ── kitchen workflow ─────────────────────────────────────────────────────────

async def accept_order(
    session: AsyncSession, order: Order, *, actor: str, actor_name: str, actor_id: Optional[int] = None
) -> Order:
    return await transition(
        session, order, OrderStatus.ACCEPTED.value,
        actor="kitchen", actor_name=actor_name, actor_id=actor_id,
        reason="Accepted by the kitchen",
    )


async def reject_order(
    session: AsyncSession, order: Order, *, actor: str, actor_name: str,
    actor_id: Optional[int] = None, reason: str = "",
) -> Order:
    order.error = (reason or "")[:400] or None
    return await transition(
        session, order, OrderStatus.REJECTED.value,
        actor="kitchen", actor_name=actor_name, actor_id=actor_id, reason=reason,
    )


async def start_preparation(
    session: AsyncSession, order: Order, *, actor: str, actor_name: str,
    actor_id: Optional[int] = None,
) -> Order:
    return await transition(
        session, order, OrderStatus.PREPARING.value,
        actor="kitchen", actor_name=actor_name, actor_id=actor_id,
        reason="Preparation started",
    )


async def mark_ready(
    session: AsyncSession, order: Order, *, actor: str, actor_name: str,
    actor_id: Optional[int] = None,
) -> Order:
    """The kitchen finished the food.

    ``READY_FOR_PICKUP`` (food on the counter) and ``READY_FOR_DISPATCH``
    (handover to the robot) are two separate steps so the loading confirmation
    stays meaningful.
    """
    if order.kind == OrderKind.CALL.value:
        return await transition(
            session, order, OrderStatus.READY_FOR_PICKUP.value,
            actor="kitchen", actor_name=actor_name, actor_id=actor_id,
            reason="Call request acknowledged",
        )
    return await transition(
        session, order, OrderStatus.READY_FOR_PICKUP.value,
        actor="kitchen", actor_name=actor_name, actor_id=actor_id,
        reason="Food is ready",
    )


async def ready_for_dispatch(
    session: AsyncSession, order: Order, *, actor: str, actor_name: str,
    actor_id: Optional[int] = None,
) -> Order:
    return await transition(
        session, order, OrderStatus.READY_FOR_DISPATCH.value,
        actor="kitchen", actor_name=actor_name, actor_id=actor_id,
        reason="Handed over to the robot area",
    )


async def cancel_order(
    session: AsyncSession, order: Order, *, actor: str, actor_name: str,
    actor_id: Optional[int] = None, reason: str = "",
) -> Order:
    if order.status in (OrderStatus.OUT_FOR_DELIVERY.value,
                        OrderStatus.ARRIVED_AT_TABLE.value,
                        OrderStatus.COLLECTION_CONFIRMED.value):
        raise OrderError(
            "The robot is already on its way — stop the robot from the app instead", 409)
    order.error = (reason or "")[:400] or None
    return await transition(
        session, order, OrderStatus.CANCELLED.value,
        actor=actor, actor_name=actor_name, actor_id=actor_id, reason=reason,
    )


# ── robot workflow ───────────────────────────────────────────────────────────

async def assign_robot(
    session: AsyncSession, order: Order, robot, *, actor: str, actor_name: str,
    actor_id: Optional[int] = None,
) -> Any:
    """Assign a robot to the order and create the delivery mission."""
    if order.status not in (
        OrderStatus.READY_FOR_DISPATCH.value, OrderStatus.DELIVERY_FAILED.value
    ):
        raise OrderError(
            f"An order can only be assigned to a robot when it is ready for dispatch "
            f"(this one is {order.status})", 409)

    table = await menu_repo.get_table(session, order.table_id) if order.table_id else None
    if table is None:
        raise OrderError("The table of this order no longer exists", 404)
    destination = (table.destination_id or "").strip()
    if not destination:
        raise OrderError(
            f"Table '{table.display_name}' has no approved robot destination yet. "
            "Set one in the admin dashboard (Table configuration).", 409)

    existing = await order_repo.get_order(session, order.id)
    if existing is not None and existing.mission_id:
        old = await order_repo.get_order(session, order.id)
        if old and old.mission_id:
            old_mission = await _get_mission(session, old.mission_id)
            if old_mission is not None and not mission_service.mission_sm.is_terminal(old_mission.status):
                raise OrderError(
                    f"This order already has an active mission (#{old_mission.id})", 409)

    mission = await mission_service.create_mission(
        session,
        order=order,
        robot=robot,
        destination_id=destination,
        destination_label=table.display_name,
        actor="admin",
        actor_id=actor_id,
    )
    order.mission_id = mission.id
    order.assigned_robot_id = robot.id
    await session.flush()

    if order.kind == OrderKind.CALL.value:
        # Nothing to load: the call request is ready to go as soon as a robot
        # accepts it.
        await transition(
            session, order, OrderStatus.LOADED.value,
            actor="system", actor_name="system",
            reason="Call request — no load required",
        )
        await mission_service._transition_mission(
            session, mission, mission_service.MissionStatus.READY_TO_DISPATCH.value, "system",
        )
    else:
        await mission_service._transition_mission(
            session, mission,
            mission_service.MissionStatus.AWAITING_LOAD_CONFIRMATION.value, "system",
        )

    await mission_service.notify_gateway_of_assignment(session, mission, robot)

    await audit_service.record(
        session, actor=actor_name, action="order.assign_robot",
        resource_type="order", resource_id=order.id,
        metadata={"robot_id": robot.robot_id, "mission_id": mission.id},
    )
    return mission


async def confirm_loaded(
    session: AsyncSession, order: Order, *, actor: str, actor_name: str,
    actor_id: Optional[int] = None,
) -> Any:
    """Staff confirmed that the food is physically on the robot."""
    if order.status != OrderStatus.READY_FOR_DISPATCH.value:
        raise OrderError(
            f"The order must be ready for dispatch before loading it (it is {order.status})", 409)
    if order.mission_id is None:
        raise OrderError("No robot is assigned to this order", 409)

    mission = await _get_mission(session, order.mission_id)
    if mission is None:
        raise OrderError("The mission of this order no longer exists", 404)

    await transition(
        session, order, OrderStatus.LOADED.value,
        actor="courier", actor_name=actor_name, actor_id=actor_id,
        reason="Food loaded on the robot",
    )
    order.loaded_by_id = actor_id
    await session.flush()

    await mission_service._transition_mission(
        session, mission, mission_service.MissionStatus.READY_TO_DISPATCH.value, "courier",
    )
    await mission_service.send_mission_command(
        session, robot=await _robot_of(session, mission), command="PREPARE_DISPATCH",
        mission=mission, actor=actor_name, wait=False,
    )
    await audit_service.record(
        session, actor=actor_name, action="order.confirm_loaded",
        resource_type="order", resource_id=order.id,
        metadata={"mission_id": mission.id},
    )
    return mission


async def dispatch(
    session: AsyncSession, order: Order, *, actor: str, actor_name: str,
    actor_id: Optional[int] = None, command_id: Optional[str] = None,
) -> Any:
    """Send the robot to the table.

    The order must be **loaded** — a dispatch without a loading confirmation is
    refused by the backend.
    """
    if order.kind == OrderKind.ORDER.value and order.status != OrderStatus.LOADED.value:
        raise OrderError(
            "The food must be loaded on the robot before dispatching "
            f"(the order is {order.status})", 409)
    if order.kind == OrderKind.CALL.value and order.status not in (
        OrderStatus.LOADED.value, OrderStatus.READY_FOR_DISPATCH.value
    ):
        raise OrderError(f"The call request is {order.status} and cannot be dispatched", 409)
    if order.mission_id is None:
        raise OrderError("No robot is assigned to this order", 409)

    mission = await _get_mission(session, order.mission_id)
    if mission is None:
        raise OrderError("The mission of this order no longer exists", 404)
    if mission.status in (
        mission_service.MissionStatus.DISPATCH_REQUESTED.value,
        mission_service.MissionStatus.NAVIGATING_TO_TABLE.value,
    ):
        # Idempotent: the dispatch is already on its way.
        return mission
    if mission.status != mission_service.MissionStatus.READY_TO_DISPATCH.value:
        raise OrderError(
            f"The mission is '{mission.status}' and cannot be dispatched", 409)

    robot = await _robot_of(session, mission)
    if robot is None:
        raise OrderError("The robot of this mission no longer exists", 404)
    if robot.connection_status != "ONLINE":
        raise OrderError(f"{robot.name} is not connected", 409)
    if robot.fault_code:
        raise OrderError(f"{robot.name} reports a fault: {robot.fault_code}", 409)
    threshold = settings.MIN_BATTERY_PERCENT_TO_DISPATCH
    if threshold and robot.battery_percent is not None and robot.battery_percent < threshold:
        raise OrderError(
            f"{robot.name} has only {robot.battery_percent:.0f}% battery "
            f"(minimum {threshold}%)", 409)

    cmd = mission_service.build_command(
        "DISPATCH_MISSION",
        robot.robot_id,
        mission_uid=mission.mission_uid,
        destination_id=mission.destination_id,
        order_code=order.code,
    )
    if command_id:
        cmd.command_id = command_id

    try:
        ack = await mission_service.registry.send_command(robot.robot_id, cmd)
    except Exception as exc:
        raise MissionError(f"The robot did not accept the dispatch command: {exc}", 503) from exc

    mission.dispatch_command_id = cmd.command_id
    await session.flush()
    await mission_service._transition_mission(
        session, mission, mission_service.MissionStatus.DISPATCH_REQUESTED.value, "admin",
    )
    await session.flush()
    await mission_service.robot_repo.add_mission_event(
        session, mission, cmd.event_uid, "DISPATCH_COMMAND_ACKNOWLEDGED", "gateway",
        {"command_id": cmd.command_id, "ack": ack},
    )
    await audit_service.record(
        session, actor=actor_name, action="order.dispatch",
        resource_type="order", resource_id=order.id,
        metadata={"mission_id": mission.id, "robot_id": robot.robot_id,
                  "command_id": cmd.command_id},
    )
    return mission


async def confirm_collection(
    session: AsyncSession, order: Order, *, actor: str, actor_name: str,
    actor_id: Optional[int] = None, note: str = "",
) -> Any:
    """The customer (or staff) collected the order."""
    if order.status != OrderStatus.ARRIVED_AT_TABLE.value:
        raise OrderError(
            f"Collection can only be confirmed after the robot arrived "
            f"(the order is {order.status})", 409)
    if order.mission_id is None:
        raise OrderError("This order has no robot mission", 409)

    mission = await _get_mission(session, order.mission_id)
    if mission is None:
        raise OrderError("The mission of this order no longer exists", 404)

    robot = await _robot_of(session, mission)
    if robot is None:
        raise OrderError("The robot of this mission no longer exists", 404)

    await mission_service._confirm_collection(session, mission, robot, actor=actor)
    await audit_service.record(
        session, actor=actor_name, action="order.confirm_collection",
        resource_type="order", resource_id=order.id,
        metadata={"mission_id": mission.id, "note": note},
    )
    return mission


# ── small helpers ────────────────────────────────────────────────────────────

async def _get_mission(session: AsyncSession, mission_id: int):
    from ..repositories import robots as robots_repo

    return await robots_repo.get_mission(session, mission_id)


async def _robot_of(session: AsyncSession, mission):
    from ..repositories import robots as robots_repo

    if mission is None or mission.robot_id is None:
        return None
    return await robots_repo.get_robot(session, mission.robot_id)
