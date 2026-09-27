"""Agent orchestrator — intent routing, multi-tool plans, deterministic answers.

The orchestrator never invents numbers: every sentence is rendered from the
structured output of the read tools (which reuse the Phase 2 engines), and
every answer ships the evidence alongside the narration.

Engine: `deterministic-rules-v1`. When an LLM key is configured later, the
LLM replaces ONLY the narration/paraphrasing step — tool selection, evidence
and actions stay exactly where they are (architecture note in config).
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.database import db
from app.agent import tools, recommendations as recs
from app.agent.actions import ActionError, approve_and_execute, cancel_action, prepare_action

ENGINE = "deterministic-rules-v1"


def _parse_jsonb(value: Any, default: Any) -> Any:
    """Tolerant jsonb decode — legacy seed rows stored plain text here."""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return {"text": value}
    return default


def _clean_reco_row(r: Any) -> dict[str, Any]:
    """Normalize a recommendation row: parse jsonb strings, stringify ids."""
    d = dict(r)
    d["id"] = str(d["id"])
    d["evidence"] = _parse_jsonb(d.get("evidence"), {})
    d["proposed_action"] = _parse_jsonb(d.get("proposed_action"), {})
    d["data_sources"] = _parse_jsonb(d.get("data_sources"), [])
    return d


def _money(v) -> str:
    try:
        return f"₹{float(v):,.0f}"
    except (TypeError, ValueError):
        return "₹0"


def _num(v) -> str:
    try:
        f = float(v)
        return f"{f:,.1f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "0"


async def _log_conversation(
    store_id: str, user_id: Optional[str], question: str, answer: str,
    tools_used: list[str], recommendations_created: int,
) -> None:
    await db.execute(
        """
        insert into ai_conversations
          (store_id, user_id, question, answer, tools_used, recommendations_created, engine)
        values ($1, $2, $3, $4, $5::jsonb, $6, $7)
        """,
        store_id,
        user_id,
        question[:500],
        answer[:4000],
        json.dumps(tools_used),
        recommendations_created,
        ENGINE,
    )


# ---------------------------------------------------------------- intents


def _match_intent(question: str) -> str:
    q = question.lower()
    if re.search(r"online|customer order|orders.*attention|channel|quick.?commerce|storefront|waiting.*confirm", q):
        return "online_orders"
    if re.search(r"today|brief|should i do|attention|morning|priorit", q):
        return "what_should_i_do_today"
    if re.search(r"diwali|navratri|holi|festival|festiv|sankranti|ganesh|raksha|dussehra|dhanteras|pongal|holiday|upcoming event", q):
        return "festival_prep"
    if re.search(r"market|external|news|government|pib|source|trend report|what.*happening", q):
        return "external_intel"
    if re.search(r"campaign|whatsapp|message.*customer|promote|marketing|offer to customer", q):
        return "marketing"
    if re.search(r"what.*selling|selling.*fast|trend|growing|declining|demand|forecast|what.*stock|next week|fastest", q):
        return "demand"
    if re.search(r"which customer|who.*contact|who.*hasn|inactive customer|segment|loyal", q):
        return "customer_targeting"
    if re.search(r"reorder|restock|purchase.*need|buy", q):
        return "reorder"
    if re.search(r"expire|expiring|expiry|shelf", q):
        return "expiry"
    if re.search(r"profit|margin|loss|why.*(down|lower|less)", q):
        return "profit"
    if re.search(r"sell.*for|price.*₹|can i sell|discount|bargain|offer of", q):
        return "pricing"
    if re.search(r"order.*(status|paid|payment)|payment.*(status|failed|pending)|split|partially paid|did.*pay", q):
        return "order_payment"
    if re.search(r"customer|regular|loyal", q):
        return "customers"
    if re.search(r"supplier|vendor|distributor", q):
        return "suppliers"
    if re.search(r"stock|inventory|dead|slow|overstock", q):
        return "inventory"
    if re.search(r"sales|sold|revenue|orders", q):
        return "sales"
    return "fallback"


# ---------------------------------------------------------------- handlers


async def _h_today(store_id: str, question: str) -> dict[str, Any]:
    """Flagship: inspect every engine, prioritize, generate recommendations."""
    created = await recs.refresh_recommendations(store_id)
    rows = await db.fetch(
        """
        select id, type, title, summary, severity, priority, priority_reason,
               evidence, proposed_action, estimated_impact, risk, status
        from ai_recommendations
        where store_id = $1 and status in ('NEW','REVIEWED','APPROVED','EXECUTING')
        order by priority desc, created_at desc
        limit 6
        """,
        store_id,
    )
    recos = [_clean_reco_row(r) for r in rows]

    today = await tools.get_today_sales(store_id)
    summary = await tools.get_store_summary(store_id)

    # Phase 8: online order actions surface in the flagship brief
    online = await tools.get_orders_needing_attention(store_id)
    online_lines: list[str] = []
    aa = len(online["groups"].get("awaiting_acceptance") or [])
    ify = len(online["groups"].get("in_fulfillment") or [])
    if aa:
        online_lines.append(f"{aa} paid online order(s) waiting for your confirmation.")
    if ify:
        online_lines.append(f"{ify} online order(s) in fulfillment.")

    if not recos:
        text = (
            "All clear right now — no critical stock, expiry or reorder issues detected. "
            f"Today so far: {_money(today['today']['sales'])} from {today['today']['orders']} order(s)."
        )
    else:
        top = recos[0]
        lines = [f"{len(recos)} thing(s) need your attention.", ""]
        for i, r in enumerate(recos, 1):
            lines.append(
                f"{i}. {r['title']} — {r['summary']} (priority {r['priority']}/100: {r['priority_reason']})"
            )
        lines.append("")
        lines.append(
            f"Top concern: {top['title']}. {top['estimated_impact'] or 'Review the details and approve an action when ready.'}"
        )
        text = "\n".join(lines)
    if online_lines:
        text = text + "\n\n" + " ".join(online_lines)

    return {
        "answer": text,
        "intent": "what_should_i_do_today",
        "tools_used": ["refresh_recommendations", "get_store_summary", "get_today_sales",
                       "inventory_engines", "pricing_engine", "expiry_engine", "reorder_engine"],
        "recommendations": recos,
        "recommendations_created": sum(v for v in created["detectors"].values() if v > 0),
        "brief": {
            "sales_today": today["today"]["sales"],
            "orders_today": today["today"]["orders"],
            "gross_profit_today": today["today"]["gross_profit"],
            "attention_count": len(recos),
            "top_concern": recos[0]["title"] if recos else None,
        },
    }


async def _h_reorder(store_id: str, question: str) -> dict[str, Any]:
    candidates = await tools.get_reorder_candidates(store_id)
    tools_used = ["get_reorder_candidates", "calculate_reorder", "get_inventory_health"]
    if not candidates:
        return {
            "answer": "Nothing needs reordering right now — all products are above their reorder points.",
            "intent": "reorder",
            "tools_used": tools_used,
            "recommendations": [],
        }
    lines = [f"{len(candidates)} product(s) need reordering:", ""]
    recos = []
    for c in candidates[:5]:
        lines.append(
            f"• {c['name']}: stock {c['current_stock']}, ~{c['velocity_per_day']}/day, "
            f"~{c['days_of_stock']} day(s) left → recommend {c['recommended_quantity']} units "
            f"(~{_money(c['estimated_purchase_cost'])})"
        )
        supplier = c.get("preferred_supplier")
        lines.append(
            f"  Last supplier: {supplier['name'] if supplier else 'no purchase history yet'}"
        )
        rec_id = await db.fetchval(
            "select id from ai_recommendations where store_id=$1 and dedup_key=$2 "
            "and status in ('NEW','REVIEWED','APPROVED','EXECUTING')",
            store_id,
            f"reorder:{c['product_id']}",
        )
        if rec_id:
            recos.append({"id": str(rec_id), "type": "REORDER_REQUIRED", "title": f"Reorder {c['name']}",
                          "summary": c["reason"], "evidence": c})
    text = "\n".join(lines)
    return {"answer": text, "intent": "reorder", "tools_used": tools_used, "recommendations": recos}


async def _h_expiry(store_id: str, question: str) -> dict[str, Any]:
    exp = await tools.get_expiring_inventory(store_id)
    batches = exp["sellable_batches"]
    tools_used = ["get_expiring_inventory", "product_velocity"]
    if not batches:
        expired = exp["expired_batches"]
        if expired:
            return {
                "answer": (
                    f"No sellable batches near expiry. {len(expired)} expired batch(es) are already "
                    "separated from stock — remove and write them off; they cannot be sold."
                ),
                "intent": "expiry",
                "tools_used": tools_used,
                "recommendations": [],
            }
        return {
            "answer": "No products currently require expiry action — nothing is near expiry.",
            "intent": "expiry",
            "tools_used": tools_used,
            "recommendations": [],
        }
    total_risk = exp["summary"]["at_risk_cost_value"]
    lines = [f"{len(batches)} batch(es) need attention — {_money(total_risk)} of stock value at risk:", ""]
    for b in batches[:5]:
        lines.append(
            f"• {b['product_name']}: {b['quantity']} units expire in {b['days_to_expiry']} day(s); "
            f"~{b['estimated_excess_units']} may remain unsold (estimate at ~{b['velocity']}/day)"
        )
        lines.append(f"  Suggested: {b['recommendation']}")
    recos = await recs.refresh_recommendations(store_id)
    return {
        "answer": "\n".join(lines),
        "intent": "expiry",
        "tools_used": tools_used + ["refresh_recommendations"],
        "recommendations": [],
        "detectors_run": recos,
    }


async def _h_profit(store_id: str, question: str) -> dict[str, Any]:
    analysis = await tools.analyze_profit_change(store_id, 7)
    totals = analysis["totals"]
    profit_now = float(totals.get("profit_now") or 0)
    profit_prev = float(totals.get("profit_prev") or 0)
    sales_now = float(totals.get("sales_now") or 0)
    sales_prev = float(totals.get("sales_prev") or 0)
    contributors = analysis["contributors"]
    tools_used = ["analyze_profit_change", "get_product_margin", "get_product_cost_history"]

    delta = profit_now - profit_prev
    if not contributors:
        if profit_prev == 0 and profit_now == 0:
            text = "No completed sales with cost data in the last two weeks, so I can't compute a profit change yet."
        else:
            text = (
                f"Gross profit is {_money(profit_now)} this week vs {_money(profit_prev)} last week — "
                "no single product explains a material change."
            )
        return {"answer": text, "intent": "profit", "tools_used": tools_used, "recommendations": []}

    direction = "down" if delta < 0 else "up"
    lines = [
        f"Gross profit is {direction} {_money(abs(delta))} this week "
        f"({_money(profit_now)} vs {_money(profit_prev)}), while sales went "
        f"{_money(sales_prev)} → {_money(sales_now)}.",
        "Products explaining most of the change:",
        "",
    ]
    for c in contributors[:3]:
        causes = []
        if c["cost_rose"]:
            causes.append(f"cost rose ₹{c['avg_cost_prev']} → ₹{c['avg_cost_now']}")
        if c["price_fell"]:
            causes.append(f"average selling price fell ₹{c['avg_price_prev']} → ₹{c['avg_price_now']}")
        if c["units_now"] != c["units_prev"]:
            causes.append(f"units {c['units_prev']:.0f} → {c['units_now']:.0f}")
        cause_text = "; ".join(causes) if causes else "mix of small changes"
        lines.append(f"• {c['name']}: profit change {_money(c['profit_change'])} ({cause_text})")
    return {
        "answer": "\n".join(lines),
        "intent": "profit",
        "tools_used": tools_used,
        "recommendations": [],
        "evidence": analysis,
    }


async def _h_pricing(store_id: str, question: str) -> dict[str, Any]:
    """'Can I sell X for ₹Y?' — find product + price in the question."""
    m = re.search(r"(?:sell|for|offer|at)\s+(?:it\s+)?(?:for\s+)?₹?\s*(\d+(?:\.\d+)?)", question.lower())
    price = float(m.group(1)) if m else None
    tools_used = ["get_inventory_health", "calculate_discount_impact", "min_acceptable_price"]
    if price is None:
        return {
            "answer": "Tell me the product and the price — e.g. \"Can I sell Maggi for ₹15?\" — and I'll check the margin against your policy.",
            "intent": "pricing",
            "tools_used": [],
            "recommendations": [],
        }
    # find the product mentioned
    items = await tools.get_inventory_health(store_id)
    q = question.lower()
    match = None
    for it in items["items"]:
        first = it["name"].lower().split()[0]
        if first and first in q:
            match = it
            break
    if not match:
        return {
            "answer": f"I found the price ₹{price} but couldn't tell which product you mean. Name the product (e.g. \"Can I sell Parle-G for ₹{price}?\").",
            "intent": "pricing",
            "tools_used": tools_used,
            "recommendations": [],
        }
    impact = await tools.calculate_discount_impact(store_id, match["product_id"], price)
    decision = "allowed" if impact["allowed"] else "below your minimum"
    floor_price = impact["minimum_acceptable_price"]
    text = (
        f"{match['name']}: selling at ₹{price} (current ₹{impact['selling_price']}, cost ₹{impact['purchase_cost']}) "
        f"gives {impact['unit_margin_pct_at_proposed']}% margin per unit — that's {decision}. "
        f"Your minimum acceptable price is ₹{floor_price}."
        + ("" if impact["allowed"] else f" Anything below ₹{floor_price} breaks your pricing policy.")
    )
    return {
        "answer": text,
        "intent": "pricing",
        "tools_used": tools_used,
        "recommendations": [],
        "evidence": impact,
    }


async def _h_customers(store_id: str, question: str) -> dict[str, Any]:
    summary = await tools.get_customer_summary(store_id)
    totals = summary["totals"]
    lapsing = summary.get("not_seen_recently_14d", [])
    lines = [
        f"{totals.get('customers', 0)} customers on record; recent activity:",
        "",
    ]
    for c in summary["recent_customers"][:5]:
        lines.append(
            f"• {c['name']}: {c.get('orders', 0)} order(s), {_money(c.get('total_spent', 0))} lifetime"
            + (f", last seen {str(c.get('last_purchase', ''))[:10]}" if c.get("last_purchase") else ", never purchased")
        )
    if lapsing:
        lines.append("")
        lines.append(
            f"{len(lapsing)} regular(s) haven't purchased in 14+ days — worth a personal follow-up."
        )
    return {
        "answer": "\n".join(lines),
        "intent": "customers",
        "tools_used": ["get_customer_summary", "get_recent_customers"],
        "recommendations": [],
        "evidence": {"lapsing": lapsing[:10]},
    }


async def _h_suppliers(store_id: str, question: str) -> dict[str, Any]:
    items = await tools.get_inventory_health(store_id)
    with_velocity = [i for i in items["items"] if i["velocity"] and float(i["velocity"]) > 0]
    target = with_velocity[0] if with_velocity else (items["items"][0] if items["items"] else None)
    if not target:
        return {
            "answer": "Add products first, then I can compare suppliers for them.",
            "intent": "suppliers",
            "tools_used": ["get_inventory_health"],
            "recommendations": [],
        }
    cmp = await tools.get_supplier_comparison(store_id, target["product_id"], 1)
    opts = cmp.get("options", [])
    with_data = [o for o in opts if o.get("comparison_status") == "ok"]
    lines = [f"Supplier options for {target['name']} (from actual purchase history):", ""]
    for o in opts[:5]:
        if o["comparison_status"] == "ok":
            lines.append(
                f"• {o['name']}: avg ₹{o['avg_cost']}/unit over {o['purchases']} purchase(s)"
                + (f", lead time {o['lead_time_days']}d" if o.get("lead_time_days") else "")
            )
        else:
            lines.append(f"• {o['name']}: {o['message']}")
    if cmp.get("best_fit"):
        lines.append("")
        lines.append(f"Best fit: {cmp['best_fit']['name']} at ₹{cmp['best_fit']['avg_cost']} avg.")
    else:
        lines.append("")
        lines.append("Not enough purchase history to rank suppliers yet.")
    return {
        "answer": "\n".join(lines),
        "intent": "suppliers",
        "tools_used": ["get_inventory_health", "get_supplier_comparison"],
        "recommendations": [],
        "evidence": cmp,
    }


async def _h_inventory(store_id: str, question: str) -> dict[str, Any]:
    q = question.lower()
    inv = await tools.get_inventory_health(store_id)
    if "dead" in q:
        items = await tools.get_dead_stock(store_id)
        label = "dead stock"
    elif "slow" in q or "overstock" in q:
        items = await tools.get_overstocked_products(store_id)
        label = "slow-moving / overstocked"
    elif "low" in q or "critical" in q or "out of stock" in q:
        items = await tools.get_low_stock_products(store_id)
        label = "low / critical / out of stock"
    else:
        items = inv["items"]
        label = "inventory"
    tools_used = ["get_inventory_health", "get_low_stock_products", "get_dead_stock", "get_overstocked_products"]
    if not items:
        return {
            "answer": f"No {label} products right now — inventory looks healthy on that front.",
            "intent": "inventory",
            "tools_used": tools_used,
            "recommendations": [],
        }
    lines = [f"{len(items)} product(s) ({label}):", ""]
    for i in items[:8]:
        line = f"• {i['name']}: {i.get('sellable_quantity', i.get('quantity'))} units"
        if i.get("days_of_stock"):
            line += f", ~{i['days_of_stock']} day(s) of cover"
        if i.get("stock_value_cost"):
            line += f", value {_money(i['stock_value_cost'])}"
        lines.append(line)
    return {
        "answer": "\n".join(lines),
        "intent": "inventory",
        "tools_used": tools_used,
        "recommendations": [],
    }


async def _h_sales(store_id: str, question: str) -> dict[str, Any]:
    today = await tools.get_today_sales(store_id)
    week = await tools.get_sales_summary(store_id, 7)
    text = (
        f"Today: {_money(today['today']['sales'])} from {today['today']['orders']} order(s) "
        f"(avg {_money(today['today']['avg_order_value'])}). "
        f"Last 7 days: {_money(week['current_sales'])} vs {_money(week['previous_sales'])} the week before."
    )
    if today["top_products_today"]:
        top = today["top_products_today"][0]
        text += f" Top today: {top['name']} ({top['units']} units, {_money(top['revenue'])})."
    return {
        "answer": text,
        "intent": "sales",
        "tools_used": ["get_today_sales", "get_sales_summary"],
        "recommendations": [],
    }


async def _h_online_orders(store_id: str, question: str) -> dict[str, Any]:
    """Omnichannel order intelligence: what needs attention across channels."""
    tools_used = ["get_orders_needing_attention"]
    attn = await tools.get_orders_needing_attention(store_id)
    groups = attn["groups"]
    lines: list[str] = []
    aa = groups.get("awaiting_acceptance") or []
    ify = groups.get("in_fulfillment") or []
    ap = groups.get("awaiting_payment") or []
    if aa:
        lines.append(f"{len(aa)} paid online order(s) waiting for your confirmation:")
        for o in aa[:5]:
            lines.append(f"  Order {str(o['id'])[:8]}… ₹{float(o['total'] or 0):g} — accept to start preparing.")
    if ify:
        lines.append(f"{len(ify)} online order(s) in fulfillment (confirmed → preparing → ready → delivered).")
    if ap:
        lines.append(f"{len(ap)} online order(s) awaiting payment — they expire if unverified.")
    if not lines:
        lines.append("No online orders need attention right now.")
    # channel performance context (real records only)
    ch = await tools.get_channel_sales_summary(store_id, 30)
    tools_used.append("get_channel_sales_summary")
    chan_lines = []
    for c in ch["channels"][:4]:
        chan_lines.append(f"  {c['channel']}: {c['orders']} order(s), ₹{float(c['revenue']):g}")
    if chan_lines:
        lines.append("Last 30 days by channel:")
        lines.extend(chan_lines)
    return {"answer": "\n".join(lines), "intent": "online_orders",
            "tools_used": tools_used, "recommendations": []}


async def _h_order_payment(store_id: str, question: str) -> dict[str, Any]:
    """Order/payment questions. Read-only: the AI can explain but never execute payments."""
    tools_used = ["get_recent_orders"]
    orders = (await tools.get_recent_orders(store_id, 8)).get("items", [])
    if not orders:
        return {"answer": "No orders have been created yet. Complete a sale at the Smart Counter and I can explain its payment status.",
                "intent": "order_payment", "tools_used": tools_used, "recommendations": []}
    # If the question mentions a specific order id, focus on it
    import re as _re
    m = _re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", question.lower())
    lines = []
    if m:
        detail = await tools.get_order_details(store_id, m.group(0))
        tools_used.append("get_order_details")
        if detail.get("error"):
            return {"answer": "I could not find that order in your store.", "intent": "order_payment",
                    "tools_used": tools_used, "recommendations": []}
        o = detail["order"]
        lines.append(f"Order {'completed' if o['state'] == 'COMPLETED' else 'is ' + o['state'].replace('_', ' ').lower()}"
                     f" — total ₹{float(o['total'] or 0):g}, paid ₹{detail['paid_total']:g}.")
        for p in detail["payments"]:
            note = f" ({p['error_note']})" if p.get("error_note") else ""
            lines.append(f"  Payment {p['method'].upper()} ₹{float(p['amount']):g}: {p['state']}{note}.")
        if detail.get("split_group"):
            g = detail["split_group"]
            lines.append(f"  Split: ₹{g['paid_amount']:g} received, ₹{g['remaining_amount']:g} remaining across "
                         f"{len(g['splits'])} participant(s).")
        if o["state"] in ("PENDING_PAYMENT", "PARTIALLY_PAID"):
            lines.append("Payment is still being settled — the order completes only when the full amount is confirmed.")
    else:
        recent = orders[:5]
        for o in recent:
            cust = f" — {o['customer_name']}" if o.get("customer_name") else ""
            lines.append(f"Order {str(o['id'])[:8]}… {o['state'].replace('_', ' ').lower()}, "
                         f"₹{float(o['total'] or 0):g}, {o['payment_method'].upper()}{cust}")
        pending = [o for o in orders if o["state"] in ("PENDING_PAYMENT", "PARTIALLY_PAID")]
        if pending:
            lines.append(f"{len(pending)} order(s) still awaiting full payment.")
    return {"answer": "\n".join(lines), "intent": "order_payment",
            "tools_used": tools_used, "recommendations": []}


async def _h_fallback(store_id: str, question: str) -> dict[str, Any]:
    summary = await tools.get_store_summary(store_id)
    today = summary["today"]
    risk = summary["inventory_risk"]["alerts"]
    return {
        "answer": (
            "I can answer questions about your sales, inventory, expiry, reordering, pricing, "
            "customers and suppliers. Right now: today's sales are "
            f"{_money(today['sales'])} across {today['orders']} order(s); "
            f"{risk['low_stock_count']} product(s) need reorder attention and "
            f"{risk['expiry_risk_batches']} batch(es) are near expiry. "
            "Try \"What should I do today?\" for a prioritized action list."
        ),
        "intent": "fallback",
        "tools_used": ["get_store_summary"],
        "recommendations": [],
    }


async def _h_festival_prep(store_id: str, question: str) -> dict[str, Any]:
    """Cross-module: festival -> demand -> inventory -> procurement -> customers -> marketing.

    Inspects every module with real data, then returns an integrated PLAN.
    Nothing is executed automatically; each action is prepared separately."""
    tools_used = ["get_upcoming_festival_opportunities", "get_festival_demand_signals",
                  "prepare_inventory_opportunity", "get_customer_segments", "get_campaign_eligibility"]
    festivals = await tools.get_upcoming_festival_opportunities(store_id, 60)
    if not festivals:
        return {
            "answer": "No upcoming configured events in the next 60 days.",
            "intent": "festival_prep", "tools_used": tools_used, "recommendations": [],
        }
    fest = festivals[0]
    # If the question names a specific festival, prefer it
    q = question.lower()
    for f in festivals:
        if f["name"].lower().split()[0] in q:
            fest = f
            break

    signals = await tools.get_festival_demand_signals(store_id, fest["id"])
    opp = signals["opportunity"]
    evidence = signals["evidence"]
    inv = await tools.prepare_inventory_opportunity(store_id, fest["id"])

    summary = opp.get("summary", {})
    gap_products = inv.get("gap_products", [])

    lines = [
        f"{fest['name'].upper()} is in {fest['days_away']} day(s) ({fest['start_date']}).",
        "",
        "1. DEMAND",
    ]
    if evidence.get("has_evidence"):
        occ = evidence.get("occurrences", [])
        if occ:
            best = max(occ, key=lambda o: o["units_festival_week"])
            lines.append(
                f"   Your history: {best['category']} sold {best['units_festival_week']:.0f} units in the festival week "
                f"vs {best['units_14d_before']:.0f} in the two weeks before (past occurrence)."
            )
    else:
        lines.append(f"   {evidence.get('reason', 'No store-specific historical evidence available.')}")

    lines.append("")
    lines.append("2. INVENTORY & PROCUREMENT")
    if gap_products:
        lines.append(f"   {inv['message']}")
        for gp in gap_products[:4]:
            sup = f" — last supplier: {gp['preferred_supplier']}" if gp.get("preferred_supplier") else ""
            lines.append(f"   • {gp['name']}: gap {gp['gap_units']} units; reorder engine suggests {gp['recommended_quantity']} units{sup}")
        lines.append("   Review each product before ordering — nothing is ordered automatically.")
    else:
        lines.append("   No stock gap detected yet for the relevant products.")

    lines.append("")
    lines.append("3. CUSTOMERS")
    hist = summary.get("customers_with_relevant_history", 0)
    eligible = summary.get("consented_customers_with_phone", 0)
    if hist:
        lines.append(f"   {hist} customer(s) have purchased relevant categories before.")
        lines.append(f"   {eligible} customer(s) currently have marketing consent + phone (eligible audience).")
    else:
        lines.append("   No store-specific customer history for these categories; broader segments only if supported by data.")

    lines.append("")
    lines.append("4. MARKETING")
    lines.append(
        "   A festival campaign can be prepared for the eligible audience "
        "(I can draft it — you review, approve and send; development mode sends nothing real)."
    )

    # External intelligence layer (Phase 5): clearly-labelled, optional.
    ext = await tools.get_external_context(store_id)
    if ext.get("signals"):
        lines.append("")
        lines.append("5. EXTERNAL SIGNALS (from listed external sources — not your store data):")
        for sig in ext["signals"][:3]:
            lines.append(f"   • [{sig['confidence']} confidence] {sig['statement'][:140]}")
    else:
        lines.append("")
        lines.append("5. EXTERNAL SIGNALS: none available — everything above is from your own store data.")

    return {
        "answer": "\n".join(lines),
        "intent": "festival_prep",
        "tools_used": tools_used,
        "recommendations": [],
        "plan": {
            "festival": fest,
            "evidence": evidence,
            "inventory": inv,
            "customers": {"with_history": hist, "eligible": eligible},
            "next_actions": [
                "Review stock for gap products (Demand/Festivals page)",
                "Prepare purchase orders (Purchases page)",
                "Prepare a festival campaign (WhatsApp & Marketing page)",
            ],
        },
    }


async def _h_marketing(store_id: str, user_id: Optional[str], question: str) -> dict[str, Any]:
    """Campaign intent: prepare a DRAFT campaign from the question when the
    merchant asks ('create a campaign for customers who buy snacks'), else
    summarize campaign state."""
    tools_used = ["get_campaign_history", "get_customer_segments", "get_campaign_eligibility"]
    history = await tools.get_campaign_history(store_id, 5)
    q = question.lower()

    # detect category/segment targeting in the question
    categories = await tools.get_category_trends(store_id)
    matched_category = None
    for c in categories:
        first = c["category"].lower().split()[0]
        if first and first in q:
            matched_category = c["category"]
            break
    matched_segment = None
    for seg, keys in {
        "INACTIVE_CUSTOMER": ["inactive", "haven't purchased", "hasn't purchased", "not come back"],
        "HIGH_VALUE": ["high value", "top customers", "best customers"],
        "HIGH_FREQUENCY": ["frequent", "regular"],
        "NEW_CUSTOMER": ["new customer"],
    }.items():
        if any(k in q for k in keys):
            matched_segment = seg
            break

    if ("create" in q or "prepare" in q or "draft" in q) and (matched_category or matched_segment):
        eligibility = await tools.get_campaign_eligibility(
            store_id, segment=matched_segment, category=matched_category,
        )
        draft = None
        if user_id:
            try:
                draft = await tools.prepare_customer_campaign(
                    store_id, user_id,
                    name=f"{matched_category or matched_segment} campaign",
                    campaign_type="product_recommendation" if matched_category else "re_engagement",
                    segment=matched_segment, category=matched_category,
                    message_template="Hello! New stock and offers are here for you.",
                )
            except ValueError:
                draft = None
        lines = [
            f"Audience: {eligibility['count']} eligible customer(s). {eligibility['evidence']}",
        ]
        if draft:
            lines.append(f"Draft campaign created: \"{draft.get('name', 'campaign')}\" (status {draft.get('status', 'DRAFT')}).")
            lines.append("Open WhatsApp & Marketing to add products/offers, review, approve and send.")
        else:
            lines.append("Open WhatsApp & Marketing to build the campaign — nothing was sent.")
        return {
            "answer": "\n".join(lines),
            "intent": "marketing",
            "tools_used": tools_used + (["prepare_customer_campaign"] if draft else []),
            "recommendations": [],
            "draft_campaign": draft,
            "eligibility": eligibility,
        }

    lines = [f"{len(history)} recent campaign(s):", ""] if history else ["No campaigns yet.", ""]
    for h in history[:5]:
        lines.append(
            f"• {h['name']} ({h['campaign_type']}): {h['status']}, {h['sent_count']} sent / {h['failed_count']} failed"
        )
    lines.append("")
    lines.append("Ask me to \"create a campaign for customers who buy <category>\" or target inactive customers.")
    return {
        "answer": "\n".join(lines),
        "intent": "marketing",
        "tools_used": tools_used,
        "recommendations": [],
        "campaigns": history,
    }


async def _h_demand(store_id: str, question: str) -> dict[str, Any]:
    summary = await tools.get_demand_summary(store_id)
    tools_used = ["get_demand_summary", "get_product_trends", "get_sales_velocity", "forecast_product_demand"]
    q = question.lower()
    lines = []
    if "fast" in q or "selling" in q:
        rising = summary.get("rising_top", [])
        if rising:
            lines.append("Fastest-growing products (last 7 days vs the week before):")
            for r in rising[:5]:
                lines.append(f"• {r['name']}: {r['units_recent_7d']:.0f} units ({r['change_7d_pct']:+.1f}%)")
        else:
            lines.append("Not enough recent sales data to identify growing products.")
    if "declin" in q or "falling" in q or "down" in q:
        falling = summary.get("falling_top", [])
        if falling:
            lines.append("Declining products:")
            for r in falling[:5]:
                lines.append(f"• {r['name']}: {r['units_recent_7d']:.0f} units ({r['change_7d_pct']:+.1f}%)")
        else:
            lines.append("No clear declines detected in the recent windows.")
    if "stock" in q or "prepare" in q or "forecast" in q or "next week" in q:
        cats = summary.get("category_trends", [])[:3]
        if cats:
            lines.append("Category picture (14-day windows):")
            for c in cats:
                pct = f"{c['change_14d_pct']:+.1f}%" if c["change_14d_pct"] is not None else "insufficient data"
                lines.append(f"• {c['category']}: {c['recent_14d_units']:.0f} units ({pct})")
    if not lines:
        ov = summary
        lines.append(
            f"Sales last 7 days changed {ov.get('change_7d_pct')}% vs the prior week; "
            f"{ov.get('rising_count', 0)} product(s) rising, {ov.get('falling_count', 0)} falling."
        )
    # External context per category — only VERIFIED signals, attributed.
    ext = await tools.get_external_context(store_id)
    if ext.get("signals"):
        lines.append("")
        lines.append("External reports (NOT your sales data):")
        for sig in ext["signals"][:3]:
            cat = f" [{sig['category']}]" if sig.get("category") else ""
            lines.append(f"• {sig['statement'][:120]}{cat} ({sig['confidence']} confidence, verified source)")
    lines.append("")
    lines.append("Open Demand & Trends for the full picture with forecasts.")
    return {
        "answer": "\n".join(lines),
        "intent": "demand",
        "tools_used": tools_used,
        "recommendations": [],
        "evidence": {"rising": summary.get("rising_top"), "falling": summary.get("falling_top")},
    }


async def _h_customer_targeting(store_id: str, question: str) -> dict[str, Any]:
    segs = await tools.get_customer_segments(store_id)
    counts = segs.get("segment_counts", {})
    inactive = await tools.get_inactive_customers(store_id)
    tools_used = ["get_customer_segments", "get_inactive_customers", "get_customer_details"]
    lines = [
        f"Customers: {counts.get('REPEAT_CUSTOMER', 0)} repeat, {counts.get('ACTIVE_CUSTOMER', 0)} active, "
        f"{counts.get('INACTIVE_CUSTOMER', 0)} inactive (30d+), {counts.get('HIGH_VALUE', 0)} high-value.",
        "",
    ]
    inact = inactive.get("inactive_30d", [])[:4]
    if inact:
        lines.append("Worth contacting (evidence-based):")
        for c in inact:
            days = c.get("days_since_last_purchase")
            lines.append(
                f"• {c['name']}: last purchase {days} day(s) ago, {c['orders']} order(s), "
                f"₹{c['total_spent']:.0f} lifetime" + (" — has consent" if c["marketing_consent"] else " — no marketing consent (call or in-person only)")
            )
    else:
        lines.append("No inactive customers right now.")
    return {
        "answer": "\n".join(lines),
        "intent": "customer_targeting",
        "tools_used": tools_used,
        "recommendations": [],
        "evidence": {"segment_counts": counts},
    }


async def _h_external_intel(store_id: str, question: str) -> dict[str, Any]:
    """Answer external-intelligence questions with strict attribution.

    Never claims market facts without verified evidence; says so explicitly
    when the registry is empty."""
    tools_used = ["get_external_context", "get_evidence_for_category", "get_external_source_registry"]
    ext = await tools.get_external_context(store_id)
    sources = await tools.get_external_source_registry(store_id)
    enabled = [s for s in sources if s.get("enabled")]

    lines = []
    signals = ext.get("signals", [])
    if signals:
        lines.append("External reports (verified sources only — NOT your store data):")
        for sig in signals[:5]:
            cat = f" [{sig['category']}]" if sig.get("category") else ""
            lines.append(f"• {sig['statement'][:160]}{cat}")
            lines.append(f"  ({sig['confidence']} confidence · derived from verified evidence)")
    else:
        lines.append(
            "No verified external intelligence is on record for your store right now. "
            "I will not make market claims without a source."
        )
    lines.append("")
    lines.append(f"Source registry: {len(enabled)} enabled source(s) "
                 f"({', '.join(s['name'] for s in enabled[:3]) or 'none'}).")
    lines.append("Your store figures (sales, stock, customers) come only from your own records.")
    return {
        "answer": "\n".join(lines),
        "intent": "external_intel",
        "tools_used": tools_used,
        "recommendations": [],
        "evidence": {"signals": signals, "sources_enabled": len(enabled)},
    }


HANDLERS = {
    "what_should_i_do_today": _h_today,
    "festival_prep": _h_festival_prep,
    "marketing": _h_marketing,
    "demand": _h_demand,
    "customer_targeting": _h_customer_targeting,
    "external_intel": _h_external_intel,
    "online_orders": _h_online_orders,
    "order_payment": _h_order_payment,
    "reorder": _h_reorder,
    "expiry": _h_expiry,
    "profit": _h_profit,
    "pricing": _h_pricing,
    "customers": _h_customers,
    "suppliers": _h_suppliers,
    "inventory": _h_inventory,
    "sales": _h_sales,
    "fallback": _h_fallback,
}


async def ask(store_id: str, user_id: Optional[str], question: str) -> dict[str, Any]:
    """Main entry: route intent -> run the handler (targeted tools only) -> answer."""
    question = (question or "").strip()[:500]
    if not question:
        return {"answer": "Ask me anything about your store.", "intent": "empty",
                "tools_used": [], "recommendations": []}
    intent = _match_intent(question)
    # Handlers that need the acting user (draft-only write tools)
    if intent == "marketing":
        result = await _h_marketing(store_id, user_id, question)
    else:
        handler = HANDLERS[intent]
        result = await handler(store_id, question)
    result["engine"] = ENGINE
    result["question"] = question
    n_created = result.get("recommendations_created", 0)
    await _log_conversation(store_id, user_id, question, result["answer"],
                            result.get("tools_used", []), n_created if isinstance(n_created, int) else 0)
    return result


# ---------------------------------------------------------------- brief


async def business_brief(store_id: str) -> dict[str, Any]:
    """Today's Business Brief — every number from the engines."""
    today = await tools.get_today_sales(store_id)
    summary = await tools.get_store_summary(store_id)
    risk = summary["inventory_risk"]
    created = await recs.refresh_recommendations(store_id)
    rows = await db.fetch(
        """
        select id, type, title, summary, severity, priority, priority_reason, evidence,
               proposed_action, estimated_impact, risk, status
        from ai_recommendations
        where store_id = $1 and status in ('NEW','REVIEWED','APPROVED','EXECUTING')
        order by priority desc limit 8
        """,
        store_id,
    )
    recos = [_clean_reco_row(r) for r in rows]
    festivals = await tools.get_festival_opportunities(store_id, 30)
    return {
        "engine": ENGINE,
        "sales": {
            "today_sales": today["today"]["sales"],
            "today_orders": today["today"]["orders"],
            "gross_profit_today": today["today"]["gross_profit"],
        },
        "attention": risk.get("alerts", {}),
        "recommendations": recos,
        "festivals_upcoming": festivals,
        "detectors_run": created["detectors"],
    }


