"""The robot mission state machine.

A mission is **not** completed because a dispatch command was accepted.  It only
reaches ``COMPLETED`` after the gateway reported a real navigation result and the
robot reported it is home.

Transitions are declared with the roles that may request them.  ``system`` means
"the backend reacts to a validated robot event" and may not be invoked directly by
a client.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Mapping, Set, Tuple

from ..models.enums import MissionStatus

Transition = Tuple[str, str, FrozenSet[str]]

_TRANSITIONS: Tuple[Transition, ...] = (
    ("CREATED", "ASSIGNED", frozenset({"admin"})),
    ("ASSIGNED", "AWAITING_LOAD_CONFIRMATION", frozenset({"system"})),
    ("AWAITING_LOAD_CONFIRMATION", "READY_TO_DISPATCH", frozenset({"courier", "admin", "system"})),
    ("READY_TO_DISPATCH", "DISPATCH_REQUESTED", frozenset({"admin"})),
    ("DISPATCH_REQUESTED", "NAVIGATING_TO_TABLE", frozenset({"system"})),
    ("DISPATCH_REQUESTED", "ROBOT_OFFLINE", frozenset({"system"})),
    ("NAVIGATING_TO_TABLE", "ARRIVED_AT_DESTINATION", frozenset({"system"})),
    ("NAVIGATING_TO_TABLE", "NAVIGATION_FAILED", frozenset({"system"})),
    ("NAVIGATING_TO_TABLE", "BLOCKED", frozenset({"system"})),
    ("NAVIGATING_TO_TABLE", "PAUSED", frozenset({"admin"})),
    ("ARRIVED_AT_DESTINATION", "AWAITING_COLLECTION", frozenset({"system"})),
    ("ARRIVED_AT_DESTINATION", "DELIVERY_FAILED", frozenset({"admin", "system"})),
    ("AWAITING_COLLECTION", "COLLECTION_CONFIRMED", frozenset({"customer", "kitchen", "admin", "system"})),
    ("AWAITING_COLLECTION", "DELIVERY_FAILED", frozenset({"admin", "system"})),
    ("COLLECTION_CONFIRMED", "RETURN_REQUESTED", frozenset({"admin", "system"})),
    ("COLLECTION_CONFIRMED", "COMPLETED", frozenset({"admin", "system"})),
    ("RETURN_REQUESTED", "RETURNING_HOME", frozenset({"system"})),
    ("RETURN_REQUESTED", "ROBOT_OFFLINE", frozenset({"system"})),
    ("RETURNING_HOME", "DOCKED_OR_HOME", frozenset({"system"})),
    ("RETURNING_HOME", "NAVIGATION_FAILED", frozenset({"system"})),
    ("DOCKED_OR_HOME", "COMPLETED", frozenset({"system", "admin"})),
    # ── generic control transitions ──────────────────────────────────────────
    ("NAVIGATING_TO_TABLE", "CANCEL_REQUESTED", frozenset({"admin"})),
    ("RETURNING_HOME", "CANCEL_REQUESTED", frozenset({"admin"})),
    ("AWAITING_COLLECTION", "CANCEL_REQUESTED", frozenset({"admin"})),
    ("CANCEL_REQUESTED", "CANCELLED", frozenset({"system"})),
    ("BLOCKED", "RECOVERY_REQUIRED", frozenset({"system", "admin"})),
    ("NAVIGATION_FAILED", "RECOVERY_REQUIRED", frozenset({"system", "admin"})),
    ("ROBOT_OFFLINE", "RECOVERY_REQUIRED", frozenset({"system", "admin"})),
    ("RECOVERY_REQUIRED", "READY_TO_DISPATCH", frozenset({"admin", "system"})),
    ("RECOVERY_REQUIRED", "CANCELLED", frozenset({"admin", "system"})),
    ("PAUSED", "NAVIGATING_TO_TABLE", frozenset({"admin", "system"})),
    ("PAUSED", "RETURNING_HOME", frozenset({"admin", "system"})),
    ("PAUSED", "CANCEL_REQUESTED", frozenset({"admin"})),
)

_INDEX: Dict[Tuple[str, str], FrozenSet[str]] = {
    (src, dst): roles for src, dst, roles in _TRANSITIONS
}

TERMINAL_STATUSES: FrozenSet[str] = frozenset({
    MissionStatus.COMPLETED.value,
    MissionStatus.CANCELLED.value,
    MissionStatus.FAILED.value,
})

#: States in which the robot is physically moving or about to.
ACTIVE_STATUSES: FrozenSet[str] = frozenset({
    MissionStatus.DISPATCH_REQUESTED.value,
    MissionStatus.NAVIGATING_TO_TABLE.value,
    MissionStatus.RETURN_REQUESTED.value,
    MissionStatus.RETURNING_HOME.value,
})

#: States that still need human or robot action.
OPEN_STATUSES: FrozenSet[str] = frozenset({
    MissionStatus.CREATED.value,
    MissionStatus.ASSIGNED.value,
    MissionStatus.AWAITING_LOAD_CONFIRMATION.value,
    MissionStatus.READY_TO_DISPATCH.value,
    MissionStatus.DISPATCH_REQUESTED.value,
    MissionStatus.NAVIGATING_TO_TABLE.value,
    MissionStatus.ARRIVED_AT_DESTINATION.value,
    MissionStatus.AWAITING_COLLECTION.value,
    MissionStatus.COLLECTION_CONFIRMED.value,
    MissionStatus.RETURN_REQUESTED.value,
    MissionStatus.RETURNING_HOME.value,
    MissionStatus.BLOCKED.value,
    MissionStatus.PAUSED.value,
    MissionStatus.NAVIGATION_FAILED.value,
    MissionStatus.ROBOT_OFFLINE.value,
    MissionStatus.RECOVERY_REQUIRED.value,
    MissionStatus.CANCEL_REQUESTED.value,
})


class MissionTransitionError(ValueError):
    def __init__(self, message: str, current: str, target: str, actor: str):
        super().__init__(message)
        self.current = current
        self.target = target
        self.actor = actor


def can_transition(current: str, target: str, actor: str) -> bool:
    roles = _INDEX.get((current, target))
    if roles is None:
        return False
    return actor in roles


def require_transition(current: str, target: str, actor: str) -> None:
    if current == target:
        return
    if not can_transition(current, target, actor):
        permitted = sorted(dst for (src, dst), roles in _INDEX.items()
                            if src == current and actor in roles)
        raise MissionTransitionError(
            f"Mission cannot move from {current} to {target} for role '{actor}'"
            + (f" (allowed: {', '.join(permitted)})" if permitted else ""),
            current=current,
            target=target,
            actor=actor,
        )


def allowed_targets(status: str, actor: str) -> Set[str]:
    return {dst for (src, dst), roles in _INDEX.items() if src == status and actor in roles}


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def is_active(status: str) -> bool:
    return status in ACTIVE_STATUSES


def describe() -> Mapping[str, Mapping[str, Set[str]]]:
    out: Dict[str, Dict[str, Set[str]]] = {}
    for (src, dst), roles in _INDEX.items():
        out.setdefault(src, {})[dst] = set(roles)
    return out


def next_states(status: str) -> Set[str]:
    return {dst for (src, dst) in _INDEX if src == status}
