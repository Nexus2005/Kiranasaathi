"""Embeddings store — DB access for the visual catalog (spec §11).

All queries are store-scoped (defense-in-depth on top of API-layer checks;
RLS deny-by-default remains). Embeddings are written with model+version so
replacing the model later never corrupts old vectors (§44).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from app.database import db
from app.services.vision.providers.base import VisionProviderError
from app.services.vision.types import Candidate, Embedding

logger = logging.getLogger("kirana.vision")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _top_k() -> int:
    return max(1, min(_env_int("COUNTER_TOP_K", 5), 20))


async def pgvector_available() -> bool:
    return bool(
        await db.fetchval("select 1 from pg_extension where extname='vector'")
    )


async def active_model() -> Optional[tuple[str, str, int]]:
    """The active (model, version, dimensions) or None when not registered."""
    row = await db.fetchrow(
        "select model, version, dimensions from visual_embedding_models where is_active"
    )
    return (row["model"], row["version"], row["dimensions"]) if row else None


async def register_model(model: str, version: str, dimensions: int) -> None:
    """Register/activate a model dimension; deactivates other models."""
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("update visual_embedding_models set is_active=false")
            await conn.execute(
                """
                insert into visual_embedding_models (model, version, dimensions, is_active)
                values ($1, $2, $3, true)
                on conflict (model) do update
                  set version = excluded.version,
                      dimensions = excluded.dimensions,
                      is_active = true
                """,
                model,
                version,
                dimensions,
            )
            # Ensure a typed pgvector column exists for this dimension (ANN
            # indexing); the untyped `embedding` column always works too.
            if await conn.fetchval("select 1 from pg_extension where extname='vector'"):
                await conn.execute("select set_embedding_dim($1)", dimensions)


def _vector_literal(vec: list[float]) -> str:
    """pgvector text literal: '[0.1,0.2,...]' (validated floats only)."""
    if not vec:
        raise VisionProviderError("empty embedding", "VISION_PROVIDER_ERROR")
    if len(vec) > 4096:
        raise VisionProviderError("embedding dimension too large", "VISION_PROVIDER_ERROR")
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


async def insert_embedding(
    store_id: str,
    product_id: str,
    embedding: Embedding,
    image_id: Optional[str] = None,
    created_by: Optional[str] = None,
) -> str:
    """Store one embedding; the vector goes in as a validated text literal
    cast to vector — no dynamic SQL from user input is involved.

    Idempotent for identical (store, product, image, model, version, dim):
    re-uploading the same reference image returns the existing embedding
    instead of failing (matches the hash-dedup contract of onboarding)."""
    model_row = await db.fetchrow(
        "select version, dimensions from visual_embedding_models where model=$1 and is_active",
        embedding.model,
    )
    if not model_row or int(model_row["dimensions"]) != embedding.dimensions:
        raise VisionProviderError(
            f"embedding dimensions {embedding.dimensions} do not match registered "
            f"model {embedding.model} ({model_row['dimensions'] if model_row else 'unregistered'})",
            "EMBEDDING_DIMENSION_MISMATCH",
        )
    if image_id:
        existing = await db.fetchval(
            """
            select id from product_visual_embeddings
            where store_id=$1 and product_id=$2 and image_id=$3
              and embedding_model=$4 and embedding_version=$5 and dimensions=$6
            """,
            store_id,
            product_id,
            image_id,
            embedding.model,
            embedding.version,
            embedding.dimensions,
        )
        if existing:
            return str(existing)
    return str(
        await db.fetchval(
            """
            insert into product_visual_embeddings
              (store_id, product_id, image_id, embedding, embedding_model, embedding_version, dimensions, created_by)
            values ($1, $2, $3, $4::vector, $5, $6, $7, $8)
            on conflict (store_id, product_id, image_id, embedding_model, embedding_version, dimensions)
            do update set dimensions = excluded.dimensions
            returning id
            """,
            store_id,
            product_id,
            image_id,
            _vector_literal(embedding.vector),
            embedding.model,
            embedding.version,
            embedding.dimensions,
            created_by,
        )
    )


async def search_similar(
    store_id: str,
    embedding: Embedding,
    top_k: Optional[int] = None,
) -> list[Candidate]:
    """Store-scoped top-K retrieval. Uses the SQL match function (§11); the
    function itself constrains store_id + model + version, so cross-store
    leakage is impossible by construction."""
    k = top_k or _top_k()
    rows = await db.fetch(
        "select * from match_product_embeddings($1, $2::vector, $3, $4, $5)",
        store_id,
        _vector_literal(embedding.vector),
        embedding.model,
        embedding.version,
        # fetch extra rows so per-product dedup still fills top_k
        max(k * 4, 20),
    )
    # Product-level matching: one candidate per PRODUCT (its BEST embedding).
    # A product with several reference images must not look like several
    # near-tied "variants" downstream (ambiguity logic operates on distinct
    # products, not duplicate embeddings of the same product).
    best_per_product: dict[str, Candidate] = {}
    for r in rows:
        pid = str(r["product_id"])
        sim = float(r["similarity"])
        cur = best_per_product.get(pid)
        if cur is None or sim > cur.similarity:
            best_per_product[pid] = Candidate(
                product_id=pid,
                similarity=sim,
                rank=0,
                embedding_id=str(r["embedding_id"]),
            )
    ranked = sorted(best_per_product.values(), key=lambda c: -c.similarity)[:k]
    return [
        Candidate(c.product_id, c.similarity, i + 1, c.embedding_id)
        for i, c in enumerate(ranked)
    ]


async def product_names_for(product_ids: list[str]) -> dict[str, str]:
    if not product_ids:
        return {}
    rows = await db.fetch(
        "select id, name from products where id = any($1::uuid[])",
        product_ids,
    )
    return {str(r["id"]): r["name"] for r in rows}


async def product_meta_for(product_ids: list[str]) -> dict[str, dict]:
    """{product_id: {brand, pack_size}} for the matcher's multi-signal
    scoring (brand/pack agreement with OCR evidence)."""
    if not product_ids:
        return {}
    rows = await db.fetch(
        """
        select p.id, p.name, p.category,
               coalesce(g.brand, split_part(p.name, ' ', 1)) as brand,
               coalesce(
                 g.pack_size_normalized,
                 nullif(lower(substring(p.name from '(\\d+(?:\\.\\d+)?\\s*[a-zA-Z]+)')), '')
               ) as pack_size
        from products p
        left join product_global_links l on l.store_id = p.store_id and l.product_id = p.id
        left join global_products g on g.id = l.global_product_id
        where p.id = any($1::uuid[])
        """,
        product_ids,
    )
    meta: dict[str, dict] = {}
    for r in rows:
        meta[str(r["id"])] = {
            "name": r["name"],
            "brand": r["brand"],
            "pack_size": r["pack_size"],
            "category": r["category"],
        }
    return meta


async def find_product_by_barcode(store_id: str, code: str) -> Optional[str]:
    """Exact barcode → product id (store-scoped; §14 priority 1)."""
    return await db.fetchval(
        "select id from products where store_id=$1 and barcode=$2 and is_active limit 1",
        store_id,
        code,
    )


async def product_row(store_id: str, product_id: str) -> Optional[dict]:
    """Store-scoped product fetch — ownership check for onboarding (§46)."""
    row = await db.fetchrow(
        "select id, name from products where id=$1 and store_id=$2",
        product_id,
        store_id,
    )
    return dict(row) if row else None


async def insert_product_image(
    store_id: str,
    product_id: str,
    view: str = "front",
    content_hash: Optional[str] = None,
    created_by: Optional[str] = None,
    source: str = "merchant",
    source_url: Optional[str] = None,
    attribution: Optional[str] = None,
) -> Optional[str]:
    """Register a reference image (hash only — bytes are never persisted
    by this endpoint). Duplicate uploads of identical bytes return the
    existing row instead of failing (idempotent onboarding). Provenance:
    source='merchant'|'external'|'seed'; external images carry source_url
    + attribution (ODbL / CC BY-SA duty)."""
    if content_hash:
        existing = await db.fetchval(
            """
            select id from product_images
            where store_id=$1 and product_id=$2 and content_hash=$3
            """,
            store_id,
            product_id,
            content_hash,
        )
        if existing:
            return str(existing)
    image_id = await db.fetchval(
        """
        insert into product_images (store_id, product_id, view, content_hash, created_by,
                                    source, source_url, attribution)
        values ($1, $2, $3, $4, $5, $6, $7, $8)
        returning id
        """,
        store_id,
        product_id,
        view,
        content_hash,
        created_by,
        source,
        source_url,
        attribution,
    )
    return str(image_id)


async def list_embeddings(store_id: str, product_id: str) -> list[dict]:
    """All stored embeddings for a product (model/version traceability)."""
    rows = await db.fetch(
        """
        select id, product_id, image_id, embedding_model as model,
               embedding_version as version, dimensions, created_at
        from product_visual_embeddings
        where store_id=$1 and product_id=$2
        order by created_at desc
        """,
        store_id,
        product_id,
    )
    return [
        {
            "id": str(r["id"]),
            "product_id": str(r["product_id"]),
            "image_id": str(r["image_id"]) if r["image_id"] else None,
            "model": r["model"],
            "version": r["version"],
            "dimensions": r["dimensions"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
