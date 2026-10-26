"""Audit logging helper.

Audit records are written inside the caller's transaction, so an audited action
and the action itself commit or roll back together.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories import notifications as notif_repo

log = logging.getLogger("robot.audit")


async def record(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    resource_type: str = "",
    resource_id: Any = "",
    metadata: Optional[dict[str, Any]] = None,
    actor_id: Optional[int] = None,
    request_id: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> None:
    try:
        await notif_repo.add_audit(
            session,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata or {},
            actor_id=actor_id,
            request_id=request_id,
            ip_address=ip_address,
        )
    except Exception:
        # An audit failure must never break the business operation, but it must
        # be visible in the log.
        log.exception("could not write audit record for action=%s actor=%s", action, actor)
