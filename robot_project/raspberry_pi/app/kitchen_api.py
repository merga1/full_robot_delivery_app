"""
kitchen_api.py — the API behind the kitchen dashboard (web) and the orders tab
of the mobile app.

Roles
  admin, kitchen   may change orders and manage the menu / tables
  admin, kitchen, user   may look at the orders

The kitchen only ever sees the orders of the tables — never the customer tokens.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import database, ordering
from .auth import require_service, require_staff

log = logging.getLogger("robot.kitchen_api")

router = APIRouter(prefix="/kitchen/api", tags=["kitchen"])


# ── Request bodies ────────────────────────────────────────────────────────────

class MenuItemBody(BaseModel):
    id: int | None = None
    name: str
    price: float
    description: str = ""
    category: str = "General"
    available: bool = True


class TableBody(BaseModel):
    code: str | None = None
    name: str
    destination: str = ""
    seats: int = 4
    active: bool = True


class StatusBody(BaseModel):
    reason: str = ""


# ── Orders ────────────────────────────────────────────────────────────────────

@router.get("/orders")
def list_orders(status: str | None = None, kind: str | None = None,
                table_code: str | None = None, limit: int = 100,
                _auth=Depends(require_staff)):
    """Orders for the kitchen board.  Without a status the open ones come first."""
    if status == "open":
        orders = ordering.open_orders()
    else:
        orders = ordering.list_orders(status=status, kind=kind,
                                      table_code=table_code, limit=limit)
    return {"orders": orders, "count": len(orders)}


@router.post("/orders/{order_id}/advance")
async def advance_order(order_id: int, _auth=Depends(require_service)):
    """Press the 'next' button: placed → accepted → preparing → ready → delivering.

    Async on purpose: the last step creates the robot mission, which needs the
    running event loop.
    """
    try:
        order = ordering.advance_order(order_id)
    except ordering.OrderingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {"ok": True, "order": order}


@router.post("/orders/{order_id}/dispatch")
async def dispatch_order(order_id: int, _auth=Depends(require_service)):
    """Send the robot to the table of this order (creates a mission)."""
    try:
        order = ordering.dispatch_order(order_id)
    except ordering.OrderingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {"ok": True, "order": order}


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, body: StatusBody | None = None,
                 _auth=Depends(require_service)):
    try:
        order = ordering.cancel_order(order_id, reason=(body.reason if body else ""))
    except ordering.OrderingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {"ok": True, "order": order}


@router.post("/orders/{order_id}/status")
def set_status(order_id: int, body: dict, _auth=Depends(require_service)):
    """Set an explicit status (used by the app's status chips)."""
    status = (body.get("status") or "").strip()
    try:
        order = ordering.set_order_status(order_id, status, error=body.get("error"))
    except ordering.OrderingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {"ok": True, "order": order}


# ── Menu ──────────────────────────────────────────────────────────────────────

@router.get("/menu")
def list_menu(_auth=Depends(require_staff)):
    return {"menu": database.list_menu_items()}


@router.post("/menu")
def save_menu_item(body: MenuItemBody, _auth=Depends(require_service)):
    try:
        item = ordering.save_menu_item(
            name=body.name, price=body.price, description=body.description,
            category=body.category, available=body.available, item_id=body.id)
    except ordering.OrderingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError as exc:                      # duplicate name, bad number
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True, "item": item}


@router.delete("/menu/{item_id}")
def delete_menu_item(item_id: int, _auth=Depends(require_service)):
    if not ordering.remove_menu_item(item_id):
        raise HTTPException(status_code=404, detail="Item not found")
    return {"ok": True}


# ── Tables ────────────────────────────────────────────────────────────────────

@router.get("/tables")
def list_tables(_auth=Depends(require_staff)):
    return {"tables": database.list_tables()}


@router.post("/tables")
def save_table(body: TableBody, _auth=Depends(require_service)):
    try:
        table = ordering.save_table(
            name=body.name, destination=body.destination, seats=body.seats,
            active=body.active, code=body.code)
    except ordering.OrderingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {"ok": True, "table": table}


@router.delete("/tables/{code}")
def delete_table(code: str, _auth=Depends(require_service)):
    if not ordering.remove_table(code):
        raise HTTPException(status_code=404, detail="Table not found")
    return {"ok": True}
