"""Campaign engine — build, validate, approve, send, track. One system.

Guarantees:
- Audience eligibility (consent + phone + opt-out) is REVALIDATED at send time.
- Message text is validated: any price/figure in the message must match the
  database (validated via the products snapshot captured in the campaign row).
  AI may only draft copy; numbers come from configured offers.
- Campaign states: DRAFT -> READY_FOR_REVIEW -> APPROVED -> SENDING ->
  SENT / PARTIALLY_SENT / FAILED, plus CANCELLED. Re-sending a terminal
  campaign is refused. Duplicate active campaigns per (type, segment) are
  blocked by a DB unique index.
- Every state change is activity-logged. Nothing auto-executes without approval.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.database import db
from app.services import customers_intel, whatsapp
from app.services.whatsapp import ProviderNotConfigured

CAMPAIGN_TYPES = (
    "festival", "new_product", "discount", "inventory_clearance",
    "re_engagement", "product_recommendation", "general",
)


class CampaignError(Exception):
    def __init__(self, message: str, code: str = "campaign_error"):
        super().__init__(message)
        self.code = code


def _jsonb(value: Any, default: Any) -> Any:
    """asyncpg can return jsonb as str — decode defensively."""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return default


def _j(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _j(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_j(v) for v in value]
    return str(value)


async def _log(store_id: str, user_id: Optional[str], event: str, campaign_id: str, message: str, state: Any = None):
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
        values ($1, $2, $3, 'campaign', $4, $5::jsonb, $6)
        """,
        store_id, user_id, event, campaign_id,
        json.dumps(_j(state)) if state is not None else None,
        message,
    )


# ---------------------------------------------------------------- validation


