"""Roles and permission checks.

Roles
-----
``customer``     may place orders and track their own order.
``kitchen``      may accept/reject orders, start preparation, mark ready.
``courier``      (alias of kitchen-with-load-rights) may confirm that food is loaded.
``admin``        may do everything: assign robots, dispatch, return home, edit menu/tables.
``gateway``      a robot gateway. Not a person: it authenticates with its own token
                 and may only use the gateway WebSocket.
``robot_screen`` the robot touchscreen. May only read its own mission and report
                 collection confirmation.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable

from ..models.enums import Role

__all__ = [
    "Role",
    "STAFF_ROLES",
    "ADMIN_ROLES",
    "KITCHEN_ROLES",
    "LOADER_ROLES",
    "DISPATCH_ROLES",
    "is_staff",
    "is_admin",
    "can_view_kitchen",
    "can_confirm_loading",
    "can_dispatch",
    "has_any_role",
]


class RoleEnum(str, Enum):
    """Kept for backwards compatibility with older imports."""

    CUSTOMER = "customer"
    KITCHEN = "kitchen"
    COURIER = "courier"
    ADMIN = "admin"
    GATEWAY = "gateway"
    ROBOT_SCREEN = "robot_screen"


STAFF_ROLES: frozenset[str] = frozenset({Role.KITCHEN.value, Role.COURIER.value, Role.ADMIN.value})
ADMIN_ROLES: frozenset[str] = frozenset({Role.ADMIN.value})
KITCHEN_ROLES: frozenset[str] = frozenset({Role.KITCHEN.value, Role.COURIER.value, Role.ADMIN.value})
LOADER_ROLES: frozenset[str] = frozenset({Role.COURIER.value, Role.ADMIN.value})
DISPATCH_ROLES: frozenset[str] = frozenset({Role.ADMIN.value})


def is_staff(role: str) -> bool:
    return role in STAFF_ROLES


def is_admin(role: str) -> bool:
    return role in ADMIN_ROLES


def can_view_kitchen(role: str) -> bool:
    return role in KITCHEN_ROLES


def can_confirm_loading(role: str) -> bool:
    return role in LOADER_ROLES


def can_dispatch(role: str) -> bool:
    return role in DISPATCH_ROLES


def has_any_role(role: str, allowed: Iterable[str]) -> bool:
    return role in set(allowed)
