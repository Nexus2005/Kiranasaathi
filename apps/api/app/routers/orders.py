from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.security import get_current_user
from app.services import orders as orders_svc
from app.services.orders import OrderError

router = APIRouter(prefix="/orders", tags=["orders"])


class CartLineIn(BaseModel):
    product_id: str
    quantity: int = Field(gt=0, le=10000)
    unit_price: Optional[float] = Field(default=None, ge=0)
    list_price_at_cart: Optional[float] = Field(default=None, ge=0)


class OrderIn(BaseModel):
    items: list[CartLineIn] = Field(min_length=1)
    customer_id: Optional[str] = None
    discount: float = Field(default=0, ge=0)
    payment_method: str = Field(default="cash")


class StateIn(BaseModel):
    state: str


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


def _err(exc: OrderError) -> HTTPException:
    status = 404 if exc.code == "NOT_FOUND" else 400
    detail: Any = {"message": str(exc), "code": exc.code}
    if exc.issues:
        detail["issues"] = exc.issues
    return HTTPException(status, detail)


@router.get("")
async def list_orders(
    state: Optional[str] = None, limit: int = 50, user: dict = Depends(get_current_user)
) -> dict[str, Any]:
    items = await orders_svc.list_orders(user["store_id"], state, limit)
    return {"items": items}


@router.get("/{order_id}")
async def get_order(order_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    order = await orders_svc.get_order(user["store_id"], order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    reval = await orders_svc.revalidate(user["store_id"], order_id)
    return {
        "order": order,
        "revalidation": {k: reval[k] for k in ("issues", "valid", "subtotal_current", "total_current")},
    }


@router.post("", status_code=201)
async def create_order(body: OrderIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        order = await orders_svc.create_order(
            store_id=user["store_id"],
            user_id=user["id"],
            cart=[l.model_dump(exclude_none=True) for l in body.items],
            discount=body.discount,
            payment_method=body.payment_method,
            customer_id=body.customer_id,
        )
    except OrderError as exc:
        raise _err(exc)
    return {"order": order, "order_id": order["id"]}


@router.get("/{order_id}/revalidate")
async def revalidate_order(order_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        reval = await orders_svc.revalidate(user["store_id"], order_id)
    except OrderError as exc:
        raise _err(exc)
    return {k: reval[k] for k in ("issues", "valid", "subtotal_current", "total_current")}


class CheckoutIn(BaseModel):
    confirm: bool = False


@router.post("/{order_id}/checkout")
async def checkout(order_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    """Complete checkout: revalidate + create the sale via the atomic RPC.

    Normally invoked by the payments layer once funds are confirmed; exposed
    for cash-flow where the merchant completes payment at the counter at the
    same moment (cash). Payment must exist and be PAID unless method=cash.
    """
    store_id = user["store_id"]
    try:
        reval = await orders_svc.revalidate(store_id, order_id)
        if not reval["valid"]:
            return {"status": "REVIEW_REQUIRED", "issues": reval["issues"],
                    "totals": {"subtotal": reval["subtotal_current"], "total": reval["total_current"]}}
        order = reval["order"]
        if order["payment_method"] != "cash":
            paid = await orders_svc.db.fetchval(
                "select coalesce(sum(amount),0) from payments where store_id=$1 and order_id=$2 and state='PAID'",
                store_id, order_id,
            )
            if float(paid or 0) < reval["total_current"]:
                return {"status": "AWAITING_PAYMENT",
                        "paid": float(paid or 0), "total": reval["total_current"]}
        result = await orders_svc.complete_order(store_id, user["id"], order_id)
    except OrderError as exc:
        raise _err(exc)
    return {
        "status": "COMPLETED" if not result.get("already_completed") else "ALREADY_COMPLETED",
        "sale_id": result["sale_id"],
        "order": result["order"],
    }


@router.post("/{order_id}/cancel")
async def cancel_order(order_id: str, body: CancelIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        order = await orders_svc.cancel_order(user["store_id"], user["id"], order_id, body.reason)
    except OrderError as exc:
        raise _err(exc)
    return {"order": order}


@router.post("/{order_id}/state")
async def set_state(order_id: str, body: StateIn, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    try:
        order = await orders_svc.set_state(user["store_id"], order_id, body.state)
    except OrderError as exc:
        raise _err(exc)
    return {"order": order}


@router.get("/{order_id}/events")
async def order_events(order_id: str, user: dict = Depends(get_current_user)) -> dict[str, Any]:
    rows = await orders_svc.db.fetch(
        "select event_type, actor, payload, created_at from order_events "
        "where store_id=$1 and order_id=$2 order by created_at",
        user["store_id"], order_id,
    )
    return {"items": [dict(r) for r in rows]}