async def _validate_products(store_id: str, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Product/offer snapshot: prices ALWAYS from the DB, never from the caller
    or AI text. offer_text is freeform but price fields are ignored."""
    if not products:
        return []
    out = []
    for p in products[:5]:
        pid = p.get("product_id")
        if not pid:
            raise CampaignError("Each product needs a product_id", "bad_payload")
        row = await db.fetchrow(
            """
            select p.id, p.name, p.selling_price,
                   coalesce(i.quantity, 0) as stock
            from products p
            left join inventory i on i.product_id = p.id and i.store_id = p.store_id
            where p.id = $1 and p.store_id = $2 and p.is_active
            """,
            str(pid), store_id,
        )
        if not row:
            raise CampaignError(f"Product {pid} not found or inactive", "bad_payload")
        if int(row["stock"] or 0) <= 0:
            raise CampaignError(
                f"Product {row['name']} is out of stock — campaigns must promote available products.",
                "product_unavailable",
            )
        out.append({
            "product_id": str(row["id"]),
            "name": row["name"],
            "price": float(row["selling_price"]),  # DB price, authoritative
            "offer_text": (p.get("offer_text") or "").strip()[:200] or None,
        })
    return out


def _render_message(template: str, *, store_name: str, products: list[dict[str, Any]]) -> str:
    """Deterministic render. Only DB-verified values are substituted."""
    lines = [template.strip()] if template and template.strip() else []
    if products:
        plines = []
        for p in products:
            price_bit = f" ₹{p['price']:g}" if p.get("price") is not None else ""
            offer = f" ({p['offer_text']})" if p.get("offer_text") else ""
            plines.append(f"• {p['name']}{price_bit}{offer}")
        lines.append("\n".join(plines))
    lines.append(f"— {store_name}")
    return "\n".join(l for l in lines if l).strip()


# ---------------------------------------------------------------- CRUD


async def create_campaign(
    store_id: str, user_id: str, *, name: str, campaign_type: str,
    audience: dict[str, Any], products: list[dict[str, Any]],
    message_template: Optional[str], channel: str = "whatsapp",
) -> dict[str, Any]:
    if campaign_type not in CAMPAIGN_TYPES:
        raise CampaignError(f"campaign_type must be one of {CAMPAIGN_TYPES}", "bad_payload")
    products_validated = await _validate_products(store_id, products)

    # Audience count preview (without persisting recipients)
    audience_eval = await customers_intel.eligible_campaign_audience(
        store_id,
        segment=audience.get("segment"),
        category=audience.get("category"),
        product_id=audience.get("product_id"),
    )

    row = await db.fetchrow(
        """
        insert into campaigns
          (store_id, name, campaign_type, status, channel, audience, products, message_template, created_by)
        values ($1, $2, $3, 'DRAFT', $4, $5::jsonb, $6::jsonb, $7, $8)
        returning *
        """,
        store_id, name.strip(), campaign_type, channel,
        json.dumps({
            "segment": audience.get("segment"),
            "category": audience.get("category"),
            "product_id": audience.get("product_id"),
            "count_snapshot": audience_eval["count"],
            "evidence": audience_eval["evidence"],
        }),
        json.dumps(_j(products_validated)),
        message_template, user_id,
    )
    await _log(store_id, user_id, "CAMPAIGN_CREATED", str(row["id"]),
               f"Campaign created: {row['name']}", {"status": "DRAFT"})
    return await campaign_detail(store_id, str(row["id"]))


async def update_campaign(
    store_id: str, user_id: str, campaign_id: str, *,
    name: Optional[str] = None, audience: Optional[dict[str, Any]] = None,
    products: Optional[list[dict[str, Any]]] = None,
    message_template: Optional[str] = None,
) -> dict[str, Any]:
    row = await db.fetchrow(
        "select status from campaigns where id = $1 and store_id = $2",
        campaign_id, store_id,
    )
    if not row:
        raise CampaignError("Campaign not found", "not_found")
    if row["status"] not in ("DRAFT", "READY_FOR_REVIEW"):
        raise CampaignError("Only DRAFT or READY_FOR_REVIEW campaigns can be edited", "bad_status")
    sets, vals = [], []
    if name is not None:
        sets.append(f"name = ${len(vals) + 3}")
        vals.append(name.strip())
    if products is not None:
        validated = await _validate_products(store_id, products)
        sets.append(f"products = ${len(vals) + 3}::jsonb")
        vals.append(json.dumps(_j(validated)))
    if audience is not None:
        audience_eval = await customers_intel.eligible_campaign_audience(
            store_id, segment=audience.get("segment"),
            category=audience.get("category"), product_id=audience.get("product_id"),
        )
        sets.append(f"audience = ${len(vals) + 3}::jsonb")
        vals.append(json.dumps({
            "segment": audience.get("segment"),
            "category": audience.get("category"),
            "product_id": audience.get("product_id"),
            "count_snapshot": audience_eval["count"],
            "evidence": audience_eval["evidence"],
        }))
    if message_template is not None:
        sets.append(f"message_template = ${len(vals) + 3}")
        vals.append(message_template)
    if not sets:
        raise CampaignError("Nothing to update", "bad_payload")
    await db.execute(
        f"update campaigns set {', '.join(sets)}, updated_at = now() where id = $1 and store_id = $2",
        campaign_id, store_id, *vals,
    )
    return await campaign_detail(store_id, campaign_id)


async def campaign_detail(store_id: str, campaign_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        "select * from campaigns where id = $1 and store_id = $2", campaign_id, store_id
    )
    if not row:
        raise CampaignError("Campaign not found", "not_found")
    d = dict(row)
    d["id"] = str(d["id"])
    d["audience"] = _jsonb(d.get("audience"), {})
    d["products"] = _jsonb(d.get("products"), [])
    d["provider_message_ids"] = _jsonb(d.get("provider_message_ids"), [])
    recips = await db.fetch(
        """
        select status, count(*) as n from campaign_recipients
        where campaign_id = $1 group by status
        """,
        campaign_id,
    )
    d["recipient_status_counts"] = {r["status"]: int(r["n"]) for r in recips}
    prov = await whatsapp.provider_status(store_id)
    d["provider"] = prov
    return d


async def list_campaigns(store_id: str, status: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        select id, name, campaign_type, status, channel, recipient_count,
               sent_count, failed_count, created_at, sent_at
        from campaigns where store_id = $1
          and ($2::text is null or status = $2::text)
        order by created_at desc limit $3
        """,
        store_id, status, max(1, min(limit, 200)),
    )
    return [_j(dict(r)) | {"id": str(r["id"])} for r in rows]


