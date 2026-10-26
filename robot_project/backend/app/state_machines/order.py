"""The order state machine.

Every transition is explicit: the target state, the roles allowed to perform it and
the evidence the backend requires.  The database cannot reach an illegal state
because the service layer refuses to write it — the enum column only guarantees the
*value* is legal, not that the *path* was.

Terminal states: ``DELIVERED``, ``REJECTED``, ``CANCELLED``.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Mapping, Optional, Set, Tuple

from ..models.enums import OrderStatus

Transition = Tuple[str, str, FrozenSet[str]]

#: ``(from_state, to_state, allowed_roles)``
_TRANSITIONS: Tuple[Transition, ...] = (
    ("PENDING", "ACCEPTED", frozenset({"kitchen", "admin"})),
    ("PENDING", "REJECTED", frozenset({"kitchen", "admin"})),
    ("PENDING", "CANCELLED", frozenset({"customer", "kitchen", "admin"})),
    ("ACCEPTED", "PREPARING", frozenset({"kitchen", "admin"})),
    ("ACCEPTED", "CANCELLED", frozenset({"kitchen", "admin"})),
    ("PREPARING", "READY_FOR_PICKUP", frozenset({"kitchen", "admin"})),
    ("PREPARING", "CANCELLED", frozenset({"kitchen", "admin"})),
    ("READY_FOR_PICKUP", "READY_FOR_DISPATCH", frozenset({"kitchen", "admin"})),
    ("READY_FOR_PICKUP", "CANCELLED", frozenset({"kitchen", "admin"})),
    ("READY_FOR_DISPATCH", "LOADED", frozenset({"courier", "admin"})),
    ("READY_FOR_DISPATCH", "CANCELLED", frozenset({"kitchen", "admin"})),
    ("LOADED", "OUT_FOR_DELIVERY", frozenset({"admin", "system"})),
    ("LOADED", "CANCELLED", frozenset({"admin", "system"})),
    ("OUT_FOR_DELIVERY", "ARRIVED_AT_TABLE", frozenset({"system"})),
    ("OUT_FOR_DELIVERY", "DELIVERY_FAILED", frozenset({"admin", "system"})),
    ("ARRIVED_AT_TABLE", "COLLECTION_CONFIRMED", frozenset({"customer", "kitchen", "admin", "system"})),
    ("ARRIVED_AT_TABLE", "DELIVERY_FAILED", frozenset({"admin", "system"})),
    ("COLLECTION_CONFIRMED", "DELIVERED", frozenset({"admin", "system"})),
    ("DELIVERY_FAILED", "READY_FOR_DISPATCH", frozenset({"admin", "system"})),
    ("DELIVERY_FAILED", "CANCELLED", frozenset({"admin", "system"})),
)

_TRANSITION_INDEX: Dict[Tuple[str, str], FrozenSet[str]] = {
    (src, dst): roles for src, dst, roles in _TRANSITIONS
}

#: States in which the order still needs somebody to act on it.
OPEN_STATUSES: FrozenSet[str] = frozenset({
    OrderStatus.PENDING.value,
    OrderStatus.ACCEPTED.value,
    OrderStatus.PREPARING.value,
    OrderStatus.READY_FOR_PICKUP.value,
    OrderStatus.READY_FOR_DISPATCH.value,
    OrderStatus.LOADED.value,
    OrderStatus.OUT_FOR_DELIVERY.value,
    OrderStatus.ARRIVED_AT_TABLE.value,
    OrderStatus.DELIVERY_FAILED.value,
})

TERMINAL_STATUSES: FrozenSet[str] = frozenset({
    OrderStatus.DELIVERED.value,
    OrderStatus.REJECTED.value,
    OrderStatus.CANCELLED.value,
})

#: Statuses that mean "the robot physically left the kitchen".
IN_DELIVERY_STATUSES: FrozenSet[str] = frozenset({
    OrderStatus.OUT_FOR_DELIVERY.value,
    OrderStatus.ARRIVED_AT_TABLE.value,
    OrderStatus.COLLECTION_CONFIRMED.value,
})

#: Statuses for which a robot mission may exist.
MISSION_ELIGIBLE_STATUSES: FrozenSet[str] = frozenset({
    OrderStatus.READY_FOR_DISPATCH.value,
    OrderStatus.LOADED.value,
    OrderStatus.DELIVERY_FAILED.value,
})


class OrderTransitionError(ValueError):
    """Raised when a requested order transition is not allowed."""

    def __init__(self, message: str, current: str, target: str, actor: str):
        super().__init__(message)
        self.current = current
        self.target = target
        self.actor = actor


def allowed_targets(status: str, actor: str) -> Set[str]:
    """Targets reachable from ``status`` by ``actor``."""
    out: Set[str] = set()
    for (src, dst), roles in _TRANSITION_INDEX.items():
        if src == status and (actor in roles or "system" in roles and actor == "system"):
            out.add(dst)
    return out


def can_transition(current: str, target: str, actor: str) -> bool:
    roles = _TRANSITION_INDEX.get((current, target))
    if roles is None:
        return False
    return actor in roles


def require_transition(current: str, target: str, actor: str) -> None:
    """Raise :class:`OrderTransitionError` when the transition is not permitted."""
    if current == target:
        return
    if not can_transition(current, target, actor):
        permitted = sorted(
            dst for (src, dst), roles in _TRANSITION_INDEX.items()
            if src == current and actor in roles
        )
        raise OrderTransitionError(
            f"Order cannot move from {current} to {target} for role '{actor}'"
            + (f" (allowed: {', '.join(permitted)})" if permitted else ""),
            current=current,
            target=target,
            actor=actor,
        )


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def is_open(status: str) -> bool:
    return status in OPEN_STATUSES


def describe() -> Mapping[str, Mapping[str, Set[str]]]:
    """Full transition table — used by the API docs and by the admin UI."""
    out: Dict[str, Dict[str, Set[str]]] = {}
    for (src, dst), roles in _TRANSITION_INDEX.items():
        out.setdefault(src, {})[dst] = set(roles)
    return out


def next_states(status: str) -> Set[str]:
    return {dst for (src, dst) in _TRANSITION_INDEX if src == status}


def default_status_for_kind(kind: str) -> str:
    return OrderStatus.PENDING.value


def can_customer_cancel(current: str) -> bool:
    return current in {
        OrderStatus.PENDING.value,
        OrderStatus.ACCEPTED.value,
        OrderStatus.PREPARING.value,
    }


def customer_visible_status(status: str) -> str:
    """Map internal states onto the wording the customer sees."""
    mapping = {
        OrderStatus.PENDING.value: "pending",
        OrderStatus.ACCEPTED.value: "accepted",
        OrderStatus.PREPARING.value: "preparing",
        OrderStatus.READY_FOR_PICKUP.value: "ready",
        OrderStatus.READY_FOR_DISPATCH.value: "ready",
        OrderStatus.LOADED.value: "ready",
        OrderStatus.OUT_FOR_DELIVERY.value: "on_the_way",
        OrderStatus.ARRIVED_AT_TABLE.value: "arrived",
        OrderStatus.COLLECTION_CONFIRMED.value: "collected",
        OrderStatus.DELIVERED.value: "delivered",
        OrderStatus.REJECTED.value: "rejected",
        OrderStatus.CANCELLED.value: "cancelled",
        OrderStatus.DELIVERY_FAILED.value: "failed",
    }
    return mapping.get(status, status.lower())


def customer_visible_timeline(status: str) -> list[dict]:
    """The fixed customer-facing progress steps plus the current one."""
    steps = [
        ("placed", "Order received", (OrderStatus.PENDING.value,)),
        ("accepted", "Kitchen accepted your order", (OrderStatus.ACCEPTED.value,)),
        ("preparing", "Your food is being prepared",
         (OrderStatus.PREPARING.value, OrderStatus.READY_FOR_PICKUP.value,
          OrderStatus.READY_FOR_DISPATCH.value, OrderStatus.LOADED.value)),
        ("on_the_way", "The robot is on the way",
         (OrderStatus.OUT_FOR_DELIVERY.value,)),
        ("arrived", "The robot arrived at your table",
         (OrderStatus.ARRIVED_AT_TABLE.value,)),
        ("collected", "Collection confirmed",
         (OrderStatus.COLLECTION_CONFIRMED.value, OrderStatus.DELIVERED.value)),
    ]
    out = []
    reached_index = -1
    for idx, (key, label, matching) in enumerate(steps):
        if status in matching:
            reached_index = idx
    for idx, (key, label, matching) in enumerate(steps):
        if status in (OrderStatus.REJECTED.value, OrderStatus.CANCELLED.value):
            state = "cancelled" if idx > reached_index else "done"
        elif idx < reached_index:
            state = "done"
        elif idx == reached_index:
            state = "current"
        else:
            state = "upcoming"
        out.append({"key": key, "label": label, "state": state})
    return out


def customer_status_detail(status: str) -> Optional[str]:
    """Extra sentence shown to the customer for the current state."""
    return {
        OrderStatus.ARRIVED_AT_TABLE.value:
            "Please collect your order from the robot tray.",
        OrderStatus.OUT_FOR_DELIVERY.value:
            "The robot is driving to your table.",
        OrderStatus.DELIVERY_FAILED.value:
            "The robot could not complete the delivery. Staff have been notified.",
    }.get(status)
