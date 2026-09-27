from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import db
from app.security import get_current_user

router = APIRouter(tags=["inventory", "suppliers", "meta"])


@router.get("/inventory")
async def inventory_overview(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    store_id = user["store_id"]
    summary = await db.fetchrow(
        """
        select
          count(*) as total_products,
          count(*) filter (where i.quantity > 0) as in_stock,
          count(*) filter (where i.quantity = 0) as out_of_stock,
          count(*) filter (where i.quantity > 0 and i.quantity <= p.reorder_level) as low_stock,
          coalesce(sum(i.quantity * p.purchase_price), 0) as inventory_value,
          count(*) filter (where exists (
            select 1 from inventory_batches b
            where b.product_id = p.id and b.store_id = p.store_id
              and b.quantity > 0 and b.expiry_date <= current_date + 30
          )) as expiring_soon
        from inventory i
        join products p on p.id = i.product_id
        where i.store_id = $1 and p.is_active
        """,
        store_id,
    )
    return dict(summary)


class SupplierIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: Optional[str] = None
    address: Optional[str] = None


# NOTE: /suppliers GET/POST moved to app/routers/suppliers.py (Phase 2) which
# provides richer profiles, search and honest "insufficient data" states.


@router.get("/activity")
async def list_activity(limit: int = 50, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    rows = await db.fetch(
        """
        select id, event_type, entity_type, entity_id, message, created_at
        from activity_logs
        where store_id = $1
        order by created_at desc
        limit $2
        """,
        user["store_id"],
        max(1, min(limit, 200)),
    )
    return {"items": [dict(r) for r in rows]}


@router.get("/recommendations")
async def list_recommendations(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    rows = await db.fetch(
        """
        select id, type, title, description, evidence, severity, confidence, status,
               proposed_action, outcome, created_at, updated_at
        from ai_recommendations
        where store_id = $1
        order by created_at desc
        limit 50
        """,
        user["store_id"],
    )
    return {"items": [dict(r) for r in rows]}


class RecommendationStatusIn(BaseModel):
    status: str = Field(pattern="^(NEW|REVIEWED|APPROVED|REJECTED|EXECUTED|COMPLETED|FAILED|DISMISSED)$")


@router.patch("/recommendations/{rec_id}")
async def update_recommendation(
    rec_id: str, body: RecommendationStatusIn, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        update ai_recommendations
        set status = $3, updated_at = now()
        where id = $1 and store_id = $2
        returning *
        """,
        rec_id,
        user["store_id"],
        body.status,
    )
    if not row:
        raise HTTPException(404, "Recommendation not found")
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
        values ($1,$2,'RECOMMENDATION_STATUS','ai_recommendation',$3,$4,$5)
        """,
        user["store_id"],
        user["id"],
        rec_id,
        {"status": body.status},
        f"Recommendation marked {body.status}",
    )
    return dict(row)


# NOTE: /inventory/adjust removed in Phase 2 — it silently clamped negative
# stock and bypassed batch depletion. All stock mutations now go through
# app/routers/inventory.py /inventory/products/{id}/adjust (FEFO-aware,
# transactional, refuses oversell).
