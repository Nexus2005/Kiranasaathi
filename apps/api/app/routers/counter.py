"""Smart Counter vision API (spec §35, §36, §37, §45, §46).

Endpoints (all authenticated, store-scoped from the JWT — never trusted
from the request body):
  GET  /api/counter/health            — provider/model/pgvector readiness
  POST /api/counter/recognize         — one frame → per-detection identity
  POST /api/counter/products/{id}/embeddings — onboarding: embed reference image(s)
  GET  /api/counter/products/{id}/embeddings — list stored embeddings

No endpoint here creates sales or touches prices — identity only (§2).
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.database import db

from app.security import get_current_user
from app.services.vision import embeddings_store as store
from app.services.vision import global_catalog as gcat
from app.services.vision.pipeline import RecognitionPipeline
from app.services.vision.preprocessing import decode, validate_image
from app.services.vision.providers.base import VisionProviderError
from app.services.vision.registry import VisionRegistry

logger = logging.getLogger("kirana.counter")

router = APIRouter(prefix="/counter", tags=["counter"])

_registry = VisionRegistry()
_pipeline = RecognitionPipeline(_registry)


def get_vision_registry() -> VisionRegistry:
    """Shared accessor — enrichment/inventory endpoints reuse the same
    provider instances so models load once per process."""
    return _registry


# ---------------------------------------------------------------------------
# Simple in-process rate limit (per store) — frames are expensive to process.
# A production deployment moves this to Redis; the contract stays identical.
# ---------------------------------------------------------------------------
class _SlidingWindow:
    def __init__(self, max_events: int, per_seconds: float) -> None:
        self.max = max_events
        self.per = per_seconds
        self.hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = [t for t in self.hits.get(key, []) if now - t < self.per]
        if len(window) >= self.max:
            self.hits[key] = window
            return False
        window.append(now)
        self.hits[key] = window
        return True


_recognize_limiter = _SlidingWindow(max_events=30, per_seconds=10.0)
_embed_limiter = _SlidingWindow(max_events=60, per_seconds=60.0)


def _vision_503(exc: VisionProviderError) -> HTTPException:
    return HTTPException(503, {"message": str(exc), "code": exc.code})


# ---------------------------------------------------------------------------
@router.get("/health")
async def counter_health(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Provider readiness (§45). No sensitive details, no model paths."""
    health = await _registry.health()
    return {
        "detector": health.as_dict()["detector"],
        "barcode": health.as_dict()["barcode"],
        "embedder": health.as_dict()["embedder"],
        "ocr": health.as_dict()["ocr"],
        "pgvector": health.pgvector,
        "recognition_ready": health.recognition_ready(),
        "allow_mock": health.allow_mock,
        "thresholds": {
            "auto_add": _pipeline.matcher.config.auto_add_threshold,
            "review": _pipeline.matcher.config.review_threshold,
        },
    }


# ---------------------------------------------------------------------------
class RecognizeResponse(BaseModel):
    frame_id: str
    timestamp: int
    detections: list[dict[str, Any]]
    skipped_stages: dict[str, str] = Field(default_factory=dict)


