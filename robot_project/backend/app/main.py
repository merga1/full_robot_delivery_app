"""FastAPI application entry point.

Responsibilities
----------------
* wire the routers,
* configure CORS from an explicit allow-list,
* start the transactional-outbox publisher,
* start the heartbeat sweeper that marks silent robots as offline,
* expose ``/api/health``, ``/docs`` and ``/openapi.json``.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .core.config import settings
from .core.logging import configure_logging, request_id_var
from .db.session import SessionLocal, dispose_engine
from .events.hub import hub
from .events.outbox import publisher
from .services import robot_service
from .services.gateway_registry import registry

log = logging.getLogger("robot.main")

DESCRIPTION = """
The authoritative backend of the smart restaurant robot system.

* **Customers** place orders and track them.
* **Kitchen staff** accept, prepare and hand over the orders.
* **Administrators** assign robots, dispatch missions and manage the catalogue.
* **The ROS 2 gateway** connects over an authenticated WebSocket and reports
  the robot's real status — the backend never pretends to drive the robot.
"""


async def _heartbeat_sweeper() -> None:
    """Mark robots whose gateway stopped heartbeating as offline."""
    interval = max(2, settings.ROBOT_GATEWAY_HEARTBEAT_TIMEOUT_SECONDS // 2)
    while True:
        try:
            await asyncio.sleep(interval)
            async with SessionLocal() as session:
                stale = await robot_service.sweep_stale_robots(session)
                if stale:
                    await session.commit()
                    log.warning("marked %d robot(s) offline (heartbeat timeout)", len(stale))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("heartbeat sweeper failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.LOG_LEVEL)
    log.info("starting %s v%s (%s)", settings.APP_NAME, settings.APP_VERSION, settings.APP_ENV)

    for warning in settings.validate_startup():
        log.warning("configuration: %s", warning)

    publisher.start()
    sweeper = asyncio.create_task(_heartbeat_sweeper(), name="heartbeat-sweeper")
    app.state.sweeper = sweeper
    log.info("robot gateway endpoint enabled=%s", settings.ROBOT_GATEWAY_ENABLED)

    try:
        yield
    finally:
        log.info("shutting down")
        sweeper.cancel()
        try:
            await sweeper
        except (asyncio.CancelledError, Exception):
            pass
        await publisher.stop()
        await registry.close_all()
        await hub.close_all()
        await dispose_engine()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
# An explicit allow-list only.  ``allow_credentials`` is enabled because the
# dashboards send an Authorization header, so a wildcard is never acceptable.
_origins = settings.cors_origin_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=bool(_origins),
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)

if not _origins:
    log.warning("CORS_ORIGINS is empty — browser clients from another origin will be blocked")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id")
    if not request_id:
        request_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:20]
    request_id_var.set(request_id)
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ── routers ──────────────────────────────────────────────────────────────────
from .api.routes import (  # noqa: E402  (imported after the app is configured)
    admin,
    auth,
    health,
    kitchen,
    menu,
    missions,
    notifications,
    orders,
    robots,
    tables,
    uploads,
    websocket,
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(menu.router)
app.include_router(tables.router)
app.include_router(orders.router)
app.include_router(kitchen.router)
app.include_router(robots.router)
app.include_router(missions.router)
app.include_router(notifications.router)
app.include_router(admin.router)
app.include_router(uploads.router)
app.include_router(websocket.router)


# The browser clients are intentionally static.  Serving them from the same
# origin makes local deployment deterministic while the customer folder can
# still be copied to Netlify or another CDN with ``window.__SMART_RESTAURANT_API__``.
# ``main.py`` lives at ``<project>/backend/app/main.py`` in the source tree
# and at ``/app/app/main.py`` in the container.  The web directory is the
# sibling of ``backend`` (``<project>/web`` or ``/app/web``), so parents[1]
# is the stable project root in both layouts.
_web_root = Path(__file__).resolve().parents[1] / "web"
for _name in ("customer", "kitchen", "admin", "robot-screen", "shared"):
    _folder = _web_root / _name
    if _folder.is_dir():
        app.mount(f"/{_name}", StaticFiles(directory=str(_folder), html=True), name=f"{_name}-web")

_assets_root = _web_root / "assets"
if _assets_root.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_assets_root)), name="assets")


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/api/health",
        "api_prefix": settings.API_PREFIX,
    }
