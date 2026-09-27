from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.security import get_current_user
from app.services import payments as pay_svc
from app.services.payments import PaymentError

router = APIRouter(prefix="/payments", tags=["payments"])


def _err(exc: PaymentError) -> HTTPException:
    return HTTPException(exc.status, {"message": str(exc), "code": exc.code})


class PaymentIn(BaseModel):
    order_id: str
    amount: float = Field(gt=0)
    method: str = Field(default="cash")
    provider: Optional[str] = None
    idempotency_key: Optional[str] = None


class ConfirmIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=200)


class FailIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


class SplitIn(BaseModel):
    order_id: str
    mode: str = Field(default="EQUAL")
    total: Optional[float] = Field(default=None, ge=0)
    splits: list[dict[str, Any]] = Field(min_length=1)


@router.post("", status_code=201)
async def create_payment(body: PaymentIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        payment = await pay_svc.create_payment(
            store_id=user["store_id"],
            order_id=body.order_id,
            amount=round(body.amount, 2),
            method=body.method,
            idempotency_key=body.idempotency_key,
            provider_name=body.provider,
        )
    except PaymentError as exc:
        raise _err(exc)
    return {"payment": payment}


@router.get("/order/{order_id}")
async def order_payments(order_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    items = await pay_svc.list_payments(user["store_id"], order_id)
    split = await pay_svc.get_split_group_by_order(user["store_id"], order_id)
    paid = sum(float(p["amount"]) for p in items if p["state"] == "PAID")
    return {"items": items, "paid_total": round(paid, 2), "split_group": split}


@router.post("/{payment_id}/confirm")
async def confirm_payment(payment_id: str, body: ConfirmIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        res = await pay_svc.confirm_payment(user["store_id"], payment_id, user["id"], body.note)
    except PaymentError as exc:
        raise _err(exc)
    return res


@router.post("/{payment_id}/fail")
async def fail_payment(payment_id: str, body: FailIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        res = await pay_svc.fail_payment(user["store_id"], payment_id, body.reason)
    except PaymentError as exc:
        raise _err(exc)
    return res


@router.post("/{payment_id}/cancel")
async def cancel_payment(payment_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        res = await pay_svc.cancel_payment(user["store_id"], payment_id)
    except PaymentError as exc:
        raise _err(exc)
    return res


# ---------------- SPLITS ----------------

@router.post("/splits")
async def create_split_group(body: SplitIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        group = await pay_svc.create_split_group(
            user["store_id"], body.order_id, body.mode, body.splits, body.total
        )
    except PaymentError as exc:
        raise _err(exc)
    return {"group": group}


@router.get("/splits/{group_id}")
async def get_split_group(group_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    group = await pay_svc.get_split_group(user["store_id"], group_id)
    if not group:
        raise HTTPException(404, "Split group not found")
    return {"group": group}


class SplitPayIn(BaseModel):
    provider: Optional[str] = None


@router.post("/splits/{split_id}/pay")
async def pay_split(split_id: str, body: SplitPayIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        res = await pay_svc.pay_split(user["store_id"], split_id, user["id"], body.provider)
    except PaymentError as exc:
        raise _err(exc)
    out = dict(res)
    if out.get("split") is not None and not isinstance(out["split"], dict):
        out["split"] = dict(out["split"])
    return out


# ---------------- WEBHOOK FOUNDATION ----------------

@router.post("/webhook/{provider}")
async def provider_webhook(provider: str, request: Request) -> dict[str, Any]:
    """Provider webhook entry point.

    Security: signature verification when the provider is configured with a
    secret (env WEBHOOK_SECRET_<PROVIDER>). The 'manual' provider has no
    webhooks. Unverified payloads are rejected — payment state only changes on
    verified events. Idempotency is enforced by the unique provider_event_id.
    """
    import os

    raw = await request.body()
    secret = os.environ.get(f"WEBHOOK_SECRET_{provider.upper()}", "")
    signature = request.headers.get("x-webhook-signature", "")
    if secret:
        if not signature or not pay_svc.verify_webhook_signature(secret, raw, signature):
            raise HTTPException(401, "Invalid webhook signature")
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "Invalid JSON payload")
    store_id = payload.get("store_id")
    event_id = payload.get("event_id") or payload.get("id")
    if not store_id or not event_id:
        raise HTTPException(400, "Webhook payload requires store_id and event_id")
    result = await pay_svc.process_webhook(store_id, provider, str(event_id), payload)
    return result
