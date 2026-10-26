"""Shared response helpers."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    ok: bool = True
    message: str = ""
    detail: str | None = None


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None


class Page(BaseModel, Generic[T]):
    items: list[T] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class Ack(BaseModel):
    """Acknowledgement of a command — receipt only, not execution."""

    ok: bool = True
    status: str
    message: str = ""
    command_id: str | None = None
    mission_id: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)
