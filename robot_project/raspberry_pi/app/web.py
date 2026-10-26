"""
web.py — the server-rendered web pages.

  /o/{table_code}   the customer order page (this is what the table QR points to)
  /kitchen         the kitchen dashboard
  /kitchen/login   login form for the kitchen dashboard
  /qr/{code}.png   the QR image of a table (for printing)
  /static/…        css + js

The pages are plain files in `app/web/` — the server only replaces a few
`{{PLACEHOLDER}}` markers, so there is no templating engine to install on the Pi.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from . import database, qr

router = APIRouter(tags=["web"])

WEB_DIR = Path(__file__).parent / "web"


def _read_asset(name: str) -> str:
    path = WEB_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Asset not found: {name}")
    return path.read_text(encoding="utf-8")


def _render(name: str, **markers) -> str:
    html = _read_asset(name)
    for key, value in markers.items():
        html = html.replace("{{" + key + "}}", str(value))
    return html


def public_base_url(request=None) -> str:
    """The address customers should use to open the order page.

    ROBOT_PUBLIC_HOST wins (e.g. http://192.168.1.50:8000).  Otherwise the URL
    of the incoming request is used.
    """
    host = os.getenv("ROBOT_PUBLIC_HOST", "").strip()
    if host:
        return host.rstrip("/")
    if request is not None:
        return str(request.base_url).rstrip("/")
    return "http://localhost:8000"


# ── Pages ─────────────────────────────────────────────────────────────────────

@router.get("/o/{table_code}", response_class=HTMLResponse)
def order_page(table_code: str):
    """The customer page — the QR code on the table points here."""
    table = database.get_table((table_code or "").strip().upper())
    if table is None or not table.get("active"):
        raise HTTPException(status_code=404, detail="This table is not in service")
    return _render("order.html",
                   TABLE_CODE=table["code"],
                   TABLE_NAME=table["name"])


@router.get("/kitchen", response_class=HTMLResponse)
def kitchen_page():
    return _render("kitchen.html")


@router.get("/kitchen/login", response_class=HTMLResponse)
def kitchen_login_page():
    return _render("login.html", REDIRECT="/kitchen")


# ── QR codes ──────────────────────────────────────────────────────────────────

@router.get("/qr/{table_code}.png")
def table_qr_png(table_code: str, request=None):
    """The QR image of a table — print it and stick it on the table."""
    code = (table_code or "").strip().upper()
    table = database.get_table(code)
    if table is None or not table.get("active"):
        raise HTTPException(status_code=404, detail="This table is not in service")
    url = f"{public_base_url(request)}/o/{table['code']}"
    png = qr.to_png_bytes(url)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


def mount_static(app) -> None:
    """Mount /static (called from main.py)."""
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
