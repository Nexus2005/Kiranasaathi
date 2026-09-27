from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.security import get_current_user
from app.database import db
from app.services import retail
from app.services.retail import RetailError

router = APIRouter(prefix="/retail", tags=["retail-operations"])


def _err(exc: RetailError) -> HTTPException:
    return HTTPException(exc.status, {"message": str(exc), "code": exc.code})


class ScanIn(BaseModel):
    code: str = Field(min_length=1, max_length=120)


@router.post("/scan")
async def scan(payload: ScanIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Universal scan resolution: mapped barcodes -> legacy barcode -> GS1 GTIN.

    Returns the product (each-level data) plus structured GS1 fields when the
    code carries them (batch/expiry never invented — only what the code says).
    """
    try:
        return await retail.lookup_barcode(user["store_id"], payload.code)
    except RetailError as exc:
        raise _err(exc)


# ---------------- barcodes ----------------

class BarcodeIn(BaseModel):
    product_id: str
    barcode: str = Field(min_length=1, max_length=80)
    barcode_type: str = "EAN"
    packaging_level: str = "EACH"
    quantity_represented: int = Field(default=1, ge=1)
    is_primary: bool = False


@router.post("/barcodes")
async def add_barcode(body: BarcodeIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await retail.add_barcode(
            user["store_id"], body.product_id, body.barcode.strip(), body.barcode_type,
            body.packaging_level, body.quantity_represented, body.is_primary,
        )
    except RetailError as exc:
        raise _err(exc)


@router.get("/products/{product_id}/barcodes")
async def list_barcodes(product_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": await retail.list_barcodes(user["store_id"], product_id)}


# ---------------- packaging / UOM ----------------

class PackagingIn(BaseModel):
    product_id: str
    level: str
    label: str = ""
    conversion_factor: int = Field(gt=0)


@router.post("/packaging")
async def set_packaging(body: PackagingIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await retail.set_packaging(user["store_id"], body.product_id, body.level.upper(),
                                          body.label, body.conversion_factor)
    except RetailError as exc:
        raise _err(exc)


@router.get("/products/{product_id}/packaging")
async def get_packaging(product_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": await retail.get_packaging(user["store_id"], product_id)}


# ---------------- receiving ----------------

class ReceiveLineIn(BaseModel):
    product_id: str
    level: str = "EACH"
    qty: int = Field(ge=0)
    unit_cost: float = Field(default=0, ge=0)
    batch_no: Optional[str] = None
    manufacturing_date: Optional[str] = None
    expiry_date: Optional[str] = None
    shelf_life_value: Optional[int] = Field(default=None, ge=0)
    shelf_life_unit: Optional[str] = None
    damaged: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)


class ReceiveIn(BaseModel):
    purchase_order_id: str
    lines: list[ReceiveLineIn] = Field(min_length=1)
    idempotency_key: Optional[str] = None


@router.post("/receive")
async def receive(body: ReceiveIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await retail.receive_shipment(
            user["store_id"], user["id"], body.purchase_order_id,
            [l.model_dump(exclude_none=True) for l in body.lines],
            idempotency_key=body.idempotency_key,
        )
    except RetailError as exc:
        raise _err(exc)


@router.get("/purchase-orders/{po_id}/receiving")
async def receiving_status(po_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await retail.po_receiving_status(user["store_id"], po_id)
    except RetailError as exc:
        raise _err(exc)


# ---------------- expiry assistance ----------------

class ExpiryAssistIn(BaseModel):
    expiry_date: Optional[str] = None
    manufacturing_date: Optional[str] = None
    shelf_life_value: Optional[int] = Field(default=None, ge=0)
    shelf_life_unit: Optional[str] = None


@router.post("/expiry/assist")
async def expiry_assist(body: ExpiryAssistIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Deterministic expiry calculation for form assistance (MFD + shelf life)."""
    try:
        return retail.calculate_expiry_client(
            body.expiry_date, body.manufacturing_date,
            body.shelf_life_value, body.shelf_life_unit,
        )
    except ValueError as exc:
        raise HTTPException(400, {"message": f"Invalid date: {exc}", "code": "INVALID_DATE"})


# ---------------- admin: product catalog health (iteration 16) ----------------

@router.get("/admin/product-catalog")
async def admin_product_catalog(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Catalog administration lists for THIS store: recognition readiness,
    missing images / barcodes, duplicates. Server-side store scoping (§46).
    Recognition status: NOT_ENROLLED | ENROLLED | VERIFIED (global-verified
    identity) | NEEDS_REVIEW (corrections on record)."""
    store_id = user["store_id"]
    rows = await db.fetch(
        """
        select p.id, p.name, p.category, p.barcode, p.selling_price, p.is_active,
               coalesce(i.quantity, 0) as quantity,
               (select count(*) from product_images pi where pi.product_id = p.id) as image_count,
               (select count(*) from product_visual_embeddings pe where pe.product_id = p.id) as embedding_count,
               exists(select 1 from product_global_links gl where gl.store_id = p.store_id and gl.product_id = p.id) as global_linked,
               (select count(*) from recognition_events re
                 where re.product_id = p.id and re.feedback_label = 'STRONG_NEGATIVE') as correction_count
        from products p
        left join inventory i on i.product_id = p.id and i.store_id = p.store_id
        where p.store_id = $1
        order by p.created_at desc
        """,
        store_id,
    )
    items = []
    for r in rows:
        if int(r["correction_count"] or 0) > 0:
            status = "NEEDS_REVIEW"
        elif int(r["embedding_count"] or 0) > 0 and r["global_linked"]:
            status = "VERIFIED"
        elif int(r["embedding_count"] or 0) > 0:
            status = "ENROLLED"
        else:
            status = "NOT_ENROLLED"
        items.append({
            **dict(r),
            "recognition_status": status,
            "missing_image": int(r["image_count"] or 0) == 0,
            "missing_barcode": not r["barcode"],
        })
    return {
        "items": items,
        "summary": {
            "total": len(items),
            "not_enrolled": sum(1 for i in items if i["recognition_status"] == "NOT_ENROLLED"),
            "enrolled": sum(1 for i in items if i["recognition_status"] == "ENROLLED"),
            "verified": sum(1 for i in items if i["recognition_status"] == "VERIFIED"),
            "needs_review": sum(1 for i in items if i["recognition_status"] == "NEEDS_REVIEW"),
            "missing_images": sum(1 for i in items if i["missing_image"]),
            "missing_barcodes": sum(1 for i in items if i["missing_barcode"]),
        },
    }


# ---------------- adjustments + cycle counts ----------------

class AdjustIn(BaseModel):
    product_id: str
    change: int
    reason: str
    note: Optional[str] = None
    counted_quantity: Optional[int] = Field(default=None, ge=0)
    cycle_count_id: Optional[str] = None
    idempotency_key: Optional[str] = None


@router.post("/adjustments")
async def adjust(body: AdjustIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await retail.adjust_stock(
            user["store_id"], user["id"], body.product_id, body.change, body.reason,
            note=body.note, counted=body.counted_quantity,
            cycle_count_id=body.cycle_count_id, idempotency_key=body.idempotency_key,
        )
    except RetailError as exc:
        raise _err(exc)


class CountCreateIn(BaseModel):
    product_ids: list[str] = Field(min_length=1)
    scope_note: Optional[str] = None


@router.post("/cycle-counts")
async def create_count(body: CountCreateIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await retail.create_cycle_count(user["store_id"], user["id"],
                                               body.product_ids, body.scope_note)
    except RetailError as exc:
        raise _err(exc)


class CountLineIn(BaseModel):
    product_id: str
    counted_quantity: int = Field(ge=0)
    reason: Optional[str] = None


@router.post("/cycle-counts/{count_id}/lines")
async def submit_line(count_id: str, body: CountLineIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await retail.submit_count_line(user["store_id"], user["id"], count_id,
                                              body.product_id, body.counted_quantity, body.reason)
    except RetailError as exc:
        raise _err(exc)


@router.post("/cycle-counts/{count_id}/complete")
async def complete_count(count_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await retail.complete_cycle_count(user["store_id"], user["id"], count_id)
    except RetailError as exc:
        raise _err(exc)


@router.get("/cycle-counts/{count_id}")
async def get_count(count_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    cc = await retail.db.fetchrow(
        "select * from cycle_counts where id=$1 and store_id=$2", count_id, user["store_id"]
    )
    if not cc:
        raise HTTPException(404, "Cycle count not found")
    lines = await retail.db.fetch(
        """
        select ccl.*, p.name as product_name
        from cycle_count_lines ccl join products p on p.id = ccl.product_id
        where ccl.cycle_count_id=$1
        """,
        count_id,
    )
    return {"count": dict(cc), "lines": [dict(r) for r in lines]}


# ---------------- returns ----------------

class ReturnIn(BaseModel):
    sale_id: str
    product_id: str
    quantity: int = Field(gt=0)
    classification: str
    reason: Optional[str] = None
    idempotency_key: Optional[str] = None


@router.post("/returns")
async def create_return(body: ReturnIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await retail.create_return(
            user["store_id"], user["id"], body.sale_id, body.product_id,
            body.quantity, body.classification, body.reason,
            idempotency_key=body.idempotency_key,
        )
    except RetailError as exc:
        raise _err(exc)


@router.get("/sales/{sale_id}/returns")
async def sale_returns(sale_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    rows = await retail.db.fetch(
        """
        select sr.*, p.name as product_name
        from sale_returns sr join products p on p.id = sr.product_id
        where sr.store_id=$1 and sr.sale_id=$2
        """,
        user["store_id"], sale_id,
    )
    return {"items": [dict(r) for r in rows]}


# ---------------- product batches view ----------------

@router.get("/products/{product_id}/batches")
async def product_batches(product_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await retail.product_batches(user["store_id"], product_id)
    except RetailError as exc:
        raise _err(exc)
