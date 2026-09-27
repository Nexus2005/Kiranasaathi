"""Read-only AI tools — structured data over the merchant's own store.

Every tool is store-scoped by the authenticated user's store_id (never by a
client/model-supplied id) and reuses the Phase 2 deterministic engines.
Tools return JSON-serializable structured data; the orchestrator/LLM never
queries the database directly.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.database import db
from app.services import intelligence as intel
from app.services import demand as demand_service
from app.services import festivals as fest_service
from app.services import customers_intel as cust_intel
from app.services import marketing as marketing_service
from app.services import evidence as evidence_service
from app.services import orders as orders_service
from app.services import payments as payments_service


def _j(value: Any) -> Any:
    """Recursively make values JSON-safe (Decimal/date/datetime -> str/float)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _j(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_j(v) for v in value]
    if hasattr(value, "__float__"):
        return float(value)
    return str(value)


def _rows(rows: Any) -> list[dict[str, Any]]:
    return [_j(dict(r)) for r in rows]


# ---------------------------------------------------------------- store


async def get_store_summary(store_id: str) -> dict[str, Any]:
    store = await db.fetchrow(
        "select id, name, location, currency, created_at from stores where id = $1",
        store_id,
    )
    cfg = await intel.get_settings(store_id)
    risk = await intel.get_inventory_risk_summary(store_id)
    sales_today = await db.fetchrow(
        """
        select coalesce(sum(total), 0) as sales, count(*) as orders,
               coalesce(sum(si.quantity * (si.unit_price - si.unit_cost)), 0) as gross_profit
        from sales s
        left join sale_items si on si.sale_id = s.id
        where s.store_id = $1 and s.status = 'completed' and s.created_at >= current_date
        """,
        store_id,
    )
    alerts = await db.fetch(
        """
        select type, severity, count(*) as count
        from alerts where store_id = $1 and status = 'open'
        group by type, severity order by count(*) desc limit 8
        """,
        store_id,
    )
    return _j({
        "store": dict(store) if store else None,
        "today": dict(sales_today) if sales_today else {"sales": 0, "orders": 0, "gross_profit": 0},
        "inventory_risk": risk,
        "pricing_rules": {
            "min_margin_pct": float(cfg["min_margin_pct"]),
            "expiry_warning_days": int(cfg["expiry_warning_days"]),
            "expiry_critical_days": int(cfg["expiry_critical_days"]),
            "reorder_lead_time_days": int(cfg["reorder_lead_time_days"]),
            "reorder_safety_days": int(cfg["reorder_safety_days"]),
        },
        "open_alerts_by_type": _rows(alerts),
    })


