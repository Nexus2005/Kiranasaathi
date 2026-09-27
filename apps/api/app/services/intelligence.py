"""Deterministic intelligence service — the AI-ready data layer.

Every function here returns structured data computed from actual database
records. No LLM involvement, no fabricated values. The future AI agent will
call these as tools; the API routers expose them as-is.

Conventions:
- Currency values are floats rounded to 2 decimals (INR).
- Velocity = average units/day over a configurable window.
- "None" means "insufficient data" — never a guessed number.
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any, Optional

from app.database import db

# ---------------------------------------------------------------- settings


async def get_settings(store_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        "select * from store_settings where store_id = $1", store_id
    )
    if not row:
        await db.execute(
            "insert into store_settings (store_id) values ($1) on conflict do nothing",
            store_id,
        )
        row = await db.fetchrow(
            "select * from store_settings where store_id = $1", store_id
        )
    return dict(row)


async def update_settings(store_id: str, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "min_margin_pct",
        "expiry_warning_days",
        "expiry_critical_days",
        "low_stock_days",
        "reorder_lead_time_days",
        "reorder_safety_days",
        "slow_moving_days",
        "overstock_days",
        "dead_stock_days",
        "velocity_window_days",
    )
    sets = [k for k in patch if k in allowed]
    if not sets:
        raise ValueError("No valid settings fields provided")
    cols = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(sets))
    await db.execute(
        f"update store_settings set {cols}, updated_at = now() where store_id = $1",
        store_id,
        *[patch[k] for k in sets],
    )
    await db.execute(
        """insert into activity_logs (store_id, user_id, event_type, entity_type, new_state, message)
           values ($1, $2, 'SETTINGS_UPDATED', 'store_settings', $3, $4)""",
        store_id,
        user_id,
        json.dumps({k: patch[k] for k in sets}),
        "Inventory/pricing settings updated",
    )
    return await get_settings(store_id)


# ---------------------------------------------------------------- primitives


async def _velocity(store_id: str, product_id: str, days: int = 30) -> float:
    """Average units sold per day over the trailing window. 0.0 when no sales."""
    row = await db.fetchrow(
        """
        select sum(si.quantity)::numeric / greatest($3, 1) as v
        from sale_items si
        join sales s on s.id = si.sale_id
        where s.store_id = $1 and si.product_id = $2
          and s.status = 'completed'
          and s.created_at >= current_date - greatest($3::int, 1)
        """,
        store_id,
        product_id,
        days,
    )
    return float(row["v"] or 0) if row and row["v"] is not None else 0.0


async def _velocity_series(store_id: str, product_id: str, windows: tuple[int, ...]) -> dict[str, Optional[float]]:
    out: dict[str, Optional[float]] = {}
    for w in windows:
        v = await _velocity(store_id, product_id, w)
        out[f"days_{w}"] = v if v > 0 else None
    return out


def _expiry_status(expiry_date, warn_days: int, crit_days: int) -> str:
    if expiry_date is None:
        return "NO_EXPIRY"
    if expiry_date < date_today():
        return "EXPIRED"
    days = (expiry_date - date_today()).days
    if days <= crit_days:
        return "EXPIRING_CRITICAL"
    if days <= warn_days:
        return "EXPIRING_SOON"
    return "HEALTHY_SHELF_LIFE"


def date_today():
    import datetime as _dt

    return _dt.date.today()


# ---------------------------------------------------------------- inventory


async def get_inventory_intelligence(store_id: str, q: Optional[str] = None) -> dict[str, Any]:
    """Full per-product intelligence list: computed in SQL, enriched in Python.

    One query — no N+1. Statuses from product_stock_status(), expiry from
    worst sellable batch, valuation from batch-weighted cost.
    """
    cfg = await get_settings(store_id)
    rows = await db.fetch(
        """
        with vel as (
          select si.product_id,
                 sum(si.quantity)::numeric / greatest($2, 1) as daily
          from sale_items si
          join sales s on s.id = si.sale_id
          where s.store_id = $1 and s.status = 'completed'
            and s.created_at >= current_date - greatest($2, 1)
          group by si.product_id
        ),
        batch_info as (
          select store_id, product_id,
                 sum(quantity) as batch_qty,
                 case when sum(quantity) > 0
                   then round(sum(quantity * purchase_cost) / sum(quantity), 2) end as avg_cost,
                 min(expiry_date) filter (where status = 'sellable' and expiry_date is not null) as next_expiry,
                 sum(quantity) filter (where status = 'expired' and quantity > 0) as expired_qty,
                 sum(quantity * purchase_cost) filter (where status = 'expired' and quantity > 0) as expired_value
          from inventory_batches
          where store_id = $1
          group by store_id, product_id
        )
        select p.id as product_id, p.name, p.category, p.sku, p.barcode, p.unit, p.brand,
               p.image_url,
               p.mrp, p.selling_price, p.purchase_price, p.reorder_level, p.target_stock, p.is_active,
               coalesce(i.quantity, 0) as quantity,
               p.selling_price - p.purchase_price as margin,
               case when p.selling_price > 0
                    then round(((p.selling_price - p.purchase_price) / p.selling_price * 100), 1)
                    else 0 end as margin_pct,
               v.daily as velocity,
               bi.batch_qty, bi.avg_cost, bi.next_expiry, bi.expired_qty, bi.expired_value,
               product_stock_status(
                 coalesce(i.quantity, 0), p.reorder_level, coalesce(v.daily, 0),
                 $4, $5, $6) as stock_status,
               days_of_stock(coalesce(i.quantity, 0), v.daily) as days_of_stock
        from products p
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        left join vel v on v.product_id = p.id
        left join batch_info bi on bi.product_id = p.id and bi.store_id = p.store_id
        where p.store_id = $1 and p.is_active
          and ($3::text is null or p.name ilike '%' || $3::text || '%'
               or p.sku ilike '%' || $3::text || '%' or p.category ilike '%' || $3::text || '%')
        order by p.name
        """,
        store_id,
        int(cfg["velocity_window_days"]),
        (q.strip() if q else None),
        int(cfg["slow_moving_days"]),
        int(cfg["overstock_days"]),
        int(cfg["dead_stock_days"]),
    )

    items: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        qty = int(d["quantity"] or 0)
        velocity = float(d["velocity"]) if d["velocity"] is not None else 0.0
        next_expiry = d["next_expiry"]
        expiry_status = _expiry_status(
            next_expiry, int(cfg["expiry_warning_days"]), int(cfg["expiry_critical_days"])
        )
        # Sellable qty excludes expired-batch quantity (they are separated).
        expired_qty = int(d["expired_qty"] or 0)
        sellable_qty = max(qty - expired_qty, 0)
        avg_cost = float(d["avg_cost"]) if d["avg_cost"] is not None else float(d["purchase_price"])

        # Valuation at batch-weighted COST + potential sales value at current price.
        cost_value = round(sellable_qty * avg_cost, 2)
        sales_value = round(sellable_qty * float(d["selling_price"]), 2)
        # At-risk value = value of stock whose expiry inside the warning window
        at_risk_value = 0.0
        if next_expiry and next_expiry >= date_today() and expiry_status in (
            "EXPIRING_SOON", "EXPIRING_CRITICAL"
        ):
            at_risk_value = cost_value

        reorder_point = max(
            int(d["reorder_level"] or 0),
            math.ceil(velocity * (int(cfg["reorder_lead_time_days"]) + int(cfg["reorder_safety_days"])))
            if velocity > 0 else 0,
        )
        reorder_required = velocity > 0 and qty <= reorder_point and sellable_qty > 0

        items.append({
            **d,
            "sellable_quantity": sellable_qty,
            "expired_quantity": expired_qty,
            "expiry_status": expiry_status,
            "days_to_expiry": (next_expiry - date_today()).days if next_expiry else None,
            "stock_value_cost": cost_value,
            "stock_value_sales": sales_value,
            "potential_margin": round(sales_value - cost_value, 2),
            "at_risk_value": at_risk_value,
            "reorder_point": reorder_point,
            "reorder_required": reorder_required,
            "last_purchase": d["next_expiry"],  # placeholder replaced below
        })

    # Last purchase per product (single extra grouped query — no N+1 per item)
    last_po = await db.fetch(
        """
        select pi.product_id, max(po.created_at) as last_purchase_at,
               max(pi.unit_cost) filter (where pi.unit_cost is not null) as last_cost
        from purchase_items pi
        join purchase_orders po on po.id = pi.purchase_order_id
        where po.store_id = $1 and po.status = 'received'
        group by pi.product_id
        """,
        store_id,
    )
    lp = {str(r["product_id"]): r for r in last_po}
    for it in items:
        row = lp.get(str(it["product_id"]))
        it["last_purchase_at"] = row["last_purchase_at"] if row else None
        it["last_purchase_cost"] = float(row["last_cost"]) if row and row["last_cost"] else None
        it.pop("last_purchase", None)

    # Summary
    total_cost = round(sum(i["stock_value_cost"] for i in items), 2)
    total_sales = round(sum(i["stock_value_sales"] for i in items), 2)
    at_risk = round(sum(i["at_risk_value"] for i in items), 2)
    expired_val = round(sum(float(i["expired_value"] or 0) for i in items), 2)
    dead = [i for i in items if i["stock_status"] == "DEAD_STOCK"]
    by_status: dict[str, int] = {}
    for i in items:
        by_status[i["stock_status"]] = by_status.get(i["stock_status"], 0) + 1

    return {
        "items": items,
        "count": len(items),
        "summary": {
            "total_cost_value": total_cost,
            "total_sales_value": total_sales,
            "potential_gross_margin": round(total_sales - total_cost, 2),
            "at_risk_value": at_risk,
            "expired_value": expired_val,
            "dead_stock_value": round(sum(i["stock_value_cost"] for i in dead), 2),
            "status_counts": by_status,
            "distinct_categories": len({i["category"] for i in items}),
        },
    }


async def get_low_stock_products(store_id: str) -> list[dict[str, Any]]:
    data = await get_inventory_intelligence(store_id)
    return [
        i for i in data["items"]
        if i["stock_status"] in ("LOW_STOCK", "CRITICAL_STOCK", "OUT_OF_STOCK")
    ]


async def get_overstocked_products(store_id: str) -> list[dict[str, Any]]:
    data = await get_inventory_intelligence(store_id)
    return [i for i in data["items"] if i["stock_status"] in ("OVERSTOCKED", "SLOW_MOVING")]


async def get_dead_stock(store_id: str) -> list[dict[str, Any]]:
    data = await get_inventory_intelligence(store_id)
    return [i for i in data["items"] if i["stock_status"] == "DEAD_STOCK"]


# ---------------------------------------------------------------- expiry


async def get_expiring_inventory(store_id: str) -> dict[str, Any]:
    """Batch-level expiry intelligence with velocity-based sell-through estimate.

    expected_sales_before_expiry is an ESTIMATE (velocity x days left) and is
    labelled as such. Expired batches are returned separately, never mixed
    with sellable stock.
    """
    cfg = await get_settings(store_id)
    warn, crit = int(cfg["expiry_warning_days"]), int(cfg["expiry_critical_days"])

    batches = await db.fetch(
        """
        select b.id, b.batch_no, b.quantity, b.purchase_cost, b.expiry_date,
               b.status, b.received_at,
               p.id as product_id, p.name as product_name, p.category,
               p.selling_price
        from inventory_batches b
        join products p on p.id = b.product_id
        where b.store_id = $1
          and b.quantity > 0
          and b.expiry_date is not null
        order by b.expiry_date
        """,
        store_id,
    )

    today = date_today()
    sellable: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []

    for b in batches:
        d = dict(b)
        d["days_to_expiry"] = (d["expiry_date"] - today).days
        if d["status"] == "expired" or d["expiry_date"] < today:
            d["status"] = d["status"] if d["status"] == "expired" else "expired"
            d["risk_value"] = round(float(d["quantity"]) * float(d["purchase_cost"]), 2)
            d["recommendation"] = "Remove from shelf. Expired stock must not be sold."
            expired.append(d)
            continue

        velocity = await _velocity(store_id, str(b["product_id"]), 30)
        expected = round(velocity * d["days_to_expiry"], 1)
        excess = round(max(float(b["quantity"]) - expected, 0), 1)
        d["velocity"] = round(velocity, 2)
        d["expected_sales_before_expiry_estimate"] = expected
        d["estimated_excess_units"] = excess
        d["at_risk_cost_value"] = round(excess * float(d["purchase_cost"]), 2)
        d["at_risk_sales_value"] = round(excess * float(d["selling_price"]), 2)

        if d["days_to_expiry"] <= crit:
            d["expiry_status"] = "EXPIRING_CRITICAL"
            d["severity"] = "critical"
        elif d["days_to_expiry"] <= warn:
            d["expiry_status"] = "EXPIRING_SOON"
            d["severity"] = "warning"
        else:
            d["expiry_status"] = "HEALTHY_SHELF_LIFE"
            d["severity"] = "info"

        # Deterministic rescue recommendation — merchant must approve any pricing change.
        if excess <= 0:
            d["recommendation"] = "Continue normal sale — expected to sell through before expiry (estimate)."
        elif velocity <= 0:
            d["recommendation"] = (
                "No recent sales recorded. Promote or bundle this batch; "
                "reliability is low without sales history."
            )
        elif excess <= max(float(b["quantity"]) * 0.25, 2):
            d["recommendation"] = "Discount mildly or prioritize placement to clear the small excess."
        else:
            d["recommendation"] = (
                "Significant excess expected: discount (respecting minimum price), "
                "bundle with fast movers, and pause replenishment for this product."
            )
        sellable.append(d)

    at_risk_value = round(
        sum(b["at_risk_cost_value"] for b in sellable if b["severity"] in ("warning", "critical")), 2
    )
    expired_value = round(sum(e["risk_value"] for e in expired), 2)

    return {
        "sellable_batches": [b for b in sellable if b["severity"] in ("warning", "critical")],
        "watch_batches": [b for b in sellable if b["severity"] == "info"],
        "expired_batches": expired,
        "summary": {
            "at_risk_cost_value": at_risk_value,
            "expired_value": expired_value,
            "expired_batch_count": len(expired),
            "at_risk_batch_count": len([b for b in sellable if b["severity"] in ("warning", "critical")]),
        },
    }


# ---------------------------------------------------------------- pricing


async def calculate_product_margin(store_id: str, product_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        select p.id, p.name, p.selling_price, p.purchase_price, p.mrp
        from products p
        where p.id = $1 and p.store_id = $2
        """,
        product_id,
        store_id,
    )
    if not row:
        raise ValueError("Product not found")
    sp, cp = float(row["selling_price"]), float(row["purchase_price"])
    profit = round(sp - cp, 2)
    margin_pct = round(profit / sp * 100, 1) if sp > 0 else None
    markup_pct = round(profit / cp * 100, 1) if cp > 0 else None
    floor_price = await min_acceptable_price(store_id, product_id)
    return {
        "product_id": str(row["id"]),
        "name": row["name"],
        "purchase_cost": cp,
        "selling_price": sp,
        "mrp": float(row["mrp"]) if row["mrp"] else None,
        "gross_profit": profit,
        "gross_margin_pct": margin_pct,
        "markup_pct": markup_pct,
        "minimum_acceptable_price": floor_price,
        "at_minimum_margin_price": floor_price,
    }


