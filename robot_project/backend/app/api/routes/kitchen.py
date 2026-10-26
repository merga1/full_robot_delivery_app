"""Kitchen dashboard endpoints.

The kitchen board is a *view* of the authoritative orders — it never owns a
second copy of an order.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.session import get_session
from ...models.enums import NotificationType, OrderStatus
from ...schemas.orders import OrderOut, ReasonBody
from ...services import notification_service, order_service
from ..dependencies import Principal, require_kitchen

log = logging.getLogger("robot.api.kitchen")

router = APIRouter(prefix="/api/v1/kitchen", tags=["kitchen"])

#: the states the kitchen board shows, in the order they are displayed
BOARD_ORDER = [
    OrderStatus.PENDING.value,
    OrderStatus.ACCEPTED.value,
    OrderStatus.PREPARING.value,
    OrderStatus.READY_FOR_PICKUP.value,
    OrderStatus.READY_FOR_DISPATCH.value,
    OrderStatus.LOADED.value,
    OrderStatus.DELIVERY_FAILED.value,
]


@router.get("/board", summary="The kitchen board")
async def board(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_kitchen),
) -> dict:
    """All open orders, grouped by status and in the display order.

    The dashboard calls this after every reconnect so the screen always shows
    the authoritative state, not a replayed event stream.
    """
    orders = await order_service.list_orders(session, limit=500)
    groups: dict[str, list[dict]] = {state: [] for state in BOARD_ORDER}
    counts: dict[str, int] = {}
    for order in orders:
        counts[order.status] = counts.get(order.status, 0) + 1
        if order.status in groups:
            item = OrderOut.model_validate(order)
            groups[order.status].append(item.model_dump())
    return {
        "groups": groups,
        "counts": counts,
        "order": BOARD_ORDER,
        "server_time": None,
    }


@router.get("/orders", response_model=list[OrderOut], summary="Open kitchen orders")
async def open_orders(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_kitchen),
) -> list[OrderOut]:
    orders = await order_service.list_orders(session, limit=200)
    out = []
    for order in orders:
        if order.status in (
            OrderStatus.PENDING.value,
            OrderStatus.ACCEPTED.value,
            OrderStatus.PREPARING.value,
            OrderStatus.READY_FOR_PICKUP.value,
            OrderStatus.READY_FOR_DISPATCH.value,
            OrderStatus.LOADED.value,
        ):
            out.append(OrderOut.model_validate(order))
    return out


@router.post("/orders/{order_id}/delay", summary="Report a delay for an order")
async def report_delay(
    order_id: int,
    payload: ReasonBody,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_kitchen),
) -> dict:
    order = await order_service.load_order(session, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    notifications = await notification_service.notify(
        session,
        notification_type=NotificationType.DELAY_REPORTED.value,
        title=f"Delay reported for order {order.code}",
        body=payload.reason or "The kitchen reported a delay.",
        payload={"code": order.code, "order_id": order.id,
                 "table_label": order.table_label},
        event_uid=f"delay-{order.code}",
        correlation_id=order.code,
    )
    await session.commit()
    return {"ok": True, "notifications": len(notifications)}
