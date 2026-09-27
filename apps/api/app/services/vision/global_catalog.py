"""Global Product Brain — data access for the shared product knowledge layer.

STRICT separation (master-prompt §28, §45):
  * GLOBAL layer: canonical identity only (name/brand/pack/barcodes/
    verified images + embeddings). NEVER price/cost/stock/supplier.
  * STORE layer: existing products/inventory tables remain the source of
    truth for merchant business data; product_global_links associates
    a store product with a canonical global product.

Cross-store rule: a store's LOCAL embedding index stays store-scoped
(embeddings_store.py). The GLOBAL index is shared but contains only
embeddings that passed verification (VERIFIED) for the active model.
A local product joins the global index through its link, so recognition
quality is shared without exposing any merchant business data.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.database import db
from app.services.vision.providers.base import VisionProviderError
from app.services.vision.types import Embedding

logger = logging.getLogger("kirana.vision")

_WS_RE = re.compile(r"\s+")
_PACK_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(g|gm|gram|grams|kg|ml|l|ltr|litre|liter)\b", re.IGNORECASE)
_UNIT_CANON = {"g": "g", "gm": "g", "gram": "g", "grams": "g", "kg": "kg", "ml": "ml", "l": "l", "ltr": "l", "litre": "l", "liter": "l"}


def normalize_pack(pack: Optional[str]) -> Optional[str]:
    """'55 G'/'55 gm' -> '55g'; '1 L'/'1000 ml' handled at kg/l granularity
    below; deterministic canonical form for identity matching."""
    if not pack:
        return None
    m = _PACK_RE.search(pack)
    if not m:
        return None
    num, unit = m.group(1), m.group(2).lower()
    canon = _UNIT_CANON.get(unit, unit)
    num_clean = num.rstrip("0").rstrip(".") if "." in num else num
    return f"{num_clean}{canon}"


def _slug(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    return _WS_RE.sub(" ", text)


async def find_by_barcode(barcode: str) -> Optional[dict]:
    """Global product by exact barcode (strongest shared identifier)."""
    row = await db.fetchrow(
        """
        select g.id, g.canonical_name, g.brand, g.category, g.pack_size,
               g.pack_size_normalized, g.verification_status, b.barcode,
               b.verification_status as barcode_verification
        from global_product_barcodes b
        join global_products g on g.id = b.global_product_id
        where b.barcode = $1 and b.verification_status != 'DEPRECATED'
        order by case b.verification_status when 'VERIFIED' then 0 when 'CANDIDATE' then 1 else 2 end
        limit 1
        """,
        barcode.strip(),
    )
    return dict(row) if row else None


async def create_global_product(
    canonical_name: str,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    pack_size: Optional[str] = None,
    barcode: Optional[str] = None,
    source_store_id: Optional[str] = None,
    created_by: Optional[str] = None,
    verification_status: str = "CANDIDATE",
) -> dict:
    """Canonicalize a product into the global layer (idempotent on
    (lower(name), pack)). Reuses an existing global product when the
    normalized identity matches; links the barcode when provided."""
    pack_norm = normalize_pack(pack_size)
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                """
                select id from global_products
                where lower(canonical_name) = lower($1)
                  and coalesce(pack_size_normalized, '') = coalesce($2, '')
                limit 1
                """,
                canonical_name,
                pack_norm,
            )
            if existing:
                gid = existing["id"]
            else:
                gid = await conn.fetchval(
                    """
                    insert into global_products
                      (canonical_name, brand, category, pack_size, pack_size_normalized,
                       verification_status)
                    values ($1, $2, $3, $4, $5, $6)
                    returning id
                    """,
                    canonical_name.strip(),
                    brand,
                    category,
                    pack_size,
                    pack_norm,
                    verification_status,
                )
            if barcode:
                await conn.execute(
                    """
                    insert into global_product_barcodes (global_product_id, barcode, verification_status)
                    values ($1, $2, 'CANDIDATE')
                    on conflict (barcode, barcode_type) do nothing
                    """,
                    gid,
                    barcode.strip(),
                )
    logger.info(
        "global product ready id=%s name=%s store=%s", gid, canonical_name, source_store_id
    )
    return {"id": str(gid), "canonical_name": canonical_name, "pack_normalized": pack_norm}


async def link_store_product(
    store_id: str,
    product_id: str,
    global_product_id: str,
    linked_by: Optional[str] = None,
    link_method: str = "merchant_confirmed",
) -> str:
    """Associate a store product with the canonical product (idempotent)."""
    return str(
        await db.fetchval(
            """
            insert into product_global_links (store_id, product_id, global_product_id, linked_by, link_method)
            values ($1, $2, $3, $4, $5)
            on conflict (store_id, product_id, global_product_id) do update
              set link_method = excluded.link_method
            returning id
            """,
            store_id,
            product_id,
            global_product_id,
            linked_by,
            link_method,
        )
    )


async def link_for_product(store_id: str, product_id: str) -> Optional[str]:
    return await db.fetchval(
        """
        select global_product_id from product_global_links
        where store_id=$1 and product_id=$2
        limit 1
        """,
        store_id,
        product_id,
    )


async def store_owns_global(store_id: str, global_product_id: str) -> bool:
    """True when ANY of this store's products links to the global product —
    the consent/ownership check for contribution flows (§44)."""
    return bool(
        await db.fetchval(
            """
            select 1 from product_global_links
            where store_id=$1 and global_product_id=$2
            limit 1
            """,
            store_id,
            global_product_id,
        )
    )


async def contribute_embedding(
    store_id: str,
    product_id: str,
    global_product_id: str,
    embedding_id: str,
    image_id: Optional[str],
    content_hash: Optional[str],
    quality_score: Optional[float],
    created_by: Optional[str],
) -> dict:
    """Consent-gated contribution: recorded as PENDING_REVIEW (candidate).
    It does NOT enter the authoritative global index until verification
    promotes it (never automatic, §12/§44)."""
    row = await db.fetchrow(
        """
        insert into product_visual_contributions
          (store_id, product_id, global_product_id, embedding_id, image_id,
           contribution_status, quality_score, content_hash, created_by)
        values ($1, $2, $3, $4, $5, 'PENDING_REVIEW', $6, $7, $8)
        on conflict (store_id, product_id, embedding_id) do update
          set global_product_id = excluded.global_product_id,
              contribution_status = 'PENDING_REVIEW'
        returning id, contribution_status
        """,
        store_id,
        product_id,
        global_product_id,
        embedding_id,
        image_id,
        quality_score,
        content_hash,
        created_by,
    )
    return {"contribution_id": str(row["id"]), "status": row["contribution_status"]}


async def record_consent(store_id: str, consent: bool) -> None:
    """Store the merchant's contribution policy for onboarding flows.
    The consent flag lives in store_settings — one row per store."""
    await db.execute(
        """
        insert into store_settings (store_id, visual_contribution_consent)
        values ($1, $2)
        on conflict (store_id) do update
          set visual_contribution_consent = excluded.visual_contribution_consent
        """,
        store_id,
        consent,
    )


async def consent_for(store_id: str) -> bool:
    val = await db.fetchval(
        "select visual_contribution_consent from store_settings where store_id=$1",
        store_id,
    )
    return bool(val)


async def promote_contribution(contribution_id: str) -> Optional[dict]:
    """Quality-gate promotion (operator action, never automatic): copies the
    contributed embedding into global_product_embeddings as ACCEPTED."""
    row = await db.fetchrow(
        """
        select c.*, e.embedding_model, e.embedding_version, e.dimensions, e.embedding
        from product_visual_contributions c
        join product_visual_embeddings e on e.id = c.embedding_id
        where c.id = $1
        """,
        contribution_id,
    )
    if not row:
        return None
    if not row["global_product_id"]:
        return None
    if row["embedding"] is None:
        logger.warning(
            "contribution %s has no vector (pgvector missing?); not promotable",
            contribution_id,
        )
        return None
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            gid = row["global_product_id"]
            image_id = await conn.fetchval(
                """
                insert into global_product_images
                  (global_product_id, image_url, view, source, source_store_id, content_hash, verification_status)
                select $2, i.image_url, i.view, 'store_contribution', pc.store_id, i.content_hash, 'ACCEPTED'
                from product_images i
                join product_visual_contributions pc on pc.image_id = i.id
                where i.id = $1 and pc.id = $3
                on conflict (global_product_id, content_hash) do update
                  set verification_status = 'ACCEPTED'
                returning id
                """,
                row["image_id"],
                gid,
                contribution_id,
            )
            emb_id = await conn.fetchval(
                """
                insert into global_product_embeddings
                  (global_product_id, image_id, embedding, embedding_model, embedding_version,
                   dimensions, source, source_store_id, verification_status, created_by)
                values ($1, $2, $3, $4, $5, $6, 'store_contribution', $7, 'ACCEPTED', $8)
                on conflict (global_product_id, image_id, embedding_model, embedding_version, dimensions)
                do update set verification_status = 'ACCEPTED'
                returning id
                """,
                gid,
                image_id,
                row["embedding"],
                row["embedding_model"],
                row["embedding_version"],
                row["dimensions"],
                row["store_id"],
                row["created_by"],
            )
            await conn.execute(
                "update product_visual_contributions set contribution_status='GLOBAL_APPROVED' where id=$1",
                contribution_id,
            )
    return {"global_embedding_id": str(emb_id), "global_image_id": str(image_id) if image_id else None}


async def search_verified_global(
    embedding: Embedding,
    top_k: int = 5,
    min_similarity: float = 0.0,
) -> list[dict]:
    """Top-K over the GLOBAL index — ACCEPTED/VERIFIED embeddings only,
    for the ACTIVE (model, version). Identity evidence only: the result maps
    to a store product through product_global_links; no business data exists
    on the global layer to leak."""
    rows = await db.fetch(
        """
        select e.global_product_id,
               round((1 - (e.embedding <=> $1::vector))::numeric, 6) as similarity,
               e.id as embedding_id,
               g.verification_status
        from global_product_embeddings e
        join visual_embedding_models m
          on m.model = e.embedding_model and m.version = e.embedding_version and m.is_active
        join global_products g on g.id = e.global_product_id
        where e.embedding is not null
          and e.verification_status = 'ACCEPTED'
          and (1 - (e.embedding <=> $1::vector)) >= $3
        order by e.embedding <=> $1::vector
        limit $2
        """,
        "[" + ",".join(repr(float(v)) for v in embedding.vector) + "]",
        max(1, min(top_k, 20)),
        min_similarity,
    )
    return [
        {
            "global_product_id": str(r["global_product_id"]),
            "similarity": float(r["similarity"]),
            "embedding_id": str(r["embedding_id"]),
            "verification_status": r["verification_status"],
        }
        for r in rows
    ]


async def global_products_for_store(store_id: str) -> list[dict]:
    """Global identity + link method for a store's products (no business data)."""
    rows = await db.fetch(
        """
        select l.product_id, l.global_product_id, l.link_method, g.canonical_name,
               g.brand, g.pack_size_normalized, g.verification_status
        from product_global_links l
        join global_products g on g.id = l.global_product_id
        where l.store_id = $1
        """,
        store_id,
    )
    return [dict(r) for r in rows]


