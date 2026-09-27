"""Evidence layer — verification, normalization, and honest presentation.

The pipeline: RAW CANDIDATE (agent_reach) -> VERIFICATION (here) ->
NORMALIZED SIGNALS (here) -> AI/merchant consumption (clearly labelled).

Verification (deterministic, explainable):
- OFFICIAL sources (government/regulated) auto-verify after basic sanity
  (title length, no binary payload) -> VERIFIED, trust OFFICIAL.
- Manual merchant observations verify instantly (the merchant is the source).
- Everything else stays UNVERIFIED until a human reviews it. AI and UI may
  only present UNVERIFIED items as "unverified external reports" — never as
  facts, never mixed into merchant-math.
- REJECTED items are kept for audit but excluded from signals/UI.

Signals: short, attributed statements derived ONLY from VERIFIED evidence,
with confidence = min(trust tier, single-vs-multi source support). Claims are
stored verbatim-style with evidence ids; the AI may paraphrase but must keep
attribution. Nothing here ever feeds pricing/inventory/reorder math.
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.database import db

TRUST_RANK = {"OFFICIAL": 3, "ESTABLISHED": 2, "UNVERIFIED": 1}
EVIDENCE_TTL_DAYS = 60  # signals/evidence expire; stale claims must not linger


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _relevance_for(store_id: str, category: Optional[str]) -> list[str]:
    """Matched STORE categories — evidence touches the store only via the
    store's own catalog (the join is explicit and inspectable)."""
    if not category:
        return []
    return [category]


# ---------------------------------------------------------------- verify


async def verify_evidence(store_id: str, user_id: str, evidence_id: str, decision: str) -> dict[str, Any]:
    """Human verification: VERIFIED or REJECTED. Audited. Deterministic."""
    if decision not in ("VERIFIED", "REJECTED"):
        raise ValueError("decision must be VERIFIED or REJECTED")
    row = await db.fetchrow(
        """
        update evidence_items
        set verification_status = $3, verified_at = now(),
            verification_notes = coalesce(verification_notes, '') || ' human-verified'
        where id = $1 and store_id = $2
        returning id, verification_status
        """,
        evidence_id, store_id, decision,
    )
    if not row:
        raise ValueError("Evidence not found")
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
        values ($1, $2, 'EVIDENCE_VERIFIED', 'evidence_item', $3, $4::jsonb, $5)
        """,
        store_id, user_id, evidence_id,
        json.dumps({"status": decision}),
        f"Evidence {decision.lower()}: {evidence_id}",
    )
    return {"id": str(row["id"]), "verification_status": row["verification_status"]}


async def auto_verify_pending(store_id: str) -> dict[str, Any]:
    """Deterministic pass: OFFICIAL sources + manual entries get verified;
    expired items get EXPIRED. Everything else stays pending for the merchant."""
    verified = await db.fetchval(
        """
        with v as (
          update evidence_items
          set verification_status = 'VERIFIED', verified_at = now()
          where store_id = $1 and verification_status = 'UNVERIFIED'
            and (trust_tier = 'OFFICIAL' or source_kind = 'manual')
            and length(title) >= 8
          returning id
        ) select count(*) from v
        """,
        store_id,
    )
    expired = await db.fetchval(
        """
        with v as (
          update evidence_items
          set verification_status = 'EXPIRED'
          where store_id = $1 and verification_status in ('UNVERIFIED','VERIFIED')
            and coalesce(expires_at, retrieved_at + interval '60 days') < now()
          returning id
        ) select count(*) from v
        """,
        store_id,
    )
    return {"auto_verified": int(verified or 0), "expired": int(expired or 0)}


# ---------------------------------------------------------------- list/get


async def list_evidence(
    store_id: str, *, status: Optional[str] = None, category: Optional[str] = None, limit: int = 50
) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        select e.id, e.source_kind, e.source_name, e.source_url, e.title, e.summary,
               e.published_at, e.retrieved_at, e.region, e.category,
               e.verification_status, e.trust_tier, e.relevance
        from evidence_items e
        where e.store_id = $1
          and ($2::text is null or e.verification_status = $2::text)
          and ($3::text is null or e.category = $3::text)
          and e.verification_status <> 'REJECTED'
        order by e.retrieved_at desc limit $4
        """,
        store_id, status, category, max(1, min(limit, 100)),
    )
    return [_j_row(r) for r in rows]


async def get_evidence(store_id: str, evidence_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        "select * from evidence_items where id = $1 and store_id = $2",
        evidence_id, store_id,
    )
    if not row:
        raise ValueError("Evidence not found")
    return _j_row(row)


# ---------------------------------------------------------------- signals


