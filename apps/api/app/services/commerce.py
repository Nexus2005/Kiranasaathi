"""Phase 8 — customer ordering & omnichannel fulfillment.

Reuses the Phase 6 order engine everywhere:
- cart revalidation    -> orders._cart_issues (price currency, stock, floors)
- sale creation        -> orders.complete_order -> atomic create_sale RPC
- payment verification -> payments service (server-authoritative)

No new totals math, no second inventory engine. Channels (POS, customer web,
WhatsApp link, quick commerce...) all converge on the same orders lifecycle:
PAID -> CONFIRMED -> PROCESSING -> READY -> OUT_FOR_DELIVERY -> DELIVERED ->
COMPLETED (PICKUP orders go READY -> COMPLETED).
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from app.database import db
from app.services import orders as orders_service
from app.services.orders import OrderError

VALID_CHANNELS = ("POS", "CUSTOMER_WEB", "CUSTOMER_LINK", "WHATSAPP",
                  "QUICK_COMMERCE", "MARKETPLACE", "PHONE", "MANUAL", "OTHER")
DELIVERY_METHODS = ("PICKUP", "MERCHANT_DELIVERY", "THIRD_PARTY_DELIVERY", "OTHER")

# Merchant fulfillment actions -> order state
ACTION_TRANSITIONS: dict[str, tuple[str, str]] = {
    "ACCEPT": ("PAID", "CONFIRMED"),
    "REJECT": ("PAID", "CANCELLED"),
    "PREPARE": ("CONFIRMED", "PROCESSING"),
    "READY": ("PROCESSING", "READY"),
    "DISPATCH": ("READY", "OUT_FOR_DELIVERY"),
    "DELIVER": ("OUT_FOR_DELIVERY", "DELIVERED"),
    "COMPLETE": ("READY", "COMPLETED"),           # pickup completion
    "COMPLETE_DELIVERED": ("DELIVERED", "COMPLETED"),
    "CANCEL": ("__any__", "CANCELLED"),
}

ALLOWED_ACTIONS_BY_STATE: dict[str, list[str]] = {
    "PAID": ["ACCEPT", "REJECT", "CANCEL"],
    "CONFIRMED": ["PREPARE", "COMPLETE", "CANCEL"],   # pickup: complete right after accept
    "PROCESSING": ["READY", "COMPLETE", "CANCEL"],
    "READY": ["DISPATCH", "COMPLETE", "CANCEL"],
    "OUT_FOR_DELIVERY": ["DELIVER", "CANCEL"],
    "DELIVERED": ["COMPLETE_DELIVERED"],
    "PENDING_PAYMENT": ["CANCEL"],
    "PAYMENT_FAILED": ["CANCEL"],
    "COMPLETED": [],
    "CANCELLED": [],
    "REFUND_PENDING": [],
    "REFUNDED": [],
}


class CommerceError(Exception):
    def __init__(self, message: str, code: str = "COMMERCE_ERROR", status: int = 400,
                 issues: Optional[list[dict]] = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.issues = issues or []


async def _log(store_id: str, order_id: str, event_type: str, actor: str,
               payload: Optional[dict] = None) -> None:
    await db.execute(
        "insert into order_events (store_id, order_id, event_type, actor, payload) "
        "values ($1, $2, $3, $4, $5::jsonb)",
        store_id, order_id, event_type, actor, json.dumps(payload or {}),
    )


# ==================================================================
# Storefront configuration + public catalog
# ==================================================================
async def get_store_by_slug(slug: str) -> Optional[dict[str, Any]]:
    row = await db.fetchrow(
        """
        select s.id, s.slug, s.name, s.location, s.currency,
               cs.is_published, cs.show_stock, cs.allow_guest_checkout,
               cs.delivery_fee, cs.min_order_amount
        from stores s
        left join store_catalog_settings cs on cs.store_id = s.id
        where s.slug = $1
        """,
        slug,
    )
    return dict(row) if row else None


async def get_catalog_settings(store_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        "select * from store_catalog_settings where store_id = $1", store_id
    )
    if not row:
        await db.execute(
            "insert into store_catalog_settings (store_id) values ($1) on conflict do nothing",
            store_id,
        )
        row = await db.fetchrow(
            "select * from store_catalog_settings where store_id = $1", store_id
        )
    return dict(row)  # type: ignore[return-value]


async def update_catalog_settings(store_id: str, **fields: Any) -> dict[str, Any]:
    await get_catalog_settings(store_id)  # ensure row exists
    sets = []
    params: list[Any] = []
    for key in ("is_published", "show_stock", "allow_guest_checkout",
                "delivery_fee", "min_order_amount"):
        if key in fields and fields[key] is not None:
            params.append(fields[key])
            sets.append(f"{key} = ${len(params) + 1}")
    if sets:
        await db.execute(
            f"update store_catalog_settings set {', '.join(sets)} "
            f"where store_id = $1",
            store_id, *params,
        )
    return await get_catalog_settings(store_id)


async def public_catalog(store: dict[str, Any], category: Optional[str] = None,
                         q: Optional[str] = None, limit: int = 50,
                         offset: int = 0) -> dict[str, Any]:
    """Customer-facing catalog. Exposes ONLY public fields — never cost,
    margin, min price, reorder settings, or internal inventory intelligence."""
    if not store.get("is_published"):
        raise CommerceError("This store is not accepting online orders.", "STORE_UNPUBLISHED", 404)

    show_stock = bool(store.get("show_stock"))
    rows = await db.fetch(
        """
        select p.id, p.name, p.category, p.unit, p.mrp, p.image_url,
               coalesce(cp.price, p.selling_price) as price,
               (select min(pi.quantity) from order_items pi
                 where pi.product_id = p.id) is not null as _placeholder,
               case when $5 then sellable_stock($1, p.id) end as available
        from products p
        left join channel_prices cp
          on cp.store_id = p.store_id and cp.product_id = p.id
         and cp.channel = $4 and cp.active
        where p.store_id = $1 and p.is_active
          and ($2::text is null or p.category = $2::text)
          and ($3::text is null or p.name ilike '%' || $3::text || '%')
        order by p.name
        limit $6 offset $7
        """,
        store["id"], category, (q or None), "CUSTOMER_WEB",
        show_stock, max(1, min(limit, 100)), max(0, offset),
    )
    items = []
    for r in rows:
        d = dict(r)
        d.pop("_placeholder", None)
        avail = d.get("available")
        d["in_stock"] = True if avail is None else (int(avail) > 0)
        if not show_stock:
            d.pop("available", None)
        items.append(d)
    total = await db.fetchval(
        """
        select count(*) from products p
        where p.store_id = $1 and p.is_active
          and ($2::text is null or p.category = $2::text)
          and ($3::text is null or p.name ilike '%' || $3::text || '%')
        """,
        store["id"], category, (q or None),
    )
    categories = await db.fetch(
        "select distinct category from products where store_id=$1 and is_active order by category",
        store["id"],
    )
    return {
        "store": {k: store[k] for k in ("slug", "name", "location", "currency")},
        "items": items,
        "categories": [r["category"] for r in categories],
        "total": int(total or 0),
        "limit": limit, "offset": offset,
        "delivery_fee": float(store.get("delivery_fee") or 0),
        "min_order_amount": float(store.get("min_order_amount") or 0),
        "allow_guest_checkout": bool(store.get("allow_guest_checkout", True)),
    }


async def public_product(store: dict[str, Any], product_id: str) -> dict[str, Any]:
    """Single product for the customer PDP. Public fields only."""
    if not store.get("is_published"):
        raise CommerceError("This store is not accepting online orders.", "STORE_UNPUBLISHED", 404)
    show_stock = bool(store.get("show_stock"))
    row = await db.fetchrow(
        """
        select p.id, p.name, p.category, p.unit, p.mrp, p.image_url,
               coalesce(cp.price, p.selling_price) as price,
               case when $3 then sellable_stock($1, p.id) end as available
        from products p
        left join channel_prices cp
          on cp.store_id = p.store_id and cp.product_id = p.id
         and cp.channel = 'CUSTOMER_WEB' and cp.active
        where p.store_id = $1 and p.id = $2 and p.is_active
        """,
        store["id"], product_id, show_stock,
    )
    if not row:
        raise CommerceError("Product not found.", "NOT_FOUND", 404)
    d = dict(row)
    avail = d.get("available")
    d["in_stock"] = True if avail is None else (int(avail) > 0)
    if not show_stock:
        d.pop("available", None)
    return d


# ==================================================================
# Channel price for any channel (bounded by pricing rules at checkout)
# ==================================================================
async def channel_price(store_id: str, product_id: str, channel: str) -> Optional[float]:
    row = await db.fetchval(
        """
        select cp.price from channel_prices cp
        where cp.store_id = $1 and cp.product_id = $2 and cp.channel = $3 and cp.active
        """,
        store_id, product_id, channel,
    )
    if row is not None:
        return float(row)
    row = await db.fetchval(
        "select selling_price from products where id=$1 and store_id=$2", product_id, store_id
    )
    return float(row) if row is not None else None


# ==================================================================
# Customer checkout (guest or linked customer)
# ==================================================================
async def customer_checkout(
    store: dict[str, Any], cart: list[dict[str, Any]], discount: float,
    delivery_method: str, delivery_address: Optional[str],
    customer_name: str, customer_phone: str,
    customer_id: Optional[str] = None, payment_method: str = "upi",
    campaign_id: Optional[str] = None, idempotency_key: Optional[str] = None,
) -> dict[str, Any]:
    """Create a customer order through the SAME engine as POS.

    Backend revalidates everything; the customer-supplied total is ignored.
    """
    if not store.get("is_published"):
        raise CommerceError("This store is not accepting online orders.", "STORE_UNPUBLISHED", 404)
    if not cart:
        raise CommerceError("Cart is empty.", "EMPTY_CART")
    if not customer_name or not customer_name.strip():
        raise CommerceError("Name is required.", "NAME_REQUIRED")
    phone = (customer_phone or "").strip()
    if not phone.isdigit() or len(phone) < 10:
        raise CommerceError("A valid 10-digit phone number is required.", "PHONE_REQUIRED")
    if delivery_method not in DELIVERY_METHODS:
        raise CommerceError("Invalid delivery method.", "INVALID_DELIVERY_METHOD")
    if delivery_method != "PICKUP" and not (delivery_address or "").strip():
        raise CommerceError("Delivery address is required for delivery orders.", "ADDRESS_REQUIRED")
    if not store.get("allow_guest_checkout", True) and not customer_id:
        raise CommerceError("Please sign in to place an order.", "SIGNIN_REQUIRED")

    if discount > 0 and not customer_id:
        # guests cannot self-apply discounts; merchants apply discounts in POS
        raise CommerceError("Discount is not applicable on guest orders.", "DISCOUNT_NOT_ALLOWED")

    min_total = float(store.get("min_order_amount") or 0)

    # Campaign attribution only when the campaign actually exists in this
    # store — validated BEFORE insert (never inferred, never guessed).
    if campaign_id:
        valid = await db.fetchval(
            "select 1 from campaigns where id=$1 and store_id=$2",
            campaign_id, store["id"],
        )
        if not valid:
            campaign_id = None

    # Idempotency: same key returns the original order instead of duplicating
    if idempotency_key:
        existing = await db.fetchrow(
            """
            select id from orders
            where store_id = $1
              and external_reference = $2 and channel in ('CUSTOMER_WEB','CUSTOMER_LINK','WHATSAPP')
            """,
            store["id"], f"idem:{idempotency_key}",
        )
        if existing:
            order = await orders_service.get_order(store["id"], str(existing["id"]))
            return {"order": order, "duplicate": True}

    # Resolve guest -> customer record (upsert by phone within this store)
    resolved_customer_id = customer_id
    if not resolved_customer_id:
        resolved_customer_id = await db.fetchval(
            "select id from customers where store_id=$1 and phone=$2",
            store["id"], phone,
        )
        if not resolved_customer_id:
            resolved_customer_id = await db.fetchval(
                """
                insert into customers (store_id, name, phone, marketing_consent, consent_source)
                values ($1, $2, $3, false, null)
                returning id
                """,
                store["id"], customer_name.strip(), phone,
            )

    # Build the cart for the shared engine: channel price as negotiated price,
    # list price snapshot for stale detection. The engine enforces floors.
    engine_cart: list[dict[str, Any]] = []
    for line in cart:
        pid = line.get("product_id")
        qty = int(line.get("quantity") or 0)
        if not pid or qty <= 0:
            raise CommerceError("Cart contains an invalid line.", "INVALID_LINE")
        price = await channel_price(store["id"], pid, "CUSTOMER_WEB")
        list_price = await db.fetchval(
            "select selling_price from products where id=$1 and store_id=$2", pid, store["id"]
        )
        if list_price is None:
            raise CommerceError("A product in your cart is no longer available.", "PRODUCT_UNAVAILABLE")
        engine_cart.append({
            "product_id": pid, "quantity": qty,
            "unit_price": price,                       # channel price (negotiated slot)
            "_list_price_at_cart": float(list_price),  # for stale detection at completion
        })

    # Validate against current stock/price state through the shared engine
    issues, subtotal = await orders_service._cart_issues(store["id"], engine_cart, discount)
    fatal_codes = {"PRODUCT_UNAVAILABLE", "INVALID_QUANTITY", "INSUFFICIENT_STOCK",
                   "PRICE_BELOW_FLOOR", "PRICE_ABOVE_LIST", "DISCOUNT_EXCEEDS_SUBTOTAL"}
    fatal = [i for i in issues if i["code"] in fatal_codes]
    if fatal:
        raise CommerceError(fatal[0]["message"], "CART_INVALID", 409, issues)

    total = round(max(0.0, subtotal - discount), 2)
    if min_total and total < min_total:
        raise CommerceError(
            f"Minimum order amount is ₹{min_total:g}. Add ₹{min_total - total:g} more to continue.",
            "MIN_ORDER", 409,
        )

    delivery_status = "NOT_REQUIRED" if delivery_method == "PICKUP" else "PENDING"
    order_id = await db.fetchval(
        """
        insert into orders (store_id, customer_id, state, cart, cart_discount,
                            payment_method, channel, source_name, external_reference,
                            guest_name, guest_phone, delivery_method, delivery_address,
                            delivery_status, campaign_id, expires_at, total)
        values ($1, $2, 'PENDING_PAYMENT', $3::jsonb, $4, $5,
                'CUSTOMER_WEB', 'customer_web', $6,
                $7, $8, $9, $10, $11, $12, now() + interval '24 hours', $13)
        returning id
        """,
        store["id"], resolved_customer_id,
        json.dumps([{**line, "_list_price_at_cart": line["_list_price_at_cart"]}
                    for line in engine_cart]),
        discount, payment_method if payment_method in ("cash", "upi", "card") else "upi",
        f"idem:{idempotency_key}" if idempotency_key else None,
        customer_name.strip(), phone, delivery_method, delivery_address,
        delivery_status, campaign_id,
        total,
    )
    for line in engine_cart:
        await db.execute(
            "insert into order_items (order_id, product_id, quantity, unit_price) values ($1,$2,$3,$4)",
            order_id, line["product_id"], line["quantity"], float(line["unit_price"] or 0),
        )
    await _log(store["id"], str(order_id), "ORDER_CREATED", "customer", {
        "channel": "CUSTOMER_WEB", "items": len(engine_cart), "total": total,
        "delivery_method": delivery_method,
        "campaign_attributed": campaign_id is not None,
    })

    order = await orders_service.get_order(store["id"], str(order_id))
    return {"order": order, "duplicate": False}


# ==================================================================
# Payment for customer orders (server-authoritative via payments service)
# ==================================================================
async def create_customer_payment(store_id: str, order_id: str, method: str,
                                  idempotency_key: Optional[str] = None) -> dict[str, Any]:
    from app.services import payments as payments_service

    order = await orders_service.get_order(store_id, order_id)
    if not order:
        raise CommerceError("Order not found.", "NOT_FOUND", 404)
    if order["state"] not in ("PENDING_PAYMENT", "PAYMENT_FAILED", "PARTIALLY_PAID"):
        raise CommerceError(f"Order is {order['state']}; payment is not pending.", "INVALID_STATE", 409)
    if order["channel"] == "POS":
        raise CommerceError("POS orders are paid at the counter.", "POS_ORDER", 409)

    # Manual provider in demo: the payment is created PENDING and confirmed
    # through the same verify path the merchant uses — never auto-success.
    result = await payments_service.create_payment(
        store_id, order_id, float(order["total"] or 0), method,
        idempotency_key=idempotency_key,
    )
    return result


# ==================================================================
# Merchant fulfillment actions (server-validated state machine)
# ==================================================================
async def fulfillment_action(store_id: str, user_id: str, order_id: str,
                             action: str, reason: Optional[str] = None) -> dict[str, Any]:
    action = action.upper()
    if action not in ACTION_TRANSITIONS:
        raise CommerceError(f"Unknown action '{action}'.", "INVALID_ACTION")
    order = await orders_service.get_order(store_id, order_id)
    if not order:
        raise CommerceError("Order not found.", "NOT_FOUND", 404)

    state = order["state"]
    if action not in ALLOWED_ACTIONS_BY_STATE.get(state, []):
        raise CommerceError(
            f"Action '{action}' is not allowed while the order is {state}.",
            "INVALID_ACTION_STATE", 409,
        )

    if action == "CANCEL":
        # cancellation policy by state:
        #   unpaid  -> cancel outright (matches Phase 6 cancel_order)
        #   paid+   -> refund workflow required; refused here
        if state in ("PENDING_PAYMENT", "PAYMENT_FAILED"):
            await orders_service.cancel_order(store_id, user_id, order_id, reason or "Cancelled by merchant")
        else:
            raise CommerceError(
                "Paid orders cannot be cancelled directly — use the refund workflow.",
                "REFUND_REQUIRED", 409,
            )
        updated = await orders_service.get_order(store_id, order_id)
        assert updated is not None
        return updated

    if action == "REJECT":
        # Rejection of a PAID order is a refund, never a silent cancel:
        # full refund through the payments workflow (REFUND_PENDING -> REFUNDED).
        from app.services import payments as payments_service
        pay_rows = await db.fetch(
            "select id from payments where order_id=$1 and store_id=$2 and state='PAID'",
            order_id, store_id,
        )
        if not pay_rows:
            raise CommerceError("No verified payment found to refund.", "NO_PAID_PAYMENT", 409)
        for p in pay_rows:
            payment = await payments_service.get_payment(store_id, str(p["id"]))
            assert payment is not None
            amount = float(payment["verified_amount"] or payment["amount"])
            await payments_service.create_refund(store_id, str(p["id"]), amount,
                                                 reason or "Order rejected by merchant",
                                                 user_id)
        updated = await orders_service.get_order(store_id, order_id)
        assert updated is not None
        return updated

    if action == "ACCEPT":
        # Revalidate the cart at acceptance time (stock may have moved)
        reval = await orders_service.revalidate(store_id, order_id)
        blocking = [i for i in reval["issues"] if i["code"] in
                    ("PRODUCT_UNAVAILABLE", "INVALID_QUANTITY", "INSUFFICIENT_STOCK")]
        if blocking:
            raise CommerceError(blocking[0]["message"], "CART_INVALID", 409, reval["issues"])
        # Accepted orders no longer expire; the merchant has committed.
        await db.execute(
            "update orders set state='CONFIRMED', accepted_at=now(), expires_at=null "
            "where id=$1 and store_id=$2",
            order_id, store_id,
        )
        await _log(store_id, order_id, "ORDER_ACCEPTED", "merchant", {})
    elif action in ("COMPLETE", "COMPLETE_DELIVERED"):
        # Completion creates the real sale through the atomic create_sale RPC
        # (stock re-checked, FEFO deduction, movements). If stock moved after
        # acceptance this raises honestly — no overselling.
        await orders_service.complete_order(store_id, user_id, order_id)
    else:
        if action == "DISPATCH":
            if order.get("delivery_method") == "PICKUP":
                raise CommerceError("Pickup orders are not dispatched.", "INVALID_FOR_PICKUP", 409)
            await db.execute(
                "update orders set delivery_status='OUT_FOR_DELIVERY', dispatched_at=now() where id=$1 and store_id=$2",
                order_id, store_id,
            )
        if action == "DELIVER":
            await db.execute(
                "update orders set delivery_status='DELIVERED', delivered_at=now() where id=$1 and store_id=$2",
                order_id, store_id,
            )
        new_state = ACTION_TRANSITIONS[action][1]
        await orders_service.set_state(store_id, order_id, new_state, actor="merchant")

    updated = await orders_service.get_order(store_id, order_id)
    assert updated is not None
    return updated


# ==================================================================
# Timeline (real timestamps only)
# ==================================================================
async def order_timeline(store_id: str, order_id: str) -> dict[str, Any]:
    order = await orders_service.get_order(store_id, order_id)
    if not order:
        raise CommerceError("Order not found.", "NOT_FOUND", 404)
    events = await db.fetch(
        """
        select event_type, actor, payload, created_at
        from order_events where store_id=$1 and order_id=$2
        order by created_at asc
        """,
        store_id, order_id,
    )
    return {
        "order": {
            "id": str(order["id"]), "state": order["state"], "channel": order.get("channel"),
            "total": float(order["total"] or 0), "delivery_method": order.get("delivery_method"),
            "delivery_status": order.get("delivery_status"),
        },
        "events": [
            {"event_type": e["event_type"], "actor": e["actor"],
             "payload": e["payload"] if not isinstance(e["payload"], str) else json.loads(e["payload"]),
             "created_at": e["created_at"].isoformat()}
            for e in events
        ],
    }


# ==================================================================
# Channel analytics (real records only)
# ==================================================================
async def channel_analytics(store_id: str, days: int = 30) -> dict[str, Any]:
    rows = await db.fetch(
        """
        select channel,
               count(*)::int as orders,
               coalesce(sum(o.total), 0) as revenue,
               coalesce(avg(o.total), 0) as avg_order_value,
               coalesce((select sum(oi.quantity) from order_items oi
                          join orders o2 on o2.id = oi.order_id
                         where o2.store_id = o.store_id and o2.channel = o.channel
                           and o2.created_at >= now() - make_interval(days => $2::int)), 0)::int as units
        from orders o
        where o.store_id = $1 and o.created_at >= now() - make_interval(days => $2::int)
          and o.state not in ('CANCELLED','PAYMENT_FAILED')
        group by channel, o.store_id
        order by revenue desc
        """,
        store_id, max(1, min(days, 365)),
    )
    return {"days": days, "channels": [dict(r) for r in rows]}


async def product_channel_performance(store_id: str, days: int = 30) -> list[dict[str, Any]]:
    """Per-product channel split. POS volume comes from BOTH direct counter
    sales (sale_items, no order row) and POS-channel orders; online volume
    from order_items. Real records only."""
    rows = await db.fetch(
        """
        with pos_direct as (
            select si.product_id, sum(si.quantity)::int as qty, sum(si.line_total) as revenue
            from sale_items si
            join sales s on s.id = si.sale_id
            where s.store_id = $1 and s.created_at >= now() - make_interval(days => $2::int)
            group by si.product_id
        ),
        order_ch as (
            select oi.product_id, o.channel,
                   sum(oi.quantity)::int as qty, sum(oi.line_total) as revenue
            from order_items oi
            join orders o on o.id = oi.order_id
            where o.store_id = $1
              and o.created_at >= now() - make_interval(days => $2::int)
              and o.state not in ('CANCELLED','PAYMENT_FAILED')
            group by oi.product_id, o.channel
        )
        select p.id, p.name,
               coalesce(pd.qty, 0) + coalesce(oc_pos.qty, 0) as pos_units,
               coalesce(oc_web.qty, 0) as online_units,
               coalesce(oc_qc.qty, 0) as qc_units,
               coalesce(pd.qty, 0) + coalesce(oc_all.qty, 0) as total_units,
               coalesce(pd.revenue, 0) + coalesce(oc_all.revenue, 0) as revenue
        from products p
        left join pos_direct pd on pd.product_id = p.id
        left join order_ch oc_pos on oc_pos.product_id = p.id and oc_pos.channel = 'POS'
        left join order_ch oc_web on oc_web.product_id = p.id
          and oc_web.channel in ('CUSTOMER_WEB','CUSTOMER_LINK','WHATSAPP')
        left join order_ch oc_qc on oc_qc.product_id = p.id and oc_qc.channel = 'QUICK_COMMERCE'
        left join order_ch oc_all on oc_all.product_id = p.id
        where p.store_id = $1
          and (pd.qty is not null or oc_all.qty is not null)
        order by revenue desc
        limit 50
        """,
        store_id, max(1, min(days, 365)),
    )
    return [dict(r) for r in rows]


# ==================================================================
# Demo quick-commerce simulation (truthful: exercises the REAL internal
# order pipeline; never claims a live provider API was called)
# ==================================================================
async def simulate_qc_order(store_id: str, provider: str, cart: list[dict[str, Any]],
                            external_order_id: Optional[str] = None) -> dict[str, Any]:
    """Simulated external QC order ingestion — exercises the real internal
    path (mapping -> validation -> internal order). Clearly labeled DEMO."""
    provider_row = await db.fetchrow(
        "select * from qc_providers where store_id=$1 and provider=$2",
        store_id, provider,
    )
    if not provider_row or provider_row["mode"] != "demo":
        raise CommerceError(
            "Provider is not in DEMO mode. Enable demo mode to simulate orders.",
            "NOT_DEMO", 409,
        )
    ext_id = external_order_id or f"demo-{provider.lower()}-{uuid.uuid4().hex[:10]}"
    # Duplicate external id -> one logical order (webhook dedup semantics)
    existing = await db.fetchval(
        "select id from orders where store_id=$1 and external_order_id=$2",
        store_id, ext_id,
    )
    if existing:
        order = await orders_service.get_order(store_id, str(existing))
        return {"order": order, "duplicate": True}

    items: list[dict[str, Any]] = []
    for line in cart:
        pid = line.get("product_id")
        qty = int(line.get("quantity") or 0)
        listing = await db.fetchrow(
            """
            select l.*, p.name, p.is_active from qc_listings l
            join products p on p.id = l.product_id
            where l.provider_id=$1 and l.product_id=$2
            """,
            provider_row["id"], pid,
        )
        if not listing or not listing["is_active"]:
            raise CommerceError(
                f"Product {pid} has no active {provider} listing. Sync the catalog first.",
                "NO_LISTING", 409,
            )
        price = await channel_price(store_id, pid, "QUICK_COMMERCE")
        items.append({"product_id": pid, "quantity": qty, "unit_price": price,
                      "_list_price_at_cart": price})

    issues, subtotal = await orders_service._cart_issues(store_id, items, 0)
    fatal = [i for i in issues if i["code"] in
             ("PRODUCT_UNAVAILABLE", "INVALID_QUANTITY", "INSUFFICIENT_STOCK")]
    if fatal:
        raise CommerceError(fatal[0]["message"], "CART_INVALID", 409, issues)

    order_id = await db.fetchval(
        """
        insert into orders (store_id, state, cart, cart_discount, payment_method,
                            channel, source_name, external_order_id, guest_name,
                            delivery_method, delivery_address, delivery_status,
                            expires_at, total)
        values ($1, 'PENDING_PAYMENT', $2::jsonb, 0, 'upi', 'QUICK_COMMERCE', $3, $4,
                $5 || ' (demo)', 'MERCHANT_DELIVERY', 'DEMO — simulated provider order', 'PENDING',
                now() + interval '24 hours', $6)
        returning id
        """,
        store_id, json.dumps(items), provider, ext_id, provider, round(subtotal, 2),
    )
    for line in items:
        await db.execute(
            "insert into order_items (order_id, product_id, quantity, unit_price) values ($1,$2,$3,$4)",
            order_id, line["product_id"], line["quantity"], float(line["unit_price"] or 0),
        )
    await db.execute(
        "insert into qc_events (store_id, provider, event_type, external_event_id, payload) "
        "values ($1, $2, 'ORDER_CREATED', $3, $4::jsonb)",
        store_id, provider, ext_id, json.dumps({"items": len(items)}),
    )
    await _log(store_id, str(order_id), "ORDER_CREATED", "system", {
        "channel": "QUICK_COMMERCE", "provider": provider, "simulated": True,
    })
    order = await orders_service.get_order(store_id, str(order_id))
    return {"order": order, "duplicate": False}
