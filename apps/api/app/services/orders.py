"""Order lifecycle — Phase 6.

Rules:
- The order is the pre-payment container. `sales` remains the source of truth
  for completed transactions; the sale is created ONLY through the existing
  atomic create_sale RPC (stock check, price floor, FEFO deduction, alerts).
- The frontend NEVER supplies authoritative prices/totals. Cart unit prices are
  optional *negotiated* prices which create_sale enforces against the store's
  pricing policy (min_acceptable_price).
- Before any state change the cart is revalidated against current DB state:
  product exists + active, sellable stock (inventory minus expired batches),
  price currency (list price changes are reported, not silently accepted),
  negotiated prices still within policy, discount <= subtotal.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.database import db

VALID_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"PENDING_PAYMENT", "CANCELLED"},
    "PENDING_PAYMENT": {"PARTIALLY_PAID", "PAID", "PAYMENT_FAILED", "CANCELLED"},
    "PARTIALLY_PAID": {"PARTIALLY_PAID", "PAID", "PAYMENT_FAILED", "CANCELLED"},
    # POS: PAID -> READY/COMPLETED directly. Customer channels: PAID -> CONFIRMED
    # -> PROCESSING -> READY -> OUT_FOR_DELIVERY -> DELIVERED -> COMPLETED.
    "PAID": {"CONFIRMED", "PROCESSING", "READY", "COMPLETED", "REFUND_PENDING"},
    "CONFIRMED": {"PROCESSING", "READY", "REFUND_PENDING"},
    "PROCESSING": {"READY", "COMPLETED", "REFUND_PENDING"},
    "READY": {"OUT_FOR_DELIVERY", "COMPLETED", "REFUND_PENDING"},
    "OUT_FOR_DELIVERY": {"DELIVERED", "REFUND_PENDING"},
    "DELIVERED": {"COMPLETED", "REFUND_PENDING"},
    "COMPLETED": {"REFUND_PENDING"},
    "PAYMENT_FAILED": {"PENDING_PAYMENT", "CANCELLED"},
    "REFUND_PENDING": {"REFUNDED"},
    "REFUNDED": set(),
    "CANCELLED": set(),
}


class OrderError(Exception):
    def __init__(self, message: str, code: str = "ORDER_ERROR", issues: Optional[list[dict]] = None):
        super().__init__(message)
        self.code = code
        self.issues = issues or []


async def _log_event(
    store_id: str, order_id: str, event_type: str, actor: str = "merchant",
    payload: Optional[dict] = None,
) -> None:
    await db.execute(
        """
        insert into order_events (store_id, order_id, event_type, actor, payload)
        values ($1, $2, $3, $4, $5::jsonb)
        """,
        store_id,
        order_id,
        event_type,
        actor,
        json.dumps(payload or {}),
    )


async def get_order(store_id: str, order_id: str) -> Optional[dict[str, Any]]:
    row = await db.fetchrow(
        """
        select o.*, c.name as customer_name, c.phone as customer_phone,
               s.id as sale_number, s.total as sale_total,
               (select json_agg(json_build_object(
                    'product_id', oi.product_id,
                    'name', p.name,
                    'quantity', oi.quantity,
                    'unit_price', oi.unit_price,
                    'line_total', oi.line_total
                 ) order by p.name)
                from order_items oi
                join products p on p.id = oi.product_id
                where oi.order_id = o.id) as items
        from orders o
        left join customers c on c.id = o.customer_id
        left join sales s on s.id = o.sale_id
        where o.id = $1 and o.store_id = $2
        """,
        order_id,
        store_id,
    )
    if not row:
        return None
    d = dict(row)
    if isinstance(d.get("items"), str):
        try:
            d["items"] = json.loads(d["items"])
        except (ValueError, TypeError):
            d["items"] = []
    if d.get("cart") is None or isinstance(d.get("cart"), str):
        try:
            d["cart"] = json.loads(d["cart"] or "[]")
        except (ValueError, TypeError):
            d["cart"] = []
    return d


async def list_orders(store_id: str, state: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        select o.id, o.state, o.payment_method, o.cart_discount, o.total, o.sale_id,
               o.created_at, o.channel, c.name as customer_name,
               (select count(*) from order_items oi where oi.order_id = o.id) as item_lines,
               (select coalesce(sum(oi.quantity), 0) from order_items oi where oi.order_id = o.id) as units
        from orders o
        left join customers c on c.id = o.customer_id
        where o.store_id = $1
          and ($2::text is null or o.state = $2::text)
        order by o.created_at desc
        limit $3
        """,
        store_id,
        state,
        max(1, min(limit, 200)),
    )
    return [dict(r) for r in rows]


