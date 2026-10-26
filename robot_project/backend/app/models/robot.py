"""Robots and their missions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, IdMixin, TimestampMixin
from .enums import (
    MissionStatus,
    RobotAvailability,
    RobotConnectionStatus,
)


class Robot(Base, IdMixin, TimestampMixin):
    __tablename__ = "robots"

    robot_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, default="Robot")
    description: Mapped[Optional[str]] = mapped_column(String(280), nullable=True)

    connection_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RobotConnectionStatus.UNKNOWN.value, index=True
    )
    availability: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RobotAvailability.AVAILABLE.value, index=True
    )
    fault_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    fault_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: ``None`` means "the robot does not publish a battery topic" — never faked.
    battery_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    battery_voltage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    home_destination_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    current_mission_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("robot_missions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    #: last reported pose — ``None`` until the gateway sends telemetry
    pose_x: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pose_y: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pose_yaw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pose_frame: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    gateway_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    ros_distro: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    navigation_action: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    missions: Mapped[List["RobotMission"]] = relationship(
        back_populates="robot", cascade="all, delete-orphan",
        foreign_keys="RobotMission.robot_id",
    )


class RobotMission(Base, IdMixin, TimestampMixin):
    __tablename__ = "robot_missions"
    __table_args__ = (
        UniqueConstraint("mission_uid", name="uq_robot_missions_mission_uid"),
    )

    #: globally unique id used on the wire between backend and gateway
    mission_uid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    robot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("robots.id", ondelete="SET NULL"), nullable=True, index=True
    )
    order_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    destination_id: Mapped[str] = mapped_column(String(80), nullable=False)
    destination_label: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default=MissionStatus.CREATED.value, index=True
    )
    mission_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="delivery")

    requested_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    arrived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    result: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    progress_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    distance_remaining: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    #: the id of the gateway command that started this mission (idempotency)
    dispatch_command_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    robot: Mapped[Optional[Robot]] = relationship(
        back_populates="missions", foreign_keys=[robot_id]
    )
    order: Mapped[Optional["Order"]] = relationship(
        foreign_keys=[order_id]
    )  # noqa: F821
    events: Mapped[List["MissionEvent"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan", order_by="MissionEvent.id"
    )


class MissionEvent(Base, IdMixin):
    """Every event the gateway reported for a mission — the evidence trail."""

    __tablename__ = "mission_events"

    mission_id: Mapped[int] = mapped_column(
        ForeignKey("robot_missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_uid: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(48), nullable=False, default="gateway")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    mission: Mapped["RobotMission"] = relationship(back_populates="events")

    @property
    def payload(self) -> dict[str, Any]:
        import json

        try:
            return json.loads(self.payload_json or "{}")
        except ValueError:
            return {}
