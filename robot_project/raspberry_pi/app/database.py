"""
database.py — persistence for destinations (JSON file), missions, menu items,
dining tables, orders and paired devices (SQLite).

Changes vs. the old version
  * missions now store per-step status + an error message (the app shows both)
  * destinations are validated and written atomically (no half-written JSON)
  * data directory can be moved out of the repo with ROBOT_DATA_DIR
  * missions left "in_progress" by a crash/restart are marked failed on startup
  * ordering tables (menu_items / tables / orders) and paired devices
"""

from __future__ import annotations

import json
import math
import os
import secrets
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_APP_DIR = Path(__file__).parent
DATA_DIR = Path(os.getenv("ROBOT_DATA_DIR", "") or _APP_DIR)
DB_PATH = DATA_DIR / "robot.db"
DESTINATIONS_PATH = DATA_DIR / "destinations.json"

_local = threading.local()
_dest_lock = threading.Lock()

# Alphabet without look-alike characters (0/O, 1/I/l) — these codes are read
# aloud in the kitchen and typed on a phone.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn


def init() -> None:
    """Create tables / migrate old databases. Safe to call repeatedly."""
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS missions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            status        TEXT    NOT NULL DEFAULT 'pending',
            destinations  TEXT    NOT NULL DEFAULT '[]',
            created_at    TEXT    NOT NULL,
            completed_at  TEXT
        )
        """
    )
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(missions)")}
    if "steps" not in cols:
        conn.execute("ALTER TABLE missions ADD COLUMN steps TEXT NOT NULL DEFAULT '[]'")
    if "error" not in cols:
        conn.execute("ALTER TABLE missions ADD COLUMN error TEXT")
    conn.execute(
        "UPDATE missions SET status='failed', error='Server restarted during mission', "
        "completed_at=? WHERE status IN ('pending','in_progress','running')",
        (_now(),),
    )
    _init_ordering_tables(conn)
    conn.commit()
    if not DESTINATIONS_PATH.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        DESTINATIONS_PATH.write_text("[]", encoding="utf-8")


def _init_ordering_tables(conn: sqlite3.Connection) -> None:
    """Menu items, dining tables, customer orders and paired devices."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS menu_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            description TEXT    NOT NULL DEFAULT '',
            price       REAL    NOT NULL DEFAULT 0,
            category    TEXT    NOT NULL DEFAULT 'General',
            available   INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tables (
            code        TEXT    PRIMARY KEY,
            name        TEXT    NOT NULL,
            destination TEXT    NOT NULL DEFAULT '',
            seats       INTEGER NOT NULL DEFAULT 4,
            active      INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT    NOT NULL UNIQUE,
            table_code  TEXT    NOT NULL DEFAULT '',
            kind        TEXT    NOT NULL DEFAULT 'order',
            status      TEXT    NOT NULL DEFAULT 'placed',
            items       TEXT    NOT NULL DEFAULT '[]',
            total       REAL    NOT NULL DEFAULT 0,
            note        TEXT    NOT NULL DEFAULT '',
            source      TEXT    NOT NULL DEFAULT 'web',
            mission_id  INTEGER,
            error       TEXT,
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_table ON orders(table_code)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS devices (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT    NOT NULL UNIQUE,
            name        TEXT    NOT NULL DEFAULT '',
            kind        TEXT    NOT NULL DEFAULT 'admin',
            last_seen   TEXT    NOT NULL,
            created_at  TEXT    NOT NULL
        )
        """
    )


def new_code(length: int = 5) -> str:
    """Random code that avoids characters people confuse when reading aloud."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Destinations ──────────────────────────────────────────────────────────────

def _load_destinations() -> list[dict]:
    if not DESTINATIONS_PATH.exists():
        return []
    try:
        raw = DESTINATIONS_PATH.read_text(encoding="utf-8").strip()
        data = json.loads(raw) if raw else []
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_destinations(dests: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), prefix=".dest-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(dests, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, DESTINATIONS_PATH)       # atomic on POSIX
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def get_destinations() -> list[dict]:
    with _dest_lock:
        return _load_destinations()


def upsert_destination(name: str, x: float, y: float, theta: float) -> None:
    name = name.strip()
    if not name or len(name) > 64:
        raise ValueError("Destination name must be 1-64 characters")
    if not all(math.isfinite(v) for v in (x, y, theta)):
        raise ValueError("x, y and theta must be finite numbers")
    with _dest_lock:
        dests = _load_destinations()
        for d in dests:
            if d.get("name") == name:
                d.update(x=x, y=y, theta=theta)
                break
        else:
            dests.append({"name": name, "x": x, "y": y, "theta": theta})
        _save_destinations(dests)


def delete_destination(name: str) -> bool:
    with _dest_lock:
        dests = _load_destinations()
        kept = [d for d in dests if d.get("name") != name]
        if len(kept) == len(dests):
            return False
        _save_destinations(kept)
        return True


def get_destination(name: str) -> Optional[dict]:
    for d in get_destinations():
        if d.get("name") == name:
            return d
    return None


# ── Missions ──────────────────────────────────────────────────────────────────

def create_mission(destinations: list[str], steps: list[dict]) -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO missions (status, destinations, steps, created_at) VALUES (?,?,?,?)",
        ("pending", json.dumps(destinations), json.dumps(steps), _now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def _row_to_mission(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["destinations"] = json.loads(d["destinations"])
    d["steps"] = json.loads(d.get("steps") or "[]")
    done = sum(1 for s in d["steps"] if s.get("status") == "completed")
    d["progress"] = f"{done}/{len(d['steps'])}"
    return d


def get_mission(mission_id: int) -> Optional[dict[str, Any]]:
    row = _get_conn().execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
    return _row_to_mission(row) if row else None


def list_missions(limit: int = 20) -> list[dict[str, Any]]:
    rows = _get_conn().execute(
        "SELECT * FROM missions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_mission(r) for r in rows]


def update_mission(
    mission_id: int,
    status: Optional[str] = None,
    steps: Optional[list[dict]] = None,
    error: Optional[str] = None,
    finished: bool = False,
) -> None:
    sets, args = [], []
    if status is not None:
        sets.append("status=?"); args.append(status)
    if steps is not None:
        sets.append("steps=?"); args.append(json.dumps(steps))
    if error is not None:
        sets.append("error=?"); args.append(error)
    if finished:
        sets.append("completed_at=?"); args.append(_now())
    if not sets:
        return
    args.append(mission_id)
    conn = _get_conn()
    conn.execute(f"UPDATE missions SET {', '.join(sets)} WHERE id=?", args)
    conn.commit()


# ── Menu items ────────────────────────────────────────────────────────────────

def _row_to_menu_item(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["available"] = bool(d.get("available", 1))
    d["price"] = round(float(d.get("price") or 0.0), 2)
    return d


def list_menu_items(only_available: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM menu_items"
    if only_available:
        sql += " WHERE available=1"
    sql += " ORDER BY category, name"
    return [_row_to_menu_item(r) for r in _get_conn().execute(sql).fetchall()]


def get_menu_item(item_id: int) -> Optional[dict[str, Any]]:
    row = _get_conn().execute("SELECT * FROM menu_items WHERE id=?", (item_id,)).fetchone()
    return _row_to_menu_item(row) if row else None


def upsert_menu_item(
    name: str,
    price: float,
    description: str = "",
    category: str = "General",
    available: bool = True,
    item_id: Optional[int] = None,
) -> int:
    conn = _get_conn()
    if item_id is None:
        cur = conn.execute(
            "INSERT INTO menu_items (name, description, price, category, available, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (name, description, price, category, 1 if available else 0, _now()),
        )
        conn.commit()
        return int(cur.lastrowid)
    conn.execute(
        "UPDATE menu_items SET name=?, description=?, price=?, category=?, available=? WHERE id=?",
        (name, description, price, category, 1 if available else 0, item_id),
    )
    conn.commit()
    return item_id


def delete_menu_item(item_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM menu_items WHERE id=?", (item_id,))
    conn.commit()
    return cur.rowcount > 0


# ── Dining tables ─────────────────────────────────────────────────────────────

def _row_to_table(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["active"] = bool(d.get("active", 1))
    d["seats"] = int(d.get("seats") or 0)
    return d


def list_tables(active_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM tables"
    if active_only:
        sql += " WHERE active=1"
    sql += " ORDER BY name"
    return [_row_to_table(r) for r in _get_conn().execute(sql).fetchall()]


def get_table(code: str) -> Optional[dict[str, Any]]:
    row = _get_conn().execute("SELECT * FROM tables WHERE code=?", (code,)).fetchone()
    return _row_to_table(row) if row else None


def upsert_table(
    name: str,
    destination: str = "",
    seats: int = 4,
    active: bool = True,
    code: Optional[str] = None,
) -> str:
    conn = _get_conn()
    if code is None:
        code = new_code(5)
        while get_table(code) is not None:          # astronomically unlikely
            code = new_code(5)
        conn.execute(
            "INSERT INTO tables (code, name, destination, seats, active, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (code, name, destination, seats, 1 if active else 0, _now()),
        )
    else:
        conn.execute(
            "UPDATE tables SET name=?, destination=?, seats=?, active=? WHERE code=?",
            (name, destination, seats, 1 if active else 0, code),
        )
    conn.commit()
    return code


def delete_table(code: str) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM tables WHERE code=?", (code,))
    conn.commit()
    return cur.rowcount > 0


# ── Orders ────────────────────────────────────────────────────────────────────

def _row_to_order(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["items"] = json.loads(d.get("items") or "[]")
    except json.JSONDecodeError:
        d["items"] = []
    d["total"] = round(float(d.get("total") or 0.0), 2)
    return d


def create_order(
    table_code: str,
    kind: str = "order",
    items: Optional[list[dict]] = None,
    total: float = 0.0,
    note: str = "",
    source: str = "web",
) -> dict[str, Any]:
    conn = _get_conn()
    code = new_code(4)
    while conn.execute("SELECT 1 FROM orders WHERE code=?", (code,)).fetchone():
        code = new_code(4)
    now = _now()
    cur = conn.execute(
        "INSERT INTO orders (code, table_code, kind, status, items, total, note, source, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (code, table_code, kind, "placed", json.dumps(items or []), round(total, 2),
         note, source, now, now),
    )
    conn.commit()
    order = get_order(int(cur.lastrowid))
    assert order is not None
    return order


def get_order(order_id: int) -> Optional[dict[str, Any]]:
    row = _get_conn().execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    return _row_to_order(row) if row else None


def get_order_by_code(code: str) -> Optional[dict[str, Any]]:
    row = _get_conn().execute(
        "SELECT * FROM orders WHERE code=? COLLATE NOCASE", (code,)).fetchone()
    return _row_to_order(row) if row else None


def list_orders(
    status: Optional[str] = None,
    kind: Optional[str] = None,
    table_code: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM orders WHERE 1=1"
    args: list[Any] = []
    if status:
        sql += " AND status=?"
        args.append(status)
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if table_code:
        sql += " AND table_code=?"
        args.append(table_code)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))
    return [_row_to_order(r) for r in _get_conn().execute(sql, args).fetchall()]


def update_order(order_id: int, **fields: Any) -> None:
    allowed = {"status", "items", "total", "note", "mission_id", "error", "kind"}
    sets, args = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "items":
            value = json.dumps(value)
        elif key == "total":
            value = round(float(value or 0.0), 2)
        sets.append(f"{key}=?")
        args.append(value)
    if not sets:
        return
    sets.append("updated_at=?")
    args.append(_now())
    args.append(order_id)
    conn = _get_conn()
    conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE id=?", args)
    conn.commit()


def orders_for_mission(mission_id: int) -> list[dict[str, Any]]:
    rows = _get_conn().execute(
        "SELECT * FROM orders WHERE mission_id=?", (mission_id,)).fetchall()
    return [_row_to_order(r) for r in rows]


# ── Paired devices ────────────────────────────────────────────────────────────

def register_device(device_id: str, name: str = "", kind: str = "admin") -> dict[str, Any]:
    conn = _get_conn()
    now = _now()
    row = conn.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO devices (device_id, name, kind, last_seen, created_at) "
            "VALUES (?,?,?,?,?)",
            (device_id, name or device_id, kind, now, now),
        )
    else:
        conn.execute(
            "UPDATE devices SET name=?, kind=?, last_seen=? WHERE device_id=?",
            (name or row["name"], kind, now, device_id),
        )
    conn.commit()
    row = conn.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
    return dict(row)


def touch_device(device_id: str) -> None:
    conn = _get_conn()
    conn.execute("UPDATE devices SET last_seen=? WHERE device_id=?", (_now(), device_id))
    conn.commit()


def list_devices() -> list[dict[str, Any]]:
    rows = _get_conn().execute(
        "SELECT * FROM devices ORDER BY last_seen DESC").fetchall()
    return [dict(r) for r in rows]


def delete_device(device_id: str) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM devices WHERE device_id=?", (device_id,))
    conn.commit()
    return cur.rowcount > 0
