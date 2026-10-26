"""Push-notification provider abstraction.

Firebase Cloud Messaging is used when ``FCM_PROJECT_ID`` and
``FCM_CREDENTIALS_PATH`` are configured.  Otherwise a :class:`NullPushProvider`
is used and every send is reported as *not configured* — the system never
pretends that a push was delivered.

A WebSocket event is **not** an operating-system push notification.  The Flutter
app registers its FCM token through ``POST /api/v1/notifications/register-device``
so the provider has a real target.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Protocol

from ..core.config import settings

log = logging.getLogger("robot.push")


class PushProvider(Protocol):
    name: str
    configured: bool

    async def send(self, token: str, title: str, body: str, data: dict[str, Any]) -> bool:
        ...


class NullPushProvider:
    """Fallback used when Firebase is not configured."""

    name = "null"
    configured = False

    async def send(self, token: str, title: str, body: str, data: dict[str, Any]) -> bool:
        log.debug("push not configured — dropping notification %r", title)
        return False


class FcmPushProvider:
    """Firebase Cloud Messaging provider.

    The ``firebase_admin`` SDK is imported lazily so the backend still starts
    when the optional dependency is missing.
    """

    name = "fcm"
    configured = True

    def __init__(self) -> None:
        import firebase_admin
        from firebase_admin import credentials

        if not firebase_admin._apps:                    # noqa: SLF001
            cred = credentials.Certificate(settings.FCM_CREDENTIALS_PATH)
            firebase_admin.initialize_app(cred, {"projectId": settings.FCM_PROJECT_ID})
        self._messaging = firebase_admin.messaging

    async def send(self, token: str, title: str, body: str, data: dict[str, Any]) -> bool:
        message = self._messaging.Message(
            token=token,
            notification=self._messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
        )
        try:
            await asyncio.to_thread(self._messaging.send, message)
            return True
        except Exception as exc:
            log.warning("FCM push failed: %s", exc)
            return False


_provider: Optional[PushProvider] = None


def get_provider() -> PushProvider:
    global _provider
    if _provider is not None:
        return _provider
    if settings.FCM_PROJECT_ID and settings.FCM_CREDENTIALS_PATH:
        try:
            _provider = FcmPushProvider()
            log.info("push provider: Firebase Cloud Messaging")
            return _provider
        except Exception as exc:
            log.warning("FCM is configured but could not be initialised (%s) — "
                        "falling back to the null provider", exc)
    _provider = NullPushProvider()
    log.info("push provider: null (FCM is not configured)")
    return _provider


def is_configured() -> bool:
    return bool(settings.FCM_PROJECT_ID and settings.FCM_CREDENTIALS_PATH)


async def send_many(notifications: list) -> int:
    """Send the push-flagged notifications to registered devices."""
    provider = get_provider()
    if not provider.configured:
        return 0
    from ..repositories import notifications as notif_repo  # noqa: F401  (typing only)

    sent = 0
    for notification in notifications:
        tokens = (notification.payload or {}).get("device_tokens") or []
        for token in tokens:
            ok = await provider.send(
                token,
                notification.title,
                notification.body,
                notification.payload or {},
            )
            if ok:
                sent += 1
                notification.delivery_status = "SENT"
    return sent
