"""Notification history, acknowledgement and device registration.

The WebSocket event stream is **not** an operating-system push notification.
For real push the Flutter app registers its FCM token here; without Firebase
credentials the backend reports push as *not configured* instead of pretending
to deliver.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...db.session import get_session
from ...schemas.notifications import NotificationAck, NotificationOut
from ...services import notification_service, push
from ..dependencies import Principal, require_staff

log = logging.getLogger("robot.api.notifications")

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class DeviceRegistration(BaseModel):
    token: str = Field(min_length=8, max_length=512)
    platform: str = Field(default="android", max_length=20)
    user: Optional[str] = Field(default=None, max_length=64)
    roles: list[str] = Field(default_factory=list)


@router.get("", response_model=list[NotificationOut], summary="Notification history")
async def list_notifications(
    channel: Optional[str] = None,
    recipient: Optional[str] = None,
    acknowledged: Optional[bool] = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> list[NotificationOut]:
    rows = await notification_service.history(
        session, channel=channel, recipient=recipient,
        acknowledged=acknowledged, limit=limit,
    )
    return [NotificationOut.model_validate(r) for r in rows]


@router.post("/{notification_id}/ack", response_model=NotificationOut,
             summary="Acknowledge a notification")
async def acknowledge(
    notification_id: int,
    payload: NotificationAck,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> NotificationOut:
    row = await notification_service.acknowledge(session, notification_id, principal.username)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Notification not found")
    await session.commit()
    return NotificationOut.model_validate(row)


@router.post("/register-device", summary="Register a push device token")
async def register_device(
    payload: DeviceRegistration,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> dict:
    """Store the device token so push notifications can be delivered.

    Tokens are kept in the ``DeviceToken`` table.  When FCM is not configured
    the endpoint still succeeds (the token is stored) but the response says
    that push delivery is **not configured**.
    """
    from ...repositories import notifications as notif_repo

    record = await notif_repo.register_device(
        session,
        token=payload.token,
        platform=payload.platform,
        username=principal.username,
        roles=payload.roles,
    )
    await session.commit()
    return {
        "ok": True,
        "push_configured": push.is_configured(),
        "provider": push.get_provider().name,
        "device_id": record.id,
    }


@router.get("/push-status", summary="Is the push provider configured?")
async def push_status(principal: Principal = Depends(require_staff)) -> dict:
    return {
        "configured": push.is_configured(),
        "provider": push.get_provider().name,
        "project_id": settings.FCM_PROJECT_ID or None,
        "note": (
            "Firebase Cloud Messaging is configured."
            if push.is_configured()
            else "Firebase Cloud Messaging is NOT configured — no operating-system "
                 "push notification is sent. WebSocket events still work."
        ),
    }
