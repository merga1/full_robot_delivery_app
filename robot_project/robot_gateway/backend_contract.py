from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class BackendCommand(BaseModel):
    event_uid: str = Field(default="")
    command_id: str
    command: str
    robot_id: str
    mission_uid: str | None = None
    order_code: str | None = None
    destination_id: str | None = None
    issued_at: str = ""
    expires_at: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = "1.0"


def command_expired(command: BackendCommand) -> bool:
    if not command.expires_at:
        return False
    try:
        value = datetime.fromisoformat(command.expires_at.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value <= datetime.now(timezone.utc)
    except ValueError:
        return True
