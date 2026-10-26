"""Shared FastAPI dependencies: authentication, roles, idempotency, context."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import permissions as perm
from ..core.logging import request_id_var
from ..db.session import get_session
from ..services import auth_service
from ..services.auth_service import AuthError

_bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    """The authenticated caller."""

    username: str
    role: str
    token_type: str = "access"

    @property
    def is_staff(self) -> bool:
        return perm.is_staff(self.role)

    @property
    def is_admin(self) -> bool:
        return perm.is_admin(self.role)

    @property
    def can_dispatch(self) -> bool:
        return perm.can_dispatch(self.role)

    @property
    def can_confirm_loading(self) -> bool:
        return perm.can_confirm_loading(self.role)


async def get_principal(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Principal:
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authorization header missing")
    try:
        payload = auth_service.principal_from_token(creds.credentials)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=str(exc)) from exc
    request_id_var.set(request.headers.get("x-request-id") or uuid.uuid4().hex[:16])
    return Principal(username=payload["username"], role=payload["role"],
                     token_type=payload["type"])


async def get_optional_principal(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[Principal]:
    if creds is None:
        return None
    try:
        payload = auth_service.principal_from_token(creds.credentials)
    except AuthError:
        return None
    return Principal(username=payload["username"], role=payload["role"],
                     token_type=payload["type"])


def require_roles(*roles: str):
    """Dependency factory: the caller must hold one of ``roles``."""
    allowed = set(roles)

    async def _dependency(
        principal: Principal = Depends(get_principal),
    ) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of: {', '.join(sorted(allowed))}",
            )
        return principal

    return _dependency


require_admin = require_roles(perm.Role.ADMIN.value)
require_kitchen = require_roles(*perm.KITCHEN_ROLES)
require_loader = require_roles(*perm.LOADER_ROLES)
require_dispatch = require_roles(*perm.DISPATCH_ROLES)
require_staff = require_roles(*perm.STAFF_ROLES)
require_customer = require_roles(
    perm.Role.CUSTOMER.value, *perm.STAFF_ROLES, perm.Role.ADMIN.value
)


async def require_robot_screen(
    x_robot_token: Optional[str] = Header(default=None, alias="X-Robot-Token"),
) -> str:
    """Authenticate the robot's touchscreen.

    The screen uses a dedicated shared token (``ROBOT_SCREEN_TOKEN``).  It is
    *not* a user token, so it can never be used on the staff API.
    """
    from ..core.config import settings
    from ..core.security import constant_time_equals

    expected = settings.ROBOT_SCREEN_TOKEN
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The robot screen is not configured on this backend",
        )
    if not x_robot_token or not constant_time_equals(x_robot_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid robot screen token")
    return x_robot_token


async def idempotency_key(
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> Optional[str]:
    """Read the ``Idempotency-Key`` header (max 120 characters)."""
    if not idempotency_key:
        return None
    return idempotency_key.strip()[:120] or None


@dataclass
class RequestContext:
    request_id: str
    ip_address: Optional[str]

    def audit_kwargs(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "ip_address": self.ip_address}


async def request_context(request: Request) -> RequestContext:
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    request_id_var.set(rid)
    client = request.client
    return RequestContext(request_id=rid, ip_address=client.host if client else None)


SessionDep = Depends(get_session)
