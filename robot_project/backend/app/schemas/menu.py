"""Menu and table schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .common import ORMModel


class MenuItemOut(ORMModel):
    id: int
    name: str
    description: str = ""
    category: str = "General"
    price: float
    available: bool
    image_url: Optional[str] = None
    preparation_note: Optional[str] = None
    sort_order: int = 0
    external_key: Optional[str] = None


class MenuItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    price: float = Field(ge=0, le=100000)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="General", max_length=60)
    available: bool = True
    image_url: Optional[str] = Field(default=None, max_length=300)
    preparation_note: Optional[str] = Field(default=None, max_length=280)
    sort_order: int = 0
    external_key: Optional[str] = Field(default=None, max_length=80)


class MenuItemUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    price: Optional[float] = Field(default=None, ge=0, le=100000)
    description: Optional[str] = Field(default=None, max_length=2000)
    category: Optional[str] = Field(default=None, max_length=60)
    available: Optional[bool] = None
    image_url: Optional[str] = Field(default=None, max_length=300)
    preparation_note: Optional[str] = Field(default=None, max_length=280)
    sort_order: Optional[int] = None
    external_key: Optional[str] = Field(default=None, max_length=80)


class TableOut(ORMModel):
    id: int
    table_number: str
    display_name: str
    destination_id: Optional[str] = None
    map_identifier: Optional[str] = None
    seats: int = 4
    is_active: bool
    notes: Optional[str] = None
    public_code: str
    created_at: datetime


class TableCreate(BaseModel):
    table_number: str = Field(min_length=1, max_length=16)
    display_name: str = Field(min_length=1, max_length=80)
    destination_id: Optional[str] = Field(default=None, max_length=80)
    map_identifier: Optional[str] = Field(default=None, max_length=120)
    seats: int = Field(default=4, ge=0, le=100)
    is_active: bool = True
    notes: Optional[str] = Field(default=None, max_length=280)


class TableUpdate(BaseModel):
    table_number: Optional[str] = Field(default=None, min_length=1, max_length=16)
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    destination_id: Optional[str] = Field(default=None, max_length=80)
    map_identifier: Optional[str] = Field(default=None, max_length=120)
    seats: Optional[int] = Field(default=None, ge=0, le=100)
    is_active: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=280)
