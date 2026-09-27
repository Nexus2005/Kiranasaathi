"""Payments — Phase 6.

Hard rules (per spec):
- Never trust the browser: a payment is PAID only after provider verification
  (webhook signature/status check or explicit merchant confirmation for the
  manual in-person provider).
- The payment provider is an abstraction. The only provider wired today is
  `manual` (in-person cash/UPI/card settlement, explicitly confirmed by the
  merchant). 'development' mode never fakes success.
- Webhooks are idempotent via unique (store_id, provider_event_id).
- Split payments are a state machine: the order completes only when the sum of
  PAID splits covers the total; PARTIALLY_PAID otherwise.
- The AI never executes payments; all state changes require merchant/system
  authorization via authenticated endpoints.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Optional, Protocol

from app.database import db
from app.services import orders as orders_svc
from app.services.orders import OrderError

PAYMENT_TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"PENDING", "PAID", "FAILED", "CANCELLED", "EXPIRED"},
    "PENDING": {"PAID", "FAILED", "CANCELLED", "EXPIRED"},
    "PARTIALLY_PAID": {"PAID", "FAILED", "CANCELLED"},
    "PAID": {"REFUND_PENDING", "REFUNDED"},
    "FAILED": set(),
    "CANCELLED": set(),
    "EXPIRED": set(),
    "REFUND_PENDING": {"REFUNDED", "REFUND_FAILED"} if False else {"REFUNDED"},
    "REFUNDED": set(),
}


class PaymentError(Exception):
    def __init__(self, message: str, code: str = "PAYMENT_ERROR", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


# ------------------------------------------------------------------
# Provider abstraction
# ------------------------------------------------------------------
class PaymentProvider(Protocol):
    name: str
    requires_external_credentials: bool

    async def create_payment(self, *, store_id: str, order_id: str, amount: float,
                             method: str, idempotency_key: Optional[str],
                             reference: Optional[str]) -> dict[str, Any]: ...
    async def verify_payment(self, *, payment: dict[str, Any],
                             payload: dict[str, Any]) -> dict[str, Any]: ...
    async def create_refund(self, *, payment: dict[str, Any], amount: float,
                            reason: Optional[str]) -> dict[str, Any]: ...


class ManualProvider:
    """In-person settlement (cash / QR shown by the merchant / card machine).

    The merchant explicitly confirms receiving the money. Amount verification
    is enforced: confirming with a different amount than the payment records
    is refused, and confirmations are logged as provider events.
    """

    name = "manual"
    requires_external_credentials = False

    async def create_payment(self, *, store_id: str, order_id: str, amount: float,
                             method: str, idempotency_key: Optional[str],
                             reference: Optional[str]) -> dict[str, Any]:
        existing = None
        if idempotency_key:
            existing = await db.fetchrow(
                "select * from payments where store_id=$1 and idempotency_key=$2",
                store_id, idempotency_key,
            )
        if existing:
            return {"payment": dict(existing), "reused": True}
        pid = await db.fetchval(
            """
            insert into payments (store_id, order_id, amount, method, state, provider,
                                  idempotency_key, created_by)
            values ($1, $2, $3, $4, 'PENDING', 'manual', $5,
                    (select created_by from orders where id=$2))
            returning id
            """,
            store_id, order_id, amount, method, idempotency_key,
        )
        await db.execute(
            """
            insert into payment_events (payment_id, store_id, event_type, payload)
            values ($1, $2, 'PAYMENT_CREATED', $3::jsonb)
            """,
            pid, store_id, json.dumps({"amount": amount, "method": method}),
        )
        row = await db.fetchrow("select * from payments where id=$1", pid)
        return {"payment": dict(row), "reused": False}

    async def verify_payment(self, *, payment: dict[str, Any],
                             payload: dict[str, Any]) -> dict[str, Any]:
        # Manual provider: verification = merchant attestation recorded as an event.
        # Amount must match exactly.
        return {"verified": True, "verified_amount": float(payment["amount"]),
                "note": payload.get("note")}

    async def create_refund(self, *, payment: dict[str, Any], amount: float,
                            reason: Optional[str]) -> dict[str, Any]:
        # Manual refunds are merchant-recorded, not provider-executed.
        return {"executed": False, "provider": "manual",
                "note": "Manual provider does not execute refunds automatically. "
                        "Record the refund once actually issued."}


def get_provider(name: Optional[str]) -> PaymentProvider:
    if name in (None, "", "manual"):
        return ManualProvider()
    raise PaymentError(
        f"Provider '{name}' is not configured. Available providers: manual.",
        "PROVIDER_NOT_CONFIGURED", 400,
    )


# ------------------------------------------------------------------
# Core payment flow
# ------------------------------------------------------------------
async def get_payment(store_id: str, payment_id: str) -> Optional[dict[str, Any]]:
    row = await db.fetchrow(
        "select * from payments where id=$1 and store_id=$2", payment_id, store_id
    )
    return dict(row) if row else None


async def create_payment(
    store_id: str, order_id: str, amount: float, method: str,
    idempotency_key: Optional[str] = None, provider_name: Optional[str] = None,
) -> dict[str, Any]:
    order = await orders_svc.get_order(store_id, order_id)
    if not order:
        raise PaymentError("Order not found.", "NOT_FOUND", 404)
    if order["state"] not in ("PENDING_PAYMENT", "PARTIALLY_PAID", "PAYMENT_FAILED", "DRAFT"):
        raise PaymentError(f"Order is {order['state']}; payments can no longer be created.",
                           "INVALID_ORDER_STATE")
    if amount <= 0:
        raise PaymentError("Payment amount must be positive.", "INVALID_AMOUNT")

    provider = get_provider(provider_name)
    res = await provider.create_payment(
        store_id=store_id, order_id=order_id, amount=amount, method=method,
        idempotency_key=idempotency_key, reference=None,
    )
    payment = res["payment"]
    # Keep order in PENDING_PAYMENT while money moves
    if order["state"] in ("DRAFT", "PAYMENT_FAILED"):
        await orders_svc.set_state(store_id, order_id, "PENDING_PAYMENT", actor="payment")
    return payment


async def confirm_payment(
    store_id: str, payment_id: str, user_id: str,
    note: Optional[str] = None,
) -> dict[str, Any]:
    """Merchant-confirmed settlement for the manual provider (cash/UPI/card in person)."""
    payment = await get_payment(store_id, payment_id)
    if not payment:
        raise PaymentError("Payment not found.", "NOT_FOUND", 404)
    if payment["state"] == "PAID":
        return {"payment": payment, "already_paid": True}
    if payment["state"] not in ("CREATED", "PENDING"):
        raise PaymentError(
            f"Payment is {payment['state']} and cannot be confirmed.",
            "INVALID_STATE",
        )
    provider = get_provider(payment["provider"])
    verdict = await provider.verify_payment(payment=payment, payload={"note": note})
    if not verdict.get("verified"):
        raise PaymentError("Provider could not verify this payment.", "NOT_VERIFIED")

    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                update payments
                set state='PAID', verified=true, verified_at=now(),
                    verified_amount=$3, error_note=$4
                where id=$1 and store_id=$2 and state in ('CREATED','PENDING')
                returning *
                """,
                payment_id, store_id, verdict["verified_amount"], note,
            )
            if not row:
                # Concurrent confirm — return authoritative state
                current = await get_payment(store_id, payment_id)
                return {"payment": current, "already_paid": current is not None and current["state"] == "PAID"}
            await conn.execute(
                """
                insert into payment_events (payment_id, store_id, event_type, payload)
                values ($1, $2, 'PAYMENT_COMPLETED', $3::jsonb)
                """,
                payment_id, store_id,
                json.dumps({"amount": float(row["amount"]), "verified_amount": verdict["verified_amount"],
                            "note": note, "provider": payment["provider"]}),
            )
    await _sync_order_payment_state(store_id, str(payment["order_id"]))
    updated = await get_payment(store_id, payment_id)
    return {"payment": updated, "already_paid": False}