async def _cart_issues(
    store_id: str, cart: list[dict[str, Any]], discount: float
) -> tuple[list[dict[str, Any]], float]:
    """Deterministic revalidation. Returns (issues, server_subtotal).

    Server subtotal uses the CURRENT list price unless the line carries a
    negotiated unit_price, which must satisfy the pricing policy floor.
    """
    issues: list[dict[str, Any]] = []
    subtotal = 0.0
    for line in cart:
        pid = line.get("product_id")
        qty = int(line.get("quantity") or 0)
        offered = line.get("unit_price")
        row = await db.fetchrow(
            """
            select p.id, p.name, p.selling_price, p.purchase_price, p.is_active,
                   sellable_stock($1, p.id) as sellable
            from products p
            where p.id = $2 and p.store_id = $1
            """,
            store_id,
            pid,
        )
        if not row or not row["is_active"]:
            issues.append({"code": "PRODUCT_UNAVAILABLE", "product_id": pid,
                           "message": "Product no longer exists or is inactive."})
            continue
        if qty <= 0:
            issues.append({"code": "INVALID_QUANTITY", "product_id": pid,
                           "message": f"Invalid quantity for {row['name']}."})
            continue
        if row["sellable"] < qty:
            issues.append({
                "code": "INSUFFICIENT_STOCK", "product_id": pid, "product": row["name"],
                "available": row["sellable"], "requested": qty,
                "message": f"Only {row['sellable']} unit(s) of {row['name']} currently available (requested {qty}).",
            })
            continue
        price_now = float(row["selling_price"])
        line_price = price_now
        if offered is not None:
            floor_row = await db.fetchrow(
                "select min_acceptable_price($1, $2) as floor",
                store_id, pid,
            )
            floor = float(floor_row["floor"]) if floor_row and floor_row["floor"] is not None else 0.0
            offered_f = float(offered)
            if offered_f > price_now:
                issues.append({"code": "PRICE_ABOVE_LIST", "product_id": pid, "product": row["name"],
                               "list_price": price_now, "offered": offered_f,
                               "message": f"Negotiated price ₹{offered_f:g} exceeds list price ₹{price_now:g} for {row['name']}."})
                continue
            if offered_f < floor:
                issues.append({"code": "PRICE_BELOW_FLOOR", "product_id": pid, "product": row["name"],
                               "floor": floor, "offered": offered_f,
                               "message": f"Negotiated price ₹{offered_f:g} is below the minimum acceptable ₹{floor:g} for {row['name']}."})
                continue
            line_price = offered_f
        else:
            # Stale list-price detection happens against the snapshot stored on the order
            expected = line.get("list_price_at_cart", line.get("_list_price_at_cart"))
            if expected is not None and abs(float(expected) - price_now) > 0.001:
                issues.append({
                    "code": "PRICE_CHANGED", "product_id": pid, "product": row["name"],
                    "old_price": float(expected), "current_price": price_now,
                    "message": f"Price of {row['name']} changed from ₹{float(expected):g} to ₹{price_now:g}. Review the updated cart.",
                })
        subtotal += line_price * qty

    if subtotal - discount < 0:
        issues.append({"code": "DISCOUNT_EXCEEDS_SUBTOTAL",
                       "message": f"Discount ₹{discount:g} exceeds the order subtotal ₹{subtotal:g}."})
    return issues, round(subtotal, 2)


