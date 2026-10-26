"""Order endpoints — the customer, kitchen, courier and admin workflow.

Ownership rules
---------------
* ``POST /orders`` is public (a customer at a table).  The response contains
  the order **code** and a **tracking token** — the sequential row id is never
  used as the public identifier.
* ``GET /orders/{id}`` requires staff, or the customer who owns the order
  (proved with ``X-Order-Token``).
* Every workflow action requires the role that owns it.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core import permissions as perm
from ...db.session import get_session
from ...models.enums import OrderKind, OrderStatus
from ...schemas.orders import (
    AssignRobotBody,
    CollectionBody,
    DispatchBody,
    OrderCreate,
    OrderDetail,
    OrderHistoryOut,
    OrderOut,
    ReasonBody,
)
from ...services import order_service, robot_service
from ...services.mission_service import MissionError
from ...services.order_service import OrderError
from ..dependencies import (
    Principal,
    get_optional_principal,
    get_principal,
    idempotency_key,
    require_admin,
    require_dispatch,
    require_kitchen,
    require_loader,
)

log = logging.getLogger("robot.api.orders")

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


# ── helpers ──────────────────────────────────────────────────────────────────

def _detail(order) -> OrderDetail:
    payload = order_service.order_payload(order)
    data = OrderDetail(
        **OrderOut.model_validate(order).model_dump(),
        items=payload["items"],
    )
    from ...state_machines import order as order_sm

    data.customer_status = order_sm.customer_visible_status(order.status)
    data.customer_status_detail = order_sm.customer_status_detail(order.status)
    data.timeline = order_sm.customer_visible_timeline(order.status)
    return data


async def _load_or_404(session: AsyncSession, order_id: int):
    order = await order_service.load_order(session, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, OrderError):
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if isinstance(exc, MissionError):
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    raise exc


# ── customer endpoints ───────────────────────────────────────────────────────

@router.post("", response_model=OrderDetail, status_code=status.HTTP_201_CREATED,
             summary="Place an order (or a robot call)")
async def create_order(
    payload: OrderCreate,
    session: AsyncSession = Depends(get_session),
    principal: Optional[Principal] = Depends(get_optional_principal),
    idem_key: Optional[str] = Depends(idempotency_key),
) -> OrderDetail:
    """Create an order.

    Prices are computed from the database menu — the client cannot influence
    them.  Repeating the request with the same ``Idempotency-Key`` returns the
    original order instead of creating a second one.
    """
    customer_id = None
    customer_name = payload.customer_name
    if principal is not None and principal.role != perm.Role.CUSTOMER.value:
        customer_name = customer_name or principal.username

    try:
        order = await order_service.create_order(
            session,
            table_code=payload.table_code,
            table_id=payload.table_id,
            items=payload.items,
            note=payload.note,
            kind=payload.kind,
            source="web",
            customer_id=customer_id,
            customer_name=customer_name,
            customer_phone=payload.customer_phone,
            idempotency_key=idem_key,
            actor="customer",
            actor_name=customer_name or "customer",
        )
    except OrderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    await session.commit()
    await session.refresh(order)
    detail = _detail(order)
    detail.tracking_token = order.tracking_token
    return detail


@router.get("", response_model=list[OrderOut], summary="List orders (staff)")
async def list_orders(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    kind: Optional[str] = None,
    table_id: Optional[int] = None,
    search: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[OrderOut]:
    if not principal.is_staff:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only staff can list all orders")
    orders = await order_service.list_orders(
        session, status=status_filter, kind=kind, table_id=table_id,
        search=search, limit=limit, offset=offset,
    )
    out = []
    for order in orders:
        item = OrderOut.model_validate(order)
        item.items = order.item_payload
        out.append(item)
    return out


@router.get("/track/{code}", response_model=OrderDetail,
            summary="Track an order with its code and tracking token")
async def track_order(
    code: str,
    token: str = Query(..., description="The tracking token returned when the order was placed"),
    session: AsyncSession = Depends(get_session),
) -> OrderDetail:
    """Public tracking.

    The code alone is not enough — the tracking token is required, so knowing
    an order number reveals nothing.
    """
    from ...repositories import orders as order_repo

    order = await order_repo.get_order_by_tracking_token(session, code, token)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Unknown order code or tracking token")
    detail = _detail(order)
    detail.tracking_token = order.tracking_token
    return detail


@router.get("/{order_id}", response_model=OrderDetail, summary="Order details")
async def get_order(
    order_id: int,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    order_token: Optional[str] = Header(default=None, alias="X-Order-Token"),
) -> OrderDetail:
    order = await _load_or_404(session, order_id)
    if not principal.is_staff and order.tracking_token != (order_token or ""):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="This order belongs to another customer")
    detail = _detail(order)
    detail.history = [
        OrderHistoryOut.model_validate(h)
        for h in await order_service.get_history(session, order.id)
    ]
    if order.mission_id:
        from ...repositories import robots as robots_repo
        from ...schemas.orders import MissionSummary

        mission = await robots_repo.get_mission(session, order.mission_id)
        if mission is not None:
            robot = (await robots_repo.get_robot(session, mission.robot_id)
                     if mission.robot_id else None)
            detail.mission = MissionSummary(
                id=mission.id,
                mission_uid=mission.mission_uid,
                status=mission.status,
                destination_label=mission.destination_label,
                robot_id=robot.robot_id if robot else None,
                started_at=mission.started_at,
                arrived_at=mission.arrived_at,
            )
    if not principal.is_staff:
        detail.tracking_token = order.tracking_token
    return detail


@router.get("/{order_id}/history", response_model=list[OrderHistoryOut],
            summary="The order's status history")
async def order_history(
    order_id: int,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[OrderHistoryOut]:
    order = await _load_or_404(session, order_id)
    if not principal.is_staff:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only staff can read the full history")
    return [OrderHistoryOut.model_validate(h)
            for h in await order_service.get_history(session, order.id)]


# ── kitchen workflow ─────────────────────────────────────────────────────────

@router.post("/{order_id}/accept", response_model=OrderDetail, summary="Accept an order")
async def accept_order(
    order_id: int,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_kitchen),
) -> OrderDetail:
    order = await _load_or_404(session, order_id)
    try:
        await order_service.accept_order(
            session, order, actor=principal.role, actor_name=principal.username)
    except OrderError as exc:
        _raise_http(exc)
    await session.commit()
    await session.refresh(order)
    return _detail(order)


@router.post("/{order_id}/reject", response_model=OrderDetail, summary="Reject an order")
async def reject_order(
    order_id: int,
    payload: ReasonBody,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_kitchen),
) -> OrderDetail:
    order = await _load_or_404(session, order_id)
    try:
        await order_service.reject_order(
            session, order, actor=principal.role, actor_name=principal.username,
            reason=payload.reason)
    except OrderError as exc:
        _raise_http(exc)
    await session.commit()
    await session.refresh(order)
    return _detail(order)


@router.post("/{order_id}/start-preparation", response_model=OrderDetail,
             summary="Start preparing an order")
async def start_preparation(
    order_id: int,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_kitchen),
) -> OrderDetail:
    order = await _load_or_404(session, order_id)
    try:
        await order_service.start_preparation(
            session, order, actor=principal.role, actor_name=principal.username)
    except OrderError as exc:
        _raise_http(exc)
    await session.commit()
    await session.refresh(order)
    return _detail(order)


@router.post("/{order_id}/ready", response_model=OrderDetail,
             summary="Mark the food as ready")
async def mark_ready(
    order_id: int,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_kitchen),
) -> OrderDetail:
    order = await _load_or_404(session, order_id)
    try:
        await order_service.mark_ready(
            session, order, actor=principal.role, actor_name=principal.username)
    except OrderError as exc:
        _raise_http(exc)
    await session.commit()
    await session.refresh(order)
    return _detail(order)


@router.post("/{order_id}/ready-for-dispatch", response_model=OrderDetail,
             summary="Hand the order over to the robot area")
async def ready_for_dispatch(
    order_id: int,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_kitchen),
) -> OrderDetail:
    order = await _load_or_404(session, order_id)
    try:
        await order_service.ready_for_dispatch(
            session, order, actor=principal.role, actor_name=principal.username)
    except OrderError as exc:
        _raise_http(exc)
    await session.commit()
    await session.refresh(order)
    return _detail(order)


@router.post("/{order_id}/cancel", response_model=OrderDetail, summary="Cancel an order")
async def cancel_order(
    order_id: int,
    payload: ReasonBody,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> OrderDetail:
    order = await _load_or_404(session, order_id)
    if not principal.is_staff and not order_service.order_sm.can_customer_cancel(order.status):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="This order can no longer be cancelled")
    try:
        await order_service.cancel_order(
            session, order, actor=principal.role, actor_name=principal.username,
            reason=payload.reason)
    except OrderError as exc:
        _raise_http(exc)
    await session.commit()
    await session.refresh(order)
    return _detail(order)


# ── robot workflow ───────────────────────────────────────────────────────────

@router.post("/{order_id}/assign-robot", summary="Assign a robot to the order")
async def assign_robot(
    order_id: int,
    payload: AssignRobotBody,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_dispatch),
) -> dict:
    order = await _load_or_404(session, order_id)
    robot = await robot_service.get_robot(session, payload.robot_id)
    if robot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Robot not found")
    if robot.connection_status != "ONLINE":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"{robot.name} is not connected")
    if robot.fault_code:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"{robot.name} reports a fault: {robot.fault_code}")
    try:
        mission = await order_service.assign_robot(
            session, order, robot, actor=principal.role, actor_name=principal.username)
    except (OrderError, MissionError) as exc:
        _raise_http(exc)
    await session.commit()
    return {"ok": True, "mission_id": mission.id, "mission_uid": mission.mission_uid,
            "status": mission.status, "robot_id": robot.robot_id}


@router.post("/{order_id}/confirm-loaded", summary="Confirm the food is on the robot")
async def confirm_loaded(
    order_id: int,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_loader),
) -> dict:
    order = await _load_or_404(session, order_id)
    try:
        mission = await order_service.confirm_loaded(
            session, order, actor=principal.role, actor_name=principal.username)
    except (OrderError, MissionError) as exc:
        _raise_http(exc)
    await session.commit()
    return {"ok": True, "mission_id": mission.id, "status": mission.status,
            "order_status": order.status}


@router.post("/{order_id}/dispatch", summary="Send the robot to the table")
async def dispatch(
    order_id: int,
    payload: DispatchBody,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_dispatch),
    idem_key: Optional[str] = Depends(idempotency_key),
) -> dict:
    order = await _load_or_404(session, order_id)
    try:
        mission = await order_service.dispatch(
            session, order, actor=principal.role, actor_name=principal.username,
            command_id=payload.command_id or idem_key,
        )
    except (OrderError, MissionError) as exc:
        _raise_http(exc)
    await session.commit()
    return {"ok": True, "mission_id": mission.id, "mission_uid": mission.mission_uid,
            "status": mission.status, "command_id": mission.dispatch_command_id,
            "order_status": order.status}


@router.post("/{order_id}/confirm-collection", response_model=OrderDetail,
             summary="Confirm that the customer collected the order")
async def confirm_collection(
    order_id: int,
    payload: CollectionBody,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> OrderDetail:
    order = await _load_or_404(session, order_id)
    if not principal.is_staff:
        # A customer confirms with the tracking token.
        from ...repositories import orders as order_repo

        supplied = payload.collected_by
        if not supplied or supplied != order.tracking_token:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Invalid tracking token")
    try:
        await order_service.confirm_collection(
            session, order, actor=principal.role, actor_name=principal.username,
            note=payload.note)
    except (OrderError, MissionError) as exc:
        _raise_http(exc)
    await session.commit()
    await session.refresh(order)
    return _detail(order)


@router.post("/track/{code}/confirm-collection", response_model=OrderDetail,
             summary="Customer confirms the collection with the tracking token")
async def customer_confirm_collection(
    code: str,
    token: str = Query(..., description="The order tracking token"),
    session: AsyncSession = Depends(get_session),
) -> OrderDetail:
    from ...repositories import orders as order_repo

    order = await order_repo.get_order_by_tracking_token(session, code, token)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Unknown order code or tracking token")
    try:
        await order_service.confirm_collection(
            session, order, actor="customer", actor_name="customer", note="customer app")
    except (OrderError, MissionError) as exc:
        _raise_http(exc)
    await session.commit()
    await session.refresh(order)
    return _detail(order)


# ── state machine introspection ─────────────────────────────────────────────

@router.get("/meta/state-machine", summary="The order state machine")
async def state_machine() -> dict:
    from ...state_machines import order as order_sm

    return {
        "states": [s.value for s in OrderStatus],
        "transitions": {
            src: {dst: sorted(roles) for dst, roles in targets.items()}
            for src, targets in order_sm.describe().items()
        },
        "terminal": sorted(order_sm.TERMINAL_STATUSES),
        "kinds": [k.value for k in OrderKind],
    }