async def get_today_sales(store_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        select
          coalesce(sum(s.total), 0) as sales,
          count(distinct s.id) as orders,
          coalesce(avg(s.total), 0) as avg_order_value,
          coalesce(sum(si.quantity * (si.unit_price - si.unit_cost)), 0) as gross_profit
        from sales s
        left join sale_items si on si.sale_id = s.id
        where s.store_id = $1 and s.status = 'completed' and s.created_at >= current_date
        """,
        store_id,
    )
    yesterday = await db.fetchrow(
        """
        select coalesce(sum(total), 0) as sales, count(*) as orders
        from sales s
        where s.store_id = $1 and s.status = 'completed'
          and s.created_at >= current_date - 1 and s.created_at < current_date
        """,
        store_id,
    )
    top = await db.fetch(
        """
        select p.name, sum(si.quantity) as units, sum(si.line_total) as revenue
        from sale_items si
        join sales s on s.id = si.sale_id
        join products p on p.id = si.product_id
        where s.store_id = $1 and s.status = 'completed' and s.created_at >= current_date
        group by p.name order by revenue desc limit 5
        """,
        store_id,
    )
    return _j({
        "today": dict(row) if row else {},
        "yesterday": dict(yesterday) if yesterday else {},
        "top_products_today": _rows(top),
    })


async def get_sales_summary(store_id: str, days: int = 7) -> dict[str, Any]:
    days = max(1, min(int(days), 90))
    daily = await db.fetch(
        """
        select d::date as day,
               coalesce(sum(s.total) filter (where s.id is not null), 0) as sales,
               coalesce(count(s.id), 0) as orders
        from generate_series(current_date - $2::int, current_date - 1, interval '1 day') d
        left join sales s
          on s.created_at >= d and s.created_at < d + interval '1 day'
         and s.store_id = $1 and s.status = 'completed'
        group by d order by d
        """,
        store_id,
        days,
    )
    week = await db.fetchrow(
        """
        select
          (select coalesce(sum(total), 0) from sales
            where store_id = $1 and status = 'completed'
              and created_at >= current_date - $2::int) as current_sales,
          (select coalesce(sum(total), 0) from sales
            where store_id = $1 and status = 'completed'
              and created_at >= current_date - 2 * $2::int
              and created_at < current_date - $2::int) as previous_sales
        """,
        store_id,
        days,
    )
    return _j({
        "window_days": days,
        "current_sales": float(week["current_sales"] or 0) if week else 0,
        "previous_sales": float(week["previous_sales"] or 0) if week else 0,
        "daily": _rows(daily),
    })


async def get_product_sales_history(store_id: str, product_id: str, days: int = 14) -> dict[str, Any]:
    row = await db.fetchrow(
        "select id, name, unit from products where id = $1 and store_id = $2",
        product_id,
        store_id,
    )
    if not row:
        raise ValueError("Product not found")
    daily = await db.fetch(
        """
        select d::date as day, coalesce(sum(si.quantity), 0) as units,
               coalesce(sum(si.line_total), 0) as revenue
        from generate_series(current_date - $3::int, current_date - 1, interval '1 day') d
        left join sales s
          on s.created_at >= d and s.created_at < d + interval '1 day'
         and s.store_id = $1 and s.status = 'completed'
        left join sale_items si on si.sale_id = s.id and si.product_id = $2
        group by d order by d
        """,
        store_id,
        product_id,
        max(1, min(days, 90)),
    )
    v30 = await intel._velocity(store_id, product_id, 30)
    v7 = await intel._velocity(store_id, product_id, 7)
    return _j({
        "product": dict(row),
        "daily": _rows(daily),
        "velocity_30d_per_day": round(v30, 2),
        "velocity_7d_per_day": round(v7, 2),
        "has_history": float(v30) > 0,
    })


# ---------------------------------------------------------------- inventory


async def get_inventory_health(store_id: str) -> dict[str, Any]:
    return await intel.get_inventory_intelligence(store_id)


async def get_low_stock_products(store_id: str) -> list[dict[str, Any]]:
    return _j(await intel.get_low_stock_products(store_id))


async def get_critical_stock_products(store_id: str) -> list[dict[str, Any]]:
    data = await intel.get_inventory_intelligence(store_id)
    return _j([
        i for i in data["items"] if i["stock_status"] in ("CRITICAL_STOCK", "OUT_OF_STOCK")
    ])


async def get_expiring_inventory(store_id: str) -> dict[str, Any]:
    return _j(await intel.get_expiring_inventory(store_id))


async def get_overstocked_products(store_id: str) -> list[dict[str, Any]]:
    return _j(await intel.get_overstocked_products(store_id))


async def get_dead_stock(store_id: str) -> list[dict[str, Any]]:
    return _j(await intel.get_dead_stock(store_id))


async def get_reorder_candidates(store_id: str) -> list[dict[str, Any]]:
    data = await intel.get_inventory_intelligence(store_id)
    out = []
    for i in data["items"]:
        if not i["reorder_required"]:
            continue
        r = await intel.calculate_reorder(store_id, i["product_id"])
        if r.get("reorder_required"):
            out.append(r)
    out.sort(key=lambda r: r.get("days_of_stock") if r.get("days_of_stock") is not None else 999)
    return _j(out)


async def get_product_details(store_id: str, product_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        select p.*, coalesce(i.quantity, 0) as quantity
        from products p
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        where p.id = $1 and p.store_id = $2
        """,
        product_id,
        store_id,
    )
    if not row:
        raise ValueError("Product not found")
    batches = await db.fetch(
        """
        select id, batch_no, quantity, purchase_cost, expiry_date, status, received_at
        from inventory_batches
        where store_id = $1 and product_id = $2 and quantity > 0
        order by expiry_date nulls last, received_at
        """,
        store_id,
        product_id,
    )
    return _j({**dict(row), "batches": _rows(batches)})


async def get_product_cost_history(store_id: str, product_id: str) -> dict[str, Any]:
    return _j(await intel.get_product_cost_history(store_id, product_id))


async def get_product_margin(store_id: str, product_id: str) -> dict[str, Any]:
    return _j(await intel.calculate_product_margin(store_id, product_id))


async def calculate_discount_impact(store_id: str, product_id: str, proposed_price: float) -> dict[str, Any]:
    return _j(await intel.calculate_discount_impact(store_id, product_id, float(proposed_price)))