async def min_acceptable_price(store_id: str, product_id: str) -> Optional[float]:
    """Minimum permitted selling price per the store's pricing policy.

    Policy (transparent, deterministic):
    - Base: price must yield at least store_settings.min_margin_pct margin on price.
    - Clearance exception: if a sellable batch expires within the warning
      window AND expected sell-through (velocity x days left) < batch qty,
      breakeven is allowed so the merchant can rescue the stock.
    Returns None only if the product doesn't exist (callers raise).
    """
    cfg = await get_settings(store_id)
    row = await db.fetchrow(
        "select purchase_price from products where id=$1 and store_id=$2",
        product_id,
        store_id,
    )
    if not row:
        return None
    cost = float(row["purchase_price"])
    min_margin = float(cfg["min_margin_pct"])
    warn_days = int(cfg["expiry_warning_days"])

    floor = math.ceil(cost / (1 - min_margin / 100.0)) if min_margin < 100 else cost

    near = await db.fetchrow(
        """
        select b.quantity, b.expiry_date
        from inventory_batches b
        where b.store_id = $1 and b.product_id = $2 and b.status = 'sellable'
          and b.quantity > 0 and b.expiry_date is not null
          and b.expiry_date <= current_date + ($3::int)
        order by b.expiry_date
        limit 1
        """,
        store_id,
        product_id,
        warn_days,
    )
    if near:
        days_left = max((near["expiry_date"] - date_today()).days, 0)
        velocity = await _velocity(store_id, product_id, 30)
        expected = velocity * days_left
        if float(near["quantity"]) > expected:
            return math.floor(cost)  # breakeven clearance allowed
    return float(floor)


