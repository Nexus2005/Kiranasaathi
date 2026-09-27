from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import db
from app.security import get_current_user
from app.services import intelligence as intel

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("")
async def list_alerts(
    status: Optional[str] = None,
    type: Optional[str] = None,
    unread_only: bool = False,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    rows = await db.fetch(
        """
        select a.id, a.type, a.severity, a.title, a.description, a.status,
               a.reference_id, a.created_at, a.read_at,
               case when a.read_at is null then false else true end as is_read
        from alerts a
        where a.store_id = $1
          and ($2::text is null or a.status = $2::text)
          and ($3::text is null or a.type = $3::text)
          and ($4 = false or a.read_at is null)
        order by case a.severity when 'critical' then 0 when 'warning' then 1 else 2 end,
                 a.created_at desc
        limit 200
        """,
        user["store_id"],
        status if status in ("open", "acknowledged", "resolved") else None,
        type,
        unread_only,
    )
    open_count = await db.fetchval(
        "select count(*) from alerts where store_id=$1 and status='open'", user["store_id"]
    )
    unread_count = await db.fetchval(
        "select count(*) from alerts where store_id=$1 and status='open' and read_at is null",
        user["store_id"],
    )
    return {
        "items": [dict(r) for r in rows],
        "count": len(rows),
        "open_count": int(open_count or 0),
        "unread_count": int(unread_count or 0),
    }


class AlertAction(BaseModel):
    note: Optional[str] = Field(default=None, max_length=300)


@router.post("/{alert_id}/read")
async def mark_read(alert_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        update alerts set read_at = now()
        where id = $1 and store_id = $2 and read_at is null
        returning id
        """,
        alert_id,
        user["store_id"],
    )
    if not row:
        raise HTTPException(404, "Alert not found or already read")
    return {"id": alert_id, "read": True}


@router.post("/{alert_id}/acknowledge")
async def acknowledge(alert_id: str, body: AlertAction, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        update alerts
        set status = 'acknowledged',
            description = case when $3::text is not null
                          then description || ' | Acknowledged: ' || $3::text else description end
        where id = $1 and store_id = $2 and status = 'open'
        returning id
        """,
        alert_id,
        user["store_id"],
        body.note,
    )
    if not row:
        raise HTTPException(404, "Open alert not found")
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, message)
        values ($1, $2, 'ALERT_ACKNOWLEDGED', 'alert', $3, $4)
        """,
        user["store_id"],
        user["id"],
        alert_id,
        body.note or "Alert acknowledged",
    )
    return {"id": alert_id, "status": "acknowledged"}


@router.post("/{alert_id}/dismiss")
async def dismiss(alert_id: str, body: AlertAction, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        update alerts set status = 'resolved'
        where id = $1 and store_id = $2 and status in ('open','acknowledged')
        returning id
        """,
        alert_id,
        user["store_id"],
    )
    if not row:
        raise HTTPException(404, "Alert not found")
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, message)
        values ($1, $2, 'ALERT_DISMISSED', 'alert', $3, $4)
        """,
        user["store_id"],
        user["id"],
        alert_id,
        body.note or "Alert dismissed",
    )
    return {"id": alert_id, "status": "resolved"}


@router.post("/refresh")
async def refresh_alerts(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """State-based intelligence refresh — creates/resolves alerts idempotently."""
    return await intel.run_refresh(user["store_id"])