async def create_order(
    store_id: str,
    user_id: str,
    cart: list[dict[str, Any]],
    discount: float,
    payment_method: str,
    customer_id: Optional[str] = None,
) -> dict[str, Any]:
    if not cart:
        raise OrderError("Cart is empty. Add at least one product.", "EMPTY_CART")
    if discount < 0:
        raise OrderError("Discount cannot be negative.", "INVALID_DISCOUNT")
    if payment_method not in ("cash", "upi", "card", "split", "credit"):
        raise OrderError("Invalid payment method.", "INVALID_PAYMENT_METHOD")

    if customer_id:
        ok = await db.fetchval(
            "select 1 from customers where id=$1 and store_id=$2", customer_id, store_id
        )
        if not ok:
            raise OrderError("Invalid customer.", "INVALID_CUSTOMER")

    clean_cart: list[dict[str, Any]] = []
    for line in cart:
        clean_cart.append({
            "product_id": line.get("product_id"),
            "quantity": int(line.get("quantity") or 0),
            "unit_price": line.get("unit_price"),
            "_list_price_at_cart": line.get("_list_price_at_cart"),
        })

    issues, subtotal = await _cart_issues(store_id, clean_cart, discount)
    fatal = [i for i in issues if i["code"] in
             ("PRODUCT_UNAVAILABLE", "INVALID_QUANTITY", "INSUFFICIENT_STOCK",
              "PRICE_BELOW_FLOOR", "PRICE_ABOVE_LIST", "DISCOUNT_EXCEEDS_SUBTOTAL")]
    if fatal:
        raise OrderError(fatal[0]["message"], "CART_INVALID", issues)
    # Projected total at creation time (revalidated authoritatively at checkout;
    # create_sale recomputes the final total inside the atomic RPC).

    # Persist snapshot price for stale detection at checkout
    snapshot: list[dict[str, Any]] = []
    for line in clean_cart:
        row = await db.fetchrow(
            "select selling_price from products where id=$1 and store_id=$2",
            line["product_id"], store_id,
        )
        snapshot.append({**{k: v for k, v in line.items() if k != "_list_price_at_cart"},
                         "list_price_at_cart": float(row["selling_price"])})

    order_id = await db.fetchval(
        """
        insert into orders (store_id, customer_id, state, cart, cart_discount,
                            payment_method, created_by, expires_at, total)
        values ($1, $2, 'PENDING_PAYMENT', $3::jsonb, $4, $5, $6,
                now() + interval '24 hours', $7)
        returning id
        """,
        store_id,
        customer_id,
        json.dumps(snapshot),
        discount,
        payment_method,
        user_id,
        round(max(0.0, subtotal - discount), 2),
    )
    for line in snapshot:
        await db.execute(
            """
            insert into order_items (order_id, product_id, quantity, unit_price)
            values ($1, $2, $3, $4)
            """,
            order_id,
            line["product_id"],
            line["quantity"],
            # order_items keeps the charged price (negotiated or list at creation);
            # authoritative totals are always recomputed at completion.
            float(line.get("unit_price") or line["list_price_at_cart"]),
        )
    await _log_event(store_id, str(order_id), "ORDER_CREATED", payload={
        "items": len(snapshot), "discount": discount, "payment_method": payment_method,
    })
    order = await get_order(store_id, str(order_id))
    assert order is not None
    return order


async def revalidate(store_id: str, order_id: str) -> dict[str, Any]:
    """Recompute the order against current DB state. Returns issues + fresh totals."""
    order = await get_order(store_id, order_id)
    if not order:
        raise OrderError("Order not found.", "NOT_FOUND")
    cart = order["cart"] or []
    issues, subtotal = await _cart_issues(store_id, cart, float(order["cart_discount"] or 0))
    state_ok = order["state"] in ("DRAFT", "PENDING_PAYMENT", "PARTIALLY_PAID", "PAYMENT_FAILED")
    if not state_ok:
        issues.append({"code": "ORDER_STATE", "state": order["state"],
                       "message": f"Order is {order['state']} and can no longer be checked out."})
    if order.get("expires_at") and order["expires_at"] < datetime.now(timezone.utc) and state_ok:
        issues.append({"code": "ORDER_EXPIRED",
                       "message": "This order has expired. Create a new order to continue."})
    return {
        "order": order,
        "issues": issues,
        "valid": len(issues) == 0,
        "subtotal_current": subtotal,
        "discount": float(order["cart_discount"] or 0),
        "total_current": round(max(0.0, subtotal - float(order["cart_discount"] or 0)), 2),
    }


async def cancel_order(store_id: str, user_id: str, order_id: str, reason: str) -> dict[str, Any]:
    order = await get_order(store_id, order_id)
    if not order:
        raise OrderError("Order not found.", "NOT_FOUND")
    state = order["state"]
    if state not in ("DRAFT", "PENDING_PAYMENT", "PAYMENT_FAILED"):
        raise OrderError(
            f"Order is {state}. Paid orders must follow the refund workflow.",
            "INVALID_TRANSITION",
        )
    await db.execute("update orders set state='CANCELLED' where id=$1 and store_id=$2",
                     order_id, store_id)
    await db.execute(
        "update payments set state='CANCELLED' where order_id=$1 and state in ('CREATED','PENDING')",
        order_id,
    )
    await db.execute(
        "update payment_split_groups set status='CANCELLED' where order_id=$1 and status='OPEN'",
        order_id,
    )
    await _log_event(store_id, order_id, "ORDER_CANCELLED", payload={"reason": reason})
    updated = await get_order(store_id, order_id)
    assert updated is not None
    return updated


