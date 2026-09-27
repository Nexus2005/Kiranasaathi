"""External product enrichment + inventory-visual-enrollment endpoints.

Flow (directive §3–§10):
  GET  /api/enrichment/product/{barcode}    — local hit first, else OFF (cached)
  POST /api/enrichment/create-from-external — merchant CONFIRMS the prefill;
      business fields are merchant-only (OFF never sets price/stock/supplier)
  POST /api/enrichment/products/{id}/image-from-url — ingest a legally usable
      external reference image WITH attribution, then visually enroll it
      (DINOv2 → product_visual_embeddings). Enrollment, not training.

Reuses the existing products/counter infrastructure; no second product table.
"""

from __future__ import annotations

import json
import hashlib
import logging
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.security import get_current_user
from app.database import db
from app.services.enrichment import openfoodfacts as off
from app.services.vision import embeddings_store as store
from app.services.vision.global_catalog import (
    consent_for,
    contribute_embedding,
    create_global_product,
    link_for_product,
    link_store_product,
)
from app.services.vision.preprocessing import validate_image
from app.services.vision.providers.base import VisionProviderError
from app.services.vision.registry import VisionRegistry

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/enrichment", tags=["enrichment"])

# Reuse the same registry singletons the counter router uses so the model is
# loaded once per process.
from app.routers.counter import get_vision_registry  # noqa: E402


def _registry_shared() -> VisionRegistry:
    return get_vision_registry()