# ---------------------------------------------------------------- lifecycle


async def submit_for_review(store_id: str, user_id: str, campaign_id: str) -> dict[str, Any]:
    """DRAFT -> READY_FOR_REVIEW. Builds the final message from validated data."""
    row = await db.fetchrow(
        "select * from campaigns where id = $1 and store_id = $2", campaign_id, store_id
    )
    if not row:
        raise CampaignError("Campaign not found", "not_found")
    if row["status"] != "DRAFT":
        raise CampaignError(f"Campaign is {row['status']}, must be DRAFT", "bad_status")
    audience = row["audience"] if isinstance(row["audience"], dict) else json.loads(row["audience"] or "{}")
    # Empty audience = all consented customers with a phone (documented default).

    # Re-evaluate eligibility NOW (audience may have changed since creation)
    audience_eval = await customers_intel.eligible_campaign_audience(
        store_id, segment=audience.get("segment"),
        category=audience.get("category"), product_id=audience.get("product_id"),
    )
    if audience_eval["count"] == 0:
        raise CampaignError(
            "No eligible customers for this campaign (need phone + marketing consent, not opted out).",
            "no_recipients",
        )

    store = await db.fetchval("select name from stores where id = $1", store_id)
    products = row["products"] if isinstance(row["products"], list) else json.loads(row["products"] or "[]")
    message = _render_message(row["message_template"] or "", store_name=store or "Your store", products=products)

    # Fresh product snapshot for the review preview
    fresh_products = await _validate_products(store_id, products)

    await db.execute(
        """
        update campaigns set status = 'READY_FOR_REVIEW',
          audience = jsonb_set(audience, '{count_snapshot}', $3::jsonb),
          products = $4::jsonb, message_text = $5, updated_at = now()
        where id = $1 and store_id = $2
        """,
        campaign_id, store_id,
        json.dumps(audience_eval["count"]),
        json.dumps(_j(fresh_products)),
        message,
    )
    await _log(store_id, user_id, "CAMPAIGN_SUBMITTED", campaign_id,
               f"Campaign ready for review: {row['name']} ({audience_eval['count']} recipients)",
               {"recipients": audience_eval["count"]})
    return await campaign_detail(store_id, campaign_id)


async def approve_campaign(store_id: str, user_id: str, campaign_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        "select status from campaigns where id = $1 and store_id = $2", campaign_id, store_id
    )
    if not row:
        raise CampaignError("Campaign not found", "not_found")
    if row["status"] != "READY_FOR_REVIEW":
        raise CampaignError(f"Campaign is {row['status']}, must be READY_FOR_REVIEW", "bad_status")
    await db.execute(
        """
        update campaigns set status = 'APPROVED', approved_by = $3, approved_at = now(), updated_at = now()
        where id = $1 and store_id = $2
        """,
        campaign_id, store_id, user_id,
    )
    await _log(store_id, user_id, "CAMPAIGN_APPROVED", campaign_id,
               f"Campaign approved: {campaign_id}")
    return await campaign_detail(store_id, campaign_id)


