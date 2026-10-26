"""Password hashing, JWT issue/verify and opaque tokens.

Password hashing uses ``bcrypt`` directly (the ``passlib`` + ``bcrypt>=5``
combination used by the older robot API crashes at import time).

JWTs are signed with HS256 through ``python-jose``.  Two token families exist and
are **never interchangeable**:

* ``access``  — short lived, carries the role, used by every REST call.
* ``refresh`` — long lived, only usable on ``/auth/refresh``.
* ``robot``   — long lived bearer credential for a single robot gateway, tied to
  one robot id; it can never be used on the staff/customer REST API.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional

import bcrypt
from jose import JWTError, jwt

from ..core.config import settings

TokenType = Literal["access", "refresh", "robot"]

_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    raw = (password or "").encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=10)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        raw = (password or "").encode("utf-8")[:_BCRYPT_MAX_BYTES]
        return bcrypt.checkpw(raw, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _secret() -> str:
    return settings.effective_jwt_secret


def create_token(
    subject: str,
    role: str,
    token_type: TokenType = "access",
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    now = datetime.now(timezone.utc)
    if expires_delta is None:
        if token_type == "access":
            expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        elif token_type == "refresh":
            expires_delta = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        else:
            expires_delta = timedelta(days=365)
    payload: Dict[str, Any] = {
        "sub": subject,
        "role": role,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _secret(), algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str, expected_type: Optional[TokenType] = None) -> Dict[str, Any]:
    """Decode and validate a token.

    Raises ``ValueError`` for anything invalid, expired or of the wrong type.
    """
    try:
        payload = jwt.decode(token, _secret(), algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise ValueError(str(exc)) from exc
    if expected_type is not None and payload.get("type") != expected_type:
        raise ValueError(f"Expected a {expected_type} token")
    return payload


def new_opaque_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def hash_opaque_token(token: str) -> str:
    """Deterministic hash of an opaque token — safe to store in the database."""
    return hmac.new(_secret().encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a or "", b or "")
