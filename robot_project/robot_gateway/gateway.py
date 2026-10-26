from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import websockets

from .backend_contract import BackendCommand, command_expired

from .audio_announcer import AudioAnnouncer
from .config import GatewayConfig
from .destinations import DestinationRegistry
from .nav2_adapter import NavigationAdapter, NavigationUnavailable, SimulationNavigationAdapter

log = logging.getLogger("robot.gateway")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RobotGateway:
    def __init__(self, config: GatewayConfig, destinations: DestinationRegistry,
                 adapter: NavigationAdapter | None = None) -> None:
        self.config = config
        self.destinations = destinations
        self.adapter = adapter or SimulationNavigationAdapter(
            destinations.ids(), seconds=3.0
        )
        self.announcer = AudioAnnouncer(config.tts_binary)
        self._send_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._seen_commands: set[str] = set()
        self._mission_uid: str | None = None
        self._destination_id: str | None = None
        self._order_code: str | None = None
        self._navigation_task: asyncio.Task | None = None
        self._cancel_event = asyncio.Event()
        self._socket: Any = None

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("gateway connection lost: %s", exc)
                await asyncio.sleep(self.config.reconnect_seconds)

    async def stop(self) -> None:
        self._stop.set()
        self._cancel_event.set()
        if self._navigation_task:
            self._navigation_task.cancel()

    async def _run_once(self) -> None:
        async with websockets.connect(
            self.config.backend_url,
            extra_headers={"Authorization": f"Bearer {self.config.robot_token}"},
            open_timeout=self.config.connect_timeout_seconds,
            ping_interval=20,
        ) as socket:
            self._socket = socket
            self._cancel_event = asyncio.Event()
            await self._send(self._hello())
            heartbeat = asyncio.create_task(self._heartbeat_loop())
            try:
                async for raw in socket:
                    await self._handle_message(raw)
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                self._socket = None

    def _hello(self) -> dict[str, Any]:
        return {
            "frame": "HELLO",
            "robot_id": self.config.robot_id,
            "gateway_version": self.config.version,
            "ros_distro": self.config.ros_distro,
            "navigation_action": self.config.navigation_action,
            "destinations": self.destinations.ids(),
            "capabilities": ["navigation", "mission_events", "heartbeat", "assistance"],
        }

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.heartbeat_seconds)
            await self._send({
                "frame": "HEARTBEAT",
                "robot_id": self.config.robot_id,
                "mission_uid": self._mission_uid,
                "mission_status": "NAVIGATING" if self._navigation_task else "IDLE",
            })

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._socket is None:
            return
        async with self._send_lock:
            await self._socket.send(json.dumps({"event_uid": f"gw_{uuid.uuid4().hex}", "timestamp": _now(), **payload}))

    async def _handle_message(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
            command = BackendCommand.model_validate(payload)
        except Exception as exc:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode(errors="replace")
            log.warning("ignoring invalid backend frame %r: %s", raw, exc)
            return
        await self._handle_command(command)

    async def _ack(self, command: BackendCommand, status: str = "ACCEPTED", message: str = "") -> None:
        await self._send({"frame": "COMMAND_ACK", "robot_id": self.config.robot_id,
                          "command_id": command.command_id, "mission_uid": command.mission_uid,
                          "status": status, "message": message})

    async def _reject(self, command: BackendCommand, reason: str) -> None:
        await self._send({"frame": "COMMAND_REJECTED", "robot_id": self.config.robot_id,
                          "command_id": command.command_id, "mission_uid": command.mission_uid,
                          "reason": reason})

    async def _handle_command(self, command: BackendCommand) -> None:
        if command.robot_id != self.config.robot_id:
            await self._reject(command, "command is addressed to another robot")
            return
        if command_expired(command):
            await self._reject(command, "command expired")
            return
        if command.command_id in self._seen_commands:
            await self._ack(command, status="DUPLICATE", message="command already processed")
            return
        self._seen_commands.add(command.command_id)
        try:
            if command.destination_id:
                self.destinations.require(command.destination_id)
            if command.command == "ASSIGN_MISSION":
                self._mission_uid = command.mission_uid
                self._destination_id = command.destination_id
                self._order_code = command.order_code
                await self._ack(command)
                await self.announcer.announce(f"Mission assigned to {command.payload.get('destination_label', command.destination_id)}")
                return
            if command.command == "PREPARE_DISPATCH":
                await self._ack(command, message="robot is ready for dispatch")
                return
            if command.command == "DISPATCH_MISSION":
                if command.mission_uid != self._mission_uid:
                    raise ValueError("mission is not assigned to this robot")
                await self._ack(command)
                self._start_navigation(command, home=False)
                return
            if command.command == "RETURN_HOME":
                await self._ack(command)
                self._start_navigation(command, home=True)
                return
            if command.command == "PAUSE_MISSION":
                self._cancel_event.set()
                await self._ack(command, message="pause requested")
                return
            if command.command == "RESUME_MISSION":
                await self._ack(command, message="resume accepted")
                return
            if command.command == "CANCEL_MISSION":
                self._cancel_event.set()
                await self._ack(command, message="cancel requested")
                await self._event("MISSION_CANCELLED", command.mission_uid, "Mission cancelled")
                return
            if command.command == "REQUEST_ASSISTANCE":
                await self._ack(command, message="assistance request received")
                await self._event("ASSISTANCE_REQUIRED", command.mission_uid, command.payload.get("note", "Staff assistance requested"))
                return
            raise ValueError(f"unsupported command '{command.command}'")
        except (ValueError, NavigationUnavailable) as exc:
            await self._reject(command, str(exc))

    def _start_navigation(self, command: BackendCommand, *, home: bool) -> None:
        if self._navigation_task and not self._navigation_task.done():
            self._cancel_event.set()
            self._navigation_task.cancel()
        self._cancel_event = asyncio.Event()
        self._navigation_task = asyncio.create_task(self._navigate(command, home=home))

    async def _navigate(self, command: BackendCommand, *, home: bool) -> None:
        destination_id = self.config.home_destination if home else command.destination_id
        if not destination_id:
            await self._event("NAVIGATION_FAILED", command.mission_uid, "No destination was supplied")
            return
        try:
            destination = self.destinations.require(destination_id)
            await self._event("NAVIGATION_STARTED", command.mission_uid, f"Navigating to {destination.label}")
            async def progress(value: float) -> None:
                await self._event("NAVIGATION_PROGRESS", command.mission_uid, "Navigation progress", {"progress_percent": value})
            await self.adapter.navigate(destination, on_progress=progress, cancel_event=self._cancel_event)
            await self._event("NAVIGATION_SUCCEEDED", command.mission_uid, f"Reached {destination.label}")
        except asyncio.CancelledError:
            return
        except Exception as exc:
            await self._event("NAVIGATION_FAILED", command.mission_uid, str(exc))

    async def _event(self, event_type: str, mission_uid: str | None, message: str,
                     data: dict[str, Any] | None = None) -> None:
        if not mission_uid:
            return
        await self._send({"frame": "MISSION_EVENT", "robot_id": self.config.robot_id,
                          "mission_uid": mission_uid, "event_type": event_type,
                          "message": message, "data": data or {}})


def build_gateway(config: GatewayConfig) -> RobotGateway:
    destinations = DestinationRegistry.from_file(config.destinations_file)
    if config.allow_simulation:
        adapter: NavigationAdapter = SimulationNavigationAdapter(destinations.ids())
    else:
        from .nav2_adapter import RosNav2Adapter
        adapter = RosNav2Adapter(destinations.ids(), config.navigation_action)
    return RobotGateway(config, destinations, adapter)
