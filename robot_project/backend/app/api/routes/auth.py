"""Authentication endpoints.

Token families are kept apart on purpose:

* ``access``  — short lived, used for every REST call.
* ``refresh`` — long lived, only usable on ``/auth/refresh``.

A robot gateway credential is **not** an access token and can never be used
here.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...db.session import get_session
from ...schemas.auth import LoginRequest, MeResponse, RefreshRequest, TokenResponse, UserOut
from ...services import auth_service
from ...services.auth_service import AuthError
from ..dependencies import Principal, get_principal

log = logging.getLogger("robot.api.auth")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse, summary="Staff / customer login")
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    try:
        user = await auth_service.authenticate(session, payload.username, payload.password)
    except AuthError as exc:
        log.info("failed login for %r", payload.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    tokens = auth_service.issue_tokens(user)
    await session.commit()
    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        role=user.role,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenResponse, summary="Exchange a refresh token")
async def refresh(
    payload: RefreshRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    try:
        user = await auth_service.refresh(session, payload.refresh_token)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    tokens = auth_service.issue_tokens(user)
    await session.commit()
    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        role=user.role,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout", summary="Log out (client discards the tokens)")
async def logout() -> dict:
    """Tokens are stateless, so the client simply drops them.

    The endpoint exists so every client has one uniform call to make and so
    the action can be audited later without changing the client.
    """
    return {"ok": True, "message": "Logged out"}


@router.get("/me", response_model=MeResponse, summary="The authenticated principal")
async def me(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> MeResponse:
    from ...repositories import users as user_repo

    user = await user_repo.get_user_by_username(session, principal.username)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return MeResponse(
        user=UserOut.model_validate(user),
        permissions=auth_service.permissions_for(user.role),
    )