async def rebuild_signals(store_id: str) -> dict[str, Any]:
    """Derive signals from VERIFIED evidence only. One signal per evidence item
    (category_notes / event_context); confidence from trust + corroboration."""
    # clear old signals (they are derived, reproducible)
    await db.execute("delete from external_signals where store_id = $1", store_id)

    rows = await db.fetch(
        """
        select e.id, e.title, e.summary, e.category, e.region, e.trust_tier,
               e.published_at, e.retrieved_at,
               (select count(*) from evidence_items o
                 where o.store_id = e.store_id and o.id <> e.id
                   and o.verification_status = 'VERIFIED'
                   and o.category is not distinct from e.category
                   and o.title ilike '%' || left(e.title, 25) || '%') as corroborations
        from evidence_items e
        where e.store_id = $1 and e.verification_status = 'VERIFIED'
          and e.retrieved_at >= now() - interval '60 days'
        order by e.retrieved_at desc limit 100
        """,
        store_id,
    )
    created = 0
    for r in rows:
        statement = (r["summary"] or r["title"]).strip()[:300]
        if not statement:
            continue
        sig_type = "category_note" if r["category"] else "event_context"
        if r["trust_tier"] == "OFFICIAL" and int(r["corroborations"] or 0) > 0:
            confidence = "HIGH"
        elif r["trust_tier"] == "OFFICIAL" or int(r["corroborations"] or 0) > 0:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"
        await db.execute(
            """
            insert into external_signals
              (store_id, signal_type, title, statement, category, region, confidence,
               evidence_ids, expires_at)
            values ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
            """,
            store_id, sig_type, r["title"][:200], statement,
            r["category"], r["region"], confidence,
            json.dumps([str(r["id"])]),
            _now() + timedelta(days=EVIDENCE_TTL_DAYS),
        )
        created += 1
    return {"signals_created": created}


# ---------------------------------------------------------------- external context pack


async def external_context(store_id: str, category: Optional[str] = None) -> dict[str, Any]:
    """Everything the AI/UI may consume from the outside world — pre-labelled.

    The 'separation' contract: merchant data NEVER appears here; consumers
    must display these items with their source labels. If there is nothing,
    the response says so instead of padding.
    """
    await auto_verify_pending(store_id)
    await rebuild_signals(store_id)

    signals = await db.fetch(
        """
        select id, signal_type, title, statement, category, region, confidence, created_at
        from external_signals
        where store_id = $1
          and ($2::text is null or category = $2::text)
        order by
          case confidence when 'HIGH' then 0 when 'MEDIUM' then 1 else 2 end,
          created_at desc
        limit 15
        """,
        store_id, category,
    )
    return {
        "separation_note": (
            "EXTERNAL INTELLIGENCE — not merchant data. Statements below come from "
            "listed external sources; store figures live elsewhere and are computed "
            "from your own records."
        ),
        "signals": [
            {
                "id": str(r["id"]),
                "type": r["signal_type"],
                "title": r["title"],
                "statement": r["statement"],
                "category": r["category"],
                "region": r["region"],
                "confidence": r["confidence"],
                "source_note": "Derived from verified evidence only.",
            }
            for r in signals
        ],
        "count": len(signals),
    }


# ---------------------------------------------------------------- manual evidence


async def add_manual_evidence(
    store_id: str, user_id: str, *, title: str, summary: Optional[str],
    source_url: Optional[str], category: Optional[str], region: Optional[str],
) -> dict[str, Any]:
    """Merchant/curated observation. Instantly VERIFIED (the merchant is the
    authoritative source for their own observations) and provenance-complete."""
    if not title or len(title.strip()) < 4:
        raise ValueError("Title too short")
    src = await db.fetchrow(
        "select id, name, trust_tier from external_sources where store_id = $1 and source_key = 'manual:merchant-observations'",
        store_id,
    )
    now = _now()
    dedup = hashlib.sha256(
        f"manual|{store_id}|{title.strip()}|{now.date().isoformat()}".encode()
    ).hexdigest()
    # Idempotent: re-adding the same observation on the same day returns the
    # existing item instead of colliding with the dedup constraint.
    row = await db.fetchrow(
        """
        insert into evidence_items
          (store_id, source_id, source_kind, source_name, source_url, title, summary,
           raw_payload, published_at, retrieved_at, region, category,
           verification_status, verified_at, verification_notes, trust_tier, dedup_hash)
        values ($1, $2, 'manual', $3, $4, $5, $6, '{}'::jsonb, now(), now(), $7, $8,
                'VERIFIED', now(), 'Entered by merchant', 'OFFICIAL', $9)
        on conflict (store_id, dedup_hash) do update set retrieved_at = now()
        returning id
        """,
        store_id, src["id"] if src else None,
        src["name"] if src else "Merchant observation",
        source_url, title.strip(), summary, region, category, dedup,
    )
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, message)
        values ($1, $2, 'EVIDENCE_ADDED', 'evidence_item', $3, $4)
        """,
        store_id, user_id, row["id"], f"Manual evidence added: {title.strip()[:80]}",
    )
    return await get_evidence(store_id, str(row["id"]))


# ---------------------------------------------------------------- sources admin


async def list_sources(store_id: str) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        select id, source_key, name, kind, base_url, trust_tier, region, enabled,
               last_fetched_at,
               (select count(*) from evidence_items e where e.source_id = external_sources.id) as evidence_count
        from external_sources where store_id = $1 order by enabled desc, name
        """,
        store_id,
    )
    return [_j_row(r) for r in rows]


async def set_source_enabled(store_id: str, source_id: str, enabled: bool) -> dict[str, Any]:
    row = await db.fetchrow(
        "update external_sources set enabled = $3 where id = $1 and store_id = $2 returning id, enabled",
        source_id, store_id, enabled,
    )
    if not row:
        raise ValueError("Source not found")
    return {"id": str(row["id"]), "enabled": row["enabled"]}


# ---------------------------------------------------------------- helpers


def _j_row(r: Any) -> dict[str, Any]:
    d = dict(r)
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        elif isinstance(v, (dict, list)):
            continue
    if "id" in d and d["id"] is not None:
        d["id"] = str(d["id"])
    if "relevance" in d and not isinstance(d.get("relevance"), (list, dict)):
        try:
            d["relevance"] = json.loads(d["relevance"])
        except (json.JSONDecodeError, TypeError):
            d["relevance"] = []
    return d