async def calculate_bargain(store_id: str, product_id: str, customer_offer: float, quantity: int = 1) -> dict[str, Any]:
    return _j(await intel.calculate_bargain(store_id, product_id, float(customer_offer), int(quantity)))


async def calculate_reorder(store_id: str, product_id: str) -> dict[str, Any]:
    return _j(await intel.calculate_reorder(store_id, product_id))


async def get_supplier_comparison(store_id: str, product_id: str, quantity: int = 1) -> dict[str, Any]:
    cmp = await intel.get_supplier_comparison(store_id, product_id)
    cmp["quantity_requested"] = int(quantity)
    for o in cmp.get("options", []):
        if o.get("avg_cost") is not None:
            o["estimated_total_for_quantity"] = round(float(o["avg_cost"]) * int(quantity), 2)
    return _j(cmp)


async def get_supplier_details(store_id: str, supplier_id: str) -> dict[str, Any]:
    try:
        return _j(await intel.get_supplier_profile(store_id, supplier_id))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


# ---------------------------------------------------------------- customers


async def get_customer_summary(store_id: str) -> dict[str, Any]:
    totals = await db.fetchrow(
        """
        select count(*) as customers,
               count(*) filter (where phone is not null) as with_phone
        from customers where store_id = $1
        """,
        store_id,
    )
    recent = await db.fetch(
        """
        select c.id, c.name, c.phone,
               count(s.id) as orders,
               coalesce(sum(s.total), 0) as total_spent,
               max(s.created_at) as last_purchase
        from customers c
        left join sales s on s.customer_id = c.id and s.status = 'completed'
        where c.store_id = $1
        group by c.id, c.name, c.phone
        order by coalesce(max(s.created_at), c.created_at) desc
        limit 10
        """,
        store_id,
    )
    lapsing = await db.fetch(
        """
        select c.id, c.name, max(s.created_at) as last_purchase,
               count(s.id) as orders, coalesce(sum(s.total), 0) as total_spent
        from customers c
        join sales s on s.customer_id = c.id and s.status = 'completed'
        where c.store_id = $1
        group by c.id, c.name
        having max(s.created_at) < current_date - 14
        order by max(s.created_at)
        limit 10
        """,
        store_id,
    )
    return _j({
        "totals": dict(totals) if totals else {},
        "recent_customers": _rows(recent),
        "not_seen_recently_14d": _rows(lapsing),
    })


async def get_recent_customers(store_id: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        select c.id, c.name, c.phone, max(s.created_at) as last_purchase,
               coalesce(sum(s.total), 0) as total_spent
        from customers c
        left join sales s on s.customer_id = c.id and s.status = 'completed'
        where c.store_id = $1
        group by c.id, c.name, c.phone
        order by max(s.created_at) desc nulls last
        limit $2
        """,
        store_id,
        max(1, min(int(limit), 50)),
    )
    return _rows(rows)


async def get_customer_purchase_history(store_id: str, customer_id: str) -> dict[str, Any]:
    cust = await db.fetchrow(
        "select id, name, phone from customers where id = $1 and store_id = $2",
        customer_id,
        store_id,
    )
    if not cust:
        raise ValueError("Customer not found")
    sales = await db.fetch(
        """
        select s.id, s.total, s.created_at,
               coalesce(
                 (select json_agg(json_build_object('name', p.name, 'quantity', si.quantity))
                  from sale_items si join products p on p.id = si.product_id
                  where si.sale_id = s.id), '[]'::json) as items
        from sales s
        where s.customer_id = $1 and s.store_id = $2 and s.status = 'completed'
        order by s.created_at desc limit 20
        """,
        customer_id,
        store_id,
    )
    items = _rows(sales)
    for it in items:
        if isinstance(it.get("items"), str):
            it["items"] = json.loads(it["items"])
    return _j({"customer": dict(cust), "sales": items})


# ---------------------------------------------------------------- events & alerts


async def get_alerts(store_id: str, status: str = "open") -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        select id, type, severity, title, description, status, created_at
        from alerts where store_id = $1 and ($2::text is null or status = $2::text)
        order by case severity when 'critical' then 0 when 'warning' then 1 else 2 end,
                 created_at desc
        limit 30
        """,
        store_id,
        status if status in ("open", "acknowledged", "resolved") else None,
    )
    return _rows(rows)


