"""
auth.py — JWT authentication for the robot API.

  POST /auth/login   { username, password } → { access_token, role }

Environment
  ROBOT_ADMIN_USER / ROBOT_ADMIN_PASS   (default admin / admin  — CHANGE THEM)
  ROBOT_USER_USER  / ROBOT_USER_PASS    (default user  / user   — CHANGE THEM)
  ROBOT_KITCHEN_USER / ROBOT_KITCHEN_PASS (default kitchen / kitchen — CHANGE THEM)
  ROBOT_JWT_SECRET                      (default: random per start; set it to keep
                                         tokens valid across server restarts)
  ROBOT_TOKEN_TTL_HOURS                 (default 12)

Uses the `bcrypt` package directly.  The old code used passlib, which crashes at
import with bcrypt >= 5 ("password cannot be longer than 72 bytes").
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

log = logging.getLogger("robot.auth")

_env_secret = os.getenv("ROBOT_JWT_SECRET", "").strip()
if not _env_secret:
    log.warning("ROBOT_JWT_SECRET not set — using a random secret; users must log in "
                "again after every server restart.")
SECRET_KEY = _env_secret or secrets.token_urlsafe(48)
ALGORITHM = "HS256"
try:
    TOKEN_TTL = timedelta(hours=float(os.getenv("ROBOT_TOKEN_TTL_HOURS", "12")))
except ValueError:
    TOKEN_TTL = timedelta(hours=12)

COOKIE_NAME = "robot_token"
STAFF_ROLES = ("admin", "user", "kitchen")
SERVICE_ROLES = ("admin", "kitchen")
OPERATOR_ROLES = ("admin", "user")


def _hash(password: str) -> bytes:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=10))


def _check(password: str, hashed: bytes) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed)
    except ValueError:
        return False


_admin = (os.getenv("ROBOT_ADMIN_USER", "admin"), os.getenv("ROBOT_ADMIN_PASS", "admin"))
_user = (os.getenv("ROBOT_USER_USER", "user"), os.getenv("ROBOT_USER_PASS", "user"))
_kitchen = (os.getenv("ROBOT_KITCHEN_USER", "kitchen"), os.getenv("ROBOT_KITCHEN_PASS", "kitchen"))
if _admin[1] == "admin" or _user[1] == "user" or _kitchen[1] == "kitchen":
    log.warning("Default login passwords are in use — set ROBOT_ADMIN_PASS / "
                "ROBOT_USER_PASS / ROBOT_KITCHEN_PASS in real1_link.env.")

_USERS: dict[str, dict] = {
    _admin[0]: {"hashed": _hash(_admin[1]), "role": "admin"},
    _user[0]: {"hashed": _hash(_user[1]), "role": "user"},
    _kitchen[0]: {"hashed": _hash(_kitchen[1]), "role": "kitchen"},
}

router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


def _create_token(username: str, role: str, extra: dict | None = None) -> str:
    expire = datetime.now(timezone.utc) + TOKEN_TTL
    payload = {"sub": username, "role": role, "exp": expire}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_token(username: str, role: str, extra: dict | None = None) -> str:
    return _create_token(username, role, extra=extra)


def _verify_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid or expired token")


def create_customer_token(table_code: str) -> str:
    return _create_token(f"table:{table_code}", "customer", extra={"table_code": table_code})


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest):
    # Plain `def` → FastAPI runs it in a worker thread, so bcrypt never blocks
    # the event loop (and therefore never delays the joystick).
    user = _USERS.get(req.username)
    if not user or not _check(req.password, user["hashed"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Incorrect username or password")
    return TokenResponse(access_token=_create_token(req.username, user["role"]),
                         role=user["role"])


def _get_token_from_request(request: Request,
                            creds: HTTPAuthorizationCredentials | None) -> str | None:
    if creds and creds.credentials:
        return creds.credentials
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return token
    # Also allow ?token= for simple browser flows
    token = request.query_params.get("token")
    if token:
        return token
    return None


def require_auth(request: Request,
                  creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    token = _get_token_from_request(request, creds)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authorization required")
    return _verify_token(token)


def require_admin(payload: dict = Depends(require_auth)) -> dict:
    if payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Admin role required")
    return payload


def require_staff(payload: dict = Depends(require_auth)) -> dict:
    role = payload.get("role")
    if role not in STAFF_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Staff role required")
    return payload


def require_service(payload: dict = Depends(require_auth)) -> dict:
    role = payload.get("role")
    if role not in SERVICE_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Service role required")
    return payload


def require_operator(payload: dict = Depends(require_auth)) -> dict:
    """May drive the robot (admin or operator).

    The kitchen account manages the orders but must not move the robot.
    """
    role = payload.get("role")
    if role not in OPERATOR_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Operator role required")
    return payload


def require_customer(payload: dict = Depends(require_auth)) -> dict:
    if payload.get("role") != "customer":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Customer token required")
    return payload
