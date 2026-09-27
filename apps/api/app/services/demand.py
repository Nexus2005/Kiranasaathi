"""Demand & Trends service — deterministic analysis of actual sales history.

Methodology (transparent, no invented numbers):
- Windows: recent N days vs previous N days (equal lengths, always labelled).
- Stockout-awareness: daily availability is reconstructed from
  inventory_movements (quantity_after). A day counts as OUT when the store
  quantity was 0 at any point that day; unavailability ratio reduces the
  "observed" baseline so low sales during stockouts are not read as low demand.
- Trend classification uses BOTH the recent/previous unit change and a
  stockout-correction note. Classes: RISING, FALLING, STABLE, SEASONAL_CANDIDATE,
  INSUFFICIENT_DATA. Movement thresholds are small-evidence by default: a
  product needs >= MIN_UNITS_FOR_TREND observed units in the previous window
  before a percentage is treated as a trend at all.
- Forecast: units/day from recent window adjusted by the previous-window
  ratio (bounded), multiplied by horizon days. Output is a RANGE:
  floor = baseline * (1 - sigma), ceiling = baseline * (1 + sigma) where
  sigma comes from the observed day-to-day variability (coefficient of
  variation), clamped to a sane band. Data-quality categories
  (HIGH/MEDIUM/LOW/INSUFFICIENT) are derived from history length and
  availability coverage — never fabricated percentages.

Everything reads the SAME sales/sale_items/inventory/inventory_movements
tables the rest of the system uses. No duplicated sales data.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from app.database import db

MIN_UNITS_FOR_TREND = 5      # previous-window observed units needed to call a % a trend
STABLE_BAND_PCT = 20.0       # +/-20% = STABLE (when evidence exists)
BIG_CHANGE_PCT = 40.0        # beyond +/-40% labelled a clear rise/fall
SEASONAL_MIN_WEEKS = 8       # weeks of history before seasonality is even considered


def _pct_change(current: float, previous: float) -> Optional[float]:
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100.0, 1)


def _label_from_change(pct: Optional[float], prev_units: float) -> str:
    if pct is None or prev_units < MIN_UNITS_FOR_TREND:
        return "INSUFFICIENT_DATA"
    if pct >= BIG_CHANGE_PCT:
        return "RISING"
    if pct <= -BIG_CHANGE_PCT:
        return "FALLING"
    if abs(pct) <= STABLE_BAND_PCT:
        return "STABLE"
    return "WATCH"  # between bands: direction visible but not yet a clear trend


def _quality(days_history: int, availability: float) -> str:
    if days_history < 14 or days_history == 0:
        return "INSUFFICIENT_DATA"
    if availability < 0.5:
        return "LOW"  # was unavailable half the time; signals unreliable
    if days_history >= 56:
        return "HIGH"
    return "MEDIUM"


# ---------------------------------------------------------------- availability


async def _availability_ratio(store_id: str, product_id: str, days: int) -> Optional[float]:
    """Fraction of the last `days` days where the product had stock on the shelf.

    Rebuilt from inventory_movements.quantity_after (the audit trail every
    sale/purchase/adjustment already writes). Days before the first movement
    are UNKNOWN — they are excluded from the ratio, and the caller labels
    confidence accordingly. Returns None when no movement history exists.
    """
    rows = await db.fetch(
        """
        select d::date as day,
               (select m.quantity_after
                from inventory_movements m
                where m.store_id = $1 and m.product_id = $2
                  and m.created_at >= d and m.created_at < d + interval '1 day'
                order by m.created_at desc limit 1) as end_qty,
               (select min(m.quantity_after)
                from inventory_movements m
                where m.store_id = $1 and m.product_id = $2
                  and m.created_at >= d and m.created_at < d + interval '1 day') as min_qty,
               (select max(m.quantity_after)
                from inventory_movements m
                where m.store_id = $1 and m.product_id = $2
                  and m.created_at >= d and m.created_at < d + interval '1 day') as max_qty
        from generate_series(current_date - ($3::int - 1), current_date, interval '1 day') d
        """,
        store_id,
        product_id,
        days,
    )
    known = 0
    available = 0
    for r in rows:
        if r["end_qty"] is None and r["min_qty"] is None and r["max_qty"] is None:
            continue  # no data that day — unknown, excluded
        known += 1
        # out-of-stock if it hit 0 at any recorded point that day
        if (r["min_qty"] is not None and int(r["min_qty"]) <= 0) or (
            r["max_qty"] is not None and int(r["max_qty"]) <= 0
        ):
            continue
        available += 1
    if known == 0:
        return None
    return round(available / known, 3)


# ---------------------------------------------------------------- velocity


async def sales_velocity(store_id: str, product_id: str) -> dict[str, Any]:
    """Comparative velocity with clearly labelled equal-length windows."""
    row = await db.fetchrow(
        """
        select
          coalesce(sum(si.quantity) filter (where s.created_at >= current_date - 7), 0) as units_7d,
          coalesce(sum(si.quantity) filter (where s.created_at >= current_date - 14 and s.created_at < current_date - 7), 0) as units_prev_7d,
          coalesce(sum(si.quantity) filter (where s.created_at >= current_date - 30), 0) as units_30d,
          coalesce(sum(si.quantity) filter (where s.created_at >= current_date - 60 and s.created_at < current_date - 30), 0) as units_prev_30d,
          min(s.created_at) as first_sale_at
        from sale_items si
        join sales s on s.id = si.sale_id
        where s.store_id = $1 and si.product_id = $2 and s.status = 'completed'
        """,
        store_id,
        product_id,
    )
    if not row:
        return {"status": "INSUFFICIENT_DATA"}
    u7, u7p = float(row["units_7d"] or 0), float(row["units_prev_7d"] or 0)
    u30, u30p = float(row["units_30d"] or 0), float(row["units_prev_30d"] or 0)
    days_history = (
        (row["first_sale_at"].date() - (await _today())).days if row["first_sale_at"] else 0
    )
    return {
        "status": "ok" if (u30 or u7) else "no_sales_history",
        "recent_7d_units": u7,
        "previous_7d_units": u7p,
        "change_7d_pct": _pct_change(u7, u7p),
        "recent_30d_units": u30,
        "previous_30d_units": u30p,
        "change_30d_pct": _pct_change(u30, u30p),
        "avg_daily_units_30d": round(u30 / 30.0, 2),
        "avg_weekly_units_30d": round(u30 * 7.0 / 30.0, 2),
        "comparison_note": "Recent window vs the immediately preceding window of equal length.",
        "days_since_first_sale": max(days_history, 0),
    }


async def _today():
    from app.services.intelligence import date_today

    return date_today()


# ---------------------------------------------------------------- product trend


async def product_trend(store_id: str, product_id: str) -> dict[str, Any]:
    """Stockout-aware trend classification for one product."""
    prod = await db.fetchrow(
        """
        select p.id, p.name, p.category, coalesce(i.quantity, 0) as stock
        from products p
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        where p.id = $1 and p.store_id = $2
        """,
        product_id,
        store_id,
    )
    if not prod:
        raise ValueError("Product not found")

    vel = await sales_velocity(store_id, product_id)
    if vel.get("status") != "ok":
        return {
            "product_id": product_id,
            "name": prod["name"],
            "category": prod["category"],
            "current_stock": int(prod["stock"]),
            "trend": "INSUFFICIENT_DATA",
            "data_quality": "INSUFFICIENT_DATA",
            "explanation": "No completed sales recorded for this product yet.",
            "velocity": vel,
        }

    days_history = int(vel.get("days_since_first_sale") or 0)
    availability = await _availability_ratio(store_id, product_id, 30)
    avail_note = None
    avail_factor = 1.0
    if availability is None:
        avail_note = "Availability history is not recorded, so stockout effects cannot be ruled out."
    elif availability < 0.85:
        missed = round((1 - availability) * 100)
        avail_note = (
            f"Product was unavailable (stock 0) for about {missed}% of the last 30 days; "
            "recent sales understate real demand."
        )
        avail_factor = 1.0 / max(availability, 0.25)  # inflate observed demand

    u7, u7p = vel["recent_7d_units"], vel["previous_7d_units"]
    pct = vel["change_7d_pct"]
    trend = _label_from_change(pct, u7p)

    seasonal = None
    if days_history >= SEASONAL_MIN_WEEKS * 7:
        # crude seasonality candidate: month-over-month repetition
        row = await db.fetchrow(
            """
            select
              coalesce(sum(si.quantity) filter (where s.created_at >= current_date - 28 and s.created_at < current_date - 21), 0) as w4,
              coalesce(sum(si.quantity) filter (where s.created_at >= current_date - 56 and s.created_at < current_date - 49), 0) as w8
            from sale_items si join sales s on s.id = si.sale_id
            where s.store_id = $1 and si.product_id = $2 and s.status = 'completed'
            """,
            store_id,
            product_id,
        )
        if row and float(row["w8"] or 0) >= MIN_UNITS_FOR_TREND:
            w_ratio = _pct_change(float(row["w4"] or 0), float(row["w8"] or 0))
            if w_ratio is not None and abs(w_ratio) >= 60:
                seasonal = f"Week-4 vs week-8 sales differ by {w_ratio}% — possible repeating pattern; needs more history to confirm."

    explanation_bits = [
        f"Recent 7 days: {u7:.0f} units vs previous 7 days: {u7p:.0f} units"
        + (f" ({pct:+.1f}%)" if pct is not None else " (no baseline to compare)"),
    ]
    if avail_note:
        explanation_bits.append(avail_note)

    data_quality = _quality(days_history, availability if availability is not None else 1.0)

    return {
        "product_id": product_id,
        "name": prod["name"],
        "category": prod["category"],
        "current_stock": int(prod["stock"]),
        "trend": trend,
        "change_7d_pct": pct,
        "data_quality": data_quality,
        "availability_ratio_30d": availability,
        "explanation": ". ".join(explanation_bits),
        "seasonality_note": seasonal,
        "velocity": vel,
        "stockout_aware": availability is not None and availability < 0.85,
    }


async def product_trends_bulk(
    store_id: str, limit: int = 200
) -> list[dict[str, Any]]:
    """Trends for all active products in ONE query set (no per-product loops).

    Computes per-product recent/previous 7d units + 30d availability proxy
    from inventory_movements, then classifies in Python with the same rules.
    """
    rows = await db.fetch(
        """
        with sold as (
          select si.product_id,
            coalesce(sum(si.quantity) filter (where s.created_at >= current_date - 7), 0) as u7,
            coalesce(sum(si.quantity) filter (where s.created_at >= current_date - 14 and s.created_at < current_date - 7), 0) as u7p,
            coalesce(sum(si.quantity) filter (where s.created_at >= current_date - 30), 0) as u30,
            min(s.created_at) as first_sale
          from sale_items si join sales s on s.id = si.sale_id
          where s.store_id = $1 and s.status = 'completed'
          group by si.product_id
        ),
        avail as (
          select product_id,
            count(*)::numeric as known_days,
            count(*) filter (where day_min_qty > 0)::numeric as avail_days
          from (
            select m.product_id, m.created_at::date as d,
                   min(m.quantity_after) as day_min_qty
            from inventory_movements m
            where m.store_id = $1 and m.created_at >= current_date - 30
            group by m.product_id, m.created_at::date
          ) mm
          group by product_id
        )
        select p.id as product_id, p.name, p.category, coalesce(i.quantity, 0) as stock,
               s.u7, s.u7p, s.u30, s.first_sale,
               case when a.known_days > 0 then round(a.avail_days / a.known_days, 3) end as availability
        from products p
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        left join sold s on s.product_id = p.id
        left join avail a on a.product_id = p.id
        where p.store_id = $1 and p.is_active
        order by p.name
        limit $2
        """,
        store_id,
        limit,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        u7, u7p = float(r["u7"] or 0), float(r["u7p"] or 0)
        u30 = float(r["u30"] or 0)
        first_sale = r["first_sale"]
        days_history = (first_sale.date() - (await _today())).days if first_sale else 0
        pct = _pct_change(u7, u7p)
        trend = _label_from_change(pct, u7p)
        availability = float(r["availability"]) if r["availability"] is not None else None
        if u30 == 0 and u7 == 0 and u7p == 0:
            trend = "INSUFFICIENT_DATA" if days_history < 14 else "NO_RECENT_SALES"
        dq = _quality(days_history, availability if availability is not None else 1.0)
        out.append({
            "product_id": str(r["product_id"]),
            "name": r["name"],
            "category": r["category"],
            "current_stock": int(r["stock"]),
            "recent_7d_units": u7,
            "previous_7d_units": u7p,
            "units_30d": u30,
            "change_7d_pct": pct,
            "trend": trend,
            "data_quality": dq,
            "availability_ratio_30d": availability,
            "stockout_aware": availability is not None and availability < 0.85,
        })
    return out


# ---------------------------------------------------------------- category trend


async def category_trends(store_id: str) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        with cat as (
          select p.category,
            coalesce(sum(si.quantity) filter (where s.created_at >= current_date - 14), 0) as u14,
            coalesce(sum(si.quantity) filter (where s.created_at >= current_date - 28 and s.created_at < current_date - 14), 0) as u14p,
            coalesce(sum(si.line_total) filter (where s.created_at >= current_date - 14), 0) as rev14,
            min(s.created_at) as first_sale
          from sale_items si
          join sales s on s.id = si.sale_id
          join products p on p.id = si.product_id
          where s.store_id = $1 and s.status = 'completed'
          group by p.category
        )
        select * from cat order by rev14 desc
        """,
        store_id,
    )
    out = []
    for r in rows:
        u, up = float(r["u14"] or 0), float(r["u14p"] or 0)
        pct = _pct_change(u, up)
        first_sale = r["first_sale"]
        days_history = (first_sale.date() - (await _today())).days if first_sale else 0
        out.append({
            "category": r["category"],
            "recent_14d_units": u,
            "previous_14d_units": up,
            "change_14d_pct": pct,
            "revenue_14d": float(r["rev14"] or 0),
            "trend": _label_from_change(pct, up),
            "data_quality": "HIGH" if days_history >= 56 else ("MEDIUM" if days_history >= 14 else "INSUFFICIENT_DATA"),
        })
    return out


