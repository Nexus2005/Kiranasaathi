"""Customer intelligence — deterministic RFM + behavioral segments.

Rules (all transparent, shown with the evidence):
- Recency: days since last completed purchase.
- Frequency: total completed orders.
- Monetary: lifetime spend.
- Segments are assigned from explicit thresholds, in priority order.
  A customer can appear in multiple segments; the 'primary_segment' is the
  first match in that priority order.

No invented attributes: everything comes from customers + sales(+items).
Consent fields gate all marketing eligibility.
"""

from __future__ import annotations

from typing import Any, Optional

from app.database import db

# Deterministic segment thresholds (documented, shown in UI)
INACTIVE_DAYS = 30          # no purchase in 30+ days => INACTIVE
RECENTLY_INACTIVE_DAYS = 14 # last purchase 14-30 days ago w/ frequent history => RECENTLY_INACTIVE
HIGH_FREQUENCY_ORDERS = 8   # >= 8 lifetime orders => HIGH_FREQUENCY
HIGH_VALUE_SPEND = 5000.0   # lifetime spend >= ₹5000 => HIGH_VALUE
NEW_DAYS = 7                # registered within 7 days => NEW_CUSTOMER
AVG_INTERVAL_MIN_ORDERS = 3 # need >= 3 orders to estimate a personal purchase interval


def _interval_days(rows_count: int, first_created, last_created) -> Optional[float]:
    if rows_count < 2 or first_created is None or last_created is None:
        return None
    span = (last_created - first_created).total_seconds() / 86400.0
    return round(span / (rows_count - 1), 1)


def _segments(
    *, orders: int, total_spent: float, days_since_last: Optional[int],
    created_days_ago: int, avg_interval: Optional[float],
    top_category: Optional[str],
) -> list[str]:
    segs: list[str] = []
    if created_days_ago <= NEW_DAYS and orders <= 1:
        segs.append("NEW_CUSTOMER")
    if orders >= 2:
        segs.append("REPEAT_CUSTOMER")
    if orders >= HIGH_FREQUENCY_ORDERS:
        segs.append("HIGH_FREQUENCY")
    if total_spent >= HIGH_VALUE_SPEND:
        segs.append("HIGH_VALUE")
    if top_category and orders >= 3:
        segs.append(f"CATEGORY_SPECIFIC:{top_category}")
    if days_since_last is not None:
        if days_since_last >= INACTIVE_DAYS and orders >= 2:
            segs.append("INACTIVE_CUSTOMER")
        elif (
            RECENTLY_INACTIVE_DAYS <= days_since_last < INACTIVE_DAYS
            and orders >= AVG_INTERVAL_MIN_ORDERS
            and avg_interval is not None
            and days_since_last > avg_interval * 2
        ):
            segs.append("RECENTLY_INACTIVE")
        elif orders >= 1 and days_since_last <= RECENTLY_INACTIVE_DAYS:
            segs.append("ACTIVE_CUSTOMER")
    return segs


async def customer_overview(store_id: str) -> dict[str, Any]:
    """Segment distribution + counts for the Customers page / AI tools."""
    rows = await db.fetch(
        """
        select c.id, c.name, c.phone, c.marketing_consent, c.created_at,
               count(s.id) as orders,
               coalesce(sum(s.total), 0) as total_spent,
               max(s.created_at) as last_purchase,
               min(s.created_at) as first_purchase
        from customers c
        left join sales s on s.customer_id = c.id and s.status = 'completed'
        where c.store_id = $1
        group by c.id, c.name, c.phone, c.marketing_consent, c.created_at
        """,
        store_id,
    )
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    items = []
    counts: dict[str, int] = {}
    for r in rows:
        orders = int(r["orders"] or 0)
        spent = float(r["total_spent"] or 0)
        last = r["last_purchase"]
        days_since = (now - last).days if last else None
        interval = _interval_days(orders, r["first_purchase"], last)
        created_days_ago = (now - r["created_at"]).days
        segs = _segments(
            orders=orders, total_spent=spent, days_since_last=days_since,
            created_days_ago=created_days_ago, avg_interval=interval, top_category=None,
        )
        if not segs:
            segs = ["NO_PURCHASE_YET"]
        for s in segs:
            counts[s] = counts.get(s, 0) + 1
        items.append({
            "customer_id": str(r["id"]),
            "name": r["name"],
            "orders": orders,
            "total_spent": round(spent, 2),
            "days_since_last_purchase": days_since,
            "avg_purchase_interval_days": interval,
            "marketing_consent": bool(r["marketing_consent"]),
            "segments": segs,
        })
    with_phone = len([i for i in items if i["orders"] > 0])
    return {
        "total_customers": len(items),
        "with_purchase_history": with_phone,
        "segment_counts": counts,
        "thresholds": {
            "inactive_days": INACTIVE_DAYS,
            "recently_inactive_days": RECENTLY_INACTIVE_DAYS,
            "high_frequency_orders": HIGH_FREQUENCY_ORDERS,
            "high_value_spend": HIGH_VALUE_SPEND,
            "new_customer_days": NEW_DAYS,
        },
        "customers": items,
    }


