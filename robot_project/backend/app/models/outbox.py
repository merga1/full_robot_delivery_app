"""Transactional outbox and idempotency records.

The outbox guarantees that a database change and the event that announces it are
committed together.  A publisher task then drains the outbox and pushes events to
the WebSocket channels.  If the process crashes between the commit and the push,
the event is still in the outbox and is delivered after the restart — nothing is
silently lost and nothing is published twice.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, IdMixin, utcnow


class OutboxEvent(Base, IdMixin):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("event_uid", name="uq_outbox_events_event_uid"),
    )

    event_uid: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="all", index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    correlation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    @property
    def payload(self) -> dict[str, Any]:
        import json

        try:
            return json.loads(self.payload_json or "{}")
        except ValueError:
            return {}


class IdempotencyRecord(Base, IdMixin):
    """Stores the result of a mutating request keyed by the caller's key.

    A repeated ``POST /orders`` with the same ``Idempotency-Key`` returns the
    original response instead of creating a second order.
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("scope", "key", name="uq_idempotency_records_scope_key"),
    )

    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    response_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    resource_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
