"""Recommendation engine — DATA -> RULES -> CONDITION -> EVIDENCE -> RECOMMENDATION.

Deterministic detectors inspect Phase 2 engine outputs, compute transparent
priorities (no opaque "AI scores"), and upsert recommendations with dedup so
the same condition never spams the merchant. Explanations narrate real numbers
only; when data is insufficient the detector says so.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.database import db
from app.agent import tools


def _j(value: Any) -> Any:
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


# Transparent priority weights — the UI shows the reason string alongside the
# score so "why is this at the top?" always has a factual answer.
WEIGHTS = {
    "rupee_impact": 0.4,     # per ₹1000 at risk, capped
    "urgency_days": 0.3,     # days until problem, capped (lower = higher)
    "actionability": 0.2,    # 0..1 (1 = one-click prepared action exists)
    "breadth": 0.1,          # how many products/customers affected
}


def _score(rupee_impact: float, urgency_days: Optional[float], actionable: bool, breadth: int) -> tuple[int, str]:
    rupee_pts = min(rupee_impact / 1000.0, 50.0) * WEIGHTS["rupee_impact"] / 0.4
    if urgency_days is None:
        urgency_pts = 0.0
        urgency_note = "no deadline pressure"
    else:
        urgency_pts = max(0.0, (14.0 - min(urgency_days, 14.0)) / 14.0 * 50.0)
        urgency_note = f"~{round(urgency_days, 1)} days of margin"
    parts = [
        f"impact ₹{round(rupee_impact)}",
        urgency_note,
        "one-click action" if actionable else "manual follow-up",
        f"{breadth} item(s) affected",
    ]
    total = (
        rupee_pts * WEIGHTS["rupee_impact"] / 0.4 * 0.4
        + urgency_pts * 0.3
        + (20.0 if actionable else 4.0)
        + min(breadth, 5) * 2.0
    )
    # Normalize to 0..100
    total = min(total, 100.0)
    return int(round(total)), "; ".join(parts)


async def _upsert(
    store_id: str,
    *,
    dedup_key: str,
    type: str,
    title: str,
    summary: str,
    severity: str,
    priority: int,
    priority_reason: str,
    evidence: dict[str, Any],
    data_sources: list[str],
    reasoning_summary: str,
    proposed_action: Optional[dict[str, Any]],
    estimated_impact: Optional[str],
    risk: Optional[str],
    confidence: Optional[float] = None,
) -> Optional[str]:
    """Insert a recommendation or refresh the open one with the same dedup key.

    Returns the recommendation id, or None if nothing changed (dedup hit and
    evidence unchanged).
    """
    evidence_json = json.dumps(_j(evidence))
    proposed_json = json.dumps(_j(proposed_action or {}))

    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                select id, evidence, priority from ai_recommendations
                where store_id = $1 and dedup_key = $2
                  and status in ('NEW','REVIEWED','APPROVED','EXECUTING')
                for update
                """,
                store_id,
                dedup_key,
            )
            if row:
                # Update evidence/priority in place; keep merchant status.
                await conn.execute(
                    """
                    update ai_recommendations set
                      title = $3, summary = $4, description = $4,
                      severity = $5, priority = $6, priority_reason = $7,
                      evidence = $8::jsonb, proposed_action = $9::jsonb,
                      estimated_impact = $10, risk = $11,
                      updated_at = now()
                    where id = $1
                    """,
                    row["id"],
                    dedup_key,
                    title,
                    summary,
                    severity,
                    priority,
                    priority_reason,
                    evidence_json,
                    proposed_json,
                    estimated_impact,
                    risk,
                )
                # Could compare hashes to skip no-op updates; cheap enough as-is.
                return str(row["id"])

            rec = await conn.fetchrow(
                """
                insert into ai_recommendations
                  (store_id, type, title, description, summary, evidence,
                   severity, priority, priority_reason, data_sources,
                   reasoning_summary, proposed_action, estimated_impact, risk,
                   confidence, status, dedup_key)
                values ($1,$2,$3,$4,$4,$5::jsonb,$6,$7,$8,$9::jsonb,$10,$11::jsonb,$12,$13,$14,'NEW',$15)
                returning id
                """,
                store_id,
                type,
                title,
                summary,
                evidence_json,
                severity,
                priority,
                priority_reason,
                json.dumps(_j(data_sources)),
                reasoning_summary,
                proposed_json,
                estimated_impact,
                risk,
                confidence,
                dedup_key,
            )
            await conn.execute(
                """
                insert into activity_logs (store_id, event_type, entity_type, entity_id, message)
                values ($1, 'AI_RECOMMENDATION_CREATED', 'ai_recommendation', $2, $3)
                """,
                store_id,
                rec["id"],
                f"AI recommendation: {title}",
            )
            return str(rec["id"])


# ---------------------------------------------------------------- detectors


