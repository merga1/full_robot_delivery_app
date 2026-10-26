"""Notification records (history + acknowledgement) and the audit log."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, IdMixin, TimestampMixin
from .enums import NotificationDeliveryStatus, NotificationPriority, NotificationType


class Notification(Base, IdMixin, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint(
            "event_uid",
            "channel",
            "recipient",
            name="uq_notifications_event_channel_recipient",
        ),
    )

    #: the business event that produced this notification — used for deduplication
    event_uid: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    notification_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, default=NotificationPriority.NORMAL.value
    )
    #: channel this record represents (KITCHEN / ADMIN / FLUTTER / ROBOT / CUSTOMER)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: "kitchen", "admin", a customer id, a robot id, ...
    recipient: Mapped[str] = mapped_column(String(80), nullable=False, default="*", index=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    delivery_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=NotificationDeliveryStatus.PENDING.value, index=True
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    @property
    def payload(self) -> dict[str, Any]:
        import json

        try:
            return json.loads(self.payload_json or "{}")
        except ValueError:
            return {}


class DeviceToken(Base, IdMixin, TimestampMixin):
    """A push token registered by the Flutter app.

    A WebSocket event does **not** create an operating-system notification —
    the app has to register here so the push provider has a real target.
    """

    __tablename__ = "device_tokens"
    __table_args__ = (
        UniqueConstraint("token", name="uq_device_tokens_token"),
    )

    token: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default="android")
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False, default="*", index=True)
    roles_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AuditLog(Base, IdMixin):
    __tablename__ = "audit_logs"

    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system", index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False, default="")
    resource_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    #: safe metadata only — never secrets, never card data
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    @property
    def details(self) -> dict[str, Any]:
        import json

        try:
            return json.loads(self.metadata_json or "{}")
        except ValueError:
            return {}
