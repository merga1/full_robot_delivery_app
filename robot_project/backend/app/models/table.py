"""Restaurant tables and their approved navigation destination."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, IdMixin, TimestampMixin


class RestaurantTable(Base, IdMixin, TimestampMixin):
    __tablename__ = "restaurant_tables"

    #: human readable number, e.g. "5" or "T5"
    table_number: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    #: identifier of the approved navigation destination (see the gateway registry).
    #: Coordinates are NEVER derived from the table number — this must point at a
    #: destination that was measured and verified on the robot's real map.
    destination_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    map_identifier: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    seats: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    notes: Mapped[Optional[str]] = mapped_column(String(280), nullable=True)
    #: public code printed in the table's QR code
    public_code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)

    orders: Mapped[List["Order"]] = relationship(  # noqa: F821
        back_populates="table", cascade="all, delete-orphan"
    )
