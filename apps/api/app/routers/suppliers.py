from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import db
from app.security import get_current_user
from app.services import intelligence as intel

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


class SupplierIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=20)
    address: Optional[str] = Field(default=None, max_length=300)
    categories: Optional[str] = Field(default=None, max_length=200)
    lead_time_days: Optional[int] = Field(default=None, ge=0, le=120)
    min_order_value: Optional[float] = Field(default=None, ge=0)
    payment_terms: Optional[str] = Field(default=None, max_length=120)


@router.get("")
async def list_suppliers(
    q: Optional[str] = None, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    rows = await db.fetch(
        """
        select s.id, s.name, s.phone, s.address, s.categories, s.lead_time_days,
               s.min_order_value, s.payment_terms, s.is_active,
               count(po.id) filter (where po.status = 'received') as received_orders,
               coalesce(sum(po.total_amount) filter (where po.status = 'received'), 0) as total_value,
               max(po.created_at) filter (where po.status = 'received') as last_purchase_at,
               (select count(*) from purchase_orders po2
                 where po2.supplier_id = s.id and po2.status = 'pending') as pending_orders
        from suppliers s
        left join purchase_orders po on po.supplier_id = s.id
        where s.store_id = $1
          and ($2::text is null or s.name ilike '%' || $2::text || '%'
               or coalesce(s.categories, '') ilike '%' || $2::text || '%')
        group by s.id
        order by s.name
        """,
        user["store_id"],
        q.strip() if q else None,
    )
    items = []
    for r in rows:
        d = dict(r)
        d["has_history"] = int(d["received_orders"] or 0) > 0
        if not d["has_history"]:
            d["performance_note"] = "Not enough purchase history to evaluate this supplier."
        items.append(d)
    return {"items": items, "count": len(items)}


@router.post("", status_code=201)
async def create_supplier(body: SupplierIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        insert into suppliers (store_id, name, phone, address, categories, lead_time_days, min_order_value, payment_terms)
        values ($1, $2, $3, $4, $5, $6, $7, $8)
        returning *
        """,
        user["store_id"],
        body.name.strip(),
        body.phone,
        body.address,
        body.categories,
        body.lead_time_days,
        body.min_order_value,
        body.payment_terms,
    )
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
        values ($1, $2, 'SUPPLIER_ADDED', 'supplier', $3, $4, $5)
        """,
        user["store_id"],
        user["id"],
        row["id"],
        json.dumps({"name": row["name"]}),
        f"Supplier added: {row['name']}",
    )
    return dict(row)


@router.get("/{supplier_id}")
async def get_supplier(supplier_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await intel.get_supplier_profile(user["store_id"], supplier_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.patch("/{supplier_id}")
async def update_supplier(
    supplier_id: str, body: SupplierIn, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    current = await db.fetchrow(
        "select * from suppliers where id=$1 and store_id=$2", supplier_id, user["store_id"]
    )
    if not current:
        raise HTTPException(404, "Supplier not found")
    data = body.model_dump(exclude_unset=True)
    if not data:
        return dict(current)
    sets = ", ".join(f"{k}=${i + 3}" for i, k in enumerate(data))
    row = await db.fetchrow(
        f"update suppliers set {sets} where id=$1 and store_id=$2 returning *",
        supplier_id,
        user["store_id"],
        *data.values(),
    )
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, previous_state, new_state, message)
        values ($1, $2, 'SUPPLIER_UPDATED', 'supplier', $3, $4, $5, $6)
        """,
        user["store_id"],
        user["id"],
        supplier_id,
        json.dumps(dict(current), default=str),
        json.dumps(dict(row), default=str),
        f"Supplier updated: {row['name']}",
    )
    return dict(row)


@router.get("/{supplier_id}/products/{product_id}/price-history")
async def supplier_product_price_history(
    supplier_id: str, product_id: str, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    rows = await db.fetch(
        """
        select po.created_at, pi.unit_cost, pi.quantity, po.invoice_no
        from purchase_items pi
        join purchase_orders po on po.id = pi.purchase_order_id
        where po.store_id = $1 and po.supplier_id = $2 and pi.product_id = $3
          and po.status = 'received'
        order by po.created_at desc
        """,
        user["store_id"],
        supplier_id,
        product_id,
    )
    if not rows:
        return {"has_history": False, "message": "Not enough purchase history for this supplier-product pair.",
                "points": []}
    return {"has_history": True, "points": [dict(r) for r in rows]}
