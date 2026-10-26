"""The wire contract between the backend and every WebSocket client.

Two directions, two schemas:

* **backend → client** : :class:`EventEnvelope` (real-time notifications).
* **gateway ↔ backend** : :class:`GatewayFrame` subclasses (commands, acks,
  telemetry, mission events).

Every frame carries an ``event_uid``.  The backend stores it, so a duplicated or
replayed frame is detected and ignored instead of double-advancing a mission.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from ..models.enums import EventType, MissionEventType

SCHEMA_VERSION = "1.0"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "evt") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


class EventEnvelope(BaseModel):
    """What every WebSocket client receives."""

    event_uid: str = Field(default_factory=lambda: new_id("evt"))
    event_type: str
    channel: str = "all"
    occurred_at: str = Field(default_factory=utcnow_iso)
    correlation_id: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def build(
        cls,
        event_type: EventType | str,
        data: Optional[dict[str, Any]] = None,
        channel: str = "all",
        correlation_id: Optional[str] = None,
        event_uid: Optional[str] = None,
    ) -> "EventEnvelope":
        return cls(
            event_uid=event_uid or new_id("evt"),
            event_type=event_type.value if isinstance(event_type, EventType) else str(event_type),
            channel=channel,
            correlation_id=correlation_id,
            data=data or {},
        )


# ── Gateway → backend ─────────────────────────────────────────────────────────

class GatewayFrameType(str, Enum):
    HELLO = "HELLO"
    HEARTBEAT = "HEARTBEAT"
    TELEMETRY = "TELEMETRY"
    MISSION_EVENT = "MISSION_EVENT"
    COMMAND_ACK = "COMMAND_ACK"
    COMMAND_REJECTED = "COMMAND_REJECTED"
    ERROR = "ERROR"


class GatewayFrame(BaseModel):
    """Base class for every frame a gateway sends."""

    frame: str
    event_uid: str = Field(default_factory=lambda: new_id("gw"))
    robot_id: str
    timestamp: str = Field(default_factory=utcnow_iso)
    schema_version: str = SCHEMA_VERSION


class GatewayHello(GatewayFrame):
    frame: str = GatewayFrameType.HELLO.value
    robot_id: str
    gateway_version: str = ""
    ros_distro: str = ""
    navigation_action: str = "/navigate_to_pose"
    destinations: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class GatewayHeartbeat(GatewayFrame):
    frame: str = GatewayFrameType.HEARTBEAT.value
    robot_id: str
    mission_uid: Optional[str] = None
    mission_status: Optional[str] = None
    battery_percent: Optional[float] = None
    battery_voltage: Optional[float] = None
    position: Optional[dict[str, Any]] = None
    fault_code: Optional[str] = None
    fault_detail: Optional[str] = None


class GatewayTelemetry(GatewayFrame):
    frame: str = GatewayFrameType.TELEMETRY.value
    robot_id: str
    mission_uid: Optional[str] = None
    mission_status: Optional[str] = None
    connection_status: str = "ONLINE"
    battery_percent: Optional[float] = None
    battery_voltage: Optional[float] = None
    position: Optional[dict[str, Any]] = None
    navigation_status: Optional[str] = None
    distance_remaining: Optional[float] = None
    fault_code: Optional[str] = None
    fault_detail: Optional[str] = None


class GatewayMissionEvent(GatewayFrame):
    frame: str = GatewayFrameType.MISSION_EVENT.value
    robot_id: str
    mission_uid: str
    command_id: Optional[str] = None
    event_type: str
    message: str = ""
    position: Optional[dict[str, Any]] = None
    data: dict[str, Any] = Field(default_factory=dict)


class GatewayCommandAck(GatewayFrame):
    frame: str = GatewayFrameType.COMMAND_ACK.value
    robot_id: str
    command_id: str
    mission_uid: Optional[str] = None
    status: str = "ACCEPTED"
    message: str = ""


class GatewayCommandRejected(GatewayFrame):
    frame: str = GatewayFrameType.COMMAND_REJECTED.value
    robot_id: str
    command_id: str
    mission_uid: Optional[str] = None
    reason: str = ""
    status: str = "REJECTED"


class GatewayError(GatewayFrame):
    frame: str = GatewayFrameType.ERROR.value
    robot_id: str
    message: str = ""
    detail: str = ""


# ── Backend → gateway ─────────────────────────────────────────────────────────

class RobotCommandType(str, Enum):
    ASSIGN_MISSION = "ASSIGN_MISSION"
    PREPARE_DISPATCH = "PREPARE_DISPATCH"
    DISPATCH_MISSION = "DISPATCH_MISSION"
    PAUSE_MISSION = "PAUSE_MISSION"
    RESUME_MISSION = "RESUME_MISSION"
    CANCEL_MISSION = "CANCEL_MISSION"
    RETURN_HOME = "RETURN_HOME"
    REQUEST_ASSISTANCE = "REQUEST_ASSISTANCE"


class RobotCommand(BaseModel):
    """A command issued to a robot gateway.

    ``expires_at`` is enforced by the gateway: an expired command is rejected and
    never starts a physical mission.
    """

    event_uid: str = Field(default_factory=lambda: new_id("cmd_evt"))
    command_id: str = Field(default_factory=lambda: new_id("cmd"))
    command: str
    robot_id: str
    mission_uid: Optional[str] = None
    order_code: Optional[str] = None
    destination_id: Optional[str] = None
    issued_at: str = Field(default_factory=utcnow_iso)
    expires_at: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION


class CommandAckOut(BaseModel):
    """What the backend sends back over the gateway socket."""

    frame: str = "COMMAND_ACK"
    event_uid: str = Field(default_factory=lambda: new_id("ack"))
    command_id: str
    robot_id: str
    mission_uid: Optional[str] = None
    status: str = "ACCEPTED"
    message: str = ""
    timestamp: str = Field(default_factory=utcnow_iso)


class CommandRejectOut(BaseModel):
    frame: str = "COMMAND_REJECTED"
    event_uid: str = Field(default_factory=lambda: new_id("rej"))
    command_id: str
    robot_id: str
    mission_uid: Optional[str] = None
    status: str = "REJECTED"
    reason: str = ""
    timestamp: str = Field(default_factory=utcnow_iso)


# ── Validation helper ────────────────────────────────────────────────────────

_FRAME_TYPES = {
    GatewayFrameType.HELLO.value: GatewayHello,
    GatewayFrameType.HEARTBEAT.value: GatewayHeartbeat,
    GatewayFrameType.TELEMETRY.value: GatewayTelemetry,
    GatewayFrameType.MISSION_EVENT.value: GatewayMissionEvent,
    GatewayFrameType.COMMAND_ACK.value: GatewayCommandAck,
    GatewayFrameType.COMMAND_REJECTED.value: GatewayCommandRejected,
    GatewayFrameType.ERROR.value: GatewayError,
}


def parse_gateway_frame(raw: str | bytes) -> GatewayFrame:
    """Parse and validate an inbound gateway frame.

    Raises :class:`ValueError` with a readable message for malformed input.
    """
    import json

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"frame is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("frame must be a JSON object")
    frame_type = payload.get("frame")
    model = _FRAME_TYPES.get(frame_type)
    if model is None:
        raise ValueError(f"unknown frame type '{frame_type}'")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid {frame_type} frame: {exc}") from exc


MISSION_EVENT_TYPES = {e.value for e in MissionEventType}