# ---------------------------------------------------------------- store daily series


async def store_daily_sales(store_id: str, days: int = 30) -> list[dict[str, Any]]:
    days = max(1, min(int(days), 120))
    rows = await db.fetch(
        """
        select d::date as day,
               coalesce(sum(s.total) filter (where s.id is not null), 0) as sales,
               coalesce(count(distinct s.id), 0) as orders,
               coalesce(sum(si.quantity), 0) as units
        from generate_series(current_date - ($2::int - 1), current_date, interval '1 day') d
        left join sales s on s.created_at >= d and s.created_at < d + interval '1 day'
          and s.store_id = $1 and s.status = 'completed'
        left join sale_items si on si.sale_id = s.id
        group by d order by d
        """,
        store_id,
        days,
    )
    return [
        {
            "day": r["day"].isoformat(),
            "sales": float(r["sales"] or 0),
            "orders": int(r["orders"] or 0),
            "units": float(r["units"] or 0),
        }
        for r in rows
    ]


async def day_of_week_pattern(store_id: str, days: int = 56) -> dict[str, Any]:
    """Average sales by weekday over the window — needs >= 4 weeks to matter."""
    rows = await db.fetch(
        """
        select extract(dow from s.created_at)::int as dow,
               count(distinct s.id) as orders,
               coalesce(sum(s.total), 0) as sales
        from sales s
        where s.store_id = $1 and s.status = 'completed'
          and s.created_at >= current_date - $2::int
        group by dow
        """,
        store_id,
        days,
    )
    names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    counts = await db.fetchval(
        """
        select count(distinct (created_at::date))
        from sales where store_id = $1 and status = 'completed'
          and created_at >= current_date - $2::int
        """,
        store_id,
        days,
    )
    weeks_covered = (int(counts or 0)) / 7.0
    pattern = []
    for r in rows:
        occurrences = max(weeks_covered, 1)
        pattern.append({
            "weekday": names[int(r["dow"])],
            "avg_daily_sales": round(float(r["sales"] or 0) / occurrences, 2),
            "avg_orders": round(int(r["orders"] or 0) / occurrences, 1),
        })
    order = {n: i for i, n in enumerate(names)}
    pattern.sort(key=lambda p: order[p["weekday"]])
    return {
        "window_days": days,
        "distinct_days_with_sales": int(counts or 0),
        "sufficient_data": weeks_covered >= 4,
        "pattern": pattern,
        "note": (
            None if weeks_covered >= 4
            else f"Only ~{round(weeks_covered, 1)} weeks of sales history — weekday averages are indicative only."
        ),
    }