async def calculate_discount_impact(store_id: str, product_id: str, proposed_price: float) -> dict[str, Any]:
    """Margin impact of selling one unit (or the whole sellable stock) at a proposed price."""
    m = await calculate_product_margin(store_id, product_id)
    floor = await min_acceptable_price(store_id, product_id)
    proposed = round(proposed_price, 2)
    unit_profit = round(proposed - m["purchase_cost"], 2)
    unit_margin_pct = round(unit_profit / proposed * 100, 1) if proposed > 0 else None

    inv = await db.fetchrow(
        """
        select coalesce(i.quantity,0) as qty,
               coalesce((select sum(b.quantity) from inventory_batches b
                 where b.store_id = $1 and b.product_id = $2 and b.status='expired' and b.quantity > 0), 0) as expired_qty
        from products p
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        where p.id = $2 and p.store_id = $1
        """,
        store_id,
        product_id,
    )
    qty = max(int(inv["qty"] or 0) - int(inv["expired_qty"] or 0), 0) if inv else 0

    return {
        **m,
        "proposed_price": proposed,
        "allowed": floor is not None and proposed >= floor,
        "minimum_acceptable_price": floor,
        "unit_profit_at_proposed": unit_profit,
        "unit_margin_pct_at_proposed": unit_margin_pct,
        "margin_delta_vs_current": round((unit_margin_pct - m["gross_margin_pct"]), 1)
        if unit_margin_pct is not None and m["gross_margin_pct"] is not None else None,
        "total_profit_if_clear_all": round(unit_profit * qty, 2),
        "units_in_stock": qty,
    }