async def link_global_to_store_product(
    store_id: str, global_product_id: str
) -> Optional[str]:
    """Find a local product in THIS store linked to the global product."""
    return await db.fetchval(
        """
        select product_id from product_global_links
        where store_id=$1 and global_product_id=$2
        limit 1
        """,
        store_id,
        global_product_id,
    )


# ---------------------------------------------------------------------------
# Recognition event ledger + feedback
# ---------------------------------------------------------------------------
ACTION_LABEL = {
    "MERCHANT_CONFIRMED": "STRONG_POSITIVE",
    "AUTO_ACCEPTED": "WEAK_POSITIVE",
    "MERCHANT_CORRECTED": "STRONG_NEGATIVE",
    "MERCHANT_REJECTED": "STRONG_NEGATIVE",
    "REMOVED": "UNCERTAIN",
    "MANUAL_REPLACEMENT": "UNCERTAIN",
    "UNRESOLVED": "UNCERTAIN",
}


def label_for_action(action: str) -> str:
    return ACTION_LABEL.get(action, "UNCERTAIN")


async def record_event(
    store_id: str,
    product_id: Optional[str],
    detection_id: Optional[str],
    frame_id: Optional[str],
    predicted_confidence: Optional[float],
    recognition_method: Optional[str],
    visual_similarity: Optional[float],
    ocr_score: Optional[float],
    barcode_match: Optional[bool],
    model_version: Optional[str],
    user_action: Optional[str] = None,
    confirmed_product_id: Optional[str] = None,
    global_product_id: Optional[str] = None,
    checkout_status: Optional[str] = None,
    failure_reason: Optional[str] = None,
) -> str:
    """Append one recognition event. Feedback label derives from the
    merchant ACTION (never from checkout success alone). Idempotent per
    (frame_id, detection_id): a repeated action UPDATES the existing row
    instead of duplicating it."""
    # user_action may be NULL: the pipeline records the prediction first; the
    # merchant's later confirm/correct/reject UPDATES this row (idempotent).
    # UNRESOLVED detections are recorded with their terminal-for-now action.
    action = user_action
    label = label_for_action(action) if action else None
    return str(
        await db.fetchval(
            """
            insert into recognition_events
              (store_id, product_id, confirmed_product_id, global_product_id, detection_id, frame_id,
               predicted_confidence, recognition_method, visual_similarity, ocr_score, barcode_match,
               model_version, user_action, feedback_label, checkout_status, failure_reason, created_by)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,null)
            on conflict (coalesce(frame_id,''), coalesce(detection_id,'')) do update
              set user_action = excluded.user_action,
                  feedback_label = excluded.feedback_label,
                  confirmed_product_id = coalesce(excluded.confirmed_product_id, recognition_events.confirmed_product_id),
                  product_id = coalesce(recognition_events.product_id, excluded.product_id),
                  global_product_id = coalesce(excluded.global_product_id, recognition_events.global_product_id),
                  predicted_confidence = coalesce(excluded.predicted_confidence, recognition_events.predicted_confidence),
                  recognition_method = coalesce(excluded.recognition_method, recognition_events.recognition_method)
            returning id
            """,
            store_id,
            product_id,
            confirmed_product_id,
            global_product_id,
            detection_id,
            frame_id,
            predicted_confidence,
            recognition_method,
            visual_similarity,
            ocr_score,
            barcode_match,
            model_version,
            action,
            label,
            checkout_status,
            failure_reason,
        )
    )


