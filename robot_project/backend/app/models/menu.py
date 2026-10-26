"""Menu items."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, IdMixin, TimestampMixin


class MenuItem(Base, IdMixin, TimestampMixin):
    __tablename__ = "menu_items"

    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(60), nullable=False, default="General", index=True)
    price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    #: free text shown on the kitchen screen, e.g. "served with extra sauce"
    preparation_note: Mapped[Optional[str]] = mapped_column(String(280), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Stable key used by the customer website (e.g. 'Injera Firfir').
    external_key: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True, index=True,
    )

    order_items: Mapped[List["OrderItem"]] = relationship(  # noqa: F821
        back_populates="menu_item"
    )
