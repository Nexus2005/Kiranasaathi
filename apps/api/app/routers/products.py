from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.database import db
from app.security import get_current_user
from app.services.vision.preprocessing import validate_image
from app.services.vision.providers.base import VisionProviderError

router = APIRouter(prefix="/products", tags=["products"])


class ProductIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    category: str = Field(default="Other", max_length=80)
    sku: Optional[str] = Field(default=None, max_length=64)
    barcode: Optional[str] = Field(default=None, max_length=64)
    unit: str = Field(default="pcs", max_length=24)
    mrp: float = Field(ge=0)
    selling_price: float = Field(ge=0)
    purchase_price: float = Field(ge=0)
    reorder_level: int = Field(default=10, ge=0)
    initial_stock: int = Field(default=0, ge=0)
    expiry_date: Optional[str] = None
    # identity/image fields (OFF enrichment or merchant-entered)
    brand: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    image_url: Optional[str] = Field(default=None, max_length=1000)
    source: str = Field(default="manual", max_length=32)
    attribution: Optional[str] = Field(default=None, max_length=500)


class ProductPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=160)
    category: Optional[str] = None
    mrp: Optional[float] = Field(default=None, ge=0)
    selling_price: Optional[float] = Field(default=None, ge=0)
    purchase_price: Optional[float] = Field(default=None, ge=0)
    reorder_level: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None
    brand: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    image_url: Optional[str] = Field(default=None, max_length=1000)


async def _assert_product_edit_allowed(user: dict) -> None:
    """Edit-rights policy: sellers may create/edit now; flipping the
    app_settings row to {"who": "admin"} centralizes ALL product rights in
    platform admins (single-row change, no per-store flags)."""
    who = await db.fetchval(
        "select value->>'who' from app_settings where key='product_edit_policy'"
    )
    if who == "admin" and not user.get("is_platform_admin"):
        raise HTTPException(
            403,
            "Product editing is restricted to platform administrators",
        )


