"""Menu and table catalog service."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import new_opaque_token
from ..events.outbox import enqueue_event
from ..models.enums import EventType
from ..models.menu import MenuItem
from ..models.table import RestaurantTable
from ..repositories import menu as repo
from . import audit_service


class CatalogError(ValueError):
    """Business rule violation for the catalog."""


async def list_menu(session: AsyncSession, only_available: bool = False) -> list[MenuItem]:
    return await repo.list_menu_items(session, only_available=only_available)


async def get_menu_item(session: AsyncSession, item_id: int) -> Optional[MenuItem]:
    return await repo.get_menu_item(session, item_id)


async def save_menu_item(
    session: AsyncSession,
    *,
    item_id: Optional[int] = None,
    name: str,
    price: float,
    description: str = "",
    category: str = "General",
    available: bool = True,
    image_url: Optional[str] = None,
    preparation_note: Optional[str] = None,
    sort_order: int = 0,
    external_key: Optional[str] = None,
    actor: str = "admin",
) -> MenuItem:
    name = (name or "").strip()
    if not name:
        raise CatalogError("The item needs a name")
    if price < 0:
        raise CatalogError("The price cannot be negative")

    item: Optional[MenuItem] = None
    if item_id is not None:
        item = await repo.get_menu_item(session, item_id)
        if item is None:
            raise CatalogError("Menu item not found")
    else:
        item = MenuItem(name=name)
        session.add(item)

    item.name = name
    item.price = round(float(price), 2)
    item.description = (description or "").strip()
    item.category = (category or "General").strip() or "General"
    item.available = bool(available)
    item.image_url = image_url
    item.preparation_note = preparation_note
    item.sort_order = int(sort_order)
    if external_key is not None:
        item.external_key = external_key.strip() or None
    await session.flush()

    await audit_service.record(
        session, actor=actor, action="menu.upsert",
        resource_type="menu_item", resource_id=item.id,
        metadata={"name": item.name, "price": item.price, "available": item.available},
    )
    await enqueue_event(
        session, EventType.MENU_UPDATED,
        data={"item_id": item.id, "name": item.name, "available": item.available},
        channel="all",
    )
    return item


async def delete_menu_item(session: AsyncSession, item_id: int, actor: str = "admin") -> bool:
    item = await repo.get_menu_item(session, item_id)
    if item is None:
        return False
    await audit_service.record(
        session, actor=actor, action="menu.delete",
        resource_type="menu_item", resource_id=item_id, metadata={"name": item.name},
    )
    await session.delete(item)
    await session.flush()
    await enqueue_event(
        session, EventType.MENU_UPDATED,
        data={"item_id": item_id, "deleted": True}, channel="all",
    )
    return True


async def list_tables(session: AsyncSession, active_only: bool = False) -> list[RestaurantTable]:
    return await repo.list_tables(session, active_only=active_only)


async def get_table(session: AsyncSession, table_id: int) -> Optional[RestaurantTable]:
    return await repo.get_table(session, table_id)


async def get_table_by_code(session: AsyncSession, code: str) -> Optional[RestaurantTable]:
    return await repo.get_table_by_code(session, code)


async def save_table(
    session: AsyncSession,
    *,
    table_id: Optional[int] = None,
    table_number: str,
    display_name: str,
    destination_id: Optional[str] = None,
    map_identifier: Optional[str] = None,
    seats: int = 4,
    is_active: bool = True,
    notes: Optional[str] = None,
    actor: str = "admin",
) -> RestaurantTable:
    table_number = (table_number or "").strip()
    display_name = (display_name or "").strip()
    if not table_number:
        raise CatalogError("The table needs a number")
    if not display_name:
        raise CatalogError("The table needs a display name")
    if seats < 0 or seats > 100:
        raise CatalogError("The number of seats must be between 0 and 100")

    table: Optional[RestaurantTable] = None
    if table_id is not None:
        table = await repo.get_table(session, table_id)
        if table is None:
            raise CatalogError("Table not found")
    else:
        table = RestaurantTable(
            table_number=table_number,
            display_name=display_name,
            public_code=new_opaque_token(6)[:8].upper(),
        )
        session.add(table)

    table.table_number = table_number
    table.display_name = display_name
    table.destination_id = (destination_id or "").strip() or None
    table.map_identifier = map_identifier
    table.seats = int(seats)
    table.is_active = bool(is_active)
    table.notes = notes
    await session.flush()

    await audit_service.record(
        session, actor=actor, action="table.upsert",
        resource_type="restaurant_table", resource_id=table.id,
        metadata={"table_number": table.table_number, "destination_id": table.destination_id},
    )
    await enqueue_event(
        session, EventType.TABLE_UPDATED,
        data={"table_id": table.id, "table_number": table.table_number,
              "destination_id": table.destination_id},
        channel="all",
    )
    return table


async def delete_table(session: AsyncSession, table_id: int, actor: str = "admin") -> bool:
    table = await repo.get_table(session, table_id)
    if table is None:
        return False
    await audit_service.record(
        session, actor=actor, action="table.delete",
        resource_type="restaurant_table", resource_id=table_id,
        metadata={"table_number": table.table_number},
    )
    await session.delete(table)
    await session.flush()
    await enqueue_event(
        session, EventType.TABLE_UPDATED,
        data={"table_id": table_id, "deleted": True}, channel="all",
    )
    return True
