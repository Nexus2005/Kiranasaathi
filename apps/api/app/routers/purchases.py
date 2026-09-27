from __future__ import annotations

import json
from datetime import date as _date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import db
from app.security import get_current_user
from app.services import intelligence as intel

router = APIRouter(prefix="/purchases", tags=["purchases"])


def _parse_date(value: Optional[str]) -> Optional[_date]:
    """asyncpg needs real date objects once the param type resolves to date."""
    if not value:
        return None
    try:
        return _date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"Invalid date (expected YYYY-MM-DD): {value}")


class PurchaseItemIn(BaseModel):
    product_id: str
    quantity: int = Field(ge=1, le=100000)
    unit_cost: float = Field(ge=0)
    expiry_date: Optional[str] = None
    batch_no: Optional[str] = Field(default=None, max_length=64)


class PurchaseIn(BaseModel):
    supplier_id: str
    items: list[PurchaseItemIn] = Field(min_length=1)
    invoice_no: Optional[str] = Field(default=None, max_length=80)
    notes: Optional[str] = Field(default=None, max_length=500)
    expected_delivery_date: Optional[str] = None


@router.get("")
async def list_purchases(
    status: Optional[str] = None, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    rows = await db.fetch(
        """
        select po.id, po.status, po.total_amount, po.invoice_no, po.notes,
               po.expected_delivery_date, po.created_at, po.received_at,
               s.id as supplier_id, s.name as supplier_name,
               (select count(*) from purchase_items pi where pi.purchase_order_id = po.id) as line_count,
               (select coalesce(sum(pi.quantity), 0) from purchase_items pi where pi.purchase_order_id = po.id) as unit_count,
               (
                 select coalesce(
                   json_agg(json_build_object(
                     'product_id', p.id, 'name', p.name, 'quantity', pi.quantity,
                     'unit_cost', pi.unit_cost, 'line_total', pi.line_total,
                     'expiry_date', pi.expiry_date, 'batch_no', pi.batch_no))
                   , '[]'::json)
                 from purchase_items pi join products p on p.id = pi.product_id
                 where pi.purchase_order_id = po.id
               ) as items
        from purchase_orders po
        left join suppliers s on s.id = po.supplier_id
        where po.store_id = $1
          and ($2::text is null or po.status = $2::text)
        order by po.created_at desc
        limit 100
        """,
        user["store_id"],
        status if status in ("pending", "received", "cancelled") else None,
    )
    items = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("items"), str):
            d["items"] = json.loads(d["items"])
        items.append(d)
    return {"items": items, "count": len(items)}


@router.post("", status_code=201)
async def create_purchase(body: PurchaseIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Create a pending purchase order. Inventory changes only on receive."""
    store_id = user["store_id"]
    supplier = await db.fetchrow(
        "select id from suppliers where id=$1 and store_id=$2", body.supplier_id, store_id
    )
    if not supplier:
        raise HTTPException(404, "Supplier not found")

    # Validate all products up-front so no partial PO is ever created
    seen: set[str] = set()
    for it in body.items:
        if it.product_id in seen:
            raise HTTPException(400, "Duplicate product in purchase items")
        seen.add(it.product_id)
        exists = await db.fetchval(
            "select 1 from products where id=$1 and store_id=$2 and is_active",
            it.product_id,
            store_id,
        )
        if not exists:
            raise HTTPException(400, f"Unknown product in items: {it.product_id}")

    async with db.pool.acquire() as conn:
        async with conn.transaction():
            po = await conn.fetchrow(
                """
                insert into purchase_orders
                  (store_id, supplier_id, invoice_no, notes, expected_delivery_date, created_by)
                values ($1, $2, $3, $4, $5::date, $6)
                returning id, created_at
                """,
                store_id,
                body.supplier_id,
                body.invoice_no,
                body.notes,
                _parse_date(body.expected_delivery_date),
                user["id"],
            )
            po_id = po["id"]
            total = 0.0
            for it in body.items:
                await conn.execute(
                    """
                    insert into purchase_items
                      (purchase_order_id, product_id, quantity, unit_cost, expiry_date, batch_no)
                    values ($1, $2, $3, $4, $5::date, $6)
                    """,
                    po_id,
                    it.product_id,
                    it.quantity,
                    it.unit_cost,
                    _parse_date(it.expiry_date),
                    it.batch_no,
                )
                total += it.quantity * it.unit_cost

            await conn.execute(
                """
                insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
                values ($1, $2, 'PURCHASE_CREATED', 'purchase_order', $3, $4, $5)
                """,
                store_id,
                user["id"],
                po_id,
                json.dumps({"supplier_id": body.supplier_id, "lines": len(body.items),
                            "estimated_total": round(total, 2)}),
                f"Purchase order created: {len(body.items)} product(s), est. ₹{total:.2f}",
            )

    return {"id": str(po_id), "status": "pending", "estimated_total": round(total, 2),
            "items_count": len(body.items)}


@router.post("/{purchase_id}/receive")
async def receive_purchase(purchase_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Receive a pending PO — atomic: batches + inventory + cost history + events."""
    store_id = user["store_id"]
    owned = await db.fetchval(
        "select 1 from purchase_orders where id=$1 and store_id=$2", purchase_id, store_id
    )
    if not owned:
        raise HTTPException(404, "Purchase order not found")
    try:
        raw = await db.fetchval(
            "select receive_purchase($1::uuid, $2::uuid)", purchase_id, user["id"]
        )
    except Exception as exc:  # asyncpg raises PostgresError with the SQL exception text
        msg = str(exc)
        if "already received" in msg:
            raise HTTPException(409, "Purchase order already received")
        if "cancelled" in msg:
            raise HTTPException(409, "Purchase order is cancelled")
        if "not found" in msg:
            raise HTTPException(404, "Purchase order not found")
        raise
    result = json.loads(raw) if isinstance(raw, str) else raw
    return result


@router.post("/{purchase_id}/cancel")
async def cancel_purchase(purchase_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    store_id = user["store_id"]
    row = await db.fetchrow(
        "select id, status from purchase_orders where id=$1 and store_id=$2",
        purchase_id,
        store_id,
    )
    if not row:
        raise HTTPException(404, "Purchase order not found")
    if row["status"] != "pending":
        raise HTTPException(409, f"Only pending purchase orders can be cancelled (status: {row['status']})")
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "update purchase_orders set status='cancelled' where id=$1", row["id"]
            )
            await conn.execute(
                """
                insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
                values ($1, $2, 'PURCHASE_CANCELLED', 'purchase_order', $3, $4, $5)
                """,
                store_id,
                user["id"],
                row["id"],
                json.dumps({"previous_status": "pending", "new_status": "cancelled"}),
                "Purchase order cancelled",
            )
    return {"id": str(row["id"]), "status": "cancelled"}
