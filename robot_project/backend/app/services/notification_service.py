"""Central notification service.

Every business event is routed to the interfaces that care about it, following
the matrix from the specification:

============  ========  ======  =======  =====  ========
Event         Kitchen   Admin   Flutter  Robot  Customer
============  ========  ======  =======  =====  ========
new order     sound     alert   push     voice  confirm
accepted      status    alert   push     voice  status
ready         status    sound   push     update status
dispatched    live      live    push     voice  tracking
arrived       live      alert   push     voice  notify
collected     live      alert   push     voice  confirm
return home   -         live    push     voice  optional
fault         -         urgent  push     fault  delayed
============  ========  ======  =======  =====  ========

The service writes one :class:`Notification` row per (event, channel) pair and
publishes a ``notification.created`` event.  Delivery to the platform push
provider is delegated to :mod:`app.services.push`, which has a no-op provider
when Firebase credentials are not configured — push is then reported as *not
configured* instead of silently pretending to work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..events.outbox import enqueue_event
from ..models.enums import (
    EventType,
    NotificationChannel,
    NotificationPriority,
    NotificationType,
)
from ..models.notification import Notification
from ..repositories import notifications as notif_repo

log = logging.getLogger("robot.notifications")


@dataclass(frozen=True)
class Route:
    channel: str
    priority: str = NotificationPriority.NORMAL.value
    #: play an audible alert on this interface
    sound: bool = False
    #: send an operating-system push notification
    push: bool = False
    #: speak the message on the robot
    voice: bool = False


#: event type -> routes
ROUTING: dict[str, tuple[Route, ...]] = {
    NotificationType.NEW_ORDER.value: (
        Route(NotificationChannel.KITCHEN.value, NotificationPriority.HIGH.value, sound=True),
        Route(NotificationChannel.ADMIN.value, NotificationPriority.NORMAL.value, sound=True),
        Route(NotificationChannel.FLUTTER.value, NotificationPriority.HIGH.value, push=True),
        Route(NotificationChannel.CUSTOMER.value, NotificationPriority.NORMAL.value),
    ),
    NotificationType.ORDER_ACCEPTED.value: (
        Route(NotificationChannel.KITCHEN.value),
        Route(NotificationChannel.ADMIN.value),
        Route(NotificationChannel.FLUTTER.value, push=True),
        Route(NotificationChannel.ROBOT.value, voice=True),
        Route(NotificationChannel.CUSTOMER.value),
    ),
    NotificationType.ORDER_REJECTED.value: (
        Route(NotificationChannel.KITCHEN.value),
        Route(NotificationChannel.ADMIN.value, NotificationPriority.HIGH.value, sound=True),
        Route(NotificationChannel.FLUTTER.value, push=True),
        Route(NotificationChannel.CUSTOMER.value),
    ),
    NotificationType.ORDER_PREPARING.value: (
        Route(NotificationChannel.KITCHEN.value),
        Route(NotificationChannel.ADMIN.value),
        Route(NotificationChannel.FLUTTER.value, push=True),
        Route(NotificationChannel.CUSTOMER.value),
    ),
    NotificationType.ORDER_READY.value: (
        Route(NotificationChannel.KITCHEN.value),
        Route(NotificationChannel.ADMIN.value, NotificationPriority.HIGH.value, sound=True),
        Route(NotificationChannel.FLUTTER.value, NotificationPriority.HIGH.value, push=True),
        Route(NotificationChannel.ROBOT.value, voice=True),
        Route(NotificationChannel.CUSTOMER.value),
    ),
    NotificationType.ORDER_LOADED.value: (
        Route(NotificationChannel.KITCHEN.value),
        Route(NotificationChannel.ADMIN.value),
        Route(NotificationChannel.FLUTTER.value, push=True),
        Route(NotificationChannel.ROBOT.value, voice=True),
        Route(NotificationChannel.CUSTOMER.value),
    ),
    NotificationType.ORDER_DISPATCHED.value: (
        Route(NotificationChannel.KITCHEN.value),
        Route(NotificationChannel.ADMIN.value),
        Route(NotificationChannel.FLUTTER.value, push=True),
        Route(NotificationChannel.ROBOT.value, voice=True),
        Route(NotificationChannel.CUSTOMER.value),
    ),
    NotificationType.ROBOT_ARRIVED.value: (
        Route(NotificationChannel.KITCHEN.value),
        Route(NotificationChannel.ADMIN.value, NotificationPriority.HIGH.value, sound=True),
        Route(NotificationChannel.FLUTTER.value, NotificationPriority.HIGH.value, push=True),
        Route(NotificationChannel.ROBOT.value, voice=True),
        Route(NotificationChannel.CUSTOMER.value, NotificationPriority.HIGH.value, push=True),
    ),
    NotificationType.COLLECTION_CONFIRMED.value: (
        Route(NotificationChannel.KITCHEN.value),
        Route(NotificationChannel.ADMIN.value),
        Route(NotificationChannel.FLUTTER.value, push=True),
        Route(NotificationChannel.ROBOT.value, voice=True),
        Route(NotificationChannel.CUSTOMER.value),
    ),
    NotificationType.ORDER_DELIVERED.value: (
        Route(NotificationChannel.KITCHEN.value),
        Route(NotificationChannel.ADMIN.value),
        Route(NotificationChannel.FLUTTER.value, push=True),
        Route(NotificationChannel.CUSTOMER.value),
    ),
    NotificationType.RETURN_STARTED.value: (
        Route(NotificationChannel.ADMIN.value),
        Route(NotificationChannel.FLUTTER.value, push=True),
        Route(NotificationChannel.ROBOT.value, voice=True),
        Route(NotificationChannel.CUSTOMER.value, NotificationPriority.LOW.value),
    ),
    NotificationType.HOME_REACHED.value: (
        Route(NotificationChannel.ADMIN.value),
        Route(NotificationChannel.FLUTTER.value, push=True),
        Route(NotificationChannel.ROBOT.value, voice=True),
    ),
    NotificationType.ROBOT_OFFLINE.value: (
        Route(NotificationChannel.ADMIN.value, NotificationPriority.URGENT.value, sound=True),
        Route(NotificationChannel.FLUTTER.value, NotificationPriority.HIGH.value, push=True),
    ),
    NotificationType.ROBOT_FAULT.value: (
        Route(NotificationChannel.ADMIN.value, NotificationPriority.URGENT.value, sound=True),
        Route(NotificationChannel.FLUTTER.value, NotificationPriority.URGENT.value, push=True),
        Route(NotificationChannel.ROBOT.value, NotificationPriority.URGENT.value, voice=True),
        Route(NotificationChannel.CUSTOMER.value, NotificationPriority.LOW.value),
    ),
    NotificationType.ROBOT_BLOCKED.value: (
        Route(NotificationChannel.ADMIN.value, NotificationPriority.URGENT.value, sound=True),
        Route(NotificationChannel.FLUTTER.value, NotificationPriority.HIGH.value, push=True),
        Route(NotificationChannel.ROBOT.value, voice=True),
    ),
    NotificationType.LOW_BATTERY.value: (
        Route(NotificationChannel.ADMIN.value, NotificationPriority.HIGH.value),
        Route(NotificationChannel.FLUTTER.value, push=True),
        Route(NotificationChannel.ROBOT.value, voice=True),
    ),
    NotificationType.NAVIGATION_FAILED.value: (
        Route(NotificationChannel.KITCHEN.value),
        Route(NotificationChannel.ADMIN.value, NotificationPriority.URGENT.value, sound=True),
        Route(NotificationChannel.FLUTTER.value, NotificationPriority.HIGH.value, push=True),
        Route(NotificationChannel.ROBOT.value, voice=True),
        Route(NotificationChannel.CUSTOMER.value, NotificationPriority.LOW.value),
    ),
    NotificationType.RETURN_FAILED.value: (
        Route(NotificationChannel.ADMIN.value, NotificationPriority.URGENT.value, sound=True),
        Route(NotificationChannel.FLUTTER.value, NotificationPriority.HIGH.value, push=True),
        Route(NotificationChannel.ROBOT.value, voice=True),
    ),
    NotificationType.ASSISTANCE_REQUIRED.value: (
        Route(NotificationChannel.ADMIN.value, NotificationPriority.URGENT.value, sound=True),
        Route(NotificationChannel.FLUTTER.value, NotificationPriority.HIGH.value, push=True),
        Route(NotificationChannel.ROBOT.value, voice=True),
    ),
    NotificationType.DELAY_REPORTED.value: (
        Route(NotificationChannel.KITCHEN.value),
        Route(NotificationChannel.ADMIN.value),
        Route(NotificationChannel.FLUTTER.value, push=True),
        Route(NotificationChannel.CUSTOMER.value),
    ),
    NotificationType.SYSTEM.value: (
        Route(NotificationChannel.ADMIN.value),
    ),
}

#: notification type -> realtime event type published to the WebSocket channels
EVENT_FOR_NOTIFICATION: dict[str, EventType] = {
    NotificationType.NEW_ORDER.value: EventType.ORDER_CREATED,
    NotificationType.ORDER_ACCEPTED.value: EventType.ORDER_ACCEPTED,
    NotificationType.ORDER_REJECTED.value: EventType.ORDER_REJECTED,
    NotificationType.ORDER_PREPARING.value: EventType.ORDER_PREPARING,
    NotificationType.ORDER_READY.value: EventType.ORDER_READY,
    NotificationType.ORDER_LOADED.value: EventType.ORDER_LOADED,
    NotificationType.ORDER_DISPATCHED.value: EventType.ORDER_DISPATCHED,
    NotificationType.ROBOT_ARRIVED.value: EventType.ROBOT_ARRIVED,
    NotificationType.COLLECTION_CONFIRMED.value: EventType.ORDER_COLLECTION_CONFIRMED,
    NotificationType.ORDER_DELIVERED.value: EventType.ORDER_DELIVERED,
    NotificationType.RETURN_STARTED.value: EventType.ROBOT_RETURN_STARTED,
    NotificationType.HOME_REACHED.value: EventType.ROBOT_HOME_REACHED,
    NotificationType.ROBOT_OFFLINE.value: EventType.ROBOT_OFFLINE,
    NotificationType.ROBOT_FAULT.value: EventType.ROBOT_FAULT,
    NotificationType.ROBOT_BLOCKED.value: EventType.ROBOT_BLOCKED,
    NotificationType.NAVIGATION_FAILED.value: EventType.ORDER_DELIVERY_FAILED,
}


async def notify(
    session: AsyncSession,
    notification_type: str,
    title: str,
    body: str = "",
    *,
    channels: Optional[Iterable[str]] = None,
    recipient: str = "*",
    payload: Optional[dict] = None,
    event_uid: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> list[Notification]:
    """Create the notification rows and publish the realtime event.

    ``channels`` overrides the default routing matrix.
    """
    from ..events.schemas import new_id

    uid = event_uid or new_id("ntf")
    routes = ROUTING.get(notification_type)
    if routes is None:
        routes = (Route(NotificationChannel.ADMIN.value),)
    if channels is not None:
        wanted = set(channels)
        routes = tuple(r for r in routes if r.channel in wanted) or tuple(
            Route(ch, NotificationPriority.NORMAL.value) for ch in wanted
        )

    created: list[Notification] = []
    new_rows = 0
    for route in routes:
        row = await notif_repo.find_notification(
            session, event_uid=uid, channel=route.channel, recipient=recipient
        )
        if row is None:
            row = await notif_repo.add_notification(
                session,
                event_uid=uid,
                notification_type=notification_type,
                channel=route.channel,
                recipient=recipient,
                title=title,
                body=body,
                priority=route.priority,
                payload={
                    **(payload or {}),
                    "sound": route.sound,
                    "push": route.push,
                    "voice": route.voice,
                },
            )
            new_rows += 1
        created.append(row)

    event_type = EVENT_FOR_NOTIFICATION.get(notification_type)
    if event_type is not None and new_rows:
        await enqueue_event(
            session,
            event_type,
            data={
                "notification_type": notification_type,
                "title": title,
                "body": body,
                "channels": [r.channel for r in routes],
                "priority": max(
                    (r.priority for r in routes),
                    key=lambda p: _priority_rank(p),
                ),
                **(payload or {}),
            },
            channel=_primary_channel(routes),
            correlation_id=correlation_id,
        )
    return created


def _priority_rank(priority: str) -> int:
    order = ["LOW", "NORMAL", "HIGH", "URGENT"]
    try:
        return order.index(priority)
    except ValueError:
        return 0


def _primary_channel(routes: tuple[Route, ...]) -> str:
    """The channel the realtime event is published on."""
    for wanted in (NotificationChannel.ADMIN.value, NotificationChannel.KITCHEN.value,
                   NotificationChannel.CUSTOMER.value):
        if any(r.channel == wanted for r in routes):
            return wanted
    return NotificationChannel.ALL.value


async def acknowledge(session: AsyncSession, notification_id: int, by: str) -> Optional[Notification]:
    from ..db.base import utcnow

    row = await notif_repo.get_notification(session, notification_id)
    if row is None:
        return None
    row.acknowledged_at = utcnow()
    row.acknowledged_by = by
    row.delivery_status = "ACKNOWLEDGED"
    await session.flush()
    return row


async def history(
    session: AsyncSession,
    channel: Optional[str] = None,
    recipient: Optional[str] = None,
    acknowledged: Optional[bool] = None,
    limit: int = 50,
) -> list[Notification]:
    return await notif_repo.list_notifications(
        session, channel=channel, recipient=recipient,
        acknowledged=acknowledged, limit=limit,
    )


async def dispatch_push(notifications: list[Notification]) -> int:
    """Hand the push-flagged notifications to the push provider.

    Returns the number of notifications actually handed over.  When no provider
    is configured this is 0 and the caller reports push as *not configured*.
    """
    from . import push

    targeted = [n for n in notifications if (n.payload or {}).get("push")]
    if not targeted:
        return 0
    return await push.send_many(targeted)


def push_configured() -> bool:
    from . import push

    return push.is_configured()


def sound_enabled() -> bool:
    return settings.NOTIFICATION_SOUND_ENABLED
