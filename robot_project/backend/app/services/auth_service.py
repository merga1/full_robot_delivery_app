"""Authentication service: login, refresh, permission listing."""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..core import permissions as perm
from ..core.security import create_token, decode_token, verify_password
from ..models.enums import Role
from ..models.user import User
from ..repositories import users as user_repo
from . import audit_service

log = logging.getLogger("robot.auth")


class AuthError(Exception):
    """Invalid credentials or token."""


def permissions_for(role: str) -> list[str]:
    out: list[str] = []
    if perm.is_staff(role):
        out.append("orders.read")
        out.append("kitchen.use")
    if perm.can_confirm_loading(role):
        out.append("orders.load")
    if perm.can_dispatch(role):
        out.append("robots.dispatch")
        out.append("robots.return_home")
        out.append("menu.write")
        out.append("tables.write")
        out.append("orders.cancel.any")
    if role == Role.CUSTOMER.value:
        out.append("orders.create")
        out.append("orders.track")
    if role == Role.ROBOT_SCREEN.value:
        out.append("mission.read")
        out.append("mission.collect")
    if role == Role.GATEWAY.value:
        out.append("gateway.connect")
    return sorted(set(out))


async def authenticate(session: AsyncSession, username: str, password: str) -> User:
    user = await user_repo.get_user_by_username(session, username)
    if user is None or not user.is_active:
        raise AuthError("Incorrect username or password")
    if not verify_password(password, user.password_hash):
        raise AuthError("Incorrect username or password")
    await user_repo.touch_login(session, user)
    await audit_service.record(
        session, actor=user.username, actor_id=user.id,
        action="auth.login", resource_type="user", resource_id=user.id,
    )
    return user


def issue_tokens(user: User) -> dict[str, str]:
    access = create_token(user.username, user.role, "access")
    refresh = create_token(user.username, user.role, "refresh")
    return {"access_token": access, "refresh_token": refresh}


async def refresh(session: AsyncSession, refresh_token: str) -> User:
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except ValueError as exc:
        raise AuthError("Invalid or expired refresh token") from exc
    user = await user_repo.get_user_by_username(session, payload.get("sub", ""))
    if user is None or not user.is_active:
        raise AuthError("Invalid or expired refresh token")
    return user


def principal_from_token(token: str) -> dict:
    """Decode an access token into a principal dict."""
    try:
        payload = decode_token(token, expected_type="access")
    except ValueError as exc:
        raise AuthError(str(exc)) from exc
    return {
        "username": payload.get("sub", ""),
        "role": payload.get("role", ""),
        "type": payload.get("type", "access"),
        "jti": payload.get("jti", ""),
    }
