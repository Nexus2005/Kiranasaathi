from __future__ import annotations

import json
from datetime import date as _date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import db
from app.security import get_current_user
from app.services import intelligence as intel

router = APIRouter(prefix="/inventory", tags=["inventory"])


def _parse_date(value: Optional[str]) -> Optional[_date]:
    """asyncpg requires real date objects for date params."""
    if not value:
        return None
    try:
        return _date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f"Invalid date (expected YYYY-MM-DD): {value}")


class SettingsPatch(BaseModel):
    min_margin_pct: Optional[float] = Field(default=None, ge=0, le=95)
    expiry_warning_days: Optional[int] = Field(default=None, ge=1, le=180)
    expiry_critical_days: Optional[int] = Field(default=None, ge=1, le=90)
    low_stock_days: Optional[int] = Field(default=None, ge=1, le=90)
    reorder_lead_time_days: Optional[int] = Field(default=None, ge=0, le=60)
    reorder_safety_days: Optional[int] = Field(default=None, ge=0, le=60)
    slow_moving_days: Optional[int] = Field(default=None, ge=1, le=365)
    overstock_days: Optional[int] = Field(default=None, ge=1, le=730)
    dead_stock_days: Optional[int] = Field(default=None, ge=1, le=1095)
    velocity_window_days: Optional[int] = Field(default=None, ge=7, le=180)