# ---------------------------------------------------------------------------
# 1. Lookup: local store catalog first, then OFF (cached server-side)
# ---------------------------------------------------------------------------
@router.get("/product/{barcode}")
async def lookup_product(barcode: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    code = barcode.strip()
    if not code:
        raise HTTPException(400, "Empty barcode")

    local = await db.fetchrow(
        """
        select p.id, p.name, p.category, p.barcode, p.mrp, p.selling_price,
               coalesce(i.quantity, 0) as quantity,
               (select count(*) from product_images pi where pi.product_id = p.id) as image_count
        from products p
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        where p.store_id = $1 and p.barcode = $2
        limit 1
        """,
        user["store_id"],
        code,
    )
    if local:
        return {
            "result": "FOUND_LOCAL",
            "source": "store",
            "product": dict(local),
        }

    ext = await off.lookup(code)
    if ext.get("found"):
        return {
            "result": "FOUND_EXTERNAL",
            "source": "openfoodfacts",
            "product": ext["product"],
            "source_url": ext.get("source_url"),
            "license_note": ext.get("license_note"),
        }
    return {
        "result": "NOT_FOUND",
        "source": ext.get("error", "not_found"),
        "license_note": ext.get("license_note"),
    }


# ---------------------------------------------------------------------------
# 2. Create from external prefill — merchant confirms; store fields are theirs
# ---------------------------------------------------------------------------
class CreateFromExternalIn(BaseModel):
    """`product` fields come from OFF (marked SOURCE: Open Food Facts).
    Business fields are merchant-controlled (SOURCE: Store) and REQUIRED."""

    barcode: str = Field(max_length=64)
    name: str = Field(max_length=200)
    brand: Optional[str] = Field(default=None, max_length=120)
    category: Optional[str] = Field(default=None, max_length=120)
    pack_size: Optional[str] = Field(default=None, max_length=64)
    description: Optional[str] = Field(default=None, max_length=1000)
    external_image_url: Optional[str] = None
    # merchant-only business data — never prefilled from external sources
    mrp: float = Field(gt=0)
    selling_price: float = Field(gt=0)
    purchase_price: float = Field(gt=0)
    initial_stock: int = Field(ge=0)
    reorder_level: int = Field(default=10, ge=0)
    expiry_date: Optional[str] = None


@router.post("/create-from-external", status_code=201)
async def create_from_external(
    body: CreateFromExternalIn, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    store_id = user["store_id"]
    if body.selling_price < body.purchase_price:
        raise HTTPException(400, "Selling price cannot be below purchase price")

    barcode = body.barcode.strip()
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchval(
                "select 1 from products where store_id=$1 and barcode=$2",
                store_id,
                barcode,
            )
            if exists:
                raise HTTPException(409, "A product with this barcode already exists in your store")

            # identity fields may carry external enrichment; display_name keeps
            # brand + pack where available (merchant edited/confirmed already)
            product = await conn.fetchrow(
                """
                insert into products
                  (store_id, name, category, sku, barcode, unit, mrp, selling_price,
                   purchase_price, reorder_level)
                values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                returning id, name
                """,
                store_id,
                body.name.strip(),
                (body.category or "Other").strip()[:120],
                None,
                barcode,
                "pack",
                body.mrp,
                body.selling_price,
                body.purchase_price,
                body.reorder_level,
            )
            pid = product["id"]
            await conn.execute(
                "insert into inventory (store_id, product_id, quantity) values ($1,$2,$3)",
                store_id,
                pid,
                body.initial_stock,
            )
            if body.initial_stock > 0:
                await conn.execute(
                    """
                    insert into inventory_movements (store_id, product_id, change, quantity_after, reason)
                    values ($1,$2,$3,$4,'INITIAL_STOCK')
                    """,
                    store_id,
                    pid,
                    body.initial_stock,
                    body.initial_stock,
                )
                await conn.execute(
                    """
                    insert into inventory_batches (store_id, product_id, batch_no, quantity, purchase_cost, expiry_date)
                    values ($1,$2,$3,$4,$5,$6::date)
                    """,
                    store_id,
                    pid,
                    f"OPEN-{str(pid)[:8]}",
                    body.initial_stock,
                    body.purchase_price,
                    body.expiry_date,
                )
            await conn.execute(
                """
                insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
                values ($1,$2,'PRODUCT_CREATED','product',$3,$4,$5)
                """,
                store_id,
                user["id"],
                pid,
                json.dumps({"name": body.name, "source": "openfoodfacts_enriched"}),
                f"Product created from external enrichment: {body.name}",
            )

    # canonicalize into the global layer immediately (identity, not business)
    try:
        g = await create_global_product(
            canonical_name=body.name.strip(),
            brand=body.brand or (body.name.split(" ") or [None])[0],
            category=body.category,
            pack_size=body.pack_size or body.name,
            barcode=barcode,
            source_store_id=store_id,
        )
        await link_store_product(store_id, pid, g["id"], user["id"], "barcode_match")
        gid = g["id"]
    except Exception as exc:  # noqa: BLE001 — identity layer must never block creation
        logger.warning("global canonicalization skipped: %s", exc)
        gid = None

    # Optionally ingest the external image as the FIRST visual reference
    # (attribution recorded; embedding generated = visual enrollment)
    image_ingested = False
    if body.external_image_url:
        try:
            image_ingested = bool(
                await ingest_external_image(store_id, pid, body.external_image_url, user["id"])
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("external image ingest failed: %s", exc)

    return {
        "product_id": pid,
        "name": body.name,
        "global_product_id": gid,
        "image_ingested": image_ingested,
        "note": (
            "External identity prefill confirmed by merchant; store business "
            "fields are merchant-owned."
        ),
    }


# ---------------------------------------------------------------------------
# 3. Reference images: external-URL ingest + manual upload → visual enrollment
# ---------------------------------------------------------------------------
async def _embed_and_store(
    store_id: str, product_id: str, data: bytes, view: str, user_id: str,
    source: str, source_url: Optional[str], attribution: Optional[str],
) -> dict[str, Any]:
    """Shared tail of image ingest: hash-dedup → product_images → DINOv2 →
    product_visual_embeddings (+ consent-gated global contribution)."""
    registry = _registry_shared()
    embedder = registry.get_ready("embedder")
    if embedder is None:
        raise HTTPException(
            503,
            {"message": "Embedding model not configured on this deployment.", "code": "VISION_NOT_CONFIGURED"},
        )
    if not await store.pgvector_available():
        raise HTTPException(
            503, {"message": "pgvector unavailable on this database", "code": "VISION_NOT_CONFIGURED"}
        )

    embedding = embedder.embed(data)
    await store.register_model(embedding.model, embedding.version, embedding.dimensions)

    content_hash = hashlib.sha256(data).hexdigest()
    image_id = await store.insert_product_image(
        store_id, product_id, view=view, content_hash=content_hash, created_by=user_id,
        source=source, source_url=source_url, attribution=attribution,
    )
    embedding_id = await store.insert_embedding(
        store_id, product_id, embedding, image_id=image_id, created_by=user_id
    )
    # consent-gated global contribution candidate (§18) — never blocks, never
    # auto-trusts; the operator quality gate promotes it later
    contribution_status = None
    try:
        if await consent_for(store_id):
            gid = await link_for_product(store_id, product_id)
            if gid:
                contribution = await contribute_embedding(
                    store_id, product_id, gid, embedding_id, image_id,
                    content_hash, quality_score=None, created_by=user_id,
                )
                contribution_status = contribution["status"] if contribution else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("global contribution skipped: %s", exc)
    return {
        "image_id": image_id,
        "embedding_id": embedding_id,
        "dimensions": embedding.dimensions,
        "model": embedding.model,
        "contribution_status": contribution_status,
    }


async def ingest_external_image(
    store_id: str, product_id: str, url: str, user_id: str
) -> dict[str, Any]:
    """Download + enroll an external reference image WITH attribution."""
    prod = await store.product_row(store_id, product_id)
    if prod is None:
        raise HTTPException(404, "Product not found")
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        data = resp.content
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not fetch external image: {exc}")
    try:
        validate_image(data)
    except VisionProviderError as exc:
        raise HTTPException(400, {"message": str(exc), "code": exc.code})
    result = await _embed_and_store(
        store_id, product_id, data, view="front", user_id=user_id,
        source="external", source_url=url,
        attribution="Image: Open Food Facts contributors, CC BY-SA",
    )
    return dict(result, ingested=True)


class ImageFromUrlIn(BaseModel):
    url: str = Field(max_length=1000)
    view: str = Field(default="front", max_length=32)


@router.post("/products/{product_id}/image-from-url", status_code=201)
async def image_from_url(
    product_id: str, body: ImageFromUrlIn, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    return await ingest_external_image(
        user["store_id"], product_id, body.url.strip(), user["id"]
    )


class AddStockIn(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)


@router.post("/add-stock")
async def add_stock(body: AddStockIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Top-up an existing catalog product's stock (inventory onboarding for a
    product the store already carries). Atomic: inventory + movement + FEFO
    batch update in one transaction."""
    store_id = user["store_id"]
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                select p.id, p.sku, i.quantity as current_qty
                from products p
                left join inventory i on i.product_id = p.id and i.store_id = p.store_id
                where p.id = $1 and p.store_id = $2
                for update of i
                """,
                body.product_id,
                store_id,
            )
            if row is None:
                raise HTTPException(404, "Product not found")
            new_qty = int(row["current_qty"] or 0) + body.quantity
            await conn.execute(
                """
                insert into inventory (store_id, product_id, quantity)
                values ($1, $2, $3)
                on conflict (store_id, product_id) do update set quantity = $3,
                  updated_at = now()
                """,
                store_id,
                body.product_id,
                new_qty,
            )
            await conn.execute(
                """
                insert into inventory_movements (store_id, product_id, change, quantity_after, reason)
                values ($1, $2, $3, $4, 'STOCK_IN')
                """,
                store_id,
                body.product_id,
                body.quantity,
                new_qty,
            )
    return {"product_id": body.product_id, "quantity": new_qty, "added": body.quantity}


@router.post("/products/{product_id}/images", status_code=201)
async def upload_product_image(
    product_id: str,
    image: UploadFile = File(...),
    view: str = "front",
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Merchant capture/upload → visual enrollment (same path as counter)."""
    store_id = user["store_id"]
    prod = await store.product_row(store_id, product_id)
    if prod is None:
        raise HTTPException(404, "Product not found")
    data = await image.read()
    try:
        validate_image(data)
    except VisionProviderError as exc:
        raise HTTPException(400, {"message": str(exc), "code": exc.code})
    result = await _embed_and_store(
        store_id, product_id, data, view=view, user_id=user["id"],
        source="merchant", source_url=None, attribution=None,
    )
    return dict(result, enrolled=True)
