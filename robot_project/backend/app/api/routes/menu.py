"""Menu endpoints.

Unauthenticated customers only ever see **active, available** items.  Editing
requires the ``admin`` role.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.session import get_session
from ...schemas.menu import MenuItemCreate, MenuItemOut, MenuItemUpdate
from ...services import catalog_service
from ..dependencies import Principal, get_optional_principal, require_admin

log = logging.getLogger("robot.api.menu")

router = APIRouter(prefix="/api/v1/menu", tags=["menu"])


@router.get("", response_model=list[MenuItemOut], summary="Public menu")
async def list_menu(
    include_unavailable: bool = Query(default=False),
    principal: Principal | None = Depends(get_optional_principal),
    session: AsyncSession = Depends(get_session),
) -> list[MenuItemOut]:
    """The customer-facing menu.

    ``include_unavailable`` is honoured for staff members only — anonymous
    callers always get the available items.
    """
    staff = principal is not None and principal.is_staff
    items = await catalog_service.list_menu(
        session, only_available=not (include_unavailable and staff)
    )
    return [MenuItemOut.model_validate(i) for i in items]


@router.get("/{item_id}", response_model=MenuItemOut, summary="One menu item")
async def get_item(item_id: int, session: AsyncSession = Depends(get_session)) -> MenuItemOut:
    item = await catalog_service.get_menu_item(session, item_id)
    if item is None or not item.available:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Menu item not found")
    return MenuItemOut.model_validate(item)


@router.post("", response_model=MenuItemOut, status_code=status.HTTP_201_CREATED,
             summary="Create a menu item")
async def create_item(
    payload: MenuItemCreate,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> MenuItemOut:
    try:
        item = await catalog_service.save_menu_item(
            session,
            name=payload.name,
            price=payload.price,
            description=payload.description,
            category=payload.category,
            available=payload.available,
            image_url=payload.image_url,
            preparation_note=payload.preparation_note,
            sort_order=payload.sort_order,
            external_key=payload.external_key,
            actor=principal.username,
        )
    except catalog_service.CatalogError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(exc)) from exc
    await session.commit()
    return MenuItemOut.model_validate(item)


@router.patch("/{item_id}", response_model=MenuItemOut, summary="Update a menu item")
async def update_item(
    item_id: int,
    payload: MenuItemUpdate,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> MenuItemOut:
    current = await catalog_service.get_menu_item(session, item_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Menu item not found")
    data = payload.model_dump(exclude_unset=True)
    try:
        item = await catalog_service.save_menu_item(
            session,
            item_id=item_id,
            name=data.get("name", current.name),
            price=data.get("price", current.price),
            description=data.get("description", current.description),
            category=data.get("category", current.category),
            available=data.get("available", current.available),
            image_url=data.get("image_url", current.image_url),
            preparation_note=data.get("preparation_note", current.preparation_note),
            sort_order=data.get("sort_order", current.sort_order),
            external_key=data.get("external_key", current.external_key),
            actor=principal.username,
        )
    except catalog_service.CatalogError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(exc)) from exc
    await session.commit()
    return MenuItemOut.model_validate(item)


@router.delete("/{item_id}", summary="Delete a menu item")
async def delete_item(
    item_id: int,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> dict:
    ok = await catalog_service.delete_menu_item(session, item_id, actor=principal.username)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Menu item not found")
    await session.commit()
    return {"ok": True}