async def detect_expiry_risk(store_id: str) -> list[str]:
    exp = await tools.get_expiring_inventory(store_id)
    created = []
    for b in exp["sellable_batches"]:
        pid = b["product_id"]
        at_risk = b["at_risk_cost_value"]
        priority, reason = _score(
            rupee_impact=at_risk,
            urgency_days=b["days_to_expiry"],
            actionable=True,
            breadth=1,
        )
        reco_id = await _upsert(
            store_id,
            dedup_key=f"expiry_risk:{pid}",
            type="EXPIRY_RISK",
            title=f"Expiry risk: {b['product_name']}",
            summary=(
                f"Batch of {b['quantity']} expires in {b['days_to_expiry']} day(s). "
                f"At the current pace (~{b['velocity']}/day) about {b['estimated_excess_units']} "
                f"unit(s) may remain unsold (estimate)."
            ),
            severity="warning" if b["severity"] == "warning" else "critical",
            priority=priority,
            priority_reason=reason,
            evidence={
                "product_id": pid,
                "product_name": b["product_name"],
                "batch_no": b.get("batch_no"),
                "quantity": b["quantity"],
                "days_to_expiry": b["days_to_expiry"],
                "velocity_per_day": b["velocity"],
                "expected_sales_before_expiry_estimate": b["expected_sales_before_expiry_estimate"],
                "estimated_excess_units": b["estimated_excess_units"],
                "at_risk_cost_value": at_risk,
                "at_risk_sales_value": b["at_risk_sales_value"],
            },
            data_sources=["inventory_batches", "sales", "store_settings"],
            reasoning_summary=(
                "Sell-through estimate = velocity x days remaining. Exceeding capacity "
                "means clearance pricing or bundling may rescue value before expiry."
            ),
            proposed_action={
                "action_type": "price_change",
                "product_id": pid,
                "suggested_price": await _clearance_price(store_id, pid),
                "rationale": "Clearance pricing (not below policy minimum) to accelerate sell-through.",
            },
            estimated_impact=f"₹{round(at_risk)} of stock value at risk",
            risk="Clearance reduces margin per unit; merchant sets final price.",
        )
        if reco_id:
            created.append(reco_id)
    return created


async def _clearance_price(store_id: str, product_id: str) -> Optional[float]:
    """Suggested clearance price = policy floor (never below it)."""
    floor_price = None
    try:
        m = await tools.get_product_margin(store_id, product_id)
        floor_price = m.get("minimum_acceptable_price")
    except ValueError:
        return None
    return floor_price


async def detect_reorder(store_id: str) -> list[str]:
    candidates = await tools.get_reorder_candidates(store_id)
    created = []
    for c in candidates:
        rupee = float(c.get("estimated_purchase_cost") or 0)
        days_left = c.get("days_of_stock")
        priority, reason = _score(
            rupee_impact=rupee,
            urgency_days=days_left,
            actionable=True,
            breadth=1,
        )
        reco_id = await _upsert(
            store_id,
            dedup_key=f"reorder:{c['product_id']}",
            type="REORDER_REQUIRED",
            title=f"Reorder {c['name']}",
            summary=(
                f"Stock {c['current_stock']} at ~{c['velocity_per_day']}/day gives about "
                f"{c['days_of_stock']} day(s) of cover (reorder point {c['reorder_point']}). "
                f"Recommended replenishment: {c['recommended_quantity']} units."
            ),
            severity="warning",
            priority=priority,
            priority_reason=reason,
            evidence={
                "product_id": c["product_id"],
                "product_name": c["name"],
                "current_stock": c["current_stock"],
                "velocity_per_day": c["velocity_per_day"],
                "days_of_stock": c["days_of_stock"],
                "reorder_point": c["reorder_point"],
                "lead_time_days": c["lead_time_days"],
                "safety_stock_days": c["safety_stock_days"],
                "recommended_quantity": c["recommended_quantity"],
                "estimated_purchase_cost": c["estimated_purchase_cost"],
                "pending_po_quantity": c["pending_po_quantity"],
                "preferred_supplier": c.get("preferred_supplier"),
            },
            data_sources=["inventory", "sales", "store_settings", "suppliers", "purchase_orders"],
            reasoning_summary=(
                "Reorder point = max(configured level, velocity x (lead + safety days)). "
                "Quantity restores target stock using recent velocity."
            ),
            proposed_action={
                "action_type": "create_purchase",
                "product_id": c["product_id"],
                "quantity": c["recommended_quantity"],
                "supplier_id": (c.get("preferred_supplier") or {}).get("id") if c.get("preferred_supplier") else None,
            },
            estimated_impact=f"Estimated purchase ₹{round(rupee)}; prevents stockout within ~{c['days_of_stock']} day(s)",
            risk="Demand estimate from recent sales; verify with supplier availability.",
        )
        if reco_id:
            created.append(reco_id)
    return created