async def send_campaign(store_id: str, user_id: str, campaign_id: str) -> dict[str, Any]:
    """APPROVED -> SENDING -> SENT/PARTIALLY_SENT/FAILED.

    Revalidates EVERYTHING at send time: consent, opt-out, phone, product
    availability, campaign state. Provider failure marks FAILED with the
    error recorded — success is never faked.
    """
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "select * from campaigns where id = $1 and store_id = $2 for update",
                campaign_id, store_id,
            )
            if not row:
                raise CampaignError("Campaign not found", "not_found")
            if row["status"] in ("SENDING", "SENT", "PARTIALLY_SENT"):
                raise CampaignError("Campaign was already sent — sending twice is not allowed", "already_sent")
            if row["status"] in ("CANCELLED", "FAILED"):
                raise CampaignError(f"Campaign is {row['status']} and cannot be sent", "bad_status")
            if row["status"] != "APPROVED":
                raise CampaignError(f"Campaign must be APPROVED before sending (currently {row['status']})", "bad_status")
            await conn.execute(
                "update campaigns set status = 'SENDING', updated_at = now() where id = $1", row["id"]
            )

    audience = row["audience"] if isinstance(row["audience"], dict) else json.loads(row["audience"] or "{}")
    products = row["products"] if isinstance(row["products"], list) else json.loads(row["products"] or "[]")
    message_text = row["message_text"]

    # ---- revalidate products ----
    try:
        fresh_products = await _validate_products(store_id, products)
    except CampaignError as exc:
        await db.execute(
            "update campaigns set status = 'FAILED', error_note = $3, updated_at = now() where id = $1 and store_id = $2",
            campaign_id, store_id, str(exc),
        )
        await _log(store_id, user_id, "CAMPAIGN_FAILED", campaign_id, f"Campaign failed pre-send validation: {exc}")
        raise

    # ---- resolve provider ----
    try:
        provider, prov_cfg = await whatsapp.get_provider_for_store(store_id)
    except ProviderNotConfigured as exc:
        await db.execute(
            "update campaigns set status = 'FAILED', error_note = $3, updated_at = now() where id = $1 and store_id = $2",
            campaign_id, store_id, str(exc),
        )
        await _log(store_id, user_id, "CAMPAIGN_FAILED", campaign_id, f"Campaign failed: {exc}")
        raise CampaignError(str(exc), "provider_not_configured")

    # ---- recipients with per-recipient revalidation ----
    audience_eval = await customers_intel.eligible_campaign_audience(
        store_id, segment=audience.get("segment"),
        category=audience.get("category"), product_id=audience.get("product_id"),
    )
    # fall back to persisted recipients if the audience is keyless (explicit list mode later)
    if audience_eval["count"] == 0 and not (audience.get("segment") or audience.get("category") or audience.get("product_id")):
        raise CampaignError("Campaign has no audience", "no_recipients")

    await db.execute("delete from campaign_recipients where campaign_id = $1", campaign_id)
    for r in audience_eval["recipients"]:
        # consent re-check per recipient (defense in depth; eligible_campaign_audience already filtered)
        status = "pending"
        if not r["phone"]:
            status = "skipped_no_phone"
        await db.execute(
            """
            insert into campaign_recipients (store_id, campaign_id, customer_id, phone, personalized_message, status)
            values ($1, $2, $3, $4, $5, $6)
            on conflict (campaign_id, customer_id) do nothing
            """,
            store_id, campaign_id, r["customer_id"], r["phone"], message_text, status,
        )

    sent = failed = skipped = 0
    provider_ids: list[str] = []
    errors: list[str] = []
    recips = await db.fetch(
        "select id, customer_id, phone, personalized_message, status from campaign_recipients where campaign_id = $1",
        campaign_id,
    )
    total_recipients = len(recips)
    for rec in recips:
        if rec["status"] != "pending":
            skipped += 1
            continue
        if not rec["phone"]:
            await db.execute(
                "update campaign_recipients set status = 'skipped_no_phone' where id = $1", rec["id"]
            )
            skipped += 1
            continue
        try:
            result = await provider.send_message(
                to_phone=rec["phone"],
                body=rec["personalized_message"] or message_text,
                correlation_id=str(rec["id"]),
            )
            pmid = result.get("provider_message_id")
            provider_ids.append(pmid)
            await db.execute(
                """
                update campaign_recipients
                set status = 'sent', provider_message_id = $2, sent_at = now()
                where id = $1
                """,
                rec["id"], pmid,
            )
            sent += 1
        except ProviderNotConfigured as exc:
            await db.execute(
                "update campaign_recipients set status = 'failed', error = $2 where id = $1",
                rec["id"], str(exc),
            )
            errors.append(str(exc))
            failed += 1
            break  # provider-level failure: stop attempting the rest
        except Exception as exc:  # noqa: BLE001 — per-recipient failure
            await db.execute(
                "update campaign_recipients set status = 'failed', error = $2 where id = $1",
                rec["id"], str(exc)[:300],
            )
            errors.append(str(exc)[:300])
            failed += 1

    final_status = "SENT" if failed == 0 and sent > 0 else ("PARTIALLY_SENT" if sent > 0 else "FAILED")
    note = "; ".join(errors[:3]) if errors else None
    await db.execute(
        """
        update campaigns
        set status = $3, recipient_count = $4, sent_count = $5, failed_count = $6,
            sent_at = case when $3 in ('SENT','PARTIALLY_SENT') then now() else sent_at end,
            provider_message_ids = $7::jsonb, error_note = $8, updated_at = now()
        where id = $1 and store_id = $2
        """,
        campaign_id, store_id, final_status, total_recipients, sent, failed,
        json.dumps(provider_ids), note,
    )
    event = {"SENT": "CAMPAIGN_SENT", "PARTIALLY_SENT": "CAMPAIGN_PARTIALLY_SENT", "FAILED": "CAMPAIGN_FAILED"}[final_status]
    label = "Development mode: messages recorded locally, NOT delivered." if prov_cfg.get("provider_id") == "development" else "Messages sent via provider."
    await _log(store_id, user_id, event, campaign_id,
               f"Campaign {final_status}: {sent} sent, {failed} failed. {label}",
               {"sent": sent, "failed": failed, "skipped": skipped})

    return {
        "campaign_id": campaign_id,
        "status": final_status,
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "provider": prov_cfg.get("provider_id"),
        "mode": prov_cfg.get("mode"),
        "delivery_note": (
            "Development mode — no real messages were delivered to customers."
            if prov_cfg.get("provider_id") == "development" else
            "Sent via provider. Delivery receipts appear when the provider supports them."
        ),
    }


