from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.security import get_current_user
from app.services import commerce, payments, quickcommerce
from app.services.commerce import CommerceError
from app.services.orders import OrderError
from app.services.payments import PaymentError

router = APIRouter(tags=["commerce"])
public = APIRouter(prefix="/public/store", tags=["storefront"])


def _err(exc: CommerceError) -> HTTPException:
    detail: dict[str, Any] = {"message": str(exc), "code": exc.code}
    if exc.issues:
        detail["issues"] = exc.issues
    return HTTPException(exc.status, detail)


# ------------------------------------------------------------------
# Light rate limiting for public endpoints (in-memory, per-IP).
# Deliberately simple for the hackathon; a distributed limiter would
# replace this when the app scales beyond one API process.
# ------------------------------------------------------------------
_RATE_LIMIT = 60          # requests
_RATE_WINDOW = 60.0       # seconds
_hits: dict[str, deque[float]] = defaultdict(deque)


def _rate_limit(request: Request) -> None:
    ip = (request.client.host if request.client else "unknown")
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > _RATE_WINDOW:
        q.popleft()
    if len(q) >= _RATE_LIMIT:
        raise HTTPException(429, {"message": "Too many requests. Please slow down.", "code": "RATE_LIMITED"})
    q.append(now)


async def _store_or_404(slug: str) -> dict[str, Any]:
    store = await commerce.get_store_by_slug(slug)
    if not store:
        raise HTTPException(404, {"message": "Store not found.", "code": "STORE_NOT_FOUND"})
    return store


# ==================================================================
# Merchant: storefront configuration
# ==================================================================
class SlugIn(BaseModel):
    slug: str = Field(min_length=3, max_length=40, pattern=r"^[a-z0-9-]+$")


class CatalogSettingsIn(BaseModel):
    is_published: Optional[bool] = None
    show_stock: Optional[bool] = None
    allow_guest_checkout: Optional[bool] = None
    delivery_fee: Optional[float] = Field(default=None, ge=0)
    min_order_amount: Optional[float] = Field(default=None, ge=0)


