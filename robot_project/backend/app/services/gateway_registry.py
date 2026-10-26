"""Live registry of connected robot gateways.

A gateway opens an **outbound** WebSocket to the backend and authenticates with
its own robot credential.  The registry keeps the socket plus the pending
acknowledgement futures, so a dispatch request can wait for a real
``COMMAND_ACK`` instead of assuming success.

Nothing in this module talks to ROS 2 — the gateway owns the physical robot.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import WebSocket

from ..core.config import settings
from ..events.schemas import CommandAckOut, CommandRejectOut, RobotCommand, new_id

log = logging.getLogger("robot.gateway.registry")


class GatewayNotConnected(RuntimeError):
    """The robot has no live gateway connection."""


class CommandAckTimeout(RuntimeError):
    """The gateway did not acknowledge the command in time."""


class CommandRejected(RuntimeError):
    """The gateway explicitly rejected the command."""


@dataclass
class GatewayConnection:
    robot_id: str
    websocket: WebSocket
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    hello: dict[str, Any] = field(default_factory=dict)
    destinations: list[str] = field(default_factory=list)
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def resolve(self, command_id: str, result: dict[str, Any]) -> bool:
        future = self.pending.pop(command_id, None)
        if future is None or future.done():
            return False
        future.set_result(result)
        return True


class GatewayRegistry:
    """Maps ``robot_id`` → live gateway connection."""

    def __init__(self) -> None:
        self._connections: dict[str, GatewayConnection] = {}
        self._lock = asyncio.Lock()

    async def register(
        self, robot_id: str, websocket: WebSocket
    ) -> GatewayConnection:
        conn = GatewayConnection(robot_id=robot_id, websocket=websocket)
        async with self._lock:
            previous = self._connections.get(robot_id)
            self._connections[robot_id] = conn
        if previous is not None:
            log.warning("gateway for %s reconnected — closing the previous socket", robot_id)
            try:
                await previous.websocket.close(code=4000, reason="replaced by a new connection")
            except Exception:
                pass
        log.info("gateway connected: %s", robot_id)
        return conn

    async def unregister(self, robot_id: str, conn: Optional[GatewayConnection] = None) -> None:
        async with self._lock:
            current = self._connections.get(robot_id)
            if current is None:
                return
            if conn is not None and current is not conn:
                return
            self._connections.pop(robot_id, None)
        # Fail every pending command: the robot is unreachable.
        for future in list(conn.pending.values() if conn else []):
            if not future.done():
                future.set_exception(GatewayNotConnected(
                    f"gateway for {robot_id} disconnected before acknowledging"
                ))
        if conn:
            conn.pending.clear()
        log.info("gateway disconnected: %s", robot_id)

    def is_connected(self, robot_id: str) -> bool:
        return robot_id in self._connections

    def connection(self, robot_id: str) -> Optional[GatewayConnection]:
        return self._connections.get(robot_id)

    @property
    def robot_ids(self) -> list[str]:
        return list(self._connections.keys())

    def stats(self) -> dict[str, Any]:
        return {
            "connected": len(self._connections),
            "robots": list(self._connections.keys()),
        }

    # ── command delivery ────────────────────────────────────────────────────
    async def send_command(
        self,
        robot_id: str,
        command: RobotCommand,
        wait_for_ack: bool = True,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Send a command and (by default) wait for the gateway's acknowledgement.

        Returns the ack payload.  Raises :class:`GatewayNotConnected`,
        :class:`CommandRejected` or :class:`CommandAckTimeout`.
        """
        conn = self._connections.get(robot_id)
        if conn is None:
            raise GatewayNotConnected(f"robot '{robot_id}' has no gateway connection")

        timeout = timeout if timeout is not None else settings.ROBOT_COMMAND_ACK_TIMEOUT_SECONDS
        loop = asyncio.get_running_loop()
        future: Optional[asyncio.Future] = None
        if wait_for_ack:
            future = loop.create_future()
            conn.pending[command.command_id] = future

        payload = command.model_dump()
        try:
            await conn.websocket.send_json(payload)
        except Exception as exc:
            if future is not None:
                conn.pending.pop(command.command_id, None)
                if not future.done():
                    future.set_exception(exc)
            raise GatewayNotConnected(f"could not send command to '{robot_id}': {exc}") from exc

        if not wait_for_ack:
            return {"status": "SENT", "command_id": command.command_id}

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            conn.pending.pop(command.command_id, None)
            raise CommandAckTimeout(
                f"robot '{robot_id}' did not acknowledge {command.command} within {timeout:.0f}s"
            ) from exc
        except GatewayNotConnected:
            conn.pending.pop(command.command_id, None)
            raise
        except Exception:
            conn.pending.pop(command.command_id, None)
            raise

    def resolve_ack(self, robot_id: str, command_id: str, payload: dict[str, Any]) -> bool:
        conn = self._connections.get(robot_id)
        if conn is None:
            return False
        return conn.resolve(command_id, payload)

    async def broadcast_to_robot(self, robot_id: str, payload: dict[str, Any]) -> bool:
        conn = self._connections.get(robot_id)
        if conn is None:
            return False
        try:
            await conn.websocket.send_json(payload)
            return True
        except Exception:
            log.exception("could not push payload to gateway %s", robot_id)
            return False

    async def close_all(self) -> None:
        async with self._lock:
            conns = list(self._connections.values())
            self._connections.clear()
        for conn in conns:
            for future in list(conn.pending.values()):
                if not future.done():
                    future.set_exception(GatewayNotConnected("backend shutting down"))
            try:
                await conn.websocket.close()
            except Exception:
                pass


registry = GatewayRegistry()


def build_command(
    command: str,
    robot_id: str,
    *,
    mission_uid: Optional[str] = None,
    destination_id: Optional[str] = None,
    order_code: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    payload: Optional[dict[str, Any]] = None,
) -> RobotCommand:
    """Create a command with an expiry the gateway will enforce."""
    from datetime import timedelta

    ttl = ttl_seconds if ttl_seconds is not None else settings.ROBOT_COMMAND_TTL_SECONDS
    issued = datetime.now(timezone.utc)
    expires = issued + timedelta(seconds=ttl)
    return RobotCommand(
        event_uid=new_id("cmd_evt"),
        command_id=new_id("cmd"),
        command=command,
        robot_id=robot_id,
        mission_uid=mission_uid,
        destination_id=destination_id,
        order_code=order_code,
        issued_at=issued.isoformat(),
        expires_at=expires.isoformat(),
        payload=payload or {},
    )


def ack_from_frame(frame: Any) -> dict[str, Any]:
    """Normalise an inbound gateway frame into an ack dict."""
    if isinstance(frame, (CommandAckOut, CommandRejectOut)):
        return frame.model_dump()
    if hasattr(frame, "model_dump"):
        return frame.model_dump()
    if isinstance(frame, dict):
        return dict(frame)
    return {"status": "UNKNOWN", "message": str(frame)}
