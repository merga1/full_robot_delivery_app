"""Order repositories."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.order import Order, OrderItem, OrderStatusHistory


def _with_relations():
    return (
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.table))
    )


async def get_order(session: AsyncSession, order_id: int) -> Optional[Order]:
    stmt = _with_relations().where(Order.id == order_id)
    return (await session.execute(stmt)).scalars().unique().first()


async def get_order_by_code(session: AsyncSession, code: str) -> Optional[Order]:
    stmt = _with_relations().where(Order.code == (code or "").strip().upper())
    return (await session.execute(stmt)).scalars().unique().first()


async def get_order_by_tracking_token(
    session: AsyncSession, code: str, tracking_token: str
) -> Optional[Order]:
    stmt = _with_relations().where(
        Order.code == (code or "").strip().upper(),
        Order.tracking_token == (tracking_token or "").strip(),
    )
    return (await session.execute(stmt)).scalars().unique().first()


async def list_orders(
    session: AsyncSession,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    table_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    search: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Order]:
    stmt = _with_relations()
    if status:
        stmt = stmt.where(Order.status == status)
    if kind:
        stmt = stmt.where(Order.kind == kind)
    if table_id is not None:
        stmt = stmt.where(Order.table_id == table_id)
    if customer_id is not None:
        stmt = stmt.where(Order.customer_id == customer_id)
    if search:
        needle = f"%{search.strip()}%"
        stmt = stmt.where(
            (Order.code.ilike(needle)) | (Order.table_label.ilike(needle))
        )
    if since:
        stmt = stmt.where(Order.created_at >= since)
    stmt = stmt.order_by(Order.id.desc()).limit(max(1, min(limit, 500))).offset(max(0, offset))
    return list((await session.execute(stmt)).scalars().unique().all())


async def count_orders(session: AsyncSession, **filters) -> int:
    stmt = select(func.count(Order.id))
    status = filters.get("status")
    kind = filters.get("kind")
    if status:
        stmt = stmt.where(Order.status == status)
    if kind:
        stmt = stmt.where(Order.kind == kind)
    return int((await session.execute(stmt)).scalar() or 0)


async def orders_by_status(session: AsyncSession) -> dict[str, int]:
    stmt = select(Order.status, func.count(Order.id)).group_by(Order.status)
    rows = (await session.execute(stmt)).all()
    return {row[0]: int(row[1]) for row in rows}


async def add_history(
    session: AsyncSession,
    order: Order,
    previous_status: Optional[str],
    new_status: str,
    actor: str,
    actor_id: Optional[int] = None,
    reason: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> OrderStatusHistory:
    import json

    from ..db.base import utcnow

    row = OrderStatusHistory(
        order_id=order.id,
        previous_status=previous_status,
        new_status=new_status,
        actor=actor,
        actor_id=actor_id,
        reason=reason,
        metadata_json=json.dumps(metadata or {}, default=str),
        created_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def get_history(session: AsyncSession, order_id: int) -> list[OrderStatusHistory]:
    stmt = (
        select(OrderStatusHistory)
        .where(OrderStatusHistory.order_id == order_id)
        .order_by(OrderStatusHistory.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_order_items(session: AsyncSession, order_id: int) -> list[OrderItem]:
    stmt = select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id)
    return list((await session.execute(stmt)).scalars().all())


async def orders_for_robot(session: AsyncSession, robot_id: int) -> list[Order]:
    stmt = _with_relations().where(Order.assigned_robot_id == robot_id).order_by(Order.id.desc())
    return list((await session.execute(stmt)).scalars().unique().all())