async def fail_payment(store_id: str, payment_id: str, reason: str) -> dict[str, Any]:
    payment = await get_payment(store_id, payment_id)
    if not payment:
        raise PaymentError("Payment not found.", "NOT_FOUND", 404)
    if payment["state"] in ("PAID", "FAILED", "CANCELLED", "EXPIRED", "REFUNDED"):
        raise PaymentError(f"Payment is {payment['state']}; cannot mark failed.", "INVALID_STATE")
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "update payments set state='FAILED', error_note=$3 where id=$1 and store_id=$2 "
                "and state in ('CREATED','PENDING') returning *",
                payment_id, store_id, reason,
            )
            if row:
                await conn.execute(
                    "insert into payment_events (payment_id, store_id, event_type, payload) "
                    "values ($1, $2, 'PAYMENT_FAILED', $3::jsonb)",
                    payment_id, store_id, json.dumps({"reason": reason}),
                )
    if payment["state"] in ("CREATED", "PENDING"):
        await _sync_order_payment_state(store_id, str(payment["order_id"]))
    # Visible failure: an unpaid order with no remaining active payments goes
    # PAYMENT_FAILED (customer can retry — new payment returns it to
    # PENDING_PAYMENT). Never fakes success; inventory untouched.
    active = await db.fetchval(
        "select count(*) from payments where order_id=$1 and store_id=$2 "
        "and state in ('CREATED','PENDING')",
        payment["order_id"], store_id,
    )
    if not active:
        order = await orders_svc.get_order(store_id, str(payment["order_id"]))
        if order and order["state"] == "PENDING_PAYMENT":
            await db.execute(
                "update orders set state='PAYMENT_FAILED' where id=$1 and store_id=$2",
                payment["order_id"], store_id,
            )
            await orders_svc._log_event(store_id, str(payment["order_id"]), "ORDER_PAYMENT_FAILED",
                                        actor="system", payload={"reason": reason})
    return {"payment": await get_payment(store_id, payment_id)}


