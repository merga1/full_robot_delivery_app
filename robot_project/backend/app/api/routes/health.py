"""Health and readiness endpoints."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...db.session import get_session
from ...events.hub import hub
from ...services.gateway_registry import registry

log = logging.getLogger("robot.health")

router = APIRouter(tags=["health"])


@router.get("/api/health", summary="Liveness and readiness of the backend")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    """Returns the state of the backend, the database and the robot gateways."""
    database_ok = False
    database_error: str | None = None
    try:
        await session.execute(text("SELECT 1"))
        database_ok = True
    except Exception as exc:                     # pragma: no cover - depends on the DB
        database_error = str(exc)
        log.error("health check: database unreachable: %s", exc)

    payload = {
        "status": "ok" if database_ok else "degraded",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "time": datetime.now(timezone.utc).isoformat(),
        "database": {"ok": database_ok, "error": database_error},
        "websocket": hub.stats(),
        "gateways": registry.stats(),
        "push_configured": bool(settings.FCM_PROJECT_ID and settings.FCM_CREDENTIALS_PATH),
    }
    return payload


@router.get("/api/v1/health", include_in_schema=False)
async def health_v1(session: AsyncSession = Depends(get_session)) -> dict:
    return await health(session)