async def customer_segments(
    store_id: str, segment: Optional[str] = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Customers in one segment (or all), enriched with RFM evidence."""
    overview = await customer_overview(store_id)
    items = overview["customers"]
    if segment:
        # allow exact or prefix (CATEGORY_SPECIFIC:Snacks)
        items = [i for i in items if any(s == segment or s.startswith(segment + ":") for s in i["segments"])]
    items.sort(key=lambda i: -i["total_spent"])
    return items[: max(1, min(limit, 500))]


async def customer_360(store_id: str, customer_id: str) -> dict[str, Any]:
    """Full customer intelligence view for the detail page + AI tools."""
    cust = await db.fetchrow(
        """
        select c.*, 
               (now() - c.created_at) as customer_age
        from customers c where c.id = $1 and c.store_id = $2
        """,
        customer_id,
        store_id,
    )
    if not cust:
        raise ValueError("Customer not found")

    agg = await db.fetchrow(
        """
        select count(*) as orders,
               coalesce(sum(total), 0) as total_spent,
               coalesce(avg(total), 0) as avg_order_value,
               max(created_at) as last_purchase,
               min(created_at) as first_purchase
        from sales where customer_id = $1 and store_id = $2 and status = 'completed'
        """,
        customer_id,
        store_id,
    )
    orders = int(agg["orders"] or 0)
    last = agg["last_purchase"]
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    days_since = (now - last).days if last else None
    interval = _interval_days(orders, agg["first_purchase"], last)

    fav_products = await db.fetch(
        """
        select p.id as product_id, p.name, p.category,
               sum(si.quantity) as units, count(*) as times,
               coalesce(sum(si.line_total), 0) as spend
        from sale_items si
        join sales s on s.id = si.sale_id
        join products p on p.id = si.product_id
        where s.customer_id = $1 and s.store_id = $2 and s.status = 'completed'
        group by p.id, p.name, p.category
        order by units desc limit 8
        """,
        customer_id,
        store_id,
    )
    fav_categories = await db.fetch(
        """
        select p.category, sum(si.quantity) as units,
               coalesce(sum(si.line_total), 0) as spend
        from sale_items si
        join sales s on s.id = si.sale_id
        join products p on p.id = si.product_id
        where s.customer_id = $1 and s.store_id = $2 and s.status = 'completed'
        group by p.category order by spend desc limit 5
        """,
        customer_id,
        store_id,
    )
    recent = await db.fetch(
        """
        select s.id, s.total, s.created_at, s.payment_method,
               coalesce(
                 (select json_agg(json_build_object('product_id', si.product_id, 'name', p.name, 'quantity', si.quantity, 'line_total', si.line_total))
                  from sale_items si join products p on p.id = si.product_id
                  where si.sale_id = s.id), '[]'::json) as items
        from sales s
        where s.customer_id = $1 and s.store_id = $2 and s.status = 'completed'
        order by s.created_at desc limit 15
        """,
        customer_id,
        store_id,
    )
    campaigns = await db.fetch(
        """
        select cr.id, cr.status, cr.sent_at, cr.error,
               c.id as campaign_id, c.name as campaign_name, c.campaign_type, c.message_text
        from campaign_recipients cr
        join campaigns c on c.id = cr.campaign_id
        where cr.customer_id = $1 and cr.store_id = $2
        order by cr.created_at desc limit 10
        """,
        customer_id,
        store_id,
    )

    top_category = fav_categories[0]["category"] if fav_categories else None
    created_days_ago = (now - cust["created_at"]).days
    segs = _segments(
        orders=orders, total_spent=float(agg["total_spent"] or 0),
        days_since_last=days_since, created_days_ago=created_days_ago,
        avg_interval=interval, top_category=top_category,
    )
    if not segs:
        segs = ["NO_PURCHASE_YET"]

    # Campaign opportunity: only ever an observation + eligibility, never auto-send
    opportunity = None
    if days_since is not None and orders >= 2:
        if days_since >= INACTIVE_DAYS:
            opportunity = {
                "type": "RE_ENGAGEMENT",
                "evidence": f"Last purchase {days_since} days ago across {orders} orders.",
            }
        elif days_since is not None and interval is not None and days_since > interval * 1.5:
            opportunity = {
                "type": "RECENTLY_INACTIVE",
                "evidence": f"Usually buys every ~{interval} days; last purchase {days_since} days ago.",
            }
    if orders > 0 and top_category:
        opportunity = opportunity or {
            "type": "CATEGORY_PROMOTION",
            "evidence": f"Most-purchased category: {top_category}.",
        }

    d = dict(cust)
    d["id"] = str(d["id"])
    return {
        "customer": {
            "id": d["id"],
            "name": d["name"],
            "phone": d["phone"],
            "created_at": d["created_at"].isoformat() if d["created_at"] else None,
            "marketing_consent": bool(d["marketing_consent"]),
            "consent_source": d["consent_source"],
            "consent_timestamp": d["consent_timestamp"].isoformat() if d["consent_timestamp"] else None,
            "opt_out_timestamp": d["opt_out_timestamp"].isoformat() if d["opt_out_timestamp"] else None,
            "notes": d["notes"],
        },
        "rfm": {
            "orders": orders,
            "total_spent": round(float(agg["total_spent"] or 0), 2),
            "avg_order_value": round(float(agg["avg_order_value"] or 0), 2),
            "days_since_last_purchase": days_since,
            "avg_purchase_interval_days": interval,
            "last_purchase": last.isoformat() if last else None,
            "first_purchase": agg["first_purchase"].isoformat() if agg["first_purchase"] else None,
        },
        "segments": segs,
        "favorite_products": [
            {
                "product_id": str(r["product_id"]), "name": r["name"], "category": r["category"],
                "units": int(r["units"]), "times_purchased": int(r["times"]), "spend": float(r["spend"]),
            }
            for r in fav_products
        ],
        "favorite_categories": [
            {"category": r["category"], "units": int(r["units"]), "spend": float(r["spend"])}
            for r in fav_categories
        ],
        "recent_orders": [
            {
                "id": str(r["id"]), "total": float(r["total"]),
                "created_at": r["created_at"].isoformat(), "payment_method": r["payment_method"],
                "items": r["items"] if isinstance(r["items"], list) else [],
            }
            for r in recent
        ],
        "campaign_history": [
            {
                "campaign_id": str(r["campaign_id"]), "campaign_name": r["campaign_name"],
                "type": r["campaign_type"], "status": r["status"],
                "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
                "error": r["error"],
            }
            for r in campaigns
        ],
        "opportunity": opportunity,
    }


async def update_consent(
    store_id: str, user_id: str, customer_id: str,
    *, marketing_consent: bool, consent_source: Optional[str],
) -> dict[str, Any]:
    """Merchant records consent change. Audited. Refuses invalid sources."""
    valid_sources = ("store_entry", "verbal", "written", "signup", "import")
    if consent_source is not None and consent_source not in valid_sources:
        raise ValueError(f"consent_source must be one of {valid_sources}")
    row = await db.fetchrow(
        """
        update customers
        set marketing_consent = $3,
            consent_source = case when $3 then coalesce($4, consent_source) else null end,
            consent_timestamp = case when $3 then now() else consent_timestamp end,
            opt_out_timestamp = case when $3 then null else now() end
        where id = $1 and store_id = $2
        returning id, marketing_consent
        """,
        customer_id,
        store_id,
        marketing_consent,
        consent_source,
    )
    if not row:
        raise ValueError("Customer not found")
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
        values ($1, $2, 'CUSTOMER_CONSENT_UPDATED', 'customer', $3, $4::jsonb, $5)
        """,
        store_id,
        user_id,
        customer_id,
        __import__("json").dumps({"marketing_consent": marketing_consent, "source": consent_source}),
        f"Marketing consent {'granted' if marketing_consent else 'revoked'}",
    )
    return {"customer_id": str(row["id"]), "marketing_consent": row["marketing_consent"]}


