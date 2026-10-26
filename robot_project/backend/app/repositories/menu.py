"""Menu and table repositories."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.menu import MenuItem
from ..models.table import RestaurantTable


async def list_menu_items(
    session: AsyncSession, only_available: bool = False
) -> list[MenuItem]:
    stmt = select(MenuItem)
    if only_available:
        stmt = stmt.where(MenuItem.available.is_(True))
    stmt = stmt.order_by(MenuItem.sort_order, MenuItem.category, MenuItem.name)
    return list((await session.execute(stmt)).scalars().all())


async def get_menu_item(session: AsyncSession, item_id: int) -> Optional[MenuItem]:
    return await session.get(MenuItem, item_id)


async def get_menu_item_by_external_key(
    session: AsyncSession, key: str
) -> Optional[MenuItem]:
    stmt = select(MenuItem).where(MenuItem.external_key == key)
    return (await session.execute(stmt)).scalars().first()


async def list_tables(session: AsyncSession, active_only: bool = False) -> list[RestaurantTable]:
    stmt = select(RestaurantTable)
    if active_only:
        stmt = stmt.where(RestaurantTable.is_active.is_(True))
    stmt = stmt.order_by(RestaurantTable.table_number)
    return list((await session.execute(stmt)).scalars().all())


async def get_table(session: AsyncSession, table_id: int) -> Optional[RestaurantTable]:
    return await session.get(RestaurantTable, table_id)


async def get_table_by_code(session: AsyncSession, code: str) -> Optional[RestaurantTable]:
    stmt = select(RestaurantTable).where(
        RestaurantTable.public_code == (code or "").strip().upper()
    )
    return (await session.execute(stmt)).scalars().first()


async def get_table_by_number(session: AsyncSession, number: str) -> Optional[RestaurantTable]:
    stmt = select(RestaurantTable).where(
        RestaurantTable.table_number == (number or "").strip()
    )
    return (await session.execute(stmt)).scalars().first()
