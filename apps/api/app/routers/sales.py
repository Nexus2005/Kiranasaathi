from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import db
from app.security import get_current_user
from app.services import retail

router = APIRouter(prefix="/sales", tags=["sales"])


class SaleItemIn(BaseModel):
    product_id: str
    quantity: int = Field(gt=0, le=10000)
    # Optional negotiated price (bargaining). Must respect the store's pricing
    # policy — enforced server-side by create_sale (min_acceptable_price).
    unit_price: Optional[float] = Field(default=None, ge=0)


class SaleIn(BaseModel):
    items: list[SaleItemIn] = Field(min_length=1)
    customer_id: Optional[str] = None
    discount: float = Field(default=0, ge=0)
    payment_method: str = Field(default="cash")
    idempotency_key: Optional[str] = None


@router.get("")
async def list_sales(limit: int = 50, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    rows = await db.fetch(
        """
        select s.id, s.subtotal, s.discount, s.total, s.payment_method, s.status, s.created_at,
               c.name as customer_name,
               coalesce(
                 (select json_agg(json_build_object(
                    'name', p.name,
                    'quantity', si.quantity,
                    'unit_price', si.unit_price,
                    'line_total', si.line_total
                  ) order by p.name)
                  from sale_items si
                  join products p on p.id = si.product_id
                  where si.sale_id = s.id),
                 '[]'::json
               ) as items
        from sales s
        left join customers c on c.id = s.customer_id
        where s.store_id = $1
        order by s.created_at desc
        limit $2
        """,
        user["store_id"],
        max(1, min(limit, 200)),
    )
    items = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("items"), str):
            try:
                d["items"] = json.loads(d["items"])
            except (ValueError, TypeError):
                d["items"] = []
        items.append(d)
    return {"items": items}


@router.post("", status_code=201)
async def create_sale(body: SaleIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    store_id = user["store_id"]
    if body.discount < 0:
        raise HTTPException(400, "Discount cannot be negative")

    # Idempotent sale creation: a timed-out request retried with the same key
    # returns the original sale instead of double-selling.
    stored = await retail.idempotent_result(store_id, body.idempotency_key, "create_sale")
    if stored:
        return stored["result"]

    if body.customer_id:
        cust = await db.fetchval(
            "select 1 from customers where id=$1 and store_id=$2",
            body.customer_id,
            store_id,
        )
        if not cust:
            raise HTTPException(400, "Invalid customer")

    items_json = json.dumps([i.model_dump(exclude_none=True) for i in body.items])
    try:
        result = await db.fetchrow(
            """
            select create_sale($1, $2, $3::jsonb, $4, $5, $6) as result
            """,
            store_id,
            body.customer_id,
            items_json,
            body.discount,
            body.payment_method,
            user["id"],
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "Insufficient stock" in msg or "Cart is empty" in msg or "Invalid" in msg:
            raise HTTPException(400, msg.split("DETAIL:")[-1].strip() if "DETAIL" in msg else msg)
        if "duplicate" in msg.lower():
            raise HTTPException(409, "Duplicate submission rejected")
        raise HTTPException(400, msg)

    payload = result["result"]
    if isinstance(payload, str):
        import json as _json

        payload = _json.loads(payload)

    sale = await db.fetchrow(
        """
        select s.*, c.name as customer_name
        from sales s
        left join customers c on c.id = s.customer_id
        where s.id = $1
        """,
        payload["sale_id"],
    )
    result_payload = {
        "sale": dict(sale),
        "sale_id": payload["sale_id"],
        "subtotal": float(payload["subtotal"]),
        "discount": float(payload["discount"]),
        "total": float(payload["total"]),
    }
    await retail.store_idempotent_result(store_id, body.idempotency_key, "create_sale",
                                         result_payload, 201)
    return result_payload