@router.get("")
async def list_products(
    q: Optional[str] = None,
    low_stock: bool = False,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    store_id = user["store_id"]
    rows = await db.fetch(
        """
        select p.id, p.name, p.category, p.sku, p.barcode, p.unit,
               p.mrp, p.selling_price, p.purchase_price, p.reorder_level, p.is_active,
               p.brand, p.image_url, p.description, p.source, p.attribution,
               coalesce(i.quantity, 0) as quantity,
               case
                 when coalesce(i.quantity, 0) = 0 then 'out_of_stock'
                 when coalesce(i.quantity, 0) <= p.reorder_level then 'low_stock'
                 else 'in_stock'
               end as status,
               (p.selling_price - p.purchase_price) as margin,
               case when p.selling_price > 0
                 then round(((p.selling_price - p.purchase_price) / p.selling_price * 100)::numeric, 1)
                 else 0 end as margin_pct
        from products p
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        where p.store_id = $1 and p.is_active
          and ($2::text is null or p.name ilike '%' || $2::text || '%' or p.sku ilike '%' || $2::text || '%' or p.category ilike '%' || $2::text || '%')
        order by p.name
        """,
        store_id,
        q.strip() if q else None,
    )
    items = [dict(r) for r in rows]
    if low_stock:
        items = [r for r in items if r["status"] in ("low_stock", "out_of_stock")]
    return {"items": items, "count": len(items)}


@router.get("/barcode/{code}")
async def barcode_lookup(code: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Barcode scan foundation: resolve a scanned code to a product.

    Handles: unknown code (404 with a clear message), duplicate barcode
    (returns all matches so the merchant can pick), inactive product and
    out-of-stock product are returned with explicit status so the caller can
    decide (adding an out-of-stock item to a cart is refused client-side).
    """
    store_id = user["store_id"]
    code = code.strip()
    if not code:
        raise HTTPException(400, "Empty barcode")
    rows = await db.fetch(
        """
        select p.id, p.name, p.category, p.sku, p.barcode, p.unit,
               p.brand, p.image_url,
               p.mrp, p.selling_price, p.purchase_price, p.is_active,
               coalesce(i.quantity, 0) as quantity,
               case
                 when coalesce(i.quantity, 0) = 0 then 'out_of_stock'
                 when coalesce(i.quantity, 0) <= p.reorder_level then 'low_stock'
                 else 'in_stock'
               end as status
        from products p
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        where p.store_id = $1 and p.barcode = $2
        order by p.is_active desc, p.name
        """,
        store_id,
        code,
    )
    if not rows:
        raise HTTPException(404, {"message": f"No product with barcode {code} in this store.",
                                  "code": "UNKNOWN_BARCODE"})
    matches = [dict(r) for r in rows]
    if len(matches) > 1:
        return {"result": "MULTIPLE_MATCHES", "matches": matches}
    m = matches[0]
    if not m["is_active"]:
        return {"result": "INACTIVE_PRODUCT", "product": m}
    return {"result": "FOUND", "product": m}


@router.post("", status_code=201)
async def create_product(body: ProductIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    store_id = user["store_id"]
    await _assert_product_edit_allowed(user)
    if body.selling_price < body.purchase_price:
        raise HTTPException(400, "Selling price cannot be below purchase price")

    async with db.pool.acquire() as conn:
        async with conn.transaction():
            if body.sku:
                exists = await conn.fetchval(
                    "select 1 from products where store_id=$1 and sku=$2",
                    store_id,
                    body.sku,
                )
                if exists:
                    raise HTTPException(400, "SKU already exists for this store")

            product = await conn.fetchrow(
                """
                insert into products
                  (store_id, name, category, sku, barcode, unit, mrp, selling_price,
                   purchase_price, reorder_level, brand, description, image_url,
                   source, attribution, external_id, updated_by)
                values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                returning *
                """,
                store_id,
                body.name.strip(),
                body.category.strip() or "Other",
                body.sku.strip() if body.sku else None,
                body.barcode.strip() if body.barcode else None,
                body.unit,
                body.mrp,
                body.selling_price,
                body.purchase_price,
                body.reorder_level,
                body.brand,
                body.description,
                body.image_url,
                body.source,
                body.attribution,
                body.barcode.strip() if body.barcode else None,
                user["id"],
            )
            inv = await conn.fetchrow(
                """
                insert into inventory (store_id, product_id, quantity)
                values ($1, $2, $3)
                returning quantity
                """,
                store_id,
                product["id"],
                body.initial_stock,
            )
            if body.initial_stock > 0:
                await conn.execute(
                    """
                    insert into inventory_movements (store_id, product_id, change, quantity_after, reason)
                    values ($1,$2,$3,$4,'INITIAL_STOCK')
                    """,
                    store_id,
                    product["id"],
                    body.initial_stock,
                    body.initial_stock,
                )
                await conn.execute(
                    """
                    insert into inventory_batches (store_id, product_id, batch_no, quantity, purchase_cost, expiry_date)
                    values ($1,$2,$3,$4,$5, $6::date)
                    """,
                    store_id,
                    product["id"],
                    f"OPEN-{product['sku'] or str(product['id'])[:8]}",
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
                product["id"],
                json.dumps({"name": product["name"]}),
                f"Product created: {product['name']}",
            )

    return {
        "id": product["id"],
        "name": product["name"],
        "quantity": inv["quantity"] if inv else 0,
    }


@router.patch("/{product_id}")
async def update_product(
    product_id: str, body: ProductPatch, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    store_id = user["store_id"]
    await _assert_product_edit_allowed(user)
    current = await db.fetchrow(
        "select * from products where id=$1 and store_id=$2",
        product_id,
        store_id,
    )
    if not current:
        raise HTTPException(404, "Product not found")

    data = body.model_dump(exclude_unset=True)
    if not data:
        return {"updated": False, "id": product_id}

    fields = ", ".join(f"{k}=${i+3}" for i, k in enumerate(data.keys()))
    values = list(data.values())
    row = await db.fetchrow(
        f"""
        update products set {fields}, updated_by=${len(values) + 3}, updated_at=now()
        where id=$1 and store_id=$2
        returning *
        """,
        product_id,
        store_id,
        *values,
    )
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, previous_state, new_state, message)
        values ($1,$2,'PRODUCT_UPDATED','product',$3,$4::jsonb,$5::jsonb,$6)
        """,
        store_id,
        user["id"],
        product_id,
        json.dumps(dict(current), default=str),
        json.dumps(dict(row), default=str),
        f"Product updated: {row['name']}",
    )
    return dict(row)


@router.get("/{product_id}")
async def get_product(product_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    row = await db.fetchrow(
        """
        select p.*, coalesce(i.quantity, 0) as quantity
        from products p
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        where p.id=$1 and p.store_id=$2
        """,
        product_id,
        user["store_id"],
    )
    if not row:
        raise HTTPException(404, "Product not found")
    batches = await db.fetch(
        """
        select id, batch_no, quantity, purchase_cost, expiry_date, received_at
        from inventory_batches
        where store_id=$1 and product_id=$2 and quantity > 0
        order by expiry_date nulls last
        """,
        user["store_id"],
        product_id,
    )
    return {**dict(row), "batches": [dict(b) for b in batches]}


@router.post("/{product_id}/image", status_code=201)
async def upload_product_image(
    product_id: str,
    image: UploadFile = File(...),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Merchant product photo: persists bytes server-side (media/products/),
    sets products.image_url to the served path, records provenance, and
    enrolls the image for visual recognition (DINOv2 → pgvector) when the
    vision stack is available. Enrollment failure never blocks the upload."""
    store_id = user["store_id"]
    await _assert_product_edit_allowed(user)
    prod = await db.fetchrow(
        "select id, name from products where id=$1 and store_id=$2",
        product_id,
        store_id,
    )
    if not prod:
        raise HTTPException(404, "Product not found")

    data = await image.read()
    try:
        validate_image(data)
    except VisionProviderError as exc:
        raise HTTPException(400, {"message": str(exc), "code": exc.code})

    media_dir = Path(__file__).resolve().parents[2] / "media" / "products"
    media_dir.mkdir(parents=True, exist_ok=True)
    content_hash = hashlib.sha256(data).hexdigest()
    ext = ".jpg"  # validate_image only accepts JPEG/PNG-capable payloads; normalized on save
    filename = f"{product_id}-{content_hash[:12]}{ext}"
    (media_dir / filename).write_bytes(data)
    served_url = f"/media/products/{filename}"

    await db.execute(
        """
        update products set image_url=$3, source=case when source in ('openfoodfacts','seed') then source else 'merchant' end,
                           updated_by=$4, updated_at=now()
        where id=$1 and store_id=$2
        """,
        product_id,
        store_id,
        served_url,
        user["id"],
    )
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
        values ($1,$2,'PRODUCT_IMAGE_UPLOADED','product',$3,$4,$5)
        """,
        store_id,
        user["id"],
        product_id,
        json.dumps({"image_url": served_url}),
        f"Product image uploaded: {prod['name']}",
    )

    # visual enrollment (best-effort — never blocks the upload)
    enrolled: dict[str, Any] = {"enrolled": False, "reason": None}
    try:
        from app.routers.counter import get_vision_registry
        from app.services import embeddings_store as store
        from app.services.vision.preprocessing import decode, crop_detection  # noqa: F401

        registry = get_vision_registry()
        embedder = registry.get_ready("embedder")
        if embedder is not None and await store.pgvector_available():
            embedding = embedder.embed(data)
            await store.register_model(embedding.model, embedding.version, embedding.dimensions)
            image_id = await store.insert_product_image(
                store_id, product_id, view="front", content_hash=content_hash,
                created_by=user["id"], source="merchant",
            )
            await store.insert_embedding(store_id, product_id, embedding, image_id=image_id, created_by=user["id"])
            enrolled = {"enrolled": True, "reason": None}
        else:
            enrolled = {"enrolled": False, "reason": "vision_not_configured"}
    except Exception as exc:  # noqa: BLE001
        enrolled = {"enrolled": False, "reason": str(exc)[:120]}

    return {"image_url": served_url, **enrolled}
