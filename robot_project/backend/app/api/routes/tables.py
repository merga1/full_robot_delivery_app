"""Restaurant table endpoints.

A table maps to an **approved navigation destination** (``destination_id``).
The destination id is a name registered in the robot gateway's destination
registry — coordinates are never derived from a table number.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.session import get_session
from ...core.config import settings
from ...schemas.menu import TableCreate, TableOut, TableUpdate
from ...services import catalog_service
from ..dependencies import Principal, require_admin

log = logging.getLogger("robot.api.tables")

router = APIRouter(prefix="/api/v1/tables", tags=["tables"])


@router.get("", response_model=list[TableOut], summary="List the tables")
async def list_tables(
    include_inactive: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[TableOut]:
    tables = await catalog_service.list_tables(session, active_only=not include_inactive)
    return [TableOut.model_validate(t) for t in tables]


@router.get("/by-code/{code}", response_model=TableOut, summary="Find a table by its QR code")
async def get_table_by_code(code: str, session: AsyncSession = Depends(get_session)) -> TableOut:
    table = await catalog_service.get_table_by_code(session, code)
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    return TableOut.model_validate(table)


@router.get("/qr/{code}.png", summary="Generate a table QR code")
async def table_qr(code: str, session: AsyncSession = Depends(get_session)) -> Response:
    table = await catalog_service.get_table_by_code(session, code)
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    try:
        import qrcode
        from io import BytesIO
        image = qrcode.make(
            f"{settings.PUBLIC_CUSTOMER_URL.rstrip('/')}/?table={table.public_code}"
        )
        output = BytesIO()
        image.save(output, format="PNG")
        return Response(content=output.getvalue(), media_type="image/png")
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="QR generation dependency is not installed") from exc


@router.get("/{table_id}", response_model=TableOut, summary="One table")
async def get_table(table_id: int, session: AsyncSession = Depends(get_session)) -> TableOut:
    table = await catalog_service.get_table(session, table_id)
    if table is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    return TableOut.model_validate(table)


@router.post("", response_model=TableOut, status_code=status.HTTP_201_CREATED,
             summary="Create a table")
async def create_table(
    payload: TableCreate,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> TableOut:
    try:
        table = await catalog_service.save_table(
            session,
            table_number=payload.table_number,
            display_name=payload.display_name,
            destination_id=payload.destination_id,
            map_identifier=payload.map_identifier,
            seats=payload.seats,
            is_active=payload.is_active,
            notes=payload.notes,
            actor=principal.username,
        )
    except catalog_service.CatalogError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(exc)) from exc
    await session.commit()
    return TableOut.model_validate(table)


@router.patch("/{table_id}", response_model=TableOut, summary="Update a table")
async def update_table(
    table_id: int,
    payload: TableUpdate,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> TableOut:
    current = await catalog_service.get_table(session, table_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    data = payload.model_dump(exclude_unset=True)
    try:
        table = await catalog_service.save_table(
            session,
            table_id=table_id,
            table_number=data.get("table_number", current.table_number),
            display_name=data.get("display_name", current.display_name),
            destination_id=data.get("destination_id", current.destination_id),
            map_identifier=data.get("map_identifier", current.map_identifier),
            seats=data.get("seats", current.seats),
            is_active=data.get("is_active", current.is_active),
            notes=data.get("notes", current.notes),
            actor=principal.username,
        )
    except catalog_service.CatalogError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(exc)) from exc
    await session.commit()
    return TableOut.model_validate(table)


@router.delete("/{table_id}", summary="Delete a table")
async def delete_table(
    table_id: int,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> dict:
    ok = await catalog_service.delete_table(session, table_id, actor=principal.username)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    await session.commit()
    return {"ok": True}