# ---------------------------------------------------------------- forecast


async def forecast_product_demand(
    store_id: str, product_id: str, horizon_days: int = 7
) -> dict[str, Any]:
    """Transparent range forecast. Outputs a RANGE, never a point promise.

    baseline = recent-7d daily rate, blended toward the 30d rate when both
    exist; stockout-corrected when availability data shows missed days.
    sigma = coefficient of variation of daily units over the recent window,
    clamped to [0.15, 0.6]. Range = baseline*(1-sigma) .. baseline*(1+sigma).
    """
    horizon_days = max(1, min(int(horizon_days), 30))
    prod = await db.fetchrow(
        """
        select p.id, p.name, coalesce(i.quantity, 0) as stock
        from products p
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        where p.id = $1 and p.store_id = $2
        """,
        product_id,
        store_id,
    )
    if not prod:
        raise ValueError("Product not found")

    daily = await db.fetch(
        """
        select d::date as day, coalesce(sum(si.quantity), 0) as units
        from generate_series(current_date - 29, current_date - 1, interval '1 day') d
        left join sales s on s.created_at >= d and s.created_at < d + interval '1 day'
          and s.store_id = $1 and s.status = 'completed'
        left join sale_items si on si.sale_id = s.id and si.product_id = $2
        group by d order by d
        """,
        store_id,
        product_id,
    )
    units = [float(r["units"] or 0) for r in daily]
    total30 = sum(units)
    recent7 = sum(units[-7:])
    prev7 = sum(units[-14:-7])
    days_with_any_sale = len([u for u in units if u > 0])

    if total30 <= 0:
        return {
            "product_id": product_id,
            "name": prod["name"],
            "horizon_days": horizon_days,
            "status": "INSUFFICIENT_DATA",
            "data_quality": "INSUFFICIENT_DATA",
            "explanation": "No sales in the last 30 days — no basis for a demand estimate.",
            "forecast_range": None,
        }

    availability = await _availability_ratio(store_id, product_id, 30)
    base_daily = recent7 / 7.0
    if prev7 > 0:
        ratio = max(0.5, min(2.0, (recent7 / prev7)))
        base_daily = (recent7 / 7.0) * (0.6 + 0.4 * ratio)  # bounded blend
    else:
        base_daily = max(base_daily, total30 / 30.0)
    if availability is not None and availability < 0.85:
        base_daily *= 1.0 / max(availability, 0.25)

    mean = total30 / 30.0
    if mean > 0:
        variance = sum((u - mean) ** 2 for u in units) / len(units)
        cv = math.sqrt(variance) / mean
    else:
        cv = 0.6
    sigma = max(0.15, min(0.6, cv))

    baseline_total = base_daily * horizon_days
    low = max(baseline_total * (1 - sigma), 0.0)
    high = baseline_total * (1 + sigma)

    if days_with_any_sale < 5:
        quality = "LOW"
    elif total30 < 20:
        quality = "MEDIUM"
    else:
        quality = "HIGH"
    if availability is not None and availability < 0.5:
        quality = "LOW"

    explanation = (
        f"Based on {total30:.0f} units sold in the last 30 days "
        f"({recent7:.0f} in the most recent week)."
    )
    if availability is not None and availability < 0.85:
        explanation += (
            f" Adjusted upward for stockouts: the product was unavailable ~{round((1-availability)*100)}% of days."
        )
    if days_with_any_sale < 5:
        explanation += " Sales happened on few days only — treat the range as wide and indicative."

    return {
        "product_id": product_id,
        "name": prod["name"],
        "horizon_days": horizon_days,
        "status": "ok",
        "data_quality": quality,
        "baseline_daily_units": round(base_daily, 2),
        "forecast_range": {
            "low": math.floor(low),
            "high": math.ceil(high),
            "unit": "units",
            "period": f"next {horizon_days} days",
        },
        "current_stock": int(prod["stock"]),
        "availability_ratio_30d": availability,
        "explanation": explanation,
        "method": "recent-7d daily rate blended with previous week (bounded), stockout-corrected; range from day-to-day variability",
    }


