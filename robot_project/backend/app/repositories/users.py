"""User / customer repositories."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import Customer, User
from ..core.security import new_opaque_token


async def get_user_by_username(session: AsyncSession, username: str) -> Optional[User]:
    stmt = select(User).where(User.username == (username or "").strip())
    return (await session.execute(stmt)).scalars().first()


async def get_user_by_id(session: AsyncSession, user_id: int) -> Optional[User]:
    return await session.get(User, user_id)


async def get_user_by_email(session: AsyncSession, email: str) -> Optional[User]:
    stmt = select(User).where(User.email == (email or "").strip().lower())
    return (await session.execute(stmt)).scalars().first()


async def list_users(session: AsyncSession, limit: int = 100) -> list[User]:
    stmt = select(User).order_by(User.id).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def touch_login(session: AsyncSession, user: User) -> None:
    user.last_login_at = datetime.now(timezone.utc)
    await session.flush()


async def get_or_create_customer(
    session: AsyncSession,
    user_id: Optional[int] = None,
    display_name: Optional[str] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
) -> Customer:
    """Return the customer linked to a user, or create an anonymous one."""
    if user_id is not None:
        stmt = select(Customer).where(Customer.user_id == user_id)
        existing = (await session.execute(stmt)).scalars().first()
        if existing is not None:
            if display_name:
                existing.display_name = display_name
            if phone:
                existing.phone = phone
            return existing
    customer = Customer(
        user_id=user_id,
        display_name=display_name,
        phone=phone,
        email=email,
        public_token=new_opaque_token(24),
    )
    session.add(customer)
    await session.flush()
    return customer


async def get_customer(session: AsyncSession, customer_id: int) -> Optional[Customer]:
    return await session.get(Customer, customer_id)
