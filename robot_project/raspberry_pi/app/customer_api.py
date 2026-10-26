"""
customer_api.py — the public API used by the customer web page / phone app.

Endpoints (no staff login needed)

  GET  /customer/menu                 the menu (only available items)
  POST /customer/session              { table_code } → a customer token
  POST /customer/orders               place a food order
  POST /customer/calls                "send the robot to my table"
  GET  /customer/orders/{code}        track an order
  GET  /customer/tables               the tables that are in service

The customer token is scoped to one table: it can only place orders for, and
read orders of, the table it was created for.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import database, ordering
from .auth import create_customer_token, require_customer

log = logging.getLogger("robot.customer_api")

router = APIRouter(prefix="/customer", tags=["customer"])


# ── Request bodies ────────────────────────────────────────────────────────────

class SessionRequest(BaseModel):
    table_code: str


class OrderItem(BaseModel):
    menu_id: int
    qty: int = 1


class OrderRequest(BaseModel):
    table_code: str = ""
    items: list[OrderItem] = Field(default_factory=list)
    note: str = ""


class CallRequest(BaseModel):
    table_code: str = ""
    note: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _table_of(payload: dict) -> str:
    code = (payload.get("table_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="table_code is required")
    return code.upper()


def _order_response(order: dict) -> dict:
    """Public shape of an order — no internal fields."""
    return {
        "id": order["id"],
        "code": order["code"],
        "kind": order["kind"],
        "status": order["status"],
        "table_code": order["table_code"],
        "items": order.get("items") or [],
        "total": order.get("total", 0.0),
        "note": order.get("note", ""),
        "error": order.get("error"),
        "created_at": order.get("created_at"),
        "updated_at": order.get("updated_at"),
    }


# ── Public endpoints ──────────────────────────────────────────────────────────

@router.get("/menu")
def get_menu():
    """The menu for the customer page — sold-out items are hidden."""
    items = database.list_menu_items(only_available=True)
    return {"menu": [
        {"id": i["id"], "name": i["name"], "description": i["description"],
         "price": i["price"], "category": i["category"]}
        for i in items
    ]}


@router.get("/tables")
def get_tables():
    """Tables that are currently in service (the page only needs their names)."""
    return {"tables": [
        {"code": t["code"], "name": t["name"]}
        for t in database.list_tables(active_only=True)
    ]}


@router.post("/session")
def create_session(req: SessionRequest):
    """Create a customer token for a table (the QR code on the table is the key)."""
    code = (req.table_code or "").strip().upper()
    table = database.get_table(code)
    if table is None or not table.get("active"):
        raise HTTPException(status_code=404, detail="This table is not in service")
    return {"ok": True, "token": create_customer_token(table["code"]),
            "table_code": table["code"], "table_name": table["name"]}


@router.post("/orders")
def place_order(req: OrderRequest, payload: dict = Depends(require_customer)):
    """Place a food order.  The table always comes from the token, never the body."""
    table_code = (payload.get("table_code") or "").strip().upper()
    raw_items = [{"menu_id": item.menu_id, "qty": item.qty} for item in req.items]
    try:
        order = ordering.place_order(table_code=table_code, raw_items=raw_items,
                                     note=req.note, source="web", kind="order")
    except ordering.OrderingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return {"ok": True, "order": _order_response(order)}


@router.post("/calls")
async def call_robot(req: CallRequest, payload: dict = Depends(require_customer)):
    """Ask the robot to come to the table.

    The order is always created first — if the robot is busy the request stays
    in the kitchen queue and the kitchen can dispatch it later.
    """
    table_code = (payload.get("table_code") or "").strip().upper()
    try:
        order = ordering.place_order(table_code=table_code, raw_items=[],
                                     note=req.note, source="web", kind="call")
    except ordering.OrderingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    dispatched = False
    try:
        ordering.dispatch_order(order["id"])
        dispatched = True
    except ordering.OrderingError as exc:
        # The robot is busy or the table has no destination — the kitchen sees
        # the request in the queue and can send the robot later.
        log.info("call %s queued for the kitchen: %s", order["code"], exc)

    fresh = ordering.get_order(order["id"]) or order
    return {"ok": True, "order": _order_response(fresh), "dispatched": dispatched}


@router.get("/orders/{code}")
def track_order(code: str, payload: dict = Depends(require_customer)):
    """Track an order of the table the token belongs to."""
    order = ordering.get_order_by_code(code)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order["table_code"] != (payload.get("table_code") or "").upper():
        raise HTTPException(status_code=403, detail="This order belongs to another table")
    return {"order": _order_response(order)}