# ---------------------------------------------------------------- recommendation lifecycle


async def set_recommendation_status(
    store_id: str, user_id: Optional[str], reco_id: str, new_status: str, note: Optional[str] = None
) -> dict[str, Any]:
    valid = {"REVIEWED", "APPROVED", "REJECTED", "DISMISSED", "COMPLETED"}
    if new_status not in valid:
        raise ActionError(f"Status must be one of {sorted(valid)}", "bad_status")
    row = await db.fetchrow(
        """
        update ai_recommendations
        set status = $3, reviewed_at = now(),
            resolved_at = case when $3 in ('REJECTED','DISMISSED','COMPLETED') then now() else resolved_at end,
            outcome = coalesce($4, outcome), updated_at = now()
        where id = $1 and store_id = $2
        returning id, status
        """,
        reco_id,
        store_id,
        new_status,
        note,
    )
    if not row:
        raise ActionError("Recommendation not found", "not_found")
    event = {
        "APPROVED": "AI_ACTION_APPROVED",
        "REJECTED": "AI_ACTION_REJECTED",
        "DISMISSED": "AI_ACTION_DISMISSED",
    }.get(new_status, "AI_RECOMMENDATION_UPDATED")
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, message)
        values ($1, $2, $3, 'ai_recommendation', $4, $5)
        """,
        store_id,
        user_id,
        event,
        reco_id,
        f"Recommendation {new_status.lower()}" + (f": {note}" if note else ""),
    )
    return {"id": str(row["id"]), "status": row["status"]}


async def list_recommendations(store_id: str, status: str = "open", limit: int = 20) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        select id, type, title, summary, description, severity, priority, priority_reason,
               evidence, data_sources, reasoning_summary, proposed_action, estimated_impact,
               risk, confidence, status, created_at, updated_at
        from ai_recommendations
        where store_id = $1
          and ($2::text is null or
               ($2::text = 'open' and status in ('NEW','REVIEWED','APPROVED','EXECUTING'))
               or ($2::text <> 'open' and status = $2::text))
        order by priority desc, created_at desc
        limit $3
        """,
        store_id,
        status if status in ("open", "NEW", "REVIEWED", "APPROVED", "EXECUTED", "COMPLETED",
                             "FAILED", "DISMISSED", "REJECTED") else "open",
        max(1, min(int(limit), 100)),
    )
    return [_clean_reco_row(r) for r in rows]
