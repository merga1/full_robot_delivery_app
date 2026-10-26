"""In-process WebSocket hub.

Clients subscribe to a channel (``kitchen``, ``admin``, ``customer``, ``robot``).
Every connection owns a bounded queue and its own writer task, so a slow client
can never block the event loop or the other clients.  A client that overflows its
queue is closed — it will reconnect and refetch the authoritative state from the
REST API, which is exactly what the reconnect contract requires.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from fastapi import WebSocket

from ..models.enums import WebSocketChannel
from .schemas import EventEnvelope

log = logging.getLogger("robot.events.hub")

_MAX_QUEUE = 256


@dataclass
class Subscription:
    """One live WebSocket client."""

    websocket: WebSocket
    channel: str
    principal: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=_MAX_QUEUE))
    writer: Optional[asyncio.Task] = None
    #: for the customer channel: the order codes / tracking token this client may see
    scopes: set[str] = field(default_factory=set)
    alive: bool = True

    def matches(self, envelope: EventEnvelope) -> bool:
        if self.channel == "all":
            return True
        if envelope.channel not in (self.channel, "all"):
            return False
        if self.channel == WebSocketChannel.CUSTOMER.value:
            if not self.scopes:
                return False
            code = str(envelope.data.get("code") or envelope.data.get("order_code") or "")
            return code in self.scopes
        return True


class EventHub:
    """Connection registry and broadcaster."""

    def __init__(self) -> None:
        self._subscribers: dict[int, Subscription] = {}
        self._lock = asyncio.Lock()
        self._dropped = 0

    # ── connection lifecycle ────────────────────────────────────────────────
    async def register(
        self,
        websocket: WebSocket,
        channel: str,
        principal: str,
        scopes: Optional[Iterable[str]] = None,
    ) -> Subscription:
        sub = Subscription(
            websocket=websocket,
            channel=channel,
            principal=principal,
            scopes=set(scopes or ()),
        )
        sub.writer = asyncio.create_task(self._writer(sub), name=f"ws-writer-{id(sub)}")
        async with self._lock:
            self._subscribers[id(sub)] = sub
        log.info("ws client connected channel=%s principal=%s total=%s",
                 channel, principal, len(self._subscribers))
        return sub

    async def unregister(self, sub: Subscription) -> None:
        async with self._lock:
            self._subscribers.pop(id(sub), None)
        sub.alive = False
        if sub.writer is not None:
            sub.writer.cancel()
        try:
            await sub.websocket.close()
        except Exception:                       # pragma: no cover - close races are normal
            pass
        log.info("ws client disconnected channel=%s principal=%s total=%s",
                 sub.channel, sub.principal, len(self._subscribers))

    # ── publishing ──────────────────────────────────────────────────────────
    async def broadcast(self, envelope: EventEnvelope) -> int:
        """Send the envelope to every subscriber that may see it.

        Returns the number of clients the event was delivered to.
        """
        async with self._lock:
            targets = [s for s in self._subscribers.values() if s.alive and s.matches(envelope)]
        if not targets:
            return 0
        message = envelope.model_dump_json()
        for sub in targets:
            try:
                sub.queue.put_nowait(message)
            except asyncio.QueueFull:
                # Slow client: close it. It reconnects and refetches state.
                self._dropped += 1
                log.warning("ws client %s too slow — closing (channel=%s)",
                            sub.principal, sub.channel)
                sub.alive = False
        return len(targets)

    async def send_to(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        """Send a direct reply (e.g. a subscription confirmation)."""
        await websocket.send_text(json.dumps(payload, default=str))

    # ── introspection ───────────────────────────────────────────────────────
    @property
    def connection_count(self) -> int:
        return len(self._subscribers)

    @property
    def dropped_count(self) -> int:
        return self._dropped

    def stats(self) -> dict[str, Any]:
        by_channel: dict[str, int] = {}
        for sub in self._subscribers.values():
            by_channel[sub.channel] = by_channel.get(sub.channel, 0) + 1
        return {
            "connections": len(self._subscribers),
            "by_channel": by_channel,
            "dropped_slow_clients": self._dropped,
        }

    async def close_all(self) -> None:
        async with self._lock:
            subs = list(self._subscribers.values())
            self._subscribers.clear()
        for sub in subs:
            sub.alive = False
            if sub.writer is not None:
                sub.writer.cancel()
            try:
                await sub.websocket.close()
            except Exception:
                pass

    # ── writer task ─────────────────────────────────────────────────────────
    async def _writer(self, sub: Subscription) -> None:
        try:
            while True:
                message = await sub.queue.get()
                await sub.websocket.send_text(message)
        except asyncio.CancelledError:            # pragma: no cover - shutdown path
            raise
        except Exception as exc:
            log.info("ws writer for %s stopped: %s", sub.principal, exc)
            sub.alive = False


hub = EventHub()