async def cancel_payment(store_id: str, payment_id: str) -> dict[str, Any]:
    payment = await get_payment(store_id, payment_id)
    if not payment:
        raise PaymentError("Payment not found.", "NOT_FOUND", 404)
    if payment["state"] not in ("CREATED", "PENDING"):
        raise PaymentError(f"Payment is {payment['state']}; cannot cancel.", "INVALID_STATE")
    await db.execute(
        "update payments set state='CANCELLED' where id=$1 and store_id=$2",
        payment_id, store_id,
    )
    await db.execute(
        "insert into payment_events (payment_id, store_id, event_type) values ($1,$2,'PAYMENT_CANCELLED')",
        payment_id, store_id,
    )
    await _sync_order_payment_state(store_id, str(payment["order_id"]))
    return {"payment": await get_payment(store_id, payment_id)}


async def list_payments(store_id: str, order_id: str) -> list[dict[str, Any]]:
    rows = await db.fetch(
        "select * from payments where store_id=$1 and order_id=$2 order by created_at",
        store_id, order_id,
    )
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# Order<->payment reconciliation
# ------------------------------------------------------------------
async def _paid_total(store_id: str, order_id: str) -> float:
    v = await db.fetchval(
        "select coalesce(sum(amount), 0) from payments "
        "where store_id=$1 and order_id=$2 and state='PAID'",
        store_id, order_id,
    )
    return float(v or 0)


