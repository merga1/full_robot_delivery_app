"""Application configuration (pydantic-settings, env driven).

All tunables live in ``backend/.env`` (copy ``.env.example``).  Nothing here reads
secrets from the repository; a missing ``JWT_SECRET_KEY`` is a hard error in
production and a loud warning in development.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = secrets.token_urlsafe(48)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── General ───────────────────────────────────────────────────────────────
    APP_ENV: str = Field(default="development")
    APP_NAME: str = "Smart Restaurant Robot API"
    APP_VERSION: str = "1.0.0"
    LOG_LEVEL: str = Field(default="INFO")
    API_PREFIX: str = "/api/v1"

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://app_user:app_user_dev_pw@127.0.0.1:5432/restaurant_db"
    )
    DB_POOL_SIZE: int = Field(default=10)
    DB_MAX_OVERFLOW: int = Field(default=20)
    DB_ECHO: bool = Field(default=False)

    # ── Security ──────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str = Field(default="")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7)
    CORS_ORIGINS: str = Field(default="https://food-delivery-robot.netlify.app")
    PUBLIC_CUSTOMER_URL: str = Field(default="http://localhost:8000/customer/")

    # ── Robot gateway ─────────────────────────────────────────────────────────
    ROBOT_GATEWAY_ENABLED: bool = Field(default=True)
    ROBOT_GATEWAY_HEARTBEAT_TIMEOUT_SECONDS: int = Field(default=15)
    ROBOT_COMMAND_TTL_SECONDS: int = Field(default=30)
    ROBOT_COMMAND_ACK_TIMEOUT_SECONDS: int = Field(default=10)
    ROBOT_DEFAULT_HOME_DESTINATION: str = Field(default="home")

    # ── Robot behaviour ───────────────────────────────────────────────────────
    AUTO_RETURN_HOME: bool = Field(default=True)
    LOW_BATTERY_THRESHOLD_PERCENT: int = Field(default=25)
    MIN_BATTERY_PERCENT_TO_DISPATCH: int = Field(default=15)
    COLLECTION_CONFIRM_TIMEOUT_SECONDS: int = Field(default=900)
    ARRIVAL_POSE_TOLERANCE_M: float = Field(default=0.60)

    # ── Robot screen (touchscreen) ────────────────────────────────────────────
    ROBOT_SCREEN_TOKEN: str = Field(default="")

    # ── Pricing ──────────────────────────────────────────────────────────────
    TAX_RATE: float = Field(default=0.0, ge=0.0, le=1.0)
    DELIVERY_FEE: float = Field(default=0.0, ge=0.0, le=100000)

    # ── Notifications ─────────────────────────────────────────────────────────
    FCM_PROJECT_ID: str = Field(default="")
    FCM_CREDENTIALS_PATH: str = Field(default="")
    NOTIFICATION_SOUND_ENABLED: bool = Field(default=True)
    OUTBOX_POLL_INTERVAL_SECONDS: float = Field(default=1.0)

    # ── Seed data ─────────────────────────────────────────────────────────────
    SEED_ADMIN_USERNAME: str = Field(default="admin")
    SEED_ADMIN_PASSWORD: str = Field(default="admin123")
    SEED_KITCHEN_USERNAME: str = Field(default="kitchen")
    SEED_KITCHEN_PASSWORD: str = Field(default="kitchen123")
    SEED_COURIER_USERNAME: str = Field(default="courier")
    SEED_COURIER_PASSWORD: str = Field(default="courier123")

    # ── Derived ───────────────────────────────────────────────────────────────
    @field_validator("LOG_LEVEL")
    @classmethod
    def _upper_log(cls, v: str) -> str:
        return v.upper()

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() in ("production", "prod")

    @property
    def cors_origin_list(self) -> List[str]:
        raw = (self.CORS_ORIGINS or "").strip()
        if not raw:
            return []
        out = []
        for item in raw.split(","):
            item = item.strip().rstrip("/")
            if item:
                out.append(item)
        return out

    @property
    def effective_jwt_secret(self) -> str:
        if self.JWT_SECRET_KEY.strip():
            return self.JWT_SECRET_KEY.strip()
        if self.is_production:
            raise RuntimeError(
                "JWT_SECRET_KEY must be set in production — refusing to start with a "
                "generated secret."
            )
        # A development secret is stable for this process.  Generating a new
        # value on every property access would make a freshly issued token
        # unverifiable on the very next request.
        return _DEV_JWT_SECRET

    def validate_startup(self) -> List[str]:
        """Return a list of human readable warnings (empty when everything is sane)."""
        warnings: List[str] = []
        if not self.JWT_SECRET_KEY.strip():
            warnings.append(
                "JWT_SECRET_KEY is not set — tokens are invalidated on every restart. "
                "Set it in .env for anything other than a local test run."
            )
        if "*" in self.CORS_ORIGINS:
            warnings.append(
                "CORS_ORIGINS contains '*'. Credentialed requests are then rejected by "
                "browsers. List the real front-end origins instead."
            )
        if self.is_production:
            if self.SEED_ADMIN_PASSWORD == "admin123":
                warnings.append("SEED_ADMIN_PASSWORD still uses the default value.")
            if not self.ROBOT_SCREEN_TOKEN:
                warnings.append("ROBOT_SCREEN_TOKEN is empty — the robot screen is unauthenticated.")
        return warnings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