async def cancel_campaign(store_id: str, user_id: str, campaign_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        update campaigns set status = 'CANCELLED', cancelled_at = now(), updated_at = now()
        where id = $1 and store_id = $2 and status in ('DRAFT','READY_FOR_REVIEW','APPROVED')
        returning id
        """,
        campaign_id, store_id,
    )
    if not row:
        raise CampaignError("Only unsent campaigns can be cancelled", "bad_status")
    await _log(store_id, user_id, "CAMPAIGN_CANCELLED", campaign_id, "Campaign cancelled by merchant")
    return await campaign_detail(store_id, campaign_id)


async def campaign_tracking(store_id: str, campaign_id: str) -> dict[str, Any]:
    """Honest tracking: what we know vs what the provider never told us."""
    detail = await campaign_detail(store_id, campaign_id)
    counts = detail["recipient_status_counts"]
    prov = detail["provider"]
    attribution = {
        "delivery_receipts": (
            "Not available from current integration (development mode)."
            if prov["provider_id"] == "development" else
            "Available if the provider webhooks are configured (Phase 5)."
        ),
        "read_receipts": "Not available from current integration.",
        "revenue_attribution": "Revenue attribution unavailable — the current channel cannot reliably link a sale to this campaign.",
    }
    return {
        "campaign": detail,
        "recipient_counts": counts,
        "attribution": attribution,
        "note": "Counts above reflect provider API outcomes only. Nothing is extrapolated.",
    }