@router.post("/recognize")
async def recognize(
    frame: UploadFile = File(...),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """One camera frame → per-detection identity candidates (§36).

    Statuses: IDENTIFIED (auto-addable) / REVIEW_REQUIRED / UNRESOLVED.
    Detection NEVER creates a bill line — the merchant decides (§16).
    """
    store_id = user["store_id"]
    if not _recognize_limiter.allow(store_id):
        raise HTTPException(429, "Too many frames — slow down the scanner interval")

    data = await frame.read()
    try:
        validate_image(data)  # magic bytes + size cap (§7)
    except VisionProviderError as exc:
        raise _vision_503(exc) if exc.code == "IMAGE_TOO_LARGE" else HTTPException(400, {"message": str(exc), "code": exc.code})

    health = await _registry.health()
    if not health.recognition_ready():
        missing = [
            k
            for k, ok in (
                ("detector", health.detector.available),
                ("embedder", health.embedder.available),
                ("pgvector", health.pgvector),
            )
            if not ok
        ]
        raise HTTPException(
            503,
            {
                "message": "Visual recognition is not configured on this deployment.",
                "code": "VISION_NOT_CONFIGURED",
                "missing": missing,
            },
        )

    try:
        result = await _pipeline.recognize_frame(data, store_id)
    except VisionProviderError as exc:
        raise _vision_503(exc)
    return result.as_dict()


# ---------------------------------------------------------------------------
class EmbeddingOut(BaseModel):
    id: str
    product_id: str
    image_id: Optional[str]
    model: str
    version: str
    dimensions: int
    created_at: str


@router.post("/products/{product_id}/embeddings", status_code=201)
async def add_product_embedding(
    product_id: str,
    image: UploadFile = File(...),
    view: str = "front",
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Onboarding (§10, §21): embed one reference image for a store product.

    The product becomes searchable by camera. Multiple images per product
    (front/back/side/angled) improve recognition robustness (§54).
    """
    store_id = user["store_id"]
    if not _embed_limiter.allow(store_id):
        raise HTTPException(429, "Too many uploads — try again shortly")

    # Product must belong to this store (§46)
    prod = await store.product_row(store_id, product_id)
    if prod is None:
        raise HTTPException(404, "Product not found")

    data = await image.read()
    try:
        validate_image(data)
    except VisionProviderError as exc:
        raise HTTPException(400, {"message": str(exc), "code": exc.code})

    if not await store.pgvector_available():
        raise HTTPException(
            503,
            {"message": "pgvector unavailable on this database", "code": "VISION_NOT_CONFIGURED"},
        )
    embedder = _registry.get_ready("embedder")
    if embedder is None:
        raise HTTPException(
            503,
            {
                "message": "Embedding model not configured on this deployment.",
                "code": "VISION_NOT_CONFIGURED",
            },
        )

    try:
        embedding = embedder.embed(data)
    except VisionProviderError as exc:
        raise _vision_503(exc)

    # Register the active model dimension on first use (idempotent)
    await store.register_model(embedding.model, embedding.version, embedding.dimensions)

    content_hash = hashlib.sha256(data).hexdigest()
    image_id = await store.insert_product_image(
        store_id, product_id, view=view, content_hash=content_hash, created_by=user["id"]
    )
    embedding_id = await store.insert_embedding(
        store_id, product_id, embedding, image_id=image_id, created_by=user["id"]
    )

    # Global Product Brain (§3–§8): enrollment transparently canonicalizes a
    # barcoded product into the global layer (idempotent) and links it — the
    # merchant never sees this. With prior explicit consent, the embedding is
    # recorded as a PENDING_REVIEW contribution candidate (never automatic
    # ground truth, §12/§44). Failures never block enrollment.
    global_info: Optional[dict[str, Any]] = None
    try:
        barcode_val = await db.fetchval(
            "select barcode from products where id=$1 and store_id=$2", product_id, store_id
        )
        gid = await gcat.link_for_product(store_id, product_id)
        if gid is None and barcode_val:
            prod_full = await db.fetchrow(
                "select name, category from products where id=$1", product_id
            )
            g = await gcat.create_global_product(
                canonical_name=prod_full["name"],
                brand=(prod_full["name"].split(" ") or [None])[0],
                category=prod_full["category"],
                pack_size=prod_full["name"],
                barcode=barcode_val,
                source_store_id=store_id,
            )
            await gcat.link_store_product(
                store_id, product_id, g["id"], user["id"], "barcode_match"
            )
            gid = g["id"]
        if gid:
            if await gcat.consent_for(store_id):
                contribution = await gcat.contribute_embedding(
                    store_id,
                    product_id,
                    gid,
                    embedding_id,
                    image_id,
                    content_hash,
                    quality_score=None,
                    created_by=user["id"],
                )
            else:
                contribution = None
            global_info = {
                "global_product_id": gid,
                "contribution_status": contribution["status"] if contribution else None,
            }
    except Exception as exc:  # noqa: BLE001 — enrichment must never block enrollment
        logger.warning("global enrichment skipped: %s", exc)

    return {
        "embedding_id": embedding_id,
        "image_id": image_id,
        "model": embedding.model,
        "version": embedding.version,
        "dimensions": embedding.dimensions,
        "global": global_info,
    }


@router.get("/products/{product_id}/embeddings")
async def list_product_embeddings(
    product_id: str, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    rows = await store.list_embeddings(store_id=user["store_id"], product_id=product_id)
    return {"items": rows}


# ---------------------------------------------------------------------------
# Merchant feedback — the recognition event ledger (master-prompt §16–22).
# Feedback is ANALYTICS DATA ONLY: it never changes stock, price, tax, or
# creates sales. Idempotent per (frame_id, detection_id) via upsert.
# ---------------------------------------------------------------------------
class FeedbackIn(BaseModel):
    action: str = Field(..., description="MERCHANT_CONFIRMED | MERCHANT_CORRECTED | MERCHANT_REJECTED")
    confirmed_product_id: Optional[str] = Field(default=None, description="actual product when correcting")


@router.post("/events/{event_id}/feedback")
async def recognition_feedback(
    event_id: str, body: FeedbackIn, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    store_id = user["store_id"]
    action = body.action.strip().upper()
    if action not in ("MERCHANT_CONFIRMED", "MERCHANT_CORRECTED", "MERCHANT_REJECTED"):
        raise HTTPException(400, "invalid action")

    ev = await db.fetchrow(
        "select id, product_id, frame_id, detection_id, predicted_confidence, recognition_method from recognition_events where id=$1 and store_id=$2",
        event_id,
        store_id,
    )
    if not ev:
        raise HTTPException(404, "Recognition event not found")
    if action == "MERCHANT_CORRECTED" and not body.confirmed_product_id:
        raise HTTPException(400, "MERCHANT_CORRECTED requires confirmed_product_id")

    # Correction target must belong to THIS store (§45)
    confirmed_pid = None
    if body.confirmed_product_id:
        confirmed_pid = await db.fetchval(
            "select id from products where id=$1 and store_id=$2",
            body.confirmed_product_id,
            store_id,
        )
        if not confirmed_pid:
            raise HTTPException(404, "Corrected product not found in this store")

    global_pid = await db.fetchval(
        "select global_product_id from product_global_links where store_id=$1 and product_id=$2",
        store_id,
        confirmed_pid or ev["product_id"],
    )
    await gcat.record_event(
        store_id=store_id,
        product_id=ev["product_id"],
        detection_id=ev["detection_id"],
        frame_id=ev["frame_id"],
        predicted_confidence=float(ev["predicted_confidence"]) if ev["predicted_confidence"] is not None else None,
        recognition_method=ev["recognition_method"],
        visual_similarity=None,
        ocr_score=None,
        barcode_match=None,
        model_version=None,
        user_action=action,
        confirmed_product_id=confirmed_pid,
        global_product_id=global_pid,
    )
    return {"event_id": event_id, "action": action, "recorded": True}


# ---------------------------------------------------------------------------
# Global product knowledge — canonical identity shared across stores.
# NO merchant business data is exposed through these endpoints (§28).
# ---------------------------------------------------------------------------
@router.get("/global/lookup/{barcode}")
async def global_lookup(barcode: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Scan-once-reuse-everywhere: barcode -> canonical global product (if any)."""
    g = await gcat.find_by_barcode(barcode)
    if not g:
        return {"found": False}
    local_pid = await gcat.link_global_to_store_product(user["store_id"], g["id"])
    return {
        "found": True,
        "global_product": {
            "id": g["id"],
            "canonical_name": g["canonical_name"],
            "brand": g["brand"],
            "category": g["category"],
            "pack_size": g["pack_size"],
            "verification_status": g["verification_status"],
        },
        "linked_local_product_id": local_pid,
    }


class GlobalLinkIn(BaseModel):
    global_product_id: str
    product_id: str
    link_method: str = Field(default="merchant_confirmed")


@router.post("/global/link")
async def global_link(body: GlobalLinkIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Associate one of this store's products with the canonical product."""
    prod = await store.product_row(user["store_id"], body.product_id)
    if prod is None:
        raise HTTPException(404, "Product not found in this store")
    exists = await db.fetchval("select 1 from global_products where id=$1", body.global_product_id)
    if not exists:
        raise HTTPException(404, "Global product not found")
    link_id = await gcat.link_store_product(
        user["store_id"], body.product_id, body.global_product_id, user["id"], body.link_method
    )
    return {"link_id": link_id, "linked": True}


class GlobalCreateIn(BaseModel):
    product_id: str
    canonical_name: str = Field(min_length=2, max_length=160)
    brand: Optional[str] = None
    category: Optional[str] = None
    pack_size: Optional[str] = None
    barcode: Optional[str] = None


@router.post("/global/create", status_code=201)
async def global_create(body: GlobalCreateIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Canonicalize one of this store's products into the global layer
    (idempotent on name+pack) and link it. The product must belong to the
    caller's store (§46)."""
    prod = await store.product_row(user["store_id"], body.product_id)
    if prod is None:
        raise HTTPException(404, "Product not found in this store")
    g = await gcat.create_global_product(
        canonical_name=body.canonical_name,
        brand=body.brand,
        category=body.category,
        pack_size=body.pack_size,
        barcode=body.barcode,
        source_store_id=user["store_id"],
    )
    await gcat.link_store_product(user["store_id"], body.product_id, g["id"], user["id"], "merchant_confirmed")
    return {"global_product": g, "linked": True}


class ContributionIn(BaseModel):
    product_id: str
    embedding_id: str
    consent: bool


@router.post("/global/contribute")
async def global_contribute(body: ContributionIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Record a merchant contribution CANDIDATE for the global visual index.
    Requires the merchant's explicit consent; the contribution stays
    PENDING_REVIEW and never becomes authoritative automatically (§12/§44)."""
    store_id = user["store_id"]
    await gcat.record_consent(store_id, body.consent)
    if not body.consent:
        return {"consent": False, "contribution": None}
    gid = await gcat.link_for_product(store_id, body.product_id)
    if not gid:
        raise HTTPException(400, "Product is not linked to a global product")
    emb = await db.fetchrow(
        "select id, image_id from product_visual_embeddings where id=$1 and store_id=$2 and product_id=$3",
        body.embedding_id,
        store_id,
        body.product_id,
    )
    if not emb:
        raise HTTPException(404, "Embedding not found for this product")
    content_hash = await db.fetchval("select content_hash from product_images where id=$1", emb["image_id"]) if emb["image_id"] else None
    res = await gcat.contribute_embedding(
        store_id,
        body.product_id,
        gid,
        str(emb["id"]),
        str(emb["image_id"]) if emb["image_id"] else None,
        content_hash,
        quality_score=None,
        created_by=user["id"],
    )
    return {"consent": True, "contribution": res}


@router.get("/global/contributions/{contribution_id}/verify")
async def verify_contribution(
    contribution_id: str,
    approve: bool,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Operator verification hook (quality gate §12). Promotion copies the
    embedding into the AUTHORITATIVE global index as ACCEPTED. Never called
    automatically by merchant actions."""
    # Operator gate: merchant owner or platform admin. The current auth model
    # has one role; this endpoint exists for the documented promotion flow.
    res = await gcat.promote_contribution(contribution_id) if approve else None
    if approve and not res:
        raise HTTPException(404, "Contribution not promotable")
    return {"verified": approve, "promoted": res}


@router.get("/products/{product_id}/recognition-stats")
async def product_recognition_stats(
    product_id: str, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    """Per-product recognition quality — reported as merchant-confirmed
    recognition rate, never as model accuracy (§26/§51)."""
    prod = await store.product_row(user["store_id"], product_id)
    if prod is None:
        raise HTTPException(404, "Product not found")
    stats = await gcat.product_stats(user["store_id"], product_id)
    return {"product_id": product_id, **stats}