async def get_business_events(store_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        select event_type, entity_type, message, created_at
        from activity_logs where store_id = $1
        order by created_at desc limit $2
        """,
        store_id,
        max(1, min(int(limit), 100)),
    )
    return _rows(rows)


async def get_activity_history(store_id: str, limit: int = 30) -> list[dict[str, Any]]:
    return await get_business_events(store_id, limit)


FESTIVALS = [
    {"name": "Diwali", "month": 11, "day": 8, "note": "Gift boxes, sweets, dry fruits sell high"},
    {"name": "Raksha Bandhan", "month": 8, "day": 9, "note": "Sweets, gift packs, roli/moli"},
    {"name": "Ganesh Chaturthi", "month": 9, "day": 6, "note": "Modak ingredients, coconuts, flowers"},
    {"name": "Navratri", "month": 10, "day": 3, "note": "Fasting foods: sabudana, fruits, makhana"},
    {"name": "Holi", "month": 3, "day": 14, "note": "Colours, sweets, thandai ingredients"},
    {"name": "Pongal / Makar Sankranti", "month": 1, "day": 14, "note": "Sugarcane, sesame, rice"},
]


async def get_festival_opportunities(store_id: str, horizon_days: int = 30) -> list[dict[str, Any]]:
    """Static festival calendar cross-checked against actual inventory categories."""
    from datetime import date, timedelta

    today = date.today()
    horizon = today + timedelta(days=int(horizon_days))
    categories = await db.fetch(
        "select distinct category from products where store_id = $1 and is_active",
        store_id,
    )
    cat_set = {r["category"].strip().lower() for r in categories}

    out = []
    for f in FESTIVALS:
        try:
            fdate = date(today.year, f["month"], f["day"])
        except ValueError:
            continue
        if fdate < today:
            fdate = date(today.year + 1, f["month"], f["day"])
        if fdate <= horizon:
            out.append({
                **f,
                "date": fdate.isoformat(),
                "days_away": (fdate - today).days,
                "matched_store_categories": sorted(cat_set) if cat_set else [],
            })
    return _j(out)


# ---------------------------------------------------------------- analytics


async def analyze_profit_change(store_id: str, days: int = 7) -> dict[str, Any]:
    """Deterministic week-over-week profit decomposition, by product.

    Explains profit change via volume, price and cost effects computed from
    sale_items.unit_price/unit_cost — no invented causes.
    """
    days = max(2, min(int(days), 60))
    rows = await db.fetch(
        """
        select * from (
          select p.id as product_id, p.name,
            coalesce(sum(si.quantity) filter (where s.created_at >= current_date - $2::int), 0) as qty_now,
            coalesce(sum(si.quantity * (si.unit_price - si.unit_cost))
              filter (where s.created_at >= current_date - $2::int), 0) as profit_now,
            coalesce(sum(si.quantity) filter (where s.created_at >= current_date - 2 * $2::int
                and s.created_at < current_date - $2::int), 0) as qty_prev,
            coalesce(sum(si.quantity * (si.unit_price - si.unit_cost))
              filter (where s.created_at >= current_date - 2 * $2::int
                and s.created_at < current_date - $2::int), 0) as profit_prev,
            coalesce(sum(si.quantity * (si.unit_price - si.unit_cost))
              filter (where s.created_at >= current_date - $2::int), 0)
              - coalesce(sum(si.quantity * (si.unit_price - si.unit_cost))
              filter (where s.created_at >= current_date - 2 * $2::int
                and s.created_at < current_date - $2::int), 0) as profit_delta,
            avg(si.unit_cost) filter (where s.created_at >= current_date - $2::int) as cost_now,
            avg(si.unit_cost) filter (where s.created_at >= current_date - 2 * $2::int
                and s.created_at < current_date - $2::int) as cost_prev,
            avg(si.unit_price) filter (where s.created_at >= current_date - $2::int) as price_now,
            avg(si.unit_price) filter (where s.created_at >= current_date - 2 * $2::int
                and s.created_at < current_date - $2::int) as price_prev
          from products p
          left join sale_items si on si.product_id = p.id
          left join sales s on s.id = si.sale_id and s.status = 'completed'
          where p.store_id = $1
          group by p.id, p.name
          having coalesce(sum(si.quantity), 0) > 0
        ) t
        order by t.profit_delta
        """,
        store_id,
        days,
    )
    contributors = []
    for r in rows:
        delta = float(r["profit_now"] or 0) - float(r["profit_prev"] or 0)
        if abs(delta) < 1:
            continue
        contributors.append({
            "product_id": str(r["product_id"]),
            "name": r["name"],
            "profit_change": round(delta, 2),
            "units_now": float(r["qty_now"] or 0),
            "units_prev": float(r["qty_prev"] or 0),
            "avg_price_now": round(float(r["price_now"]), 2) if r["price_now"] is not None else None,
            "avg_price_prev": round(float(r["price_prev"]), 2) if r["price_prev"] is not None else None,
            "avg_cost_now": round(float(r["cost_now"]), 2) if r["cost_now"] is not None else None,
            "avg_cost_prev": round(float(r["cost_prev"]), 2) if r["cost_prev"] is not None else None,
            "cost_rose": (r["cost_now"] is not None and r["cost_prev"] is not None
                          and float(r["cost_now"]) > float(r["cost_prev"]) * 1.02),
            "price_fell": (r["price_now"] is not None and r["price_prev"] is not None
                           and float(r["price_now"]) < float(r["price_prev"]) * 0.98),
        })
    totals = await db.fetchrow(
        """
        select
          coalesce(sum(si.quantity * (si.unit_price - si.unit_cost))
            filter (where s.created_at >= current_date - $2::int), 0) as profit_now,
          coalesce(sum(si.quantity * (si.unit_price - si.unit_cost))
            filter (where s.created_at >= current_date - 2 * $2::int
              and s.created_at < current_date - $2::int), 0) as profit_prev,
          coalesce(sum(s.total) filter (where s.created_at >= current_date - $2::int), 0) as sales_now,
          coalesce(sum(s.total) filter (where s.created_at >= current_date - 2 * $2::int
              and s.created_at < current_date - $2::int), 0) as sales_prev
        from sales s
        left join sale_items si on si.sale_id = s.id
        where s.store_id = $1 and s.status = 'completed'
        """,
        store_id,
        days,
    )
    return _j({
        "window_days": days,
        "totals": dict(totals) if totals else {},
        "contributors": contributors,
        "note": "Cost/price effects use average unit price/cost from sale items over each window.",
    })


# ================================================================ phase 4: demand


async def get_demand_summary(store_id: str) -> dict[str, Any]:
    return _j(await demand_service.demand_summary(store_id))


async def get_product_trends(store_id: str, limit: int = 20) -> dict[str, Any]:
    """All product trends grouped: rising / falling / stable / insufficient."""
    trends = await demand_service.product_trends_bulk(store_id, limit=max(1, min(limit, 200)))
    grouped = {
        "rising": [t for t in trends if t["trend"] == "RISING"],
        "falling": [t for t in trends if t["trend"] in ("FALLING", "WATCH")],
        "stable": [t for t in trends if t["trend"] == "STABLE"],
        "insufficient": [t for t in trends if t["trend"] in ("INSUFFICIENT_DATA", "NO_RECENT_SALES")],
    }
    return _j({"counts": {k: len(v) for k, v in grouped.items()}, **grouped})


async def get_category_trends(store_id: str) -> list[dict[str, Any]]:
    return _j(await demand_service.category_trends(store_id))


async def get_sales_velocity(store_id: str, product_id: str) -> dict[str, Any]:
    try:
        return _j(await demand_service.sales_velocity(store_id, product_id))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


async def forecast_product_demand(store_id: str, product_id: str, horizon_days: int = 7) -> dict[str, Any]:
    try:
        return _j(await demand_service.forecast_product_demand(store_id, product_id, horizon_days))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


async def get_upcoming_festival_opportunities(store_id: str, horizon_days: int = 45) -> list[dict[str, Any]]:
    return _j(await fest_service.upcoming_festivals(store_id, horizon_days))


async def get_festival_demand_signals(store_id: str, festival_id: str) -> dict[str, Any]:
    """Historical evidence + integrated opportunity for one festival."""
    evidence = await fest_service.festival_historical_evidence(store_id, festival_id)
    opportunity = await fest_service.festival_opportunity(store_id, festival_id)
    return _j({"evidence": evidence, "opportunity": opportunity})


async def prepare_inventory_opportunity(store_id: str, festival_id: str) -> dict[str, Any]:
    """Inventory/procurement part of a festival opportunity: gap products with
    reorder suggestions REUSING the existing reorder engine (no second math)."""
    opp = await fest_service.festival_opportunity(store_id, festival_id)
    gap_products = [p for p in opp.get("relevant_products", []) if p.get("gap_high", 0) and p["gap_high"] > 0]
    out = []
    for p in gap_products[:8]:
        r = await intel.calculate_reorder(store_id, p["product_id"])
        out.append({
            "product_id": p["product_id"],
            "name": p["name"],
            "gap_units": f"{p['gap_low']}\u2013{p['gap_high']}",
            "reorder_status": r.get("status"),
            "recommended_quantity": r.get("recommended_quantity"),
            "preferred_supplier": (r.get("preferred_supplier") or {}).get("name") if r.get("preferred_supplier") else None,
            "note": "Festival gap considered; reorder engine math unchanged.",
        })
    return _j({
        "festival": opp["festival"]["name"],
        "days_away": opp["festival"]["days_away"],
        "gap_products": out,
        "message": (
            f"{len(out)} product(s) may not cover festival demand at the current pace."
            if out else "No inventory gap detected for the relevant products so far."
        ),
    })


# ================================================================ phase 4: customers & marketing


async def get_customer_segments(store_id: str, segment: Optional[str] = None) -> dict[str, Any]:
    overview = await cust_intel.customer_overview(store_id)
    items = overview["customers"]
    if segment:
        items = [i for i in items if any(s == segment or s.startswith(segment + ":") for s in i["segments"])]
    return _j({
        "segment_counts": overview["segment_counts"],
        "thresholds": overview["thresholds"],
        "customers": items[:30],
    })


async def get_customer_details(store_id: str, customer_id: str) -> dict[str, Any]:
    try:
        return _j(await cust_intel.customer_360(store_id, customer_id))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


async def get_inactive_customers(store_id: str) -> dict[str, Any]:
    items = await cust_intel.customer_segments(store_id, "INACTIVE_CUSTOMER")
    recently = await cust_intel.customer_segments(store_id, "RECENTLY_INACTIVE")
    return _j({
        "inactive_30d": items[:15],
        "recently_inactive": recently[:15],
        "note": "Segments from deterministic rules (30d / 14-30d vs personal interval).",
    })


async def get_category_preferences(store_id: str, customer_id: str) -> dict[str, Any]:
    try:
        c360 = await cust_intel.customer_360(store_id, customer_id)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    return _j({
        "customer": c360["customer"]["name"],
        "favorite_categories": c360["favorite_categories"],
        "favorite_products": c360["favorite_products"][:5],
    })


async def get_campaign_history(store_id: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        select id, name, campaign_type, status, recipient_count, sent_count, failed_count,
               sent_at, created_at
        from campaigns where store_id = $1 order by created_at desc limit $2
        """,
        store_id,
        max(1, min(int(limit), 50)),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        out.append(_j(d))
    return out


async def get_campaign_eligibility(
    store_id: str,
    segment: Optional[str] = None,
    category: Optional[str] = None,
    product_id: Optional[str] = None,
) -> dict[str, Any]:
    try:
        return _j(await cust_intel.eligible_campaign_audience(
            store_id, segment=segment, category=category, product_id=product_id,
        ))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc


async def prepare_customer_campaign(
    store_id: str, user_id: str, *, name: str, campaign_type: str,
    segment: Optional[str] = None, category: Optional[str] = None,
    product_id: Optional[str] = None, products: Optional[list[dict[str, Any]]] = None,
    message_template: Optional[str] = None,
) -> dict[str, Any]:
    """Prepare (NOT send) a campaign from AI intent. Campaign stays DRAFT until
    the merchant reviews, approves and sends it in Marketing."""
    try:
        detail = await marketing_service.create_campaign(
            store_id, user_id,
            name=name,
            campaign_type=campaign_type,
            audience={k: v for k, v in {"segment": segment, "category": category, "product_id": product_id}.items() if v},
            products=products or [],
            message_template=message_template,
        )
    except marketing_service.CampaignError as exc:
        raise ValueError(str(exc)) from exc
    return _j({
        "campaign_id": detail["id"],
        "name": detail["name"],
        "status": detail["status"],
        "audience_count": detail["audience"].get("count_snapshot"),
        "audience_evidence": detail["audience"].get("evidence"),
        "message": "Campaign saved as DRAFT. The merchant must review, approve and send it in WhatsApp & Marketing.",
    })


# ================================================================ phase 5: external intelligence


async def get_external_context(store_id: str, category: Optional[str] = None) -> dict[str, Any]:
    """Verified external signals, clearly separated from merchant data.

    Returns []-style empty result when nothing exists — the caller must say
    'no external intelligence available', never invent a market claim.
    """
    return _j(await evidence_service.external_context(store_id, category))


async def get_evidence_for_category(store_id: str, category: str, limit: int = 10) -> dict[str, Any]:
    """Evidence items (any verification status, labelled) for one store category."""
    items = await evidence_service.list_evidence(
        store_id, category=category, limit=max(1, min(int(limit), 50))
    )
    verified = [i for i in items if i["verification_status"] == "VERIFIED"]
    return _j({
        "category": category,
        "items": items,
        "verified_count": len(verified),
        "note": (
            "Only VERIFIED items may be quoted as external reports; "
            "UNVERIFIED items may only be mentioned as unverified."
            if items else
            f"No external evidence on record for category '{category}'."
        ),
    })


async def get_external_source_registry(store_id: str) -> dict[str, Any]:
    return _j(await evidence_service.list_sources(store_id))


# ---------------------------------------------------------------- phase 6: commerce (read-only)


async def get_order_details(store_id: str, order_id: str) -> dict[str, Any]:
    """Full order + payment + split state for the AI to explain a transaction."""
    order = await orders_service.get_order(store_id, order_id)
    if not order:
        return {"error": "Order not found."}
    pays = await payments_service.list_payments(store_id, order_id)
    split = await payments_service.get_split_group_by_order(store_id, order_id)
    paid = round(sum(float(p["amount"]) for p in pays if p["state"] == "PAID"), 2)
    return _j({
        "order": {k: order[k] for k in ("id", "state", "payment_method", "cart_discount",
                                          "total", "sale_id", "created_at", "customer_name",
                                          "items", "cart")},
        "payments": [{k: p[k] for k in ("id", "amount", "state", "method", "provider", "error_note")}
                     for p in pays],
        "paid_total": paid,
        "split_group": split,
    })


async def get_recent_orders(store_id: str, limit: int = 10, state: Optional[str] = None) -> dict[str, Any]:
    items = await orders_service.list_orders(store_id, state, limit)
    return {"items": _j(items), "count": len(items)}


# ------------------------------------------------------------------
# Phase 8: omnichannel order intelligence (read-only)
# ------------------------------------------------------------------
async def get_orders_needing_attention(store_id: str, limit: int = 20) -> dict[str, Any]:
    """Customer-channel orders grouped by what the merchant must act on.
    Evidence comes from real order/payment records only."""
    async def _grab(states: tuple[str, ...]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for st in states:
            rows = await orders_service.list_orders(store_id, st, limit)
            out.extend(rows)
        return out

    groups = {
        "awaiting_acceptance": await _grab(("PAID",)),          # customer orders paid, need CONFIRMED
        "awaiting_payment": await _grab(("PENDING_PAYMENT", "PAYMENT_FAILED")),
        "in_fulfillment": await _grab(("CONFIRMED", "PROCESSING", "READY", "OUT_FOR_DELIVERY", "DELIVERED")),
        "partially_paid": await _grab(("PARTIALLY_PAID",)),
    }
    # keep only non-POS channels in the customer-facing groups (POS flows end at PAID/COMPLETED)
    for key in ("awaiting_payment", "partially_paid"):
        groups[key] = [o for o in groups[key] if o.get("channel", "POS") != "POS"]
    total_actions = sum(len(v) for v in groups.values())
    return {"groups": _j(groups), "total_needing_action": total_actions}


async def get_channel_sales_summary(store_id: str, days: int = 30) -> dict[str, Any]:
    """Per-channel revenue/orders/units from real order records."""
    from app.services.commerce import channel_analytics
    return await channel_analytics(store_id, days)


async def get_online_low_stock_risk(store_id: str, limit: int = 10) -> dict[str, Any]:
    """Products listed/sellable online whose sellable stock is at or below
    their reorder level — online demand can break against POS demand."""
    rows = await db.fetch(
        """
        select p.id, p.name, p.reorder_level, sellable_stock($1, p.id) as sellable,
               (select count(*) from channel_prices cp where cp.product_id = p.id and cp.active) as online_channels
        from products p
        where p.store_id = $1 and p.is_active
          and sellable_stock($1, p.id) <= p.reorder_level
        order by sellable_stock($1, p.id) asc
        limit $2
        """,
        store_id, limit,
    )
    return {"items": _j([dict(r) for r in rows]), "count": len(rows)}


async def calculate_cart_margin(store_id: str, cart: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic margin for a cart (reuses the Phase 2 cart-margin engine)."""
    return _j(await intel.calculate_cart_margin(store_id, cart))


async def validate_discount(store_id: str, product_id: str, proposed_price: float) -> dict[str, Any]:
    """Can I give this discount? Cost, floor, resulting margin — from the pricing engine."""
    return _j(await intel.calculate_discount_impact(store_id, product_id, float(proposed_price)))


async def get_order_payment_status(store_id: str, order_id: str) -> dict[str, Any]:
    pays = await payments_service.list_payments(store_id, order_id)
    paid = round(sum(float(p["amount"]) for p in pays if p["state"] == "PAID"), 2)
    order = await orders_service.get_order(store_id, order_id)
    if not order:
        return {"error": "Order not found."}
    reval = await orders_service.revalidate(store_id, order_id)
    total = reval["total_current"]
    return _j({
        "order_id": order_id, "order_state": order["state"],
        "total": total, "paid": paid,
        "remaining": round(max(0.0, total - paid), 2),
        "payments": [{k: p[k] for k in ("id", "amount", "state", "method", "provider", "error_note")}
                     for p in pays],
        "verifying": any(p["state"] == "PENDING" for p in pays),
    })


async def get_payment_split_status(store_id: str, order_id: str) -> dict[str, Any]:
    group = await payments_service.get_split_group_by_order(store_id, order_id)
    if not group:
        return {"order_id": order_id, "has_split": False}
    return _j({"order_id": order_id, "has_split": True, "group": group})


READ_TOOLS: dict[str, Any] = {
    "get_store_summary": get_store_summary,
    "get_today_sales": get_today_sales,
    "get_sales_summary": get_sales_summary,
    "get_product_sales_history": get_product_sales_history,
    "get_inventory_health": get_inventory_health,
    "get_low_stock_products": get_low_stock_products,
    "get_critical_stock_products": get_critical_stock_products,
    "get_expiring_inventory": get_expiring_inventory,
    "get_overstocked_products": get_overstocked_products,
    "get_dead_stock": get_dead_stock,
    "get_reorder_candidates": get_reorder_candidates,
    "get_product_details": get_product_details,
    "get_product_cost_history": get_product_cost_history,
    "get_product_margin": get_product_margin,
    "calculate_discount_impact": calculate_discount_impact,
    "calculate_bargain": calculate_bargain,
    "calculate_reorder": calculate_reorder,
    "get_supplier_comparison": get_supplier_comparison,
    "get_supplier_details": get_supplier_details,
    "get_customer_summary": get_customer_summary,
    "get_recent_customers": get_recent_customers,
    "get_customer_purchase_history": get_customer_purchase_history,
    "get_alerts": get_alerts,
    "get_festival_opportunities": get_festival_opportunities,
    "get_business_events": get_business_events,
    "get_activity_history": get_activity_history,
    "analyze_profit_change": analyze_profit_change,
    # phase 4: demand
    "get_demand_summary": get_demand_summary,
    "get_product_trends": get_product_trends,
    "get_category_trends": get_category_trends,
    "get_sales_velocity": get_sales_velocity,
    "forecast_product_demand": forecast_product_demand,
    # phase 4: festivals
    "get_upcoming_festival_opportunities": get_upcoming_festival_opportunities,
    "get_festival_demand_signals": get_festival_demand_signals,
    "prepare_inventory_opportunity": prepare_inventory_opportunity,
    # phase 4: customers & marketing
    "get_customer_segments": get_customer_segments,
    "get_customer_details": get_customer_details,
    "get_inactive_customers": get_inactive_customers,
    "get_category_preferences": get_category_preferences,
    "get_campaign_history": get_campaign_history,
    "get_campaign_eligibility": get_campaign_eligibility,
    # phase 5: external intelligence
    "get_external_context": get_external_context,
    "get_evidence_for_category": get_evidence_for_category,
    "get_external_source_registry": get_external_source_registry,
    # phase 6: commerce (read-only; the AI never executes payments)
    "get_order_details": get_order_details,
    "get_recent_orders": get_recent_orders,
    "calculate_cart_margin": calculate_cart_margin,
    "validate_discount": validate_discount,
    "get_order_payment_status": get_order_payment_status,
    "get_payment_split_status": get_payment_split_status,
    # phase 8: omnichannel (read-only)
    "get_orders_needing_attention": get_orders_needing_attention,
    "get_channel_sales_summary": get_channel_sales_summary,
    "get_online_low_stock_risk": get_online_low_stock_risk,
}

# Tools that need the acting user id (write-intent, still draft-only)
USER_TOOLS: dict[str, Any] = {
    "prepare_customer_campaign": prepare_customer_campaign,
}
