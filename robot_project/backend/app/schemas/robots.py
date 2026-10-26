"""Robot, mission and telemetry schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from .common import ORMModel


class RobotOut(ORMModel):
    id: int
    robot_id: str
    name: str
    description: Optional[str] = None
    connection_status: str
    availability: str
    fault_code: Optional[str] = None
    fault_detail: Optional[str] = None
    battery_percent: Optional[float] = None
    battery_voltage: Optional[float] = None
    home_destination_id: Optional[str] = None
    current_mission_id: Optional[int] = None
    last_heartbeat_at: Optional[datetime] = None
    connected_at: Optional[datetime] = None
    pose_x: Optional[float] = None
    pose_y: Optional[float] = None
    pose_yaw: Optional[float] = None
    pose_frame: Optional[str] = None
    gateway_version: Optional[str] = None
    ros_distro: Optional[str] = None
    navigation_action: Optional[str] = None


class RobotDetail(RobotOut):
    recent_missions: list["MissionOut"] = Field(default_factory=list)


class MissionEventOut(ORMModel):
    id: int
    event_uid: str
    event_type: str
    source: str
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class MissionOut(ORMModel):
    id: int
    mission_uid: str
    robot_id: Optional[str] = None
    order_id: Optional[int] = None
    destination_id: str
    destination_label: str = ""
    status: str
    mission_kind: str = "delivery"
    requested_at: datetime
    acknowledged_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    arrived_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[str] = None
    failure_reason: Optional[str] = None
    progress_percent: float = 0.0
    distance_remaining: Optional[float] = None
    dispatch_command_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MissionDetail(MissionOut):
    events: list[MissionEventOut] = Field(default_factory=list)


class ReturnHomeBody(BaseModel):
    command_id: Optional[str] = Field(default=None, max_length=64)
    force: bool = False


class MissionCommandBody(BaseModel):
    command_id: Optional[str] = Field(default=None, max_length=64)
    reason: str = Field(default="", max_length=280)


RobotDetail.model_rebuild()
