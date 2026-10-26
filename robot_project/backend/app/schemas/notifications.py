"""Notification schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from .common import ORMModel


class NotificationOut(ORMModel):
    id: int
    event_uid: str
    notification_type: str
    priority: str
    channel: str
    recipient: str
    title: str
    body: str
    payload: dict[str, Any] = Field(default_factory=dict)
    delivery_status: str
    created_at: datetime
    delivered_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None


class NotificationAck(BaseModel):
    acknowledged: bool = True
    note: str = Field(default="", max_length=280)