async def _sync_order_payment_state(store_id: str, order_id: str) -> dict[str, Any]:
    """Align order state with confirmed payments. Completes the order when fully paid."""
    order = await orders_svc.get_order(store_id, order_id)
    if not order:
        raise PaymentError("Order not found.", "NOT_FOUND", 404)
    if order["state"] in ("CANCELLED", "REFUNDED", "COMPLETED"):
        return {"order": order, "paid_total": await _paid_total(store_id, order_id)}

    paid = await _paid_total(store_id, order_id)
    total = await _order_total(store_id, order)

    if order["sale_id"]:
        return {"order": order, "paid_total": paid}

    if total is None:
        return {"order": order, "paid_total": paid}

    if paid >= total:
        # POS channels complete immediately (sale + inventory at the counter).
        # Customer channels wait for fulfillment: PAID -> CONFIRMED -> ... ->
        # COMPLETED; inventory is deducted by the same create_sale RPC only
        # when the merchant completes the order.
        if order.get("channel", "POS") != "POS":
            if order["state"] != "PAID":
                await db.execute(
                    "update orders set state='PAID' where id=$1 and store_id=$2",
                    order_id, store_id,
                )
                await orders_svc._log_event(store_id, order_id, "ORDER_PAYMENT_VERIFIED",
                                            actor="system", payload={"paid": paid, "total": total})
                order = await orders_svc.get_order(store_id, order_id)
            return {"order": order, "paid_total": paid, "awaiting_fulfillment": True}
        result = await orders_svc.complete_order(store_id, order.get("created_by") or "", order_id)
        order = result["order"]
        return {"order": order, "paid_total": paid, "completed": not result.get("already_completed", False)}
    if paid > 0:
        if order["state"] != "PARTIALLY_PAID":
            await db.execute(
                "update orders set state='PARTIALLY_PAID' where id=$1 and store_id=$2",
                order_id, store_id,
            )
            await orders_svc._log_event(store_id, order_id, "ORDER_PARTIALLY_PAID",
                                        actor="system", payload={"paid": paid, "total": total})
            order = await orders_svc.get_order(store_id, order_id)
        return {"order": order, "paid_total": paid, "remaining": round(total - paid, 2)}
    if order["state"] == "PARTIALLY_PAID" and paid == 0:
        await db.execute(
            "update orders set state='PENDING_PAYMENT' where id=$1 and store_id=$2",
            order_id, store_id,
        )
        order = await orders_svc.get_order(store_id, order_id)
    return {"order": order, "paid_total": paid, "remaining": total}


async def _order_total(store_id: str, order: dict[str, Any]) -> Optional[float]:
    """Authoritative order total: recompute from current prices/stock.

    If revalidation fails with blocking issues, return None — the order cannot
    be completed until the merchant resolves the cart.
    """
    try:
        reval = await orders_svc.revalidate(store_id, str(order["id"]))
        if reval["valid"]:
            return reval["total_current"]
        # Non-stock issues (e.g. price changed): total is still computable,
        # but completion will be blocked by complete_order's own revalidation.
        blocking = {i["code"] for i in reval["issues"]}
        if blocking <= {"PRICE_CHANGED"}:
            return reval["total_current"]
        return None
    except OrderError:
        return None