async def product_stats(store_id: str, product_id: str) -> dict:
    """Per-product recognition quality. Deliberately named
    'merchant-confirmed recognition rate' — NOT model accuracy (no ground
    truth without merchant verification, §26/§51)."""
    row = await db.fetchrow(
        """
        select
          count(*) filter (where feedback_label in ('STRONG_POSITIVE','POSITIVE_BEHAVIOR','WEAK_POSITIVE')) as positive,
          count(*) filter (where feedback_label = 'STRONG_NEGATIVE') as corrections,
          count(*) filter (where feedback_label = 'UNCERTAIN') as uncertain,
          count(*) as attempts,
          avg(visual_similarity) filter (where visual_similarity is not null) as avg_similarity,
          avg(predicted_confidence) filter (where predicted_confidence is not null) as avg_confidence,
          count(*) filter (where recognition_method = 'BARCODE') as barcode_matches,
          count(*) filter (where ocr_score > 0) as ocr_assisted
        from recognition_events
        where store_id=$1 and (product_id=$2 or confirmed_product_id=$2)
        """,
        store_id,
        product_id,
    )
    attempts = int(row["attempts"] or 0)
    positive = int(row["positive"] or 0)
    corrections = int(row["corrections"] or 0)
    return {
        "attempts": attempts,
        "merchant_confirmed": positive,
        "merchant_confirmed_rate": round(positive / attempts, 3) if attempts else None,
        "corrections": corrections,
        "uncertain": int(row["uncertain"] or 0),
        "avg_similarity": float(row["avg_similarity"]) if row["avg_similarity"] is not None else None,
        "avg_confidence": float(row["avg_confidence"]) if row["avg_confidence"] is not None else None,
        "barcode_matches": int(row["barcode_matches"] or 0),
        "ocr_assisted": int(row["ocr_assisted"] or 0),
        "metric_name": "merchant-confirmed recognition rate (not model accuracy)",
    }