@router.get("/intelligence")
async def inventory_intelligence(
    q: Optional[str] = None, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    return await intel.get_inventory_intelligence(user["store_id"], q)


@router.get("/expiry")
async def expiry_intelligence(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return await intel.get_expiring_inventory(user["store_id"])


@router.get("/risk-summary")
async def risk_summary(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return await intel.get_inventory_risk_summary(user["store_id"])


@router.get("/products/{product_id}/intelligence")
async def product_intelligence(
    product_id: str, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    store_id = user["store_id"]
    base = await intel.calculate_product_margin(store_id, product_id)
    reorder = await intel.calculate_reorder(store_id, product_id)
    comparison = await intel.get_supplier_comparison(store_id, product_id)
    cost_history = await intel.get_product_cost_history(store_id, product_id)
    return {
        "pricing": base,
        "reorder": reorder,
        "supplier_comparison": comparison,
        "cost_history": cost_history,
    }


@router.get("/products/{product_id}/pricing")
async def product_pricing(
    product_id: str, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    return await intel.calculate_product_margin(user["store_id"], product_id)


@router.get("/products/{product_id}/reorder")
async def product_reorder(
    product_id: str, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    return await intel.calculate_reorder(user["store_id"], product_id)


@router.get("/products/{product_id}/suppliers/compare")
async def product_supplier_comparison(
    product_id: str, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    return await intel.get_supplier_comparison(user["store_id"], product_id)


@router.get("/products/{product_id}/cost-history")
async def product_cost_history(
    product_id: str, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    return await intel.get_product_cost_history(user["store_id"], product_id)


class DiscountIn(BaseModel):
    proposed_price: float = Field(ge=0)


@router.post("/products/{product_id}/discount-impact")
async def discount_impact(
    product_id: str, body: DiscountIn, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    return await intel.calculate_discount_impact(
        user["store_id"], product_id, body.proposed_price
    )


class BargainIn(BaseModel):
    offer: float = Field(ge=0)
    quantity: int = Field(default=1, ge=1)


@router.post("/products/{product_id}/bargain")
async def bargain(
    product_id: str, body: BargainIn, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    store_id = user["store_id"]
    result = await intel.calculate_bargain(store_id, product_id, body.offer, body.quantity)
    # Audit trail for the future AI loop
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
        values ($1, $2, 'BARGAINING_REQUESTED', 'product', $3, $4, $5)
        """,
        store_id,
        user["id"],
        product_id,
        json.dumps({"offer": body.offer, "quantity": body.quantity,
                    "decision": result.get("decision")}),
        f"Bargain check: offer ₹{body.offer} → {result.get('decision')}",
    )
    return result


class ProductAdjustIn(BaseModel):
    change: int  # positive = add stock, negative = reduce stock
    reason: str = Field(min_length=3, max_length=80)
    expiry_date: Optional[str] = None
    unit_cost: Optional[float] = Field(default=None, ge=0)


@router.post("/products/{product_id}/adjust")
async def adjust_product_inventory(
    product_id: str, body: ProductAdjustIn, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    store_id = user["store_id"]
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            prod = await conn.fetchrow(
                "select id, name, purchase_price from products where id=$1 and store_id=$2 and is_active",
                product_id,
                store_id,
            )
            if not prod:
                raise HTTPException(404, "Product not found")

            cur = await conn.fetchrow(
                """
                select coalesce(i.quantity, 0) as qty,
                       coalesce((select sum(b.quantity) from inventory_batches b
                         where b.store_id=$1 and b.product_id=$2 and b.status='expired' and b.quantity > 0), 0) as expired_qty
                from products p
                left join inventory i on i.product_id = p.id and i.store_id = p.store_id
                where p.id = $2 and p.store_id = $1
                """,
                store_id,
                product_id,
            )
            current = int(cur["qty"] or 0)
            expired = int(cur["expired_qty"] or 0)
            sellable = current - expired

            if body.change < 0:
                reduce_by = -body.change
                if reduce_by > sellable:
                    raise HTTPException(
                        400,
                        f"Cannot reduce below sellable stock (sellable {sellable}, requested {reduce_by})",
                    )
                raw = await conn.fetchval(
                    "select deplete_batches($1, $2, $3)", store_id, product_id, reduce_by
                )
                dep = json.loads(raw) if isinstance(raw, str) else raw
                new_qty = current - reduce_by
                await conn.execute(
                    "update inventory set quantity=$3, updated_at=now() where store_id=$1 and product_id=$2",
                    store_id,
                    product_id,
                    new_qty,
                )
                await conn.execute(
                    """
                    insert into inventory_movements (store_id, product_id, change, quantity_after, reason)
                    values ($1, $2, $3, $4, $5)
                    """,
                    store_id,
                    product_id,
                    body.change,
                    new_qty,
                    f"ADJUSTMENT:{body.reason}",
                )
                await conn.execute(
                    """
                    insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
                    values ($1, $2, 'INVENTORY_DECREASED', 'product', $3, $4, $5)
                    """,
                    store_id,
                    user["id"],
                    product_id,
                    json.dumps({"change": body.change, "reason": body.reason,
                                "quantity_after": new_qty}),
                    f"Stock reduced by {reduce_by} ({body.reason}): {prod['name']} → {new_qty}",
                )
            else:
                add = body.change
                cost = body.unit_cost if body.unit_cost is not None else float(prod["purchase_price"])
                await conn.execute(
                    """
                    insert into inventory (store_id, product_id, quantity)
                    values ($1, $2, $3)
                    on conflict (store_id, product_id)
                    do update set quantity = inventory.quantity + excluded.quantity, updated_at = now()
                    """,
                    store_id,
                    product_id,
                    add,
                )
                await conn.execute(
                    """
                    insert into inventory_batches (store_id, product_id, batch_no, quantity, purchase_cost, expiry_date)
                    values ($1, $2, $3, $4, $5, $6::date)
                    """,
                    store_id,
                    product_id,
                    f"ADJ+{product_id[:8]}-{body.reason[:12].replace(' ', '')}",
                    add,
                    cost,
                    _parse_date(body.expiry_date),
                )
                row = await conn.fetchrow(
                    "select quantity from inventory where store_id=$1 and product_id=$2",
                    store_id,
                    product_id,
                )
                await conn.execute(
                    """
                    insert into inventory_movements (store_id, product_id, change, quantity_after, reason)
                    values ($1, $2, $3, $4, $5)
                    """,
                    store_id,
                    product_id,
                    body.change,
                    int(row["quantity"]),
                    f"ADJUSTMENT:{body.reason}",
                )
                await conn.execute(
                    """
                    insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
                    values ($1, $2, 'INVENTORY_INCREASED', 'product', $3, $4, $5)
                    """,
                    store_id,
                    user["id"],
                    product_id,
                    json.dumps({"change": body.change, "reason": body.reason,
                                "quantity_after": int(row["quantity"])}),
                    f"Stock added: +{add} ({body.reason}): {prod['name']} → {row['quantity']}",
                )

    await intel.run_refresh(store_id)
    prod_row = await db.fetchrow(
        "select coalesce(i.quantity, 0) as quantity from products p "
        "left join inventory i on i.product_id = p.id and i.store_id = p.store_id "
        "where p.id=$1 and p.store_id=$2",
        product_id,
        store_id,
    )
    return {"product_id": product_id, "change": body.change,
            "quantity_after": int(prod_row["quantity"]) if prod_row else None}


@router.get("/settings")
async def get_store_settings(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return await intel.get_settings(user["store_id"])


@router.patch("/settings")
async def patch_store_settings(
    body: SettingsPatch, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    try:
        return await intel.update_settings(user["store_id"], user["id"], data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/refresh")
async def refresh(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return await intel.run_refresh(user["store_id"])