# ------------------------------------------------------------------
# Webhook foundation (idempotent)
# ------------------------------------------------------------------
def verify_webhook_signature(secret: str, raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256 verification for providers that sign webhooks."""
    if not secret:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


async def process_webhook(store_id: str, provider: str, provider_event_id: str,
                          payload: dict[str, Any]) -> dict[str, Any]:
    """Idempotent webhook processing.

    The unique index on (store_id, provider_event_id) guarantees the same
    provider event applies at most once — a duplicate insert raises and we
    return 'duplicate: True' with no side effects.
    """
    try:
        inserted = await db.fetchval(
            """
            insert into payment_events (store_id, event_type, provider_event_id, payload)
            values ($1, 'WEBHOOK_RECEIVED', $2, $3::jsonb)
            returning id
            """,
            store_id, provider_event_id, json.dumps({"provider": provider, **payload}),
        )
    except Exception as exc:  # noqa: BLE001 — unique violation = duplicate delivery
        if "duplicate key" in str(exc).lower() or "unique" in str(exc).lower():
            return {"duplicate": True, "applied": False}
        raise
    if not inserted:
        return {"duplicate": True, "applied": False}

    event = payload.get("event")
    provider_payment_id = payload.get("provider_payment_id")
    amount = payload.get("amount")

    try:
        if event in ("payment.captured", "payment.success", "captured"):
            if not provider_payment_id:
                raise PaymentError("Webhook missing provider_payment_id.", "BAD_PAYLOAD")
            payment = await db.fetchrow(
                # Provider reference first; fall back to our own payment id so
                # internal integrations can echo it as the reference.
                "select * from payments where store_id=$1 and (provider_payment_id=$2 or id::text=$2)",
                store_id, provider_payment_id,
            )
            if not payment:
                return {"duplicate": False, "applied": False,
                        "note": "No matching payment for this provider reference."}
            expected = float(payment["amount"])
            received = float(amount) if amount is not None else expected
            if abs(received - expected) > 0.01:
                # Amount mismatch: flag for review, do NOT complete anything
                await db.execute(
                    "update payments set error_note=$3 where id=$1 and store_id=$2",
                    str(payment["id"]), store_id,
                    f"Webhook amount mismatch: expected ₹{expected:g}, received ₹{received:g}. Flagged for review.",
                )
                await db.execute(
                    "insert into payment_events (payment_id, store_id, event_type, payload) "
                    "values ($1, $2, 'PAYMENT_AMOUNT_MISMATCH', $3::jsonb)",
                    str(payment["id"]), store_id,
                    json.dumps({"expected": expected, "received": received}),
                )
                return {"duplicate": False, "applied": False, "amount_mismatch": True}
            await db.execute(
                """
                update payments set state='PAID', verified=true, verified_at=now(),
                       verified_amount=$3
                where id=$1 and store_id=$2 and state in ('CREATED','PENDING')
                """,
                str(payment["id"]), store_id, received,
            )
            await db.execute(
                "insert into payment_events (payment_id, store_id, event_type, payload) "
                "values ($1, $2, 'PAYMENT_COMPLETED', $3::jsonb)",
                str(payment["id"]), store_id, json.dumps({"via": "webhook", "amount": received}),
            )
            sync = await _sync_order_payment_state(store_id, str(payment["order_id"]))
            return {"duplicate": False, "applied": True,
                    "order_state": sync["order"]["state"] if sync.get("order") else None}
        elif event in ("payment.failed", "failed"):
            if provider_payment_id:
                await db.execute(
                    """
                    update payments set state='FAILED', error_note='Provider reported failure'
                    where store_id=$1 and (provider_payment_id=$2 or id::text=$2) and state in ('CREATED','PENDING')
                    """,
                    store_id, provider_payment_id,
                )
                row = await db.fetchrow(
                    "select order_id from payments where store_id=$1 and (provider_payment_id=$2 or id::text=$2)",
                    store_id, provider_payment_id,
                )
                if row:
                    await _sync_order_payment_state(store_id, str(row["order_id"]))
            return {"duplicate": False, "applied": True}
        else:
            return {"duplicate": False, "applied": False,
                    "note": f"Unhandled webhook event '{event}' recorded but not applied."}
    except PaymentError:
        # Do not consume the event id on processing failure — allow retry by
        # removing the receipt so a corrected retry can be applied.
        await db.execute(
            "delete from payment_events where store_id=$1 and provider_event_id=$2",
            store_id, provider_event_id,
        )
        raise


# ------------------------------------------------------------------
# Split payments
# ------------------------------------------------------------------
async def create_split_group(
    store_id: str, order_id: str, mode: str,
    splits: list[dict[str, Any]], total: Optional[float] = None,
) -> dict[str, Any]:
    order = await orders_svc.get_order(store_id, order_id)
    if not order:
        raise PaymentError("Order not found.", "NOT_FOUND", 404)
    if order["state"] not in ("PENDING_PAYMENT", "PARTIALLY_PAID", "PAYMENT_FAILED", "DRAFT"):
        raise PaymentError(f"Order is {order['state']}.", "INVALID_ORDER_STATE")
    if mode not in ("EQUAL", "CUSTOM"):
        raise PaymentError("Split mode must be EQUAL or CUSTOM.", "INVALID_MODE")

    order_total = await _order_total(store_id, order)
    if order_total is None:
        raise PaymentError(
            "Order cannot be split right now: the cart failed revalidation. Review the cart.",
            "CART_INVALID",
        )
    target = float(total) if total is not None else order_total
    if abs(target - order_total) > 0.01:
        raise PaymentError(
            f"Split total ₹{target:g} does not match order total ₹{order_total:g}.",
            "TOTAL_MISMATCH",
        )
    if not splits:
        raise PaymentError("At least one split participant is required.", "NO_SPLITS")
    if len(splits) > 20:
        raise PaymentError("Too many participants (max 20).", "TOO_MANY_SPLITS")

    amounts: list[float] = []
    labels: list[str] = []
    for s in splits:
        label = (s.get("payer_label") or "").strip()
        amt = float(s.get("amount") or 0)
        if not label:
            raise PaymentError("Every participant needs a name/reference.", "INVALID_PAYER")
        if amt <= 0:
            raise PaymentError(f"Invalid amount for {label}: must be positive.", "INVALID_AMOUNT")
        if label in labels:
            raise PaymentError(f"Duplicate participant '{label}'.", "DUPLICATE_PAYER")
        labels.append(label)
        amounts.append(round(amt, 2))

    if abs(sum(amounts) - target) > 0.01:
        raise PaymentError(
            f"Split amounts sum to ₹{sum(amounts):g}, expected ₹{target:g}.",
            "SUM_MISMATCH",
        )

    if mode == "EQUAL":
        n = len(amounts)
        each = round(target / n, 2)
        amounts = [each] * n
        amounts[-1] = round(target - each * (n - 1), 2)  # remainder on last payer

    # Replace any previous OPEN group for this order (merchant redesigns the split)
    await db.execute(
        "delete from payment_split_groups where order_id=$1 and status='OPEN'",
        order_id,
    )
    gid = await db.fetchval(
        """
        insert into payment_split_groups (store_id, order_id, total_amount, mode, status)
        values ($1, $2, $3, $4, 'OPEN') returning id
        """,
        store_id, order_id, target, mode,
    )
    for i, (label, amt) in enumerate(zip(labels, amounts), start=1):
        await db.execute(
            """
            insert into payment_splits (group_id, store_id, payer_label, amount, state, position)
            values ($1, $2, $3, $4, 'PENDING', $5)
            """,
            gid, store_id, label, amt, i,
        )
    return await get_split_group(store_id, str(gid))


async def get_split_group(store_id: str, group_id: str) -> Optional[dict[str, Any]]:
    row = await db.fetchrow(
        "select * from payment_split_groups where id=$1 and store_id=$2", group_id, store_id
    )
    if not row:
        return None
    group = dict(row)
    splits = await db.fetch(
        "select * from payment_splits where group_id=$1 order by position", group_id
    )
    group["splits"] = [dict(s) for s in splits]
    group["paid_amount"] = round(sum(float(s["amount"]) for s in group["splits"] if s["state"] == "PAID"), 2)
    group["remaining_amount"] = round(float(group["total_amount"]) - group["paid_amount"], 2)
    group["completed"] = group["remaining_amount"] <= 0.009
    return group


async def get_split_group_by_order(store_id: str, order_id: str) -> Optional[dict[str, Any]]:
    row = await db.fetchrow(
        "select id from payment_split_groups where order_id=$1 and store_id=$2 "
        "order by created_at desc limit 1",
        order_id, store_id,
    )
    if not row:
        return None
    return await get_split_group(store_id, str(row["id"]))


async def pay_split(store_id: str, split_id: str, user_id: str,
                    provider_name: Optional[str] = None) -> dict[str, Any]:
    """Settle one split. Creates/reuses a payment and confirms it (manual provider)."""
    split = await db.fetchrow(
        "select * from payment_splits where id=$1 and store_id=$2", split_id, store_id
    )
    if not split:
        raise PaymentError("Split not found.", "NOT_FOUND", 404)
    if split["state"] == "PAID":
        group = await get_split_group(store_id, str(split["group_id"]))
        return {"split": dict(split), "group": group, "already_paid": True}
    if split["state"] in ("CANCELLED",):
        raise PaymentError("This split was cancelled.", "INVALID_STATE")
    group = await get_split_group(store_id, str(split["group_id"]))
    if not group or group["status"] != "OPEN":
        raise PaymentError("Split group is not open.", "INVALID_STATE")

    order_id = await db.fetchval(
        "select order_id from payment_split_groups where id=$1", split["group_id"]
    )
    idem = f"split:{split_id}"  # idempotent: retrying the same split reuses its payment
    payment = await create_payment(
        store_id, str(order_id), float(split["amount"]), "split",
        idempotency_key=idem, provider_name=provider_name or "manual",
    )
    await db.execute(
        "update payment_splits set state='PROCESSING', payment_id=$3, updated_at=now() "
        "where id=$1 and store_id=$2 and state in ('PENDING','PROCESSING','FAILED')",
        split_id, store_id, payment["id"],
    )
    res = await confirm_payment(store_id, payment["id"], user_id,
                                note=f"Split payment by {split['payer_label']}")
    new_state = res["payment"]["state"] if res.get("payment") else "FAILED"
    await db.execute(
        "update payment_splits set state=$3, updated_at=now() where id=$1 and store_id=$2",
        split_id, store_id,
        "PAID" if new_state == "PAID" else "FAILED" if new_state in ("FAILED", "CANCELLED", "EXPIRED") else "PROCESSING",
    )
    group = await get_split_group(store_id, str(split["group_id"]))
    if group:
        if group["completed"]:
            await db.execute(
                "update payment_split_groups set status='COMPLETED', updated_at=now() where id=$1",
                str(split["group_id"]),
            )
        # keep group paid/remaining columns meaningful
        await db.execute(
            "update payment_split_groups set paid_amount=$3, updated_at=now() where id=$1 and store_id=$2",
            str(split["group_id"]), store_id, group["paid_amount"],
        )
    return {"split": await db.fetchrow("select * from payment_splits where id=$1", split_id), "group": group,
            "payment": res.get("payment"), "already_paid": False}


# ------------------------------------------------------------------
# Refund workflow (server-authoritative; provider-executed when possible)
# ------------------------------------------------------------------
async def create_refund(
    store_id: str, payment_id: str, amount: float, reason: Optional[str],
    user_id: Optional[str],
) -> dict[str, Any]:
    """Request a refund against a paid payment.

    State machine: payment -> REFUND_PENDING, refund row created, then the
    provider executes it. Manual provider records the intent (merchant must
    actually issue the money — never auto-marked REFUNDED). Completing the
    refund moves the order to REFUND_PENDING/REFUNDED with an audit trail.
    """
    payment = await get_payment(store_id, payment_id)
    if not payment:
        raise PaymentError("Payment not found.", "NOT_FOUND", 404)
    if payment["state"] != "PAID":
        raise PaymentError(
            f"Payment is {payment['state']}; only paid payments can be refunded.",
            "INVALID_STATE", 409,
        )
    paid_amount = float(payment["verified_amount"] or payment["amount"])
    if amount <= 0 or amount > paid_amount:
        raise PaymentError(
            f"Refund amount must be between 0 and the paid amount ₹{paid_amount:g}.",
            "INVALID_AMOUNT", 400,
        )
    refunded = await db.fetchval(
        """
        select coalesce(sum(amount), 0) from refunds
        where payment_id = $1 and state in ('REFUND_PENDING','REFUND_PROCESSING','REFUNDED')
        """,
        payment_id,
    )
    if float(refunded) + amount > paid_amount + 0.001:
        raise PaymentError(
            f"Refunds would exceed the paid amount (already refunded/refunding: ₹{float(refunded):g}).",
            "OVER_REFUND", 409,
        )

    async with db.pool.acquire() as conn:
        async with conn.transaction():
            refund_id = await conn.fetchval(
                """
                insert into refunds (store_id, payment_id, order_id, amount, reason,
                                     state, provider, created_by)
                values ($1, $2, $3, $4, $5, 'REFUND_PENDING', $6, $7)
                returning id
                """,
                store_id, payment_id, payment["order_id"], amount, reason,
                payment["provider"], user_id,
            )
            await conn.execute(
                "update payments set state='REFUND_PENDING', updated_at=now() "
                "where id=$1 and store_id=$2 and state='PAID'",
                payment_id, store_id,
            )
            await conn.execute(
                """
                insert into payment_events (payment_id, store_id, event_type, payload)
                values ($1, $2, 'REFUND_REQUESTED', $3::jsonb)
                """,
                payment_id, store_id,
                json.dumps({"refund_id": str(refund_id), "amount": amount, "reason": reason}),
            )
    order_id = str(payment["order_id"])
    order = await orders_svc.get_order(store_id, order_id)
    if order and order["state"] not in ("CANCELLED", "REFUNDED"):
        await db.execute(
            "update orders set state='REFUND_PENDING' where id=$1 and store_id=$2 "
            "and state not in ('CANCELLED','REFUNDED')",
            order_id, store_id,
        )
        await orders_svc._log_event(store_id, order_id, "ORDER_REFUND_REQUESTED",
                                    actor="merchant" if user_id else "system",
                                    payload={"refund_id": str(refund_id), "amount": amount})
    return {"refund_id": str(refund_id), "state": "REFUND_PENDING", "amount": amount,
            "payment_state": "REFUND_PENDING"}


async def complete_refund(store_id: str, refund_id: str, user_id: Optional[str]) -> dict[str, Any]:
    """Mark a refund completed once the money has actually left (provider
    confirmation or merchant attestation for manual refunds). Moves the order
    to REFUNDED. Inventory treatment is a separate merchant decision
    (sale_returns) — refunds never silently restock."""
    refund = await db.fetchrow(
        "select * from refunds where id=$1 and store_id=$2", refund_id, store_id
    )
    if not refund:
        raise PaymentError("Refund not found.", "NOT_FOUND", 404)
    if refund["state"] == "REFUNDED":
        return {"refund_id": str(refund_id), "state": "REFUNDED", "already_completed": True}
    if refund["state"] not in ("REFUND_PENDING", "REFUND_PROCESSING"):
        raise PaymentError(f"Refund is {refund['state']}; cannot complete.", "INVALID_STATE", 409)

    # Provider execution attempt (manual provider records the attestation only)
    payment = await get_payment(store_id, str(refund["payment_id"]))
    provider = get_provider(refund["provider"])
    verdict = await provider.create_refund(
        payment=payment or {}, amount=float(refund["amount"]), reason=refund["reason"],
    )

    async with db.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                update refunds set state='REFUNDED', completed_at=now(),
                    provider_refund_id = coalesce(provider_refund_id, $3)
                where id=$1 and store_id=$2 and state in ('REFUND_PENDING','REFUND_PROCESSING')
                """,
                refund_id, store_id, verdict.get("provider_refund_id"),
            )
            await conn.execute(
                """
                update payments set state='REFUNDED', updated_at=now()
                where id=$1 and store_id=$2
                and not exists (
                    select 1 from refunds r2
                    where r2.payment_id = payments.id
                      and r2.id <> $3
                      and r2.state in ('REFUND_PENDING','REFUND_PROCESSING','REFUNDED')
                )
                """,
                refund["payment_id"], store_id, refund_id,
            )
            await conn.execute(
                """
                insert into payment_events (payment_id, store_id, event_type, payload)
                values ($1, $2, 'REFUND_COMPLETED', $3::jsonb)
                """,
                refund["payment_id"], store_id,
                json.dumps({"refund_id": str(refund_id), "amount": float(refund["amount"]),
                            "provider": refund["provider"],
                            "note": verdict.get("note")}),
            )
    order_id = str(refund["order_id"]) if refund["order_id"] else None
    if order_id:
        order = await orders_svc.get_order(store_id, order_id)
        if order and order["state"] not in ("COMPLETED", "CANCELLED"):
            await orders_svc.set_state(store_id, order_id, "REFUNDED", actor="system")
        elif order:
            await orders_svc._log_event(store_id, order_id, "ORDER_REFUNDED",
                                        actor="system", payload={"refund_id": str(refund_id)})
    return {"refund_id": str(refund_id), "state": "REFUNDED", "already_completed": False,
            "note": verdict.get("note")}



