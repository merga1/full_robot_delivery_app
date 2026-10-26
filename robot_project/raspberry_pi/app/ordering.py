"""
ordering.py — menu, dining tables, customer orders and robot dispatch.

This is the glue between the customer (web page / phone app), the kitchen
(web dashboard) and the robot (missions).

Order kinds
  order   a food/drink order placed from the table QR code or the phone app
  call    "send the robot to my table" — no items, the robot comes to you

Order status machine
  placed ──▶ accepted ──▶ preparing ──▶ ready ──▶ delivering ──▶ delivered
     │           │             │            │            │
     └───────────┴─────────────┴────────────┴────────────┴──▶ cancelled

  * `preparing` only makes sense for kind='order' (a call request skips it)
  * `delivering` is set by `dispatch()` when a robot mission is created
  * `delivered` is set automatically when that mission completes

Prices are always recalculated on the server — the client never decides what
an item costs.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from . import database

log = logging.getLogger("robot.ordering")

ORDER_KINDS = ("order", "call")
ORDER_STATUSES = (
    "placed", "accepted", "preparing", "ready", "delivering", "delivered", "cancelled",
)

#: The status an order moves to when the kitchen presses the next button.
NEXT_STATUS: dict[str, str] = {
    "placed": "accepted",
    "accepted": "preparing",
    "preparing": "ready",
    "ready": "delivering",
    "delivering": "delivered",
}

#: Statuses that still need the kitchen or the robot to do something.
OPEN_STATUSES = ("placed", "accepted", "preparing", "ready", "delivering")

MAX_QTY = 20


class OrderingError(ValueError):
    """Business rule violation — the API turns this into HTTP 4xx."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ── Menu ──────────────────────────────────────────────────────────────────────

def list_menu(only_available: bool = False) -> list[dict[str, Any]]:
    return database.list_menu_items(only_available=only_available)


