"""Order schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, Field, field_validator

from .common import ORMModel


class OrderItemIn(BaseModel):
    menu_id: int = Field(gt=0)
    qty: int = Field(default=1, ge=1, le=50)
    customizations: str = Field(default="", max_length=500)
    preparation_note: Optional[str] = Field(default=None, max_length=280)

    @field_validator("customizations")
    @classmethod
    def _strip(cls, v: str) -> str:
        return (v or "").strip()


class OrderCreate(BaseModel):
    """Customer order payload.

    The client may send a ``table_code`` (from the QR code) **or** a
    ``table_id``.  Prices are *never* taken from the client — the server prices
    every line from the menu.
    """

    table_code: Optional[str] = Field(default=None, max_length=16)
    table_id: Optional[int] = Field(default=None, gt=0)
    items: list[OrderItemIn] = Field(default_factory=list)
    note: str = Field(default="", max_length=280)
    kind: str = Field(default="order", pattern="^(order|call)$")
    customer_name: Optional[str] = Field(default=None, max_length=80)
    customer_phone: Optional[str] = Field(default=None, max_length=40)

    @field_validator("note")
    @classmethod
    def _strip_note(cls, v: str) -> str:
        return (v or "").strip()


class OrderItemOut(BaseModel):
    menu_id: Optional[int]
    name: str
    price: float
    qty: int
    total: float
    customizations: str = ""
    preparation_note: Optional[str] = None


class OrderOut(ORMModel):
    id: int
    code: str
    kind: str
    status: str
    payment_status: str
    table_label: str
    #: the ORM exposes the lines through ``item_payload`` (a list of plain dicts)
    items: list[dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("item_payload", "items"),
    )
    subtotal: float
    tax: float
    fees: float
    total: float
    note: str = Field(default="", validation_alias=AliasChoices("special_instructions", "note"))
    source: str = "web"
    error: Optional[str] = None
    mission_id: Optional[int] = None
    assigned_robot_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    delivered_at: Optional[datetime] = None


class OrderTimelineStep(BaseModel):
    key: str
    label: str
    state: str


class OrderDetail(OrderOut):
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    tracking_token: Optional[str] = None
    customer_status: str = ""
    customer_status_detail: Optional[str] = None
    timeline: list[OrderTimelineStep] = Field(default_factory=list)
    history: list["OrderHistoryOut"] = Field(default_factory=list)
    mission: Optional["MissionSummary"] = None


class OrderHistoryOut(ORMModel):
    id: int
    previous_status: Optional[str]
    new_status: str
    actor: str
    reason: Optional[str]
    created_at: datetime


class MissionSummary(BaseModel):
    id: int
    mission_uid: str
    status: str
    destination_label: str
    robot_id: Optional[str] = None
    started_at: Optional[datetime] = None
    arrived_at: Optional[datetime] = None


class ReasonBody(BaseModel):
    reason: str = Field(default="", max_length=400)


class AssignRobotBody(BaseModel):
    robot_id: int = Field(gt=0)


class DispatchBody(BaseModel):
    #: optional client supplied idempotency token; a new one is generated if absent
    command_id: Optional[str] = Field(default=None, max_length=64)


class CollectionBody(BaseModel):
    collected_by: str = Field(default="customer", max_length=64)
    note: str = Field(default="", max_length=280)


OrderDetail.model_rebuild()