async def set_state(store_id: str, order_id: str, new_state: str, actor: str = "system") -> dict[str, Any]:
    order = await get_order(store_id, order_id)
    if not order:
        raise OrderError("Order not found.", "NOT_FOUND")
    cur = order["state"]
    if new_state not in VALID_TRANSITIONS.get(cur, set()):
        raise OrderError(f"Invalid transition {cur} -> {new_state}.", "INVALID_TRANSITION")
    await db.execute("update orders set state=$3 where id=$1 and store_id=$2",
                     order_id, store_id, new_state)
    await _log_event(store_id, order_id, f"ORDER_{new_state}", actor=actor, payload={"from": cur})
    updated = await get_order(store_id, order_id)
    assert updated is not None
    return updated


async def complete_order(store_id: str, user_id: str, order_id: str) -> dict[str, Any]:
    """Convert a fully-paid order into a real sale via the existing create_sale RPC.

    Called by the payments service once confirmed paid amount >= order total.
    Idempotent: if the order already has a sale_id, returns it unchanged.
    Stock/price are revalidated immediately before execution; create_sale
    re-checks stock atomically inside the RPC, so concurrent counters cannot
    oversell.
    """
    order = await get_order(store_id, order_id)
    if not order:
        raise OrderError("Order not found.", "NOT_FOUND")
    if order["sale_id"]:
        return {"already_completed": True, "sale_id": order["sale_id"], "order": order}

    reval = await revalidate(store_id, order_id)
    blocking = [i for i in reval["issues"] if i["code"] in
                ("PRODUCT_UNAVAILABLE", "INVALID_QUANTITY", "INSUFFICIENT_STOCK",
                 "PRICE_BELOW_FLOOR", "PRICE_ABOVE_LIST", "DISCOUNT_EXCEEDS_SUBTOTAL",
                 "ORDER_STATE", "ORDER_EXPIRED")]
    # Customer-channel fulfillment states are VALID completion points — the
    # sale (and its atomic stock deduction) happens when the merchant marks
    # the fulfilled order complete, not at payment time.
    fulfillment_states = {"PAID", "CONFIRMED", "PROCESSING", "READY",
                          "OUT_FOR_DELIVERY", "DELIVERED"}
    if order["state"] in fulfillment_states:
        blocking = [i for i in blocking if i["code"] != "ORDER_STATE"]
    if blocking:
        raise OrderError(blocking[0]["message"], "CHECKOUT_INVALID", reval["issues"])

    items = [
        {"product_id": l["product_id"], "quantity": l["quantity"],
         **({"unit_price": l["unit_price"]} if l.get("unit_price") is not None else {})}
        for l in (order["cart"] or [])
    ]
    items_json = json.dumps(items)
    try:
        result = await db.fetchrow(
            "select create_sale($1, $2, $3::jsonb, $4, $5, $6) as result",
            store_id,
            order["customer_id"],
            items_json,
            float(order["cart_discount"] or 0),
            order["payment_method"],
            user_id,
        )
    except Exception as exc:  # noqa: BLE001 — map DB errors to honest 4xx
        msg = str(exc)
        if "Insufficient stock" in msg:
            raise OrderError(msg.split("DETAIL:")[-1].strip(), "INSUFFICIENT_STOCK") from exc
        if "minimum acceptable" in msg or "below" in msg.lower():
            raise OrderError(msg.split("DETAIL:")[-1].strip(), "PRICE_FLOOR") from exc
        raise OrderError("Sale execution failed; nothing was changed.", "SALE_FAILED") from exc

    payload = result["result"] if result else {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    sale_id = payload["sale_id"]

    await db.execute(
        "update orders set sale_id=$3, total=$4, state='COMPLETED' where id=$1 and store_id=$2",
        order_id, store_id, sale_id, float(payload["total"]),
    )
    await _log_event(store_id, order_id, "ORDER_COMPLETED", payload={
        "sale_id": str(sale_id), "total": float(payload["total"]),
        "inventory_deducted": True,
    })
    updated = await get_order(store_id, str(order_id))
    assert updated is not None
    return {"already_completed": False, "sale_id": str(sale_id), "order": updated,
            "sale_total": float(payload["total"])}


async def order_summary_totals(store_id: str, order_id: str) -> dict[str, float]:
    order = await get_order(store_id, order_id)
    if not order:
        raise OrderError("Order not found.", "NOT_FOUND")
    reval = await revalidate(store_id, order_id)
    return {"subtotal": reval["subtotal_current"], "discount": reval["discount"],
            "total": reval["total_current"]}
