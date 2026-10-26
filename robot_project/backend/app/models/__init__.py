"""ORM model exports — importing this module registers every table."""

from __future__ import annotations

from .enums import (
    EventType,
    MissionEventType,
    MissionStatus,
    NotificationChannel,
    NotificationDeliveryStatus,
    NotificationPriority,
    NotificationType,
    OrderKind,
    OrderStatus,
    PaymentStatus,
    RobotAvailability,
    RobotConnectionStatus,
    Role,
    WebSocketChannel,
)
from .menu import MenuItem
from .notification import AuditLog, DeviceToken, Notification
from .order import Order, OrderItem, OrderStatusHistory
from .outbox import IdempotencyRecord, OutboxEvent
from .robot import MissionEvent, Robot, RobotMission
from .table import RestaurantTable
from .user import Customer, User

__all__ = [
    "EventType",
    "MissionEventType",
    "MissionStatus",
    "NotificationChannel",
    "NotificationDeliveryStatus",
    "NotificationPriority",
    "NotificationType",
    "OrderKind",
    "OrderStatus",
    "PaymentStatus",
    "RobotAvailability",
    "RobotConnectionStatus",
    "Role",
    "WebSocketChannel",
    "MenuItem",
    "AuditLog",
    "DeviceToken",
    "Notification",
    "Order",
    "OrderItem",
    "OrderStatusHistory",
    "IdempotencyRecord",
    "OutboxEvent",
    "MissionEvent",
    "Robot",
    "RobotMission",
    "RestaurantTable",
    "Customer",
    "User",
]