async def calculate_cart_margin(store_id: str, cart: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic margin check for a cart: [{product_id, quantity, unit_price?}]."""
    lines = []
    total_revenue = 0.0
    total_cost = 0.0
    for line in cart:
        pid = str(line["product_id"])
        qty = int(line["quantity"])
        m = await calculate_product_margin(store_id, pid)
        unit_price = float(line.get("unit_price") or m["selling_price"])
        revenue = round(unit_price * qty, 2)
        cost = round(m["purchase_cost"] * qty, 2)
        total_revenue += revenue
        total_cost += cost
        lines.append({
            "product_id": pid,
            "name": m["name"],
            "quantity": qty,
            "unit_price": unit_price,
            "unit_cost": m["purchase_cost"],
            "line_revenue": revenue,
            "line_cost": cost,
            "line_profit": round(revenue - cost, 2),
            "line_margin_pct": round((revenue - cost) / revenue * 100, 1) if revenue > 0 else None,
        })
    total_revenue, total_cost = round(total_revenue, 2), round(total_cost, 2)
    return {
        "lines": lines,
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "total_profit": round(total_revenue - total_cost, 2),
        "total_margin_pct": round((total_revenue - total_cost) / total_revenue * 100, 1)
        if total_revenue > 0 else None,
    }


# ---------------------------------------------------------------- bargaining


async def calculate_bargain(store_id: str, product_id: str, offer: float, quantity: int = 1) -> dict[str, Any]:
    """Deterministic accept / counter / reject recommendation.

    Rules (all from store settings — no invented numbers):
    - offer < breakeven (cost)                      -> REJECT
    - offer < min_acceptable_price                  -> COUNTER at floor
    - offer within 3% below floor (already >= floor) -> ACCEPT at offer
    - offer >= current selling price                -> invalid (never sell above price)
    """
    m = await calculate_product_margin(store_id, product_id)
    floor = await min_acceptable_price(store_id, product_id)
    cfg = await get_settings(store_id)
    current_price = m["selling_price"]
    offer = round(offer, 2)
    qty = max(int(quantity), 1)

    if offer > current_price:
        return {
            "decision": "INVALID",
            "reason": f"Offer ₹{offer} is above the current selling price ₹{current_price}.",
            "product_id": product_id,
            "quantity": qty,
            "current_price": current_price,
            "offer": offer,
            "minimum_acceptable_price": floor,
        }

    if offer < m["purchase_cost"]:
        return {
            "decision": "REJECT",
            "reason": (
                f"₹{offer} is below your purchase cost (₹{m['purchase_cost']}). "
                "Selling at this price loses money on every unit."
            ),
            "product_id": product_id,
            "quantity": qty,
            "current_price": current_price,
            "offer": offer,
            "purchase_cost": m["purchase_cost"],
            "minimum_acceptable_price": floor,
            "counteroffer": floor,
        }

    if offer < floor:
        return {
            "decision": "COUNTER",
            "reason": (
                f"₹{offer} would leave a margin below your configured minimum "
                f"({float(cfg['min_margin_pct'])}%). Suggested counteroffer: ₹{floor}."
            ),
            "product_id": product_id,
            "quantity": qty,
            "current_price": current_price,
            "offer": offer,
            "purchase_cost": m["purchase_cost"],
            "minimum_acceptable_price": floor,
            "counteroffer": floor,
            "margin_at_offer_pct": round((offer - m["purchase_cost"]) / offer * 100, 1) if offer > 0 else None,
            "margin_at_counteroffer_pct": round((floor - m["purchase_cost"]) / floor * 100, 1),
        }

    # offer >= floor: acceptable. Near-floor offers get an "accept but nudge" hint.
    near_floor = offer < floor * 1.03
    return {
        "decision": "ACCEPT",
        "reason": (
            f"₹{offer} clears your minimum acceptable price (₹{floor}). "
            + ("Margin is thin — accepting is fine, but a small nudge up may be worth it."
               if near_floor else
               f"Margin at this price: {round((offer - m['purchase_cost']) / offer * 100, 1)}%.")
        ),
        "product_id": product_id,
        "quantity": qty,
        "current_price": current_price,
        "offer": offer,
        "purchase_cost": m["purchase_cost"],
        "minimum_acceptable_price": floor,
        "margin_at_offer_pct": round((offer - m["purchase_cost"]) / offer * 100, 1) if offer > 0 else None,
        "near_floor": near_floor,
        "total_for_quantity": round(offer * qty, 2),
    }


# ---------------------------------------------------------------- reorder


async def calculate_reorder(store_id: str, product_id: str) -> dict[str, Any]:
    """Transparent reorder math from actual data. 'insufficient_data' when no history."""
    cfg = await get_settings(store_id)
    row = await db.fetchrow(
        """
        select p.id, p.name, p.reorder_level, p.target_stock, p.purchase_price,
               coalesce(i.quantity, 0) as qty,
               (select coalesce(sum(b.quantity), 0) from inventory_batches b
                 where b.store_id = $1 and b.product_id = p.id and b.status='expired' and b.quantity > 0) as expired_qty
        from products p
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        where p.id = $2 and p.store_id = $1
        """,
        store_id,
        product_id,
    )
    if not row:
        raise ValueError("Product not found")

    velocity = await _velocity(store_id, product_id, int(cfg["velocity_window_days"]))
    lead = int(cfg["reorder_lead_time_days"])
    safety = int(cfg["reorder_safety_days"])
    qty = int(row["qty"] or 0) - int(row["expired_qty"] or 0)
    target = int(row["target_stock"]) if row["target_stock"] else None

    if velocity <= 0:
        return {
            "product_id": product_id,
            "name": row["name"],
            "reorder_required": False,
            "status": "insufficient_data",
            "message": "More sales data is needed for a reliable demand estimate.",
            "current_stock": qty,
            "lead_time_days": lead,
        }

    days_cover = qty / velocity
    reorder_point = max(int(row["reorder_level"] or 0), math.ceil(velocity * (lead + safety)))
    lead_demand = math.ceil(velocity * lead)
    safety_stock = math.ceil(velocity * safety)
    suggested_qty = max(0, (target or (reorder_point + lead_demand + safety_stock)) - qty)

    # Pending PO quantity already on the way for this product
    pending = await db.fetchval(
        """
        select coalesce(sum(pi.quantity), 0)
        from purchase_items pi
        join purchase_orders po on po.id = pi.purchase_order_id
        where po.store_id = $1 and po.status = 'pending' and pi.product_id = $2
        """,
        store_id,
        product_id,
    )
    pending_qty = int(pending or 0)

    reorder_required = days_cover <= (lead + safety) and pending_qty == 0
    # Preferred supplier: lowest recent purchase cost with sufficient data, else last supplier.
    supplier = await db.fetchrow(
        """
        select s.id, s.name, pi.unit_cost, po.created_at
        from purchase_items pi
        join purchase_orders po on po.id = pi.purchase_order_id
        join suppliers s on s.id = po.supplier_id
        where po.store_id = $1 and pi.product_id = $2 and po.status = 'received'
        order by po.created_at desc
        limit 1
        """,
        store_id,
        product_id,
    )

    if not reorder_required:
        message = (
            f"Stock is healthy: ~{round(days_cover, 1)} days of cover at "
            f"{round(velocity, 1)} units/day (reorder point {reorder_point})."
            + (f" {pending_qty} unit(s) already on order." if pending_qty else "")
        )
    else:
        message = (
            f"Stock is expected to fall below the reorder point ({reorder_point}) within "
            f"~{max(round(days_cover), 0)} day(s). Recommended replenishment: {suggested_qty} units."
        )

    return {
        "product_id": product_id,
        "name": row["name"],
        "reorder_required": reorder_required,
        "status": "ok",
        "current_stock": qty,
        "velocity_per_day": round(velocity, 2),
        "days_of_stock": round(days_cover, 1),
        "reorder_point": reorder_point,
        "lead_time_days": lead,
        "safety_stock_days": safety,
        "lead_demand_estimate": lead_demand,
        "safety_stock": safety_stock,
        "target_stock": target,
        "pending_po_quantity": pending_qty,
        "recommended_quantity": suggested_qty if reorder_required else 0,
        "estimated_purchase_cost": round(suggested_qty * float(row["purchase_price"]), 2)
        if reorder_required else 0.0,
        "preferred_supplier": dict(supplier) if supplier else None,
        "reason": message,
    }


# ---------------------------------------------------------------- suppliers


async def get_supplier_comparison(store_id: str, product_id: str) -> dict[str, Any]:
    """Compare suppliers for one product from ACTUAL purchase history only.

    No invented savings or scores. With <1 purchase per supplier the outcome
    is an explicit 'insufficient data' result.
    """
    rows = await db.fetch(
        """
        select s.id as supplier_id, s.name, s.lead_time_days, s.payment_terms, s.is_active,
               count(pi.id) as purchases,
               min(pi.unit_cost) as min_cost, max(pi.unit_cost) as max_cost,
               avg(pi.unit_cost) as avg_cost,
               max(po.created_at) as last_purchase_at,
               sum(pi.quantity) as total_units
        from suppliers s
        left join purchase_orders po on po.supplier_id = s.id and po.store_id = s.store_id
             and po.status = 'received'
        left join purchase_items pi on pi.purchase_order_id = po.id and pi.product_id = $2
        where s.store_id = $1 and s.is_active
        group by s.id, s.name, s.lead_time_days, s.payment_terms, s.is_active
        order by s.name
        """,
        store_id,
        product_id,
    )

    options: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        purchases = int(d["purchases"] or 0)
        if purchases == 0 or d["avg_cost"] is None:
            d["comparison_status"] = "insufficient_data"
            d["message"] = "Not enough purchase history to compare this supplier."
            d["avg_cost"] = None
        else:
            d["comparison_status"] = "ok"
            d["avg_cost"] = round(float(d["avg_cost"]), 2)
        options.append(d)

    with_data = [o for o in options if o["comparison_status"] == "ok"]
    best = None
    if with_data:
        best = min(with_data, key=lambda o: (o["avg_cost"], o["lead_time_days"] or 999))
        best = {"supplier_id": str(best["supplier_id"]), "name": best["name"],
                "avg_cost": best["avg_cost"]}

    product = await db.fetchrow(
        "select id, name, purchase_price from products where id=$1 and store_id=$2",
        product_id,
        store_id,
    )
    if not product:
        raise ValueError("Product not found")

    return {
        "product": {"id": str(product["id"]), "name": product["name"],
                    "current_purchase_price": float(product["purchase_price"])},
        "options": options,
        "best_fit": best,
        "note": (
            "Best fit = lowest average historical cost among suppliers with data. "
            "Unit price is not the only factor — lead time, MOQ and reliability matter."
            if best else
            "Not enough purchase history to compare supplier performance."
        ),
    }


async def get_supplier_profile(store_id: str, supplier_id: str) -> dict[str, Any]:
    sup = await db.fetchrow(
        "select * from suppliers where id = $1 and store_id = $2", supplier_id, store_id
    )
    if not sup:
        raise ValueError("Supplier not found")

    stats = await db.fetchrow(
        """
        select count(po.id) as order_count,
               coalesce(sum(po.total_amount), 0) as total_value,
               max(po.created_at) as last_purchase_at
        from purchase_orders po
        where po.supplier_id = $1 and po.store_id = $2 and po.status = 'received'
        """,
        supplier_id,
        store_id,
    )
    products = await db.fetch(
        """
        select p.id, p.name, avg(pi.unit_cost) as avg_cost, count(*) as purchases,
               max(po.created_at) as last_purchase_at
        from purchase_items pi
        join purchase_orders po on po.id = pi.purchase_order_id
        join products p on p.id = pi.product_id
        where po.supplier_id = $1 and po.store_id = $2 and po.status = 'received'
        group by p.id, p.name
        order by purchases desc
        """,
        supplier_id,
        store_id,
    )
    orders = await db.fetch(
        """
        select po.id, po.status, po.total_amount, po.created_at, po.received_at,
               (select count(*) from purchase_items pi where pi.purchase_order_id = po.id) as line_count
        from purchase_orders po
        where po.supplier_id = $1 and po.store_id = $2
        order by po.created_at desc
        limit 20
        """,
        supplier_id,
        store_id,
    )

    order_count = int(stats["order_count"] or 0)
    d = dict(sup)
    d["order_count"] = order_count
    d["total_purchase_value"] = float(stats["total_value"] or 0)
    d["last_purchase_at"] = stats["last_purchase_at"]
    d["has_history"] = order_count > 0
    if order_count == 0:
        d["performance_note"] = "Not enough purchase history to evaluate this supplier."
    return {
        **d,
        "products_supplied": [dict(p) for p in products],
        "recent_orders": [dict(o) for o in orders],
    }


# ---------------------------------------------------------------- risk summary


async def get_inventory_risk_summary(store_id: str) -> dict[str, Any]:
    """Aggregate risk snapshot for dashboards + future AI agent context."""
    inv = await get_inventory_intelligence(store_id)
    expiry = await get_expiring_inventory(store_id)
    items = inv["items"]

    low = [i for i in items if i["stock_status"] in ("LOW_STOCK", "CRITICAL_STOCK", "OUT_OF_STOCK")]
    reorder = [
        {"product_id": i["product_id"], "name": i["name"], "reorder_point": i["reorder_point"],
         "current_stock": i["sellable_quantity"]}
        for i in items if i["reorder_required"]
    ]

    return {
        "inventory": inv["summary"],
        "expiry": expiry["summary"],
        "alerts": {
            "low_stock_count": len(low),
            "reorder_required_count": len(reorder),
            "expiry_risk_batches": expiry["summary"]["at_risk_batch_count"],
            "expired_batches": expiry["summary"]["expired_batch_count"],
        },
        "reorder_list": reorder,
        "valuation_note": "Values use batch-weighted purchase COST for stock value; sales value uses current selling price.",
    }


async def get_product_cost_history(store_id: str, product_id: str) -> dict[str, Any]:
    """Purchase cost + selling price history (never overwritten)."""
    cost = await db.fetch(
        """
        select h.field, h.old_value, h.new_value, h.reason, h.created_at,
               u.full_name as changed_by
        from product_price_history h
        left join users u on u.id = h.changed_by
        where h.store_id = $1 and h.product_id = $2
        order by h.created_at desc
        """,
        store_id,
        product_id,
    )
    # Fallback: purchases older than the history table
    purchases = await db.fetch(
        """
        select po.created_at, pi.unit_cost, po.invoice_no, s.name as supplier_name
        from purchase_items pi
        join purchase_orders po on po.id = pi.purchase_order_id
        left join suppliers s on s.id = po.supplier_id
        where po.store_id = $1 and pi.product_id = $2 and po.status = 'received'
        order by po.created_at desc
        """,
        store_id,
        product_id,
    )
    return {
        "changes": [dict(c) for c in cost],
        "purchases": [dict(p) for p in purchases],
    }


async def run_refresh(store_id: str) -> dict[str, Any]:
    """Trigger the state-based intelligence refresh (alerts, expiries) — idempotent."""
    raw = await db.fetchval("select refresh_inventory_intelligence($1)", store_id)
    return json.loads(raw) if isinstance(raw, str) else (raw or {})
