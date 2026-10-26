"""Orders, order lines and the append-only order status history."""

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
from .enums import OrderKind, OrderStatus, PaymentStatus


class Order(Base, IdMixin, TimestampMixin):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("code", name="uq_orders_code"),
        UniqueConstraint("tracking_token", name="uq_orders_tracking_token"),
        UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
    )

    #: short human readable order number shown to staff and the customer
    code: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    #: secret token used for anonymous customer tracking (never the row id)
    tracking_token: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    customer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    table_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("restaurant_tables.id", ondelete="SET NULL"), nullable=True, index=True
    )
    table_label: Mapped[str] = mapped_column(String(80), nullable=False, default="")

    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=OrderKind.ORDER.value, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=OrderStatus.PENDING.value, index=True
    )
    payment_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PaymentStatus.UNPAID.value, index=True
    )

    subtotal: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tax: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fees: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    special_instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="web", index=True)

    mission_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("robot_missions.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    assigned_robot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("robots.id", ondelete="SET NULL"), nullable=True, index=True
    )
    loaded_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    accepted_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    error: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)

    customer: Mapped[Optional["Customer"]] = relationship(back_populates="orders")  # noqa: F821
    table: Mapped[Optional["RestaurantTable"]] = relationship(back_populates="orders")  # noqa: F821
    items: Mapped[List["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.id"
    )
    history: Mapped[List["OrderStatusHistory"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderStatusHistory.id"
    )
    mission: Mapped[Optional["RobotMission"]] = relationship(
        foreign_keys=[mission_id]
    )  # noqa: F821

    @property
    def item_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "menu_id": i.menu_item_id,
                "name": i.name,
                "price": i.unit_price,
                "qty": i.quantity,
                "total": i.line_total,
                "customizations": i.customizations,
                "preparation_note": i.preparation_note,
            }
            for i in self.items
        ]


class OrderItem(Base, IdMixin):
    __tablename__ = "order_items"

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    menu_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("menu_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: snapshot — the menu item may be renamed or repriced later
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    line_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    customizations: Mapped[str] = mapped_column(Text, nullable=False, default="")
    preparation_note: Mapped[Optional[str]] = mapped_column(String(280), nullable=True)

    order: Mapped["Order"] = relationship(back_populates="items")
    menu_item: Mapped[Optional["MenuItem"]] = relationship(back_populates="order_items")  # noqa: F821


class OrderStatusHistory(Base, IdMixin):
    """Append-only audit of every order transition."""

    __tablename__ = "order_status_history"

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    previous_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    actor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    order: Mapped["Order"] = relationship(back_populates="history")