@router.get("/commerce/catalog-settings")
async def get_catalog_settings(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    settings = await commerce.get_catalog_settings(user["store_id"])
    slug = await commerce.db.fetchval("select slug from stores where id=$1", user["store_id"])
    return {**settings, "slug": slug}


@router.post("/commerce/slug")
async def set_slug(body: SlugIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    taken = await commerce.db.fetchval(
        "select 1 from stores where slug=$1 and id<>$2", body.slug, user["store_id"]
    )
    if taken:
        raise HTTPException(409, {"message": "That store link is already taken.", "code": "SLUG_TAKEN"})
    await commerce.db.execute("update stores set slug=$2 where id=$1", user["store_id"], body.slug)
    return {"slug": body.slug}


@router.patch("/commerce/catalog-settings")
async def patch_catalog_settings(body: CatalogSettingsIn,
                                 user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await commerce.update_catalog_settings(
            user["store_id"], **body.model_dump(exclude_none=True)
        )
    except CommerceError as exc:
        raise _err(exc)


# ==================================================================
# Merchant: fulfillment + timeline + analytics
# ==================================================================
class ActionIn(BaseModel):
    action: str
    reason: Optional[str] = None


@router.post("/commerce/orders/{order_id}/action")
async def order_action(order_id: str, body: ActionIn,
                       user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        order = await commerce.fulfillment_action(
            user["store_id"], user["id"], order_id, body.action, body.reason
        )
        # keep total in sync if completion created the sale
        return {"order": order}
    except (CommerceError, OrderError, PaymentError) as exc:
        status = getattr(exc, "status", 400)
        raise HTTPException(status, {"message": str(exc), "code": getattr(exc, "code", "ERROR")})


@router.get("/commerce/orders/{order_id}/timeline")
async def timeline(order_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await commerce.order_timeline(user["store_id"], order_id)
    except CommerceError as exc:
        raise _err(exc)


@router.get("/commerce/channel-analytics")
async def channel_analytics(days: int = 30,
                            user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return await commerce.channel_analytics(user["store_id"], days)


@router.get("/commerce/product-performance")
async def product_performance(days: int = 30,
                              user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": await commerce.product_channel_performance(user["store_id"], days)}


# ==================================================================
# Merchant: refunds (honest, provider-aware)
# ==================================================================
class RefundIn(BaseModel):
    payment_id: str
    amount: float = Field(gt=0)
    reason: Optional[str] = None


@router.post("/commerce/refunds")
async def create_refund(body: RefundIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await payments.create_refund(
            user["store_id"], body.payment_id, body.amount, body.reason, user["id"]
        )
    except PaymentError as exc:
        raise HTTPException(exc.status, {"message": str(exc), "code": exc.code})


@router.post("/commerce/refunds/{refund_id}/complete")
async def complete_refund(refund_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await payments.complete_refund(user["store_id"], refund_id, user["id"])
    except PaymentError as exc:
        raise HTTPException(exc.status, {"message": str(exc), "code": exc.code})


# ==================================================================
# Merchant: quick commerce registry + syncs + demo ingestion
# ==================================================================
class ConfigureProviderIn(BaseModel):
    provider: str
    mode: str
    connected_account: Optional[str] = None


@router.get("/qc/providers")
async def qc_providers(user: dict = Depends(get_current_user)) -> dict[str, Any]:
    return {"items": await quickcommerce.list_providers(user["store_id"])}


@router.post("/qc/providers/configure")
async def qc_configure(body: ConfigureProviderIn,
                       user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await quickcommerce.configure_provider(
            user["store_id"], body.provider.upper(), body.mode, body.connected_account
        )
    except CommerceError as exc:
        raise _err(exc)


@router.post("/qc/{provider}/sync")
async def qc_sync(provider: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await quickcommerce.sync_catalog(user["store_id"], provider.upper(), user["id"])
    except CommerceError as exc:
        raise _err(exc)


@router.post("/qc/{provider}/inventory-sync")
async def qc_inventory(provider: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await quickcommerce.sync_inventory(user["store_id"], provider.upper())
    except CommerceError as exc:
        raise _err(exc)


@router.get("/qc/{provider}/listings")
async def qc_listings(provider: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    rows = await commerce.db.fetch(
        """
        select l.*, p.name as product_name from qc_listings l
        join qc_providers qp on qp.id = l.provider_id
        join products p on p.id = l.product_id
        where l.store_id=$1 and qp.provider=$2 order by p.name limit 200
        """,
        user["store_id"], provider.upper(),
    )
    return {"items": [dict(r) for r in rows]}


class DemoOrderIn(BaseModel):
    items: list[dict[str, Any]] = Field(min_length=1)
    external_order_id: Optional[str] = None


@router.post("/qc/{provider}/demo-order")
async def qc_demo_order(provider: str, body: DemoOrderIn,
                        user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Simulate an inbound provider order (DEMO mode only, clearly labeled)."""
    try:
        return await commerce.simulate_qc_order(
            user["store_id"], provider.upper(), body.items, body.external_order_id
        )
    except CommerceError as exc:
        raise _err(exc)


@router.post("/qc/{provider}/webhook")
async def qc_webhook(provider: str, request: Request) -> dict[str, Any]:
    """Provider webhook: HMAC-SHA256 signature over the raw body in the
    X-KiranaSaathi-Signature header. Duplicate external events are no-ops."""
    store_id = request.query_params.get("store_id")
    if not store_id:
        raise HTTPException(400, {"message": "store_id query parameter is required.",
                                  "code": "STORE_REQUIRED"})
    raw = await request.body()
    signature = request.headers.get("X-KiranaSaathi-Signature")
    try:
        return await quickcommerce.ingest_webhook(
            store_id, provider.upper(), signature, raw
        )
    except CommerceError as exc:
        raise _err(exc)


# ==================================================================
# PUBLIC STOREFRONT (no auth; store-scoped by slug, order by public token)
# ==================================================================
@public.get("/{slug}")
async def storefront(slug: str, request: Request, category: Optional[str] = None,
                     q: Optional[str] = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    _rate_limit(request)
    store = await _store_or_404(slug)
    try:
        return await commerce.public_catalog(store, category, q, limit, offset)
    except CommerceError as exc:
        raise _err(exc)


@public.get("/{slug}/products/{product_id}")
async def storefront_product(slug: str, product_id: str, request: Request) -> dict[str, Any]:
    _rate_limit(request)
    store = await _store_or_404(slug)
    try:
        return await commerce.public_product(store, product_id)
    except CommerceError as exc:
        raise _err(exc)


class CustomerCheckoutIn(BaseModel):
    items: list[dict[str, Any]] = Field(min_length=1)
    customer_name: str = Field(min_length=1, max_length=80)
    customer_phone: str = Field(min_length=10, max_length=15)
    delivery_method: str = "PICKUP"
    delivery_address: Optional[str] = None
    payment_method: str = "upi"
    discount: float = Field(default=0, ge=0)
    campaign_id: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=80)


@public.post("/{slug}/checkout")
async def storefront_checkout(slug: str, body: CustomerCheckoutIn, request: Request) -> dict[str, Any]:
    _rate_limit(request)
    store = await _store_or_404(slug)
    try:
        result = await commerce.customer_checkout(
            store, body.items, body.discount, body.delivery_method.upper(),
            body.delivery_address, body.customer_name, body.customer_phone,
            payment_method=body.payment_method, campaign_id=body.campaign_id,
            idempotency_key=body.idempotency_key,
        )
        order = result["order"]
        return {
            "order_id": str(order["id"]),
            "public_token": str(order["public_token"]),
            "state": order["state"],
            "total": float(order["total"] or 0),
            "duplicate": result["duplicate"],
        }
    except CommerceError as exc:
        raise _err(exc)


class PayIn(BaseModel):
    method: str = "upi"
    idempotency_key: Optional[str] = Field(default=None, max_length=80)


@public.post("/{slug}/orders/{public_token}/pay")
async def storefront_pay(slug: str, public_token: str, body: PayIn,
                         request: Request) -> dict[str, Any]:
    """Customer initiates payment. The payment is created PENDING and only a
    verified confirmation moves it to PAID — the customer can never declare
    success. Manual/UPI-at-counter flow: the merchant verifies receipt."""
    _rate_limit(request)
    store = await _store_or_404(slug)
    order_id = await commerce.db.fetchval(
        "select id from orders where store_id=$1 and public_token=$2",
        store["id"], public_token,
    )
    if not order_id:
        raise HTTPException(404, {"message": "Order not found.", "code": "ORDER_NOT_FOUND"})
    if body.method not in ("upi", "cash", "card"):
        raise HTTPException(400, {"message": "Unsupported payment method.", "code": "INVALID_METHOD"})
    try:
        payment = await commerce.create_customer_payment(
            store["id"], str(order_id), body.method, body.idempotency_key
        )
        return {"payment": payment, "note":
                "Payment is pending verification. The merchant confirms receipt before your order is prepared."}
    except (CommerceError, PaymentError) as exc:
        status = getattr(exc, "status", 400)
        raise HTTPException(status, {"message": str(exc), "code": getattr(exc, "code", "ERROR")})


@public.get("/{slug}/orders/{public_token}")
async def customer_order_status(slug: str, public_token: str, request: Request) -> dict[str, Any]:
    _rate_limit(request)
    store = await _store_or_404(slug)
    row = await commerce.db.fetchrow(
        """
        select o.id, o.state, o.total, o.created_at, o.delivery_method, o.delivery_status,
               o.channel, o.public_token,
               (select json_agg(json_build_object('name', p.name, 'quantity', oi.quantity,
                                                  'unit_price', oi.unit_price,
                                                  'line_total', oi.line_total) order by p.name)
                from order_items oi join products p on p.id = oi.product_id
                where oi.order_id = o.id) as items
        from orders o where o.store_id=$1 and o.public_token=$2
        """,
        store["id"], public_token,
    )
    if not row:
        raise HTTPException(404, {"message": "Order not found.", "code": "ORDER_NOT_FOUND"})
    d = dict(row)
    events = await commerce.db.fetch(
        """
        select event_type, created_at from order_events
        where store_id=$1 and order_id=$2 and actor in ('merchant','system','customer')
        order by created_at asc
        """,
        store["id"], d["id"],
    )
    d["events"] = [{"event_type": e["event_type"], "created_at": e["created_at"].isoformat()}
                   for e in events]
    d["id"] = str(d["id"])
    d["public_token"] = str(d["public_token"])
    if isinstance(d.get("items"), str):
        import json as _json
        try:
            d["items"] = _json.loads(d["items"])
        except ValueError:
            d["items"] = []
    return d


class CustomerCancelIn(BaseModel):
    reason: Optional[str] = None


@public.post("/{slug}/orders/{public_token}/cancel")
async def customer_cancel(slug: str, public_token: str, body: CustomerCancelIn,
                          request: Request) -> dict[str, Any]:
    """Customer cancellation policy: unpaid orders cancel freely; paid orders
    require the merchant refund workflow (refused here with guidance)."""
    _rate_limit(request)
    store = await _store_or_404(slug)
    order_id = await commerce.db.fetchval(
        "select id from orders where store_id=$1 and public_token=$2",
        store["id"], public_token,
    )
    if not order_id:
        raise HTTPException(404, {"message": "Order not found.", "code": "ORDER_NOT_FOUND"})
    try:
        order = await commerce.fulfillment_action(
            store["id"], "", str(order_id), "CANCEL", body.reason or "Cancelled by customer"
        )
        return {"order_id": str(order["id"]), "state": order["state"]}
    except CommerceError as exc:
        raise _err(exc)