def save_menu_item(
    name: str,
    price: float,
    description: str = "",
    category: str = "General",
    available: bool = True,
    item_id: Optional[int] = None,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise OrderingError("The item needs a name")
    if len(name) > 80:
        raise OrderingError("The item name is too long (80 characters max)")
    if price < 0:
        raise OrderingError("The price cannot be negative")
    if price > 100000:
        raise OrderingError("That price looks wrong")
    if item_id is not None and database.get_menu_item(item_id) is None:
        raise OrderingError("That menu item does not exist", 404)
    item_id = database.upsert_menu_item(
        name=name,
        price=float(price),
        description=(description or "").strip()[:280],
        category=(category or "General").strip()[:40] or "General",
        available=bool(available),
        item_id=item_id,
    )
    item = database.get_menu_item(item_id)
    assert item is not None
    return item


def remove_menu_item(item_id: int) -> bool:
    return database.delete_menu_item(item_id)


# ── Tables ────────────────────────────────────────────────────────────────────

def list_tables(active_only: bool = False) -> list[dict[str, Any]]:
    return database.list_tables(active_only=active_only)


def get_table(code: str) -> Optional[dict[str, Any]]:
    return database.get_table((code or "").strip().upper())


def save_table(
    name: str,
    destination: str = "",
    seats: int = 4,
    active: bool = True,
    code: Optional[str] = None,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise OrderingError("The table needs a name")
    if len(name) > 60:
        raise OrderingError("The table name is too long (60 characters max)")
    try:
        seats = int(seats)
    except (TypeError, ValueError):
        raise OrderingError("The number of seats must be a number")
    if seats < 0 or seats > 100:
        raise OrderingError("The number of seats must be between 0 and 100")
    destination = (destination or "").strip()
    if destination and database.get_destination(destination) is None:
        raise OrderingError(
            f"There is no destination called '{destination}'. "
            "Create it on the map first (the app: map screen → Place tables).")
    code = (code or "").strip().upper() or None
    if code is not None and database.get_table(code) is None:
        code = None                       # unknown code → create a new table
    code = database.upsert_table(
        name=name,
        destination=destination,
        seats=seats,
        active=bool(active),
        code=(code or None),
    )
    table = database.get_table(code)
    assert table is not None
    return table


def remove_table(code: str) -> bool:
    return database.delete_table((code or "").strip().upper())


def table_for_order(table_code: str) -> dict[str, Any]:
    table = get_table(table_code)
    if table is None:
        raise OrderingError("Unknown table code", 404)
    if not table.get("active"):
        raise OrderingError("This table is not in service")
    return table


# ── Orders ────────────────────────────────────────────────────────────────────

def _price_items(raw_items: Any) -> tuple[list[dict[str, Any]], float]:
    """Validate the requested items and price them on the server."""
    if not isinstance(raw_items, list) or not raw_items:
        raise OrderingError("Your order is empty")
    resolved: list[dict[str, Any]] = []
    total = 0.0
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise OrderingError("Malformed order item")
        try:
            item_id = int(raw.get("menu_id", raw.get("id", 0)))
            qty = int(raw.get("qty", raw.get("quantity", 1)))
        except (TypeError, ValueError):
            raise OrderingError("Malformed order item")
        if qty < 1:
            raise OrderingError("An item must have a quantity of at least 1")
        if qty > MAX_QTY:
            raise OrderingError(f"At most {MAX_QTY} of one item please")
        item = database.get_menu_item(item_id)
        if item is None:
            raise OrderingError("One of the items is no longer on the menu")
        if not item.get("available"):
            raise OrderingError(f"'{item['name']}' is sold out")
        line_total = round(float(item["price"]) * qty, 2)
        total += line_total
        resolved.append({
            "menu_id": item_id,
            "name": item["name"],
            "price": float(item["price"]),
            "qty": qty,
            "total": line_total,
        })
    return resolved, round(total, 2)


def place_order(
    table_code: str,
    raw_items: Any,
    note: str = "",
    source: str = "web",
    kind: str = "order",
) -> dict[str, Any]:
    """Create a customer order (kind='order') or a robot call (kind='call')."""
    if kind not in ORDER_KINDS:
        raise OrderingError(f"Unknown order kind '{kind}'")
    table = table_for_order(table_code)
    if kind == "call":
        items: list[dict[str, Any]] = []
        total = 0.0
    else:
        items, total = _price_items(raw_items)
    note = (note or "").strip()[:280]
    order = database.create_order(
        table_code=table["code"],
        kind=kind,
        items=items,
        total=total,
        note=note,
        source=source,
    )
    log.info("new %s %s for table %s (%.2f)", kind, order["code"], table["name"], total)
    return order


def get_order(order_id: int) -> Optional[dict[str, Any]]:
    return database.get_order(order_id)


def get_order_by_code(code: str) -> Optional[dict[str, Any]]:
    return database.get_order_by_code((code or "").strip())


def list_orders(
    status: Optional[str] = None,
    kind: Optional[str] = None,
    table_code: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return database.list_orders(status=status, kind=kind, table_code=table_code, limit=limit)


def set_order_status(order_id: int, status: str, error: Optional[str] = None) -> dict[str, Any]:
    """Move an order to a new status (no robot dispatch)."""
    if status not in ORDER_STATUSES:
        raise OrderingError(f"Unknown status '{status}'")
    order = database.get_order(order_id)
    if order is None:
        raise OrderingError("Order not found", 404)
    if order["status"] == status:
        return order
    if order["status"] in ("delivered", "cancelled") and status != "cancelled":
        raise OrderingError(f"This order is already {order['status']}")
    database.update_order(order_id, status=status, error=error)
    updated = database.get_order(order_id)
    assert updated is not None
    log.info("order %s -> %s", updated["code"], status)
    return updated


def advance_order(order_id: int) -> dict[str, Any]:
    """Press the kitchen's 'next' button once."""
    order = database.get_order(order_id)
    if order is None:
        raise OrderingError("Order not found", 404)
    nxt = NEXT_STATUS.get(order["status"])
    if nxt is None:
        raise OrderingError(f"Nothing to do — the order is already {order['status']}")
    if nxt == "delivering":
        return dispatch_order(order_id)
    return set_order_status(order_id, nxt)


def cancel_order(order_id: int, reason: str = "") -> dict[str, Any]:
    order = database.get_order(order_id)
    if order is None:
        raise OrderingError("Order not found", 404)
    if order["status"] in ("delivering", "delivered"):
        raise OrderingError("The robot is already on its way — stop it from the app")
    updated = set_order_status(order_id, "cancelled", error=reason or None)
    return updated


# ── Robot dispatch ────────────────────────────────────────────────────────────

def dispatch_order(order_id: int) -> dict[str, Any]:
    """Send the robot to the table of this order (creates a mission)."""
    from . import missions      # local import: missions imports this module back

    order = database.get_order(order_id)
    if order is None:
        raise OrderingError("Order not found", 404)
    if order["status"] == "delivering":
        raise OrderingError("The robot is already delivering this order")
    if order["status"] in ("delivered", "cancelled"):
        raise OrderingError(f"This order is already {order['status']}")
    if order["kind"] == "order" and order["status"] not in ("ready", "accepted", "preparing"):
        raise OrderingError("The food is not ready yet")

    table = database.get_table(order["table_code"])
    if table is None:
        raise OrderingError("The table of this order no longer exists")
    destination = (table.get("destination") or "").strip()
    if not destination:
        raise OrderingError(
            f"Table '{table['name']}' has no robot destination yet. "
            "Set one in the kitchen dashboard.")
    if database.get_destination(destination) is None:
        raise OrderingError(
            f"The destination '{destination}' of table '{table['name']}' does not exist on the map")

    try:
        mission_id = missions.start_mission([destination])
    except missions.MissionError as exc:
        raise OrderingError(str(exc))

    database.update_order(order_id, status="delivering", mission_id=mission_id, error=None)
    updated = database.get_order(order_id)
    assert updated is not None
    log.info("order %s dispatched to %s (mission %s)", updated["code"], destination, mission_id)
    return updated


def on_mission_finished(mission_id: int, status: str, error: Optional[str] = None) -> None:
    """Called by missions.py when a mission ends — updates the linked orders."""
    for order in database.orders_for_mission(mission_id):
        if order["status"] != "delivering":
            continue
        if status == "completed":
            database.update_order(order["id"], status="delivered", error=None)
            log.info("order %s delivered", order["code"])
        elif status == "cancelled":
            database.update_order(order["id"], status="ready",
                                  error="The delivery was cancelled")
        else:
            database.update_order(
                order["id"], status="ready",
                error=error or "The robot could not reach the table")


def open_orders() -> list[dict[str, Any]]:
    """Everything the kitchen still has to deal with (oldest first)."""
    orders = database.list_orders(limit=200)
    return [o for o in reversed(orders) if o["status"] in OPEN_STATUSES]
