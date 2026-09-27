from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import db
from app.security import get_current_user
from app.services import customers_intel

router = APIRouter(prefix="/customers", tags=["customers"])


class CustomerIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=20)


class ConsentIn(BaseModel):
    marketing_consent: bool
    consent_source: Optional[str] = None


@router.get("")
async def list_customers(
    q: Optional[str] = None,
    segment: Optional[str] = None,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    rows = await db.fetch(
        """
        select c.id, c.name, c.phone, c.created_at,
               count(s.id) as orders,
               coalesce(sum(s.total), 0) as total_spent,
               max(s.created_at) as last_purchase
        from customers c
        left join sales s on s.customer_id = c.id
        where c.store_id = $1
          and ($2::text is null or c.name ilike '%' || $2::text || '%' or c.phone ilike '%' || $2::text || '%')
        group by c.id, c.name, c.phone, c.created_at
        order by total_spent desc, c.created_at desc
        limit 200
        """,
        user["store_id"],
        q.strip() if q else None,
    )
    items = [dict(r) for r in rows]
    if segment:
        # Reuse the SAME deterministic segment rules as the intelligence service
        overview = await customers_intel.customer_overview(user["store_id"])
        seg_map = {
            i["customer_id"]: i["segments"] for i in overview["customers"]
        }
        items = [
            i for i in items
            if any(s == segment or s.startswith(segment + ":") for s in seg_map.get(str(i["id"]), []))
        ]
    return {"items": items}


@router.post("", status_code=201)
async def create_customer(body: CustomerIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    store_id = user["store_id"]
    if body.phone:
        dup = await db.fetchval(
            "select 1 from customers where store_id=$1 and phone=$2",
            store_id,
            body.phone,
        )
        if dup:
            raise HTTPException(400, "Customer with this phone already exists")
    row = await db.fetchrow(
        "insert into customers (store_id, name, phone) values ($1,$2,$3) returning *",
        store_id,
        body.name.strip(),
        body.phone.strip() if body.phone else None,
    )
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
        values ($1,$2,'CUSTOMER_ADDED','customer',$3,$4,$5)
        """,
        store_id,
        user["id"],
        row["id"],
        {"name": row["name"], "phone": row["phone"]},
        f"Customer added: {row['name']}",
    )
    return dict(row)


@router.get("/intelligence/overview")
async def intelligence_overview(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Segment distribution + per-customer segments (deterministic rules)."""
    return await customers_intel.customer_overview(user["store_id"])


@router.get("/intelligence/segments")
async def intelligence_segments(
    segment: Optional[str] = None, limit: int = 100, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    items = await customers_intel.customer_segments(user["store_id"], segment, limit)
    return {"items": items, "count": len(items)}


@router.get("/{customer_id}/intelligence")
async def customer_intelligence(customer_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Customer 360: RFM, segments, favorites, orders, campaign history."""
    try:
        return await customers_intel.customer_360(user["store_id"], customer_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{customer_id}/consent")
async def set_consent(customer_id: str, body: ConsentIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await customers_intel.update_consent(
            user["store_id"], user["id"], customer_id,
            marketing_consent=body.marketing_consent,
            consent_source=body.consent_source,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/{customer_id}/history")
async def customer_history(customer_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    store_id = user["store_id"]
    cust = await db.fetchrow(
        "select * from customers where id=$1 and store_id=$2",
        customer_id,
        store_id,
    )
    if not cust:
        raise HTTPException(404, "Customer not found")
    sales = await db.fetch(
        """
        select id, total, discount, payment_method, created_at
        from sales
        where customer_id=$1 and store_id=$2
        order by created_at desc
        limit 50
        """,
        customer_id,
        store_id,
    )
    return {"customer": dict(cust), "sales": [dict(s) for s in sales]}
