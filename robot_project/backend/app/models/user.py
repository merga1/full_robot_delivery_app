"""Users, customers and their sessions."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, IdMixin, TimestampMixin
from .enums import Role


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=Role.ADMIN.value, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    customer: Mapped[Optional["Customer"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class Customer(Base, IdMixin, TimestampMixin):
    """A person at a table.  Privacy: only the minimum is stored."""

    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("public_token", name="uq_customers_public_token"),)

    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    display_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: random token used for anonymous order tracking — never the row id
    public_token: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: privacy: contact details are only kept while an order is open
    anonymized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped[Optional[User]] = relationship(back_populates="customer")
    orders: Mapped[List["Order"]] = relationship(  # noqa: F821
        back_populates="customer", cascade="all, delete-orphan"
    )
