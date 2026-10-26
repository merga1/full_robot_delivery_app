"""Notification, audit and idempotency repositories."""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import utcnow
from ..models.notification import AuditLog, Notification
from ..models.outbox import IdempotencyRecord


async def add_notification(
    session: AsyncSession,
    *,
    event_uid: str,
    notification_type: str,
    channel: str,
    title: str,
    body: str,
    recipient: str = "*",
    priority: str = "NORMAL",
    payload: Optional[dict[str, Any]] = None,
) -> Notification:
    row = Notification(
        event_uid=event_uid,
        notification_type=notification_type,
        priority=priority,
        channel=channel,
        recipient=recipient,
        title=title,
        body=body,
        payload_json=json.dumps(payload or {}, default=str),
        delivery_status="PENDING",
        created_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def find_notification(
    session: AsyncSession,
    *,
    event_uid: str,
    channel: str,
    recipient: str = "*",
) -> Optional[Notification]:
    """Return an existing notification for an event/channel/recipient tuple."""
    stmt = select(Notification).where(
        Notification.event_uid == event_uid,
        Notification.channel == channel,
        Notification.recipient == recipient,
    )
    return (await session.execute(stmt)).scalars().first()


async def list_notifications(
    session: AsyncSession,
    channel: Optional[str] = None,
    recipient: Optional[str] = None,
    acknowledged: Optional[bool] = None,
    limit: int = 50,
) -> list[Notification]:
    stmt = select(Notification)
    if channel:
        stmt = stmt.where(Notification.channel == channel)
    if recipient:
        stmt = stmt.where(Notification.recipient == recipient)
    if acknowledged is not None:
        stmt = stmt.where(
            Notification.acknowledged_at.is_(None)
            if acknowledged is False
            else Notification.acknowledged_at.is_not(None)
        )
    stmt = stmt.order_by(Notification.id.desc()).limit(max(1, min(limit, 500)))
    return list((await session.execute(stmt)).scalars().all())


async def get_notification(session: AsyncSession, notification_id: int) -> Optional[Notification]:
    return await session.get(Notification, notification_id)


async def add_audit(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    metadata: Optional[dict[str, Any]] = None,
    actor_id: Optional[int] = None,
    request_id: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> AuditLog:
    row = AuditLog(
        actor=actor,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id or ""),
        metadata_json=json.dumps(metadata or {}, default=str),
        request_id=request_id,
        ip_address=ip_address,
        created_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def list_audit(
    session: AsyncSession, limit: int = 100, action: Optional[str] = None
) -> list[AuditLog]:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    stmt = stmt.order_by(AuditLog.id.desc()).limit(max(1, min(limit, 500)))
    return list((await session.execute(stmt)).scalars().all())


async def find_idempotent(
    session: AsyncSession, scope: str, key: str
) -> Optional[IdempotencyRecord]:
    stmt = select(IdempotencyRecord).where(
        IdempotencyRecord.scope == scope, IdempotencyRecord.key == key
    )
    return (await session.execute(stmt)).scalars().first()


async def save_idempotent(
    session: AsyncSession,
    *,
    scope: str,
    key: str,
    request_fingerprint: str,
    response: dict[str, Any],
    status_code: int = 200,
    resource_id: Optional[int] = None,
) -> IdempotencyRecord:
    row = IdempotencyRecord(
        scope=scope,
        key=key,
        request_fingerprint=request_fingerprint,
        response_json=json.dumps(response, default=str),
        status_code=status_code,
        resource_id=resource_id,
        created_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def register_device(
    session: AsyncSession,
    *,
    token: str,
    platform: str = "android",
    username: str = "*",
    user_id: Optional[int] = None,
    roles: Optional[list[str]] = None,
) -> Any:
    """Store (or refresh) a push device token."""
    from ..models.notification import DeviceToken

    stmt = select(DeviceToken).where(DeviceToken.token == token)
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        row = DeviceToken(token=token)
        session.add(row)
    row.platform = platform or "android"
    row.username = username
    row.user_id = user_id
    row.roles_json = json.dumps(sorted(roles or []))
    row.active = True
    await session.flush()
    return row


async def list_device_tokens(
    session: AsyncSession, username: Optional[str] = None
) -> list[Any]:
    from ..models.notification import DeviceToken

    stmt = select(DeviceToken)
    if username:
        stmt = stmt.where(DeviceToken.username == username)
    return list((await session.execute(stmt)).scalars().all())
