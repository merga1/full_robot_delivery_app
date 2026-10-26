"""Enumerations shared by the ORM models and the state machines.

All enums are plain ``str`` enums so they serialise to JSON without custom
encoders and can be compared against raw database strings.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:      # pragma: no cover - trivial
        return str(self.value)


class Role(StrEnum):
    """Who is calling the API."""

    CUSTOMER = "customer"
    KITCHEN = "kitchen"
    COURIER = "courier"
    ADMIN = "admin"
    GATEWAY = "gateway"
    ROBOT_SCREEN = "robot_screen"

    @classmethod
    def values(cls) -> list[str]:
        return [r.value for r in cls]


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    PREPARING = "PREPARING"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    READY_FOR_DISPATCH = "READY_FOR_DISPATCH"
    LOADED = "LOADED"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    ARRIVED_AT_TABLE = "ARRIVED_AT_TABLE"
    COLLECTION_CONFIRMED = "COLLECTION_CONFIRMED"
    DELIVERED = "DELIVERED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    DELIVERY_FAILED = "DELIVERY_FAILED"


class PaymentStatus(StrEnum):
    UNPAID = "UNPAID"
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class OrderKind(StrEnum):
    ORDER = "order"          # food/drink order
    CALL = "call"            # "send the robot to my table"


class MissionStatus(StrEnum):
    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    AWAITING_LOAD_CONFIRMATION = "AWAITING_LOAD_CONFIRMATION"
    READY_TO_DISPATCH = "READY_TO_DISPATCH"
    DISPATCH_REQUESTED = "DISPATCH_REQUESTED"
    NAVIGATING_TO_TABLE = "NAVIGATING_TO_TABLE"
    ARRIVED_AT_DESTINATION = "ARRIVED_AT_DESTINATION"
    AWAITING_COLLECTION = "AWAITING_COLLECTION"
    COLLECTION_CONFIRMED = "COLLECTION_CONFIRMED"
    RETURN_REQUESTED = "RETURN_REQUESTED"
    RETURNING_HOME = "RETURNING_HOME"
    DOCKED_OR_HOME = "DOCKED_OR_HOME"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    NAVIGATION_FAILED = "NAVIGATION_FAILED"
    ROBOT_OFFLINE = "ROBOT_OFFLINE"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class RobotConnectionStatus(StrEnum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class RobotAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    BUSY = "BUSY"
    CHARGING = "CHARGING"
    MAINTENANCE = "MAINTENANCE"
    FAULTED = "FAULTED"


class MissionEventType(StrEnum):
    MISSION_ACCEPTED = "MISSION_ACCEPTED"
    MISSION_REJECTED = "MISSION_REJECTED"
    NAVIGATION_STARTED = "NAVIGATION_STARTED"
    NAVIGATION_PROGRESS = "NAVIGATION_PROGRESS"
    NAVIGATION_SUCCEEDED = "NAVIGATION_SUCCEEDED"
    NAVIGATION_FAILED = "NAVIGATION_FAILED"
    MISSION_CANCELLED = "MISSION_CANCELLED"
    HOME_REACHED = "HOME_REACHED"
    ROBOT_BLOCKED = "ROBOT_BLOCKED"
    ROBOT_OFFLINE = "ROBOT_OFFLINE"
    ROBOT_FAULT = "ROBOT_FAULT"
    ASSISTANCE_REQUIRED = "ASSISTANCE_REQUIRED"
    LOAD_CONFIRMED = "LOAD_CONFIRMED"
    COLLECTION_CONFIRMED = "COLLECTION_CONFIRMED"
    TELEMETRY = "TELEMETRY"
    HEARTBEAT = "HEARTBEAT"


class NotificationType(StrEnum):
    NEW_ORDER = "NEW_ORDER"
    ORDER_ACCEPTED = "ORDER_ACCEPTED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_PREPARING = "ORDER_PREPARING"
    ORDER_READY = "ORDER_READY"
    ORDER_LOADED = "ORDER_LOADED"
    ORDER_DISPATCHED = "ORDER_DISPATCHED"
    ROBOT_ARRIVED = "ROBOT_ARRIVED"
    COLLECTION_CONFIRMED = "COLLECTION_CONFIRMED"
    ORDER_DELIVERED = "ORDER_DELIVERED"
    RETURN_STARTED = "RETURN_STARTED"
    HOME_REACHED = "HOME_REACHED"
    ROBOT_OFFLINE = "ROBOT_OFFLINE"
    ROBOT_FAULT = "ROBOT_FAULT"
    ROBOT_BLOCKED = "ROBOT_BLOCKED"
    LOW_BATTERY = "LOW_BATTERY"
    NAVIGATION_FAILED = "NAVIGATION_FAILED"
    RETURN_FAILED = "RETURN_FAILED"
    ASSISTANCE_REQUIRED = "ASSISTANCE_REQUIRED"
    DELAY_REPORTED = "DELAY_REPORTED"
    SYSTEM = "SYSTEM"


class NotificationPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class NotificationChannel(StrEnum):
    KITCHEN = "KITCHEN"
    ADMIN = "ADMIN"
    FLUTTER = "FLUTTER"
    ROBOT = "ROBOT"
    CUSTOMER = "CUSTOMER"
    ALL = "ALL"


class NotificationDeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"


class EventType(StrEnum):
    """Real-time event names published over the WebSocket channels."""

    ORDER_CREATED = "order.created"
    ORDER_ACCEPTED = "order.accepted"
    ORDER_REJECTED = "order.rejected"
    ORDER_PREPARING = "order.preparing"
    ORDER_READY = "order.ready"
    ORDER_LOADED = "order.loaded"
    ORDER_DISPATCHED = "order.dispatched"
    ORDER_COLLECTION_CONFIRMED = "order.collection_confirmed"
    ORDER_DELIVERED = "order.delivered"
    ORDER_CANCELLED = "order.cancelled"
    ORDER_DELIVERY_FAILED = "order.delivery_failed"
    ORDER_UPDATED = "order.updated"
    ROBOT_COMMAND_ACKNOWLEDGED = "robot.command_acknowledged"
    ROBOT_STATUS_UPDATED = "robot.status_updated"
    ROBOT_NAVIGATION_STARTED = "robot.navigation_started"
    ROBOT_ARRIVED = "robot.arrived"
    ROBOT_RETURN_STARTED = "robot.return_started"
    ROBOT_HOME_REACHED = "robot.home_reached"
    ROBOT_BLOCKED = "robot.blocked"
    ROBOT_OFFLINE = "robot.offline"
    ROBOT_FAULT = "robot.fault"
    MISSION_UPDATED = "mission.updated"
    NOTIFICATION_CREATED = "notification.created"
    MENU_UPDATED = "menu.updated"
    TABLE_UPDATED = "table.updated"
    HEARTBEAT = "heartbeat"


class WebSocketChannel(StrEnum):
    KITCHEN = "kitchen"
    ADMIN = "admin"
    CUSTOMER = "customer"
    ROBOT = "robot"
