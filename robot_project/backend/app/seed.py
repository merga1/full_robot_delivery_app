"""Idempotent development seed for the backend.

Run after ``alembic upgrade head``.  It creates staff accounts, a safe sample
menu, and a robot registration.  It intentionally does not invent table
coordinates; tables must be created with destinations measured on the real map.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from .core.config import settings
from .core.security import hash_password
from .db.session import SessionLocal, dispose_engine
from .models.enums import Role
from .models.menu import MenuItem
from .models.robot import Robot
from .models.table import RestaurantTable
from .models.user import User


USERS = (
    (settings.SEED_ADMIN_USERNAME, settings.SEED_ADMIN_PASSWORD, Role.ADMIN.value, "Restaurant administrator"),
    (settings.SEED_KITCHEN_USERNAME, settings.SEED_KITCHEN_PASSWORD, Role.KITCHEN.value, "Kitchen staff"),
    (settings.SEED_COURIER_USERNAME, settings.SEED_COURIER_PASSWORD, Role.COURIER.value, "Delivery operator"),
)

MENU = (
    ("Injera Firfir", "Spiced shredded injera", "Food", 8.0, "firfir", "/assets/firfir.png"),
    ("Shiro Wot", "Ethiopian chickpea stew", "Food", 9.0, "shiro", "/assets/shiro.png"),
    ("Doro Wot", "Chicken stew with egg", "Food", 12.0, "doro", "/assets/doro.png"),
    ("Pasta", "Pasta with tomato sauce", "Food", 10.0, "pasta", "/assets/pasta.png"),
    ("Tea", "Hot tea", "Drinks", 3.0, "tea", "/assets/tea.png"),
    ("Coffee", "Fresh coffee", "Drinks", 4.0, "coffee", "/assets/coffee.png"),
    ("Milk", "Chilled fresh milk", "Drinks", 3.5, "milk", "/assets/milk.png"),
    ("Water", "Bottled water", "Drinks", 2.0, "water", "/assets/water.png"),
)

TABLES = (
    ("T01", "Table 01", 4),
    ("T02", "Table 02", 4),
    ("T03", "Table 03", 4),
    ("T04", "Table 04", 4),
    ("T05", "Table 05", 4),
)


async def seed() -> None:
    async with SessionLocal() as session:
        for username, password, role, full_name in USERS:
            existing = (await session.execute(select(User).where(User.username == username))).scalars().first()
            if existing is None:
                session.add(User(username=username, password_hash=hash_password(password), role=role, full_name=full_name))
            elif existing.role != role:
                existing.role = role

        for index, (name, description, category, price, external_key, image_url) in enumerate(MENU):
            existing = (await session.execute(select(MenuItem).where(MenuItem.name == name))).scalars().first()
            if existing is None:
                session.add(MenuItem(name=name, description=description, category=category,
                                     price=price, external_key=external_key,
                                     image_url=image_url, sort_order=index))
            elif not existing.image_url:
                existing.image_url = image_url

        for table_number, display_name, seats in TABLES:
            existing = (await session.execute(
                select(RestaurantTable).where(RestaurantTable.table_number == table_number)
            )).scalars().first()
            if existing is None:
                # The customer ordering flow can use these immediately.  A
                # real destination_id must be added before robot dispatch.
                session.add(RestaurantTable(
                    table_number=table_number,
                    display_name=display_name,
                    public_code=table_number,
                    seats=seats,
                    is_active=True,
                ))

        robot = (await session.execute(select(Robot).where(Robot.robot_id == "robot_01"))).scalars().first()
        if robot is None:
            session.add(Robot(robot_id="robot_01", name="Delivery Robot 01", home_destination_id="home"))
        await session.commit()
    await dispose_engine()
    print("Seed complete. Configure real table destinations before dispatching.")


if __name__ == "__main__":
    asyncio.run(seed())

