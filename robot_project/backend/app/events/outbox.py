"""Transactional outbox.

``enqueue_event()`` writes an :class:`OutboxEvent` row inside the *same* database
transaction as the business change.  A background publisher then drains the table
and pushes the events onto the WebSocket hub.

Consequences:

* A crash between the commit and the push loses nothing — the row is still there.
* A crash between the push and the ``published_at`` update may re-deliver an
  event.  Every envelope therefore carries a stable ``event_uid`` and clients
  de-duplicate on it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..db.base import utcnow
from ..db.session import SessionLocal
from ..models.enums import EventType
from ..models.outbox import OutboxEvent
from .hub import hub
from .schemas import EventEnvelope, new_id

log = logging.getLogger("robot.events.outbox")


async def enqueue_event(
    session: AsyncSession,
    event_type: EventType | str,
    data: Optional[dict[str, Any]] = None,
    channel: str = "all",
    correlation_id: Optional[str] = None,
    event_uid: Optional[str] = None,
) -> OutboxEvent:
    """Add an event to the outbox using the caller's open transaction."""
    import json

    uid = event_uid or new_id("evt")
    row = OutboxEvent(
        event_uid=uid,
        event_type=event_type.value if isinstance(event_type, EventType) else str(event_type),
        channel=channel,
        payload_json=json.dumps(data or {}, default=str),
        correlation_id=correlation_id,
        created_at=utcnow(),
    )
    session.add(row)
    return row


class OutboxPublisher:
    """Background task that drains the outbox into the WebSocket hub."""

    def __init__(self, poll_interval: Optional[float] = None) -> None:
        self.poll_interval = poll_interval or settings.OUTBOX_POLL_INTERVAL_SECONDS
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self.published = 0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="outbox-publisher")
            log.info("outbox publisher started (interval=%.2fs)", self.poll_interval)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                count = await self.drain_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("outbox drain failed")
                count = 0
            if count == 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
                except asyncio.TimeoutError:
                    pass

    async def drain_once(self, limit: int = 100) -> int:
        """Publish up to ``limit`` pending events.  Returns the number published."""
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.id)
                    .limit(limit)
                )
            ).scalars().all()
            if not rows:
                return 0
            for row in rows:
                envelope = EventEnvelope(
                    event_uid=row.event_uid,
                    event_type=row.event_type,
                    channel=row.channel,
                    correlation_id=row.correlation_id,
                    occurred_at=row.created_at.isoformat() if row.created_at else None,
                    data=row.payload,
                )
                try:
                    await hub.broadcast(envelope)
                except Exception:
                    log.exception("could not publish outbox event %s", row.event_uid)
                    row.attempts += 1
                    row.last_error = "broadcast failed"
                    continue
                row.published_at = utcnow()
                row.attempts += 1
                self.published += 1
            await session.commit()
            return len(rows)


publisher = OutboxPublisher()