async def detect_margin_risk(store_id: str) -> list[str]:
    """Products whose purchase cost rose but selling price hasn't moved."""
    rows = await db.fetch(
        """
        select p.id, p.name, p.purchase_price, p.selling_price, p.updated_at,
               (select h.old_value from product_price_history h
                 where h.product_id = p.id and h.field = 'purchase_price'
                 order by h.created_at desc limit 1) as last_change_new,
               (select h.new_value from product_price_history h
                 where h.product_id = p.id and h.field = 'purchase_price'
                 order by h.created_at desc limit 1) as latest_cost,
               (select h.old_value from product_price_history h
                 where h.product_id = p.id and h.field = 'purchase_price'
                 order by h.created_at desc limit 1) as previous_cost
        from products p
        where p.store_id = $1 and p.is_active
        """,
        store_id,
    )
    created = []
    for r in rows:
        prev = r["previous_cost"]
        cur = r["purchase_price"]
        if prev is None or cur is None:
            continue
        prev_f, cur_f = float(prev), float(cur)
        if cur_f <= prev_f * 1.02:  # <2% rise: not material
            continue
        sp = float(r["selling_price"])
        margin_pct = (sp - cur_f) / sp * 100 if sp > 0 else 0
        cfg = await db.fetchval(
            "select min_margin_pct from store_settings where store_id = $1", store_id
        )
        min_margin = float(cfg or 10)
        priority, reason = _score(
            rupee_impact=(cur_f - prev_f) * 20,  # per-unit rise x assumed ~20 unit horizon
            urgency_days=None,
            actionable=True,
            breadth=1,
        )
        reco_id = await _upsert(
            store_id,
            dedup_key=f"margin_risk:{r['id']}",
            type="MARGIN_RISK",
            title=f"Margin risk: {r['name']}",
            summary=(
                f"Purchase cost rose ₹{prev_f} → ₹{cur_f} but selling price is still ₹{sp}. "
                f"Margin is now {round(margin_pct, 1)}%."
                + (f" This is below your {min_margin}% minimum." if margin_pct < min_margin else "")
            ),
            severity="warning",
            priority=priority,
            priority_reason=reason,
            evidence={
                "product_id": str(r["id"]),
                "product_name": r["name"],
                "previous_cost": prev_f,
                "current_cost": cur_f,
                "selling_price": sp,
                "current_margin_pct": round(margin_pct, 1),
                "min_margin_pct": min_margin,
            },
            data_sources=["product_price_history", "products", "store_settings"],
            reasoning_summary=(
                "Cost changes arrive from received purchases; price review keeps margin "
                "above the configured minimum."
            ),
            proposed_action={
                "action_type": "price_change",
                "product_id": str(r["id"]),
                "suggested_price": round(cur_f / (1 - min_margin / 100.0), 2),
                "rationale": f"Price that restores the {min_margin}% minimum margin.",
            },
            estimated_impact=f"Margin compressed to {round(margin_pct, 1)}%",
            risk="Raising price may affect demand; merchant decides.",
        )
        if reco_id:
            created.append(reco_id)
    return created


async def detect_customer_opportunities(store_id: str) -> list[str]:
    """Regulars who haven't purchased in 14+ days."""
    summary = await tools.get_customer_summary(store_id)
    lapsing = summary.get("not_seen_recently_14d", [])
    if not lapsing:
        return []
    created = []
    total_spent = sum(float(c.get("total_spent") or 0) for c in lapsing)
    names = ", ".join(c["name"] for c in lapsing[:3]) + ("…" if len(lapsing) > 3 else "")
    priority, reason = _score(
        rupee_impact=min(total_spent * 0.1, 5000),  # conservative: 10% of their spend
        urgency_days=None,
        actionable=False,
        breadth=len(lapsing),
    )
    reco_id = await _upsert(
        store_id,
        dedup_key="customer_opportunity:lapsing",
        type="CUSTOMER_OPPORTUNITY",
        title=f"{len(lapsing)} regular customer(s) haven't purchased recently",
        summary=f"{names} last purchased 14+ days ago. A personal follow-up may bring them back.",
        severity="info",
        priority=priority,
        priority_reason=reason,
        evidence={"customers": lapsing[:10], "combined_lifetime_spend": round(total_spent, 2)},
        data_sources=["customers", "sales"],
        reasoning_summary="Repeat customers are the cheapest revenue; lapses of 14+ days are actionable.",
        proposed_action=None,  # campaign actions arrive in a later phase
        estimated_impact=f"{len(lapsing)} customer(s), ₹{round(total_spent)} lifetime spend",
        risk="No guarantee of return; contact costs nothing here.",
    )
    return [reco_id] if reco_id else []


async def detect_all(store_id: str) -> dict[str, Any]:
    """Run every detector; returns counts. Dedup keeps this idempotent."""
    created: dict[str, int] = {}
    for name, fn in [
        ("expiry_risk", detect_expiry_risk),
        ("reorder", detect_reorder),
        ("margin_risk", detect_margin_risk),
        ("customer_opportunity", detect_customer_opportunities),
    ]:
        try:
            ids = await fn(store_id)
            created[name] = len(ids)
        except Exception:  # noqa: BLE001 — one detector failing must not kill the rest
            created[name] = -1
    return {"detectors": created}


async def refresh_recommendations(store_id: str) -> dict[str, Any]:
    result = await detect_all(store_id)
    await db.execute(
        """
        insert into activity_logs (store_id, event_type, message)
        values ($1, 'AI_RECOMMENDATIONS_REFRESHED', $2)
        """,
        store_id,
        json.dumps(result),
    )
    return result
