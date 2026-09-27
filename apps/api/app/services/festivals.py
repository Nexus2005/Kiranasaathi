"""Festival intelligence — connects configured festival data to the store's
actual categories, sales history, inventory and suppliers.

Principles:
- Festivals are CONFIGURED APPLICATION DATA (migration 009) with provenance
  columns; no external scrape pretends to be live intelligence.
- Relevance mapping is festival -> category (configurable jsonb in the
  festivals table), matched against the store's ACTUAL product categories.
- Demand evidence comes ONLY from the store's own sales history: we compare
  the 14 days before last year's (or any past) occurrence of the same
  festival vs the preceding 14 days. If no historical occurrence exists,
  the output says exactly that — nothing is manufactured.
- Stock gap = forecast requirement band minus current sellable stock,
  computed with the SAME demand engine used everywhere else.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Optional

from app.database import db
from app.services import demand


def _parse_relevance(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return []


async def _next_occurrence(f: dict[str, Any], today: date) -> Optional[date]:
    """Next upcoming occurrence of this festival (handles recurring by month-day)."""
    start: date = f["start_date"]
    end: date = f["end_date"]
    # If any date row is in the future, use it.
    if end >= today:
        return start
    # Recur by (month, day) with length preserved — approximate for demo config.
    for year in (today.year, today.year + 1):
        try:
            candidate_start = date(year, start.month, start.day)
        except ValueError:
            return None
        length = (end - start).days
        try:
            candidate_end = candidate_start + timedelta(days=length)
        except OverflowError:
            candidate_end = date(year, 12, 31)
        if candidate_end >= today:
            return candidate_start
    return None


async def upcoming_festivals(store_id: str, horizon_days: int = 45) -> list[dict[str, Any]]:
    """Configured festivals within the horizon, with store-relevance flags."""
    today = date.today()
    rows = await db.fetch(
        "select * from festivals where store_id = $1 order by start_date",
        store_id,
    )
    out = []
    for r in rows:
        d = dict(r)
        nxt = await _next_occurrence(d, today)
        if nxt is None:
            continue
        end_date = d["end_date"] if d["end_date"] >= today else nxt + (d["end_date"] - d["start_date"])
        days_away = (nxt - today).days
        if days_away > horizon_days:
            continue
        relevance = _parse_relevance(d["relevance"])
        matched = await db.fetch(
            """
            select distinct category from products
            where store_id = $1 and is_active and category = any($2::text[])
            """,
            store_id,
            [rel.get("category") for rel in relevance if rel.get("category")],
        )
        matched_cats = sorted(m["category"] for m in matched)
        out.append({
            "id": str(d["id"]),
            "name": d["name"],
            "start_date": nxt.isoformat(),
            "end_date": end_date.isoformat(),
            "days_away": days_away,
            "region": d["region"],
            "category": d["category"],
            "description": d["description"],
            "relevance": relevance,
            "matched_store_categories": matched_cats,
            "has_store_relevance": len(matched_cats) > 0,
            "source": d["source"],
            "source_url": d["source_url"],
            "retrieved_at": d["retrieved_at"].isoformat() if d["retrieved_at"] else None,
            "data_provenance": "Configured application data (demo calendar). Not live external intelligence.",
        })
    out.sort(key=lambda f: f["days_away"])
    return out


async def festival_historical_evidence(store_id: str, festival_id: str) -> dict[str, Any]:
    """Store-only historical evidence: did sales in relevant categories rise
    around previous occurrences of this festival?

    Compares the 14 days ENDING at each past occurrence start vs the
    preceding 14 days, per relevant category. Clearly reports when no past
    occurrence exists in the store's sales window.
    """
    fest = await db.fetchrow(
        "select * from festivals where id = $1 and store_id = $2", festival_id, store_id
    )
    if not fest:
        raise ValueError("Festival not found")
    f = dict(fest)
    relevance = _parse_relevance(f["relevance"])
    cats = [rel.get("category") for rel in relevance if rel.get("category")]
    if not cats:
        return {"has_evidence": False, "reason": "No relevant categories configured for this festival."}

    today = date.today()
    start: date = f["start_date"]
    # Past occurrences within the last 2 years (by month-day recurrence)
    past_starts: list[date] = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            occ = date(year, start.month, start.day)
        except ValueError:
            continue
        if occ < today - timedelta(days=365 * 2):
            continue
        if occ < today:
            past_starts.append(occ)
    if not past_starts:
        return {
            "has_evidence": False,
            "reason": "No past occurrence of this festival falls inside your recorded sales history — no store-specific evidence available.",
        }

    per_occurrence = []
    for occ in past_starts:
        rows = await db.fetch(
            """
            select p.category,
              coalesce(sum(si.quantity) filter (where s.created_at::date >= $2::date - 14 and s.created_at::date < $2::date), 0) as units_before,
              coalesce(sum(si.quantity) filter (where s.created_at::date >= $2::date and s.created_at::date <= $2::date + 7), 0) as units_during,
              coalesce(sum(si.line_total) filter (where s.created_at::date >= $2::date - 14 and s.created_at::date < $2::date), 0) as rev_before,
              coalesce(sum(si.line_total) filter (where s.created_at::date >= $2::date and s.created_at::date <= $2::date + 7), 0) as rev_during
            from sale_items si
            join sales s on s.id = si.sale_id
            join products p on p.id = si.product_id
            where s.store_id = $1 and s.status = 'completed' and p.category = any($3::text[])
            group by p.category
            """,
            store_id,
            occ,
            cats,
        )
        for r in rows:
            per_occurrence.append({
                "occurrence": occ.isoformat(),
                "category": r["category"],
                "units_14d_before": float(r["units_before"] or 0),
                "units_festival_week": float(r["units_during"] or 0),
                "revenue_14d_before": float(r["rev_before"] or 0),
                "revenue_festival_week": float(r["rev_during"] or 0),
            })

    has_any = any(
        o["units_festival_week"] > 0 or o["units_14d_before"] > 0 for o in per_occurrence
    )
    if not has_any:
        return {
            "has_evidence": False,
            "reason": "Your recorded sales history covers a past occurrence of this festival, but there are no sales in the relevant categories from that period.",
            "past_occurrences": [o.isoformat() for o in past_starts],
        }
    return {
        "has_evidence": True,
        "past_occurrences": [o.isoformat() for o in past_starts],
        "occurrences": per_occurrence,
        "method": "Units sold in relevant categories during the festival week vs the preceding 14 days, per past occurrence.",
    }


async def festival_opportunity(store_id: str, festival_id: str) -> dict[str, Any]:
    """Integrated opportunity view: relevant products, stock, forecast gap,
    supplier lead times, customer segments, campaign readiness.

    All numbers come from the demand engine + inventory tables. Where data is
    missing the field says so — nothing is fabricated.
    """
    fest = await db.fetchrow(
        "select * from festivals where id = $1 and store_id = $2", festival_id, store_id
    )
    if not fest:
        raise ValueError("Festival not found")
    f = dict(fest)
    today = date.today()
    nxt = await _next_occurrence(f, today)
    if nxt is None:
        return {"festival": f["name"], "status": "PASSED", "message": "No upcoming occurrence of this festival is configured."}
    days_away = (nxt - today).days
    relevance = _parse_relevance(f["relevance"])
    cats = [rel.get("category") for rel in relevance if rel.get("category")]

    # Relevant products with stock, from the store's actual catalog
    products = await db.fetch(
        """
        select p.id, p.name, p.category, p.selling_price, p.purchase_price,
               coalesce(i.quantity, 0) as stock,
               coalesce((select sum(b.quantity) from inventory_batches b
                 where b.store_id = $1 and b.product_id = p.id and b.status = 'expired' and b.quantity > 0), 0) as expired_qty
        from products p
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        where p.store_id = $1 and p.is_active and p.category = any($2::text[])
        order by p.name
        """,
        store_id,
        cats,
    )

    evidence = await festival_historical_evidence(store_id, festival_id)

    product_rows = []
    total_gap_low = total_gap_high = 0
    for p in products:
        fc = await demand.forecast_product_demand(store_id, str(p["id"]), horizon_days=min(max(days_away, 7), 30))
        sellable = max(int(p["stock"] or 0) - int(p["expired_qty"] or 0), 0)
        row: dict[str, Any] = {
            "product_id": str(p["id"]),
            "name": p["name"],
            "category": p["category"],
            "current_sellable_stock": sellable,
        }
        if fc.get("status") == "ok":
            low, high = fc["forecast_range"]["low"], fc["forecast_range"]["high"]
            gap_low = max(low - sellable, 0)
            gap_high = max(high - sellable, 0)
            total_gap_low += gap_low
            total_gap_high += gap_high
            row.update({
                "estimated_demand_range": f"{low}\u2013{high} units",
                "potential_gap_units": f"{gap_low}\u2013{gap_high}" if gap_high > 0 else "0",
                "gap_low": gap_low,
                "gap_high": gap_high,
                "forecast_data_quality": fc["data_quality"],
                "gap_status": "GAP" if gap_high > 0 else ("TIGHT" if gap_low > 0 else "OK"),
            })
        else:
            row.update({
                "estimated_demand_range": None,
                "potential_gap_units": None,
                "gap_status": "INSUFFICIENT_DATA",
                "forecast_data_quality": "INSUFFICIENT_DATA",
            })
        product_rows.append(row)

    # Supplier options for gap products (lead times from actual supplier data)
    suppliers = await db.fetch(
        """
        select s.id, s.name, s.lead_time_days,
               count(pi.id) filter (where pi.product_id = any($2::uuid[])) as relevant_purchases
        from suppliers s
        left join purchase_orders po on po.supplier_id = s.id and po.store_id = s.store_id and po.status = 'received'
        left join purchase_items pi on pi.purchase_order_id = po.id
        where s.store_id = $1 and s.is_active
        group by s.id, s.name, s.lead_time_days
        order by s.name
        """,
        store_id,
        [p["id"] for p in products],
    )
    supplier_options = [
        {
            "supplier_id": str(s["id"]),
            "name": s["name"],
            "lead_time_days": s["lead_time_days"],
            "has_relevant_history": int(s["relevant_purchases"] or 0) > 0,
        }
        for s in suppliers
    ]

    # Customer connection: who actually bought these categories before
    cat_customers = await db.fetchval(
        """
        select count(distinct s.customer_id)
        from sales s join sale_items si on si.sale_id = s.id
        join products p on p.id = si.product_id
        where s.store_id = $1 and s.status = 'completed' and p.category = any($2::text[])
          and s.customer_id is not null
        """,
        store_id,
        cats,
    ) if cats else 0

    consented = await db.fetchval(
        """
        select count(*) from customers
        where store_id = $1 and marketing_consent and phone is not null
          and opt_out_timestamp is null
        """,
        store_id,
    )

    gap_products = [p for p in product_rows if p.get("gap_high", 0) and p["gap_high"] > 0]
    return {
        "festival": {
            "id": str(f["id"]),
            "name": f["name"],
            "start_date": nxt.isoformat(),
            "days_away": days_away,
            "region": f["region"],
            "description": f["description"],
            "relevance": relevance,
            "source": f["source"],
            "data_provenance": "Configured application data.",
        },
        "status": "ok",
        "relevant_products": product_rows,
        "summary": {
            "relevant_product_count": len(product_rows),
            "products_with_gap": len(gap_products),
            "total_gap_units_range": f"{total_gap_low}\u2013{total_gap_high}" if (total_gap_high or total_gap_low) else "0",
            "historical_evidence": evidence.get("has_evidence"),
            "historical_evidence_note": evidence.get("reason") or evidence.get("method"),
            "customers_with_relevant_history": int(cat_customers or 0),
            "consented_customers_with_phone": int(consented or 0),
            "supplier_count": len(supplier_options),
        },
        "supplier_options": supplier_options,
    }


async def festival_list_with_flags(store_id: str) -> dict[str, Any]:
    upcoming = await upcoming_festivals(store_id, horizon_days=120)
    today = date.today()
    past = await db.fetch(
        "select id, name, end_date, category from festivals where store_id = $1 and end_date < $2 order by end_date desc limit 6",
        store_id,
        today,
    )
    return {
        "upcoming": upcoming,
        "past": [
            {"id": str(p["id"]), "name": p["name"], "end_date": p["end_date"].isoformat(), "category": p["category"]}
            for p in past
        ],
        "note": "Festival dates are configured application data with stored provenance.",
    }
