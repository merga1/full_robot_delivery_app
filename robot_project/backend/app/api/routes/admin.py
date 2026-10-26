"""Administrator endpoints: operational overview, audit trail and catalogue."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.session import get_session
from ...models.enums import OrderStatus
from ...repositories import notifications as notif_repo
from ...repositories import orders as order_repo
from ...services import robot_service
from ..dependencies import Principal, require_admin

log = logging.getLogger("robot.api.admin")

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/overview", summary="The operational overview")
async def overview(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> dict:
    """Everything the admin dashboard shows on its first screen."""
    by_status = await order_repo.orders_by_status(session)
    robots = await robot_service.list_robots(session)

    def count(*states: str) -> int:
        return sum(by_status.get(s, 0) for s in states)

    active_missions = 0
    from ...repositories import robots as robots_repo
    from ...state_machines import mission as mission_sm

    for mission in await robots_repo.list_missions(session, limit=200):
        if mission.status in mission_sm.OPEN_STATUSES:
            active_missions += 1

    return {
        "orders": {
            "new": by_status.get(OrderStatus.PENDING.value, 0),
            "accepted": by_status.get(OrderStatus.ACCEPTED.value, 0),
            "preparing": by_status.get(OrderStatus.PREPARING.value, 0),
            "ready_for_loading": count(
                OrderStatus.READY_FOR_PICKUP.value,
                OrderStatus.READY_FOR_DISPATCH.value,
            ),
            "loaded": by_status.get(OrderStatus.LOADED.value, 0),
            "active_deliveries": count(
                OrderStatus.OUT_FOR_DELIVERY.value,
                OrderStatus.ARRIVED_AT_TABLE.value,
                OrderStatus.COLLECTION_CONFIRMED.value,
            ),
            "completed": by_status.get(OrderStatus.DELIVERED.value, 0),
            "failed_or_cancelled": count(
                OrderStatus.DELIVERY_FAILED.value,
                OrderStatus.CANCELLED.value,
                OrderStatus.REJECTED.value,
            ),
            "by_status": by_status,
        },
        "robots": {
            "total": len(robots),
            "available": sum(1 for r in robots if r.availability == "AVAILABLE"),
            "busy": sum(1 for r in robots if r.availability == "BUSY"),
            "charging": sum(1 for r in robots if r.availability == "CHARGING"),
            "faulted": sum(1 for r in robots if r.availability == "FAULTED"),
            "online": sum(1 for r in robots if r.connection_status == "ONLINE"),
            "offline": sum(1 for r in robots if r.connection_status == "OFFLINE"),
            "active_missions": active_missions,
        },
        "battery": [
            {"robot_id": r.robot_id, "name": r.name, "battery_percent": r.battery_percent,
             "connection_status": r.connection_status}
            for r in robots
        ],
        "navigation_faults": [
            {"robot_id": r.robot_id, "fault_code": r.fault_code, "detail": r.fault_detail}
            for r in robots if r.fault_code
        ],
    }


@router.get("/audit", summary="The audit trail")
async def audit(
    limit: int = Query(default=100, ge=1, le=500),
    action: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_admin),
) -> list[dict]:
    rows = await notif_repo.list_audit(session, limit=limit, action=action)
    return [
        {
            "id": r.id,
            "actor": r.actor,
            "action": r.action,
            "resource_type": r.resource_type,
            "resource_id": r.resource_id,
            "metadata": r.details,
            "request_id": r.request_id,
            "ip_address": r.ip_address,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/state-machines", summary="Both state machines")
async def state_machines(
    principal: Principal = Depends(require_admin),
) -> dict:
    from ...state_machines import mission as mission_sm
    from ...state_machines import order as order_sm

    return {
        "order": {
            "transitions": {
                src: {dst: sorted(roles) for dst, roles in targets.items()}
                for src, targets in order_sm.describe().items()
            },
            "terminal": sorted(order_sm.TERMINAL_STATUSES),
        },
        "mission": {
            "transitions": {
                src: {dst: sorted(roles) for dst, roles in targets.items()}
                for src, targets in mission_sm.describe().items()
            },
            "terminal": sorted(mission_sm.TERMINAL_STATUSES),
            "active": sorted(mission_sm.ACTIVE_STATUSES),
        },
    }