async def eligible_campaign_audience(
    store_id: str,
    *,
    segment: Optional[str] = None,
    category: Optional[str] = None,
    product_id: Optional[str] = None,
) -> dict[str, Any]:
    """Compute eligible recipients: phone present + marketing consent + opt-out respected.

    Category/product targeting uses ACTUAL purchase history (sale_items) only.
    If no historical evidence exists, the eligible set is limited to
    consent+phone customers with no history filter — and the caller is told.
    """
    base = """
        select c.id, c.name, c.phone, c.marketing_consent
        from customers c
        where c.store_id = $1 and c.marketing_consent
          and c.phone is not null and length(trim(c.phone)) >= 8
          and c.opt_out_timestamp is null
    """
    evidence_note = None
    rows: list
    if product_id:
        prod = await db.fetchrow(
            "select id, name from products where id = $1 and store_id = $2", product_id, store_id
        )
        if not prod:
            raise ValueError("Product not found")
        rows = await db.fetch(
            base + """
              and exists (
                select 1 from sales s join sale_items si on si.sale_id = s.id
                where s.customer_id = c.id and s.status = 'completed' and si.product_id = $2
              )
            """,
            store_id,
            product_id,
        )
        hist = await db.fetchval(
            """
            select count(distinct s.customer_id) from sales s
            join sale_items si on si.sale_id = s.id
            where s.store_id = $1 and si.product_id = $2 and s.status = 'completed'
            """,
            store_id,
            product_id,
        )
        evidence_note = (
            f"{int(hist or 0)} customer(s) have previously purchased this product."
            if (hist or 0) > 0 else
            "No store-specific purchase history for this product yet — audience is all consented customers with a phone."
        )
    elif category:
        rows = await db.fetch(
            base + """
              and exists (
                select 1 from sales s join sale_items si on si.sale_id = s.id
                join products p on p.id = si.product_id
                where s.customer_id = c.id and s.status = 'completed' and p.category = $2
              )
            """,
            store_id,
            category,
        )
        hist = await db.fetchval(
            """
            select count(distinct s.customer_id) from sales s
            join sale_items si on si.sale_id = s.id join products p on p.id = si.product_id
            where s.store_id = $1 and p.category = $2 and s.status = 'completed'
            """,
            store_id,
            category,
        )
        evidence_note = (
            f"{int(hist or 0)} customer(s) have previously purchased this category."
            if (hist or 0) > 0 else
            "No store-specific purchase history for this category yet — audience is all consented customers with a phone."
        )
    elif segment:
        seg_map = {
            "INACTIVE_CUSTOMER": "and c.id in (select customer_id from sales where store_id = $1 group by customer_id having max(created_at) < now() - interval '30 days')",
            "HIGH_VALUE": "and c.id in (select customer_id from sales where store_id = $1 group by customer_id having sum(total) >= 5000)",
            "HIGH_FREQUENCY": "and c.id in (select customer_id from sales where store_id = $1 group by customer_id having count(*) >= 8)",
            "NEW_CUSTOMER": "and c.created_at >= now() - interval '7 days'",
        }
        extra = seg_map.get(segment)
        if extra is None:
            raise ValueError(f"Unknown segment: {segment}")
        rows = await db.fetch(base + " " + extra, store_id)
        evidence_note = f"Segment rule: {segment} (deterministic thresholds documented on the Customers page)."
    else:
        rows = await db.fetch(base, store_id)
        evidence_note = "All customers with a phone number and marketing consent."

    return {
        "recipients": [
            {"customer_id": str(r["id"]), "name": r["name"], "phone": r["phone"]}
            for r in rows
        ],
        "count": len(rows),
        "evidence": evidence_note,
    }