async def demand_overview(store_id: str) -> dict[str, Any]:
    """Dashboard payload: overview KPIs, rising/falling products, category trends."""
    trends = await product_trends_bulk(store_id)
    categories = await category_trends(store_id)

    weekly = await db.fetchrow(
        """
        select
          coalesce(sum(total) filter (where created_at >= current_date - 7), 0) as sales_7d,
          coalesce(sum(total) filter (where created_at >= current_date - 14 and created_at < current_date - 7), 0) as sales_prev_7d
        from sales where store_id = $1 and status = 'completed'
        """,
        store_id,
    )
    s7, s7p = float(weekly["sales_7d"] or 0), float(weekly["sales_prev_7d"] or 0)

    rising = [t for t in trends if t["trend"] == "RISING"]
    falling = [t for t in trends if t["trend"] in ("FALLING", "WATCH") and t["change_7d_pct"] is not None and t["change_7d_pct"] < 0]
    rising.sort(key=lambda t: -(t["change_7d_pct"] or 0))
    falling.sort(key=lambda t: (t["change_7d_pct"] or 0))

    analyzed = [t for t in trends if t["trend"] not in ("INSUFFICIENT_DATA",)]
    return {
        "overview": {
            "sales_recent_7d": s7,
            "sales_previous_7d": s7p,
            "change_7d_pct": _pct_change(s7, s7p),
            "products_analyzed": len(analyzed),
            "products_insufficient_data": len(trends) - len(analyzed),
            "rising_count": len(rising),
            "falling_count": len(falling),
            "method_note": "Windows compare the recent 7 days to the immediately preceding 7 days.",
        },
        "rising_products": rising[:10],
        "falling_products": falling[:10],
        "category_trends": categories,
        "watch_products": [t for t in trends if t["trend"] == "WATCH"][:10],
    }


async def demand_summary(store_id: str) -> dict[str, Any]:
    """Compact AI-tool summary."""
    overview = await demand_overview(store_id)
    dow = await day_of_week_pattern(store_id)
    return {
        **overview["overview"],
        "rising_top": [
            {"name": t["name"], "change_7d_pct": t["change_7d_pct"], "units_recent_7d": t["recent_7d_units"]}
            for t in overview["rising_products"][:5]
        ],
        "falling_top": [
            {"name": t["name"], "change_7d_pct": t["change_7d_pct"], "units_recent_7d": t["recent_7d_units"]}
            for t in overview["falling_products"][:5]
        ],
        "category_trends": overview["category_trends"][:8],
        "day_of_week": dow if dow["sufficient_data"] else {
            "sufficient_data": False,
            "note": dow["note"],
        },
    }
