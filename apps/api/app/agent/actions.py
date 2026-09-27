"""Action engine — AI prepares, merchant approves, backend revalidates, executes.

Flow: prepare (snapshot state fingerprint) -> preview (exact consequences)
-> approve -> RE-VALIDATE current state (staleness detection) -> execute via
the Phase 2 transactional RPCs -> outcome recorded. Duplicate approvals are
made impossible by the store-scoped idempotency key on ai_actions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional

from app.database import db
from app.services import intelligence as intel
from app.agent import tools

VALID_ACTION_TYPES = ("create_purchase", "price_change", "inventory_adjust")


class ActionError(Exception):
    def __init__(self, message: str, code: str = "action_error"):
        super().__init__(message)
        self.code = code


def _j(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _j(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_j(v) for v in value]
    if hasattr(value, "__float__"):
        return float(value)
    return str(value)


def _fingerprint(data: Any) -> str:
    return hashlib.sha256(json.dumps(_j(data), sort_keys=True).encode()).hexdigest()[:16]


def _jsonb(value: Any, default: Any) -> Any:
    """asyncpg returns jsonb as str sometimes — decode defensively."""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return {"text": value}
    return default


async def _snapshot(store_id: str, action_type: str, product_id: Optional[str]) -> dict[str, Any]:
    """Current state relevant to this action — used for staleness detection."""
    if action_type == "create_purchase" and product_id:
        r = await intel.calculate_reorder(store_id, product_id)
        return {"stock": r.get("current_stock"), "cost": None}
    if action_type == "price_change" and product_id:
        p = await db.fetchrow(
            "select selling_price, purchase_price from products where id=$1 and store_id=$2",
            product_id,
            store_id,
        )
        if not p:
            raise ActionError("Product not found", "not_found")
        return {"price": float(p["selling_price"]), "cost": float(p["purchase_price"])}
    return {}


# ---------------------------------------------------------------- prepare


async def prepare_action(
    store_id: str,
    user_id: str,
    *,
    action_type: str,
    payload: dict[str, Any],
    recommendation_id: Optional[str] = None,
) -> dict[str, Any]:
    """Validate + build the exact preview. Persists a prepared action with a
    state fingerprint so approval can detect staleness."""
    if action_type not in VALID_ACTION_TYPES:
        raise ActionError(f"Unsupported action type: {action_type}", "bad_type")

    product_id = payload.get("product_id")
    snapshot = await _snapshot(store_id, action_type, product_id)
    preview = await build_preview(store_id, action_type, payload)

    # Duplicate-EXECUTION protection lives in the status machine (an executed
    # action returns its original outcome when approved again). This key just
    # labels the attempt; time is included so repeat prepares of the same
    # intent after a completed cycle are not silently swallowed.
    idem = _fingerprint({
        "store": store_id,
        "type": action_type,
        "payload": payload,
        "rec": recommendation_id,
        "ts": datetime.utcnow().isoformat(),
    })

    row = await db.fetchrow(
        """
        insert into ai_actions
          (store_id, recommendation_id, action_type, payload, preview, state_fingerprint, idempotency_key, created_by)
        values ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7, $8)
        returning id, status, preview
        """,
        store_id,
        recommendation_id,
        action_type,
        json.dumps(_j(payload)),
        json.dumps(_j(preview)),
        json.dumps(_j(snapshot)),
        idem,
        user_id,
    )
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, message)
        values ($1, $2, 'AI_ACTION_PREPARED', 'ai_action', $3, $4)
        """,
        store_id,
        user_id,
        row["id"],
        f"Action prepared: {action_type}",
    )
    return {"action_id": str(row["id"]), "status": row["status"], "preview": _j(preview)}


async def build_preview(store_id: str, action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Exact, evidence-backed preview of what will happen. No surprises."""
    if action_type == "create_purchase":
        return await _preview_purchase(store_id, payload)
    if action_type == "price_change":
        return await _preview_price_change(store_id, payload)
    if action_type == "inventory_adjust":
        return await _preview_inventory_adjust(store_id, payload)
    raise ActionError(f"Unsupported action type: {action_type}", "bad_type")


async def _preview_purchase(store_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    product_id = payload.get("product_id")
    quantity = int(payload.get("quantity") or 0)
    supplier_id = payload.get("supplier_id")
    if not product_id or quantity <= 0:
        raise ActionError("Product and positive quantity are required", "bad_payload")

    prod = await db.fetchrow(
        "select id, name, purchase_price from products where id=$1 and store_id=$2",
        product_id,
        store_id,
    )
    if not prod:
        raise ActionError("Product not found", "not_found")

    unit_cost = payload.get("unit_cost")
    supplier_name = None
    if supplier_id:
        sup = await db.fetchrow(
            "select id, name from suppliers where id=$1 and store_id=$2", supplier_id, store_id
        )
        if not sup:
            raise ActionError("Supplier not found", "not_found")
        supplier_name = sup["name"]
    if unit_cost is None:
        unit_cost = float(prod["purchase_price"])
    unit_cost = round(float(unit_cost), 2)

    r = await intel.calculate_reorder(store_id, product_id)
    current_stock = int(r.get("current_stock") or 0)

    return _j({
        "action_type": "create_purchase",
        "product": {"id": str(prod["id"]), "name": prod["name"]},
        "quantity": quantity,
        "supplier": supplier_name or "Store default (no supplier selected)",
        "unit_cost": unit_cost,
        "estimated_total": round(quantity * unit_cost, 2),
        "current_stock": current_stock,
        "expected_stock_after_receipt": current_stock + quantity,
        "note": "Stock changes only when the purchase is received (batches created atomically).",
    })


async def _preview_price_change(store_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    product_id = payload.get("product_id")
    new_price = payload.get("new_price")
    if not product_id or new_price is None:
        raise ActionError("Product and new price are required", "bad_payload")
    m = await intel.calculate_product_margin(store_id, product_id)
    new_price = round(float(new_price), 2)
    floor_price = m["minimum_acceptable_price"]
    new_margin = round((new_price - m["purchase_cost"]) / new_price * 100, 1) if new_price > 0 else None
    if new_price < floor_price:
        raise ActionError(
            f"Price ₹{new_price} is below your minimum acceptable price (₹{floor_price})",
            "below_floor",
        )
    return _j({
        "action_type": "price_change",
        "product": {"id": m["product_id"], "name": m["name"]},
        "current_price": m["selling_price"],
        "new_price": new_price,
        "current_margin_pct": m["gross_margin_pct"],
        "new_margin_pct": new_margin,
        "minimum_acceptable_price": floor_price,
        "purchase_cost": m["purchase_cost"],
        "reason": payload.get("reason"),
        "note": "Price history is preserved; the change is logged as PRICE_CHANGED.",
    })


async def _preview_inventory_adjust(store_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    product_id = payload.get("product_id")
    change = int(payload.get("change") or 0)
    reason = payload.get("reason") or "AI prepared adjustment"
    if not product_id or change == 0:
        raise ActionError("Product and non-zero change are required", "bad_payload")
    r = await intel.calculate_reorder(store_id, product_id)
    current = int(r.get("current_stock") or 0)
    return _j({
        "action_type": "inventory_adjust",
        "product": {"id": product_id, "name": r.get("name")},
        "change": change,
        "current_stock": current,
        "expected_stock_after": current + change,
        "reason": reason,
        "note": "Reductions deplete batches FEFO and refuse to go below sellable stock.",
    })


# ---------------------------------------------------------------- approval + execution


async def approve_and_execute(
    store_id: str,
    user_id: str,
    action_id: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Approve -> revalidate against CURRENT state -> execute.

    Stale actions are never executed blindly unless the merchant explicitly
    forces after seeing the updated numbers.
    """
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                select * from ai_actions
                where id = $1 and store_id = $2
                for update
                """,
                action_id,
                store_id,
            )
            if not row:
                raise ActionError("Action not found", "not_found")
            if row["status"] == "executed":
                # Idempotent duplicate: return the original outcome.
                return {
                    "action_id": action_id,
                    "status": "executed",
                    "duplicate": True,
                    "result": _jsonb(row["preview"], {}),
                    "executed_reference_id": str(row["executed_reference_id"]) if row["executed_reference_id"] else None,
                    "message": "This action was already executed. Nothing was done twice.",
                }
            if row["status"] in ("cancelled", "failed"):
                raise ActionError(f"Action is {row['status']} and cannot be executed", "bad_status")
            if row["status"] == "executing":
                raise ActionError("Action is already executing", "already_executing")

            await conn.execute(
                "update ai_actions set status = 'executing' where id = $1", row["id"]
            )

    payload = _jsonb(row["payload"], {})
    stored_fp = _jsonb(row["state_fingerprint"], {})

    # ---- revalidation against the CURRENT state ----
    fresh = await _snapshot(store_id, row["action_type"], payload.get("product_id"))
    stale_fields = []
    for key, old_value in stored_fp.items():
        new_value = fresh.get(key)
        if new_value is not None and old_value is not None and float(new_value) != float(old_value):
            stale_fields.append({"field": key, "at_prepare": old_value, "now": new_value})

    if stale_fields and not force:
        await db.execute("update ai_actions set status = 'prepared' where id = $1", row["id"])
        await db.execute(
            """
            insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
            values ($1, $2, 'AI_ACTION_STALE', 'ai_action', $3, $4::jsonb, $5)
            """,
            store_id,
            user_id,
            row["id"],
            json.dumps(_j({"stale_fields": stale_fields})),
            "Action not executed: state changed since preparation",
        )
        new_preview = await build_preview(store_id, row["action_type"], payload)
        return {
            "action_id": action_id,
            "status": "stale",
            "stale_fields": stale_fields,
            "updated_preview": new_preview,
            "message": (
                "The recommendation is no longer current ("
                + "; ".join(f"{s['field']}: {s['at_prepare']} → {s['now']}" for s in stale_fields)
                + "). Review the updated preview, then approve again."
            ),
        }

    # ---- execute through the Phase 2 transactional backends ----
    try:
        if row["action_type"] == "create_purchase":
            result = await _execute_purchase(store_id, user_id, payload)
        elif row["action_type"] == "price_change":
            result = await _execute_price_change(store_id, user_id, payload)
        else:
            result = await _execute_inventory_adjust(store_id, user_id, payload)
    except ActionError:
        await db.execute(
            "update ai_actions set status = 'failed', error = $2 where id = $1",
            row["id"],
            "validation failed",
        )
        raise
    except Exception as exc:  # noqa: BLE001 — record, never claim success
        err = str(exc)[:500]
        await db.execute(
            "update ai_actions set status = 'failed', error = $2 where id = $1",
            row["id"],
            err,
        )
        await db.execute(
            """
            insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, message)
            values ($1, $2, 'AI_ACTION_FAILED', 'ai_action', $3, $4)
            """,
            store_id,
            user_id,
            row["id"],
            f"Action failed: {err}",
        )
        raise ActionError(f"Action failed, nothing was changed: {err}", "execution_failed")

    ref = result.get("reference_id")
    await db.execute(
        """
        update ai_actions
        set status = 'executed', executed_reference_id = $2, executed_at = now()
        where id = $1
        """,
        row["id"],
        ref,
    )

    # Close the recommendation that produced this action
    if row["recommendation_id"]:
        await db.execute(
            """
            update ai_recommendations
            set status = 'EXECUTED', outcome = $2, updated_at = now(), resolved_at = now()
            where id = $1 and status in ('APPROVED','REVIEWED','NEW')
            """,
            row["recommendation_id"],
            json.dumps(_j({"action_id": action_id, "result": result})),
        )

    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
        values ($1, $2, 'AI_ACTION_EXECUTED', 'ai_action', $3, $4::jsonb, $5)
        """,
        store_id,
        user_id,
        row["id"],
        json.dumps(_j(result)),
        f"AI action executed: {row['action_type']}",
    )

    return {"action_id": action_id, "status": "executed", "duplicate": False, "result": result}


async def _execute_purchase(store_id: str, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Creates a pending PO. Stock updates when the merchant receives it."""
    items = [{
        "product_id": payload["product_id"],
        "quantity": int(payload["quantity"]),
        "unit_cost": float(payload.get("unit_cost") or 0),
        "expiry_date": payload.get("expiry_date"),
    }]
    if not payload.get("supplier_id"):
        raise ActionError("Supplier is required to create a purchase", "bad_payload")
    from app.routers import purchases as po_router

    body = po_router.PurchaseIn(
        supplier_id=payload["supplier_id"],
        items=[po_router.PurchaseItemIn(**items[0])],
    )
    created = await po_router.create_purchase(body, {"store_id": store_id, "id": user_id})
    return {"reference_id": created["id"], "purchase_order": created,
            "note": created.get("status")}


async def _execute_price_change(store_id: str, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    product_id = payload["product_id"]
    new_price = round(float(payload["new_price"]), 2)
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            cur = await conn.fetchrow(
                "select selling_price, name from products where id=$1 and store_id=$2 for update",
                product_id,
                store_id,
            )
            if not cur:
                raise ActionError("Product not found", "not_found")
            old_price = float(cur["selling_price"])
            if new_price == old_price:
                return {"reference_id": product_id, "changed": False,
                        "message": "Price already equals the target; nothing changed."}
            # Re-check floor at execution time (cost may have changed since prepare)
            floor_price = await intel.min_acceptable_price(store_id, product_id)
            if new_price < floor_price:
                raise ActionError(
                    f"Price ₹{new_price} is below the current minimum ₹{floor_price} — nothing was changed.",
                    "below_floor",
                )
            await conn.execute(
                "update products set selling_price = $3 where id = $1 and store_id = $2",
                product_id,
                store_id,
                new_price,
            )
            await conn.execute(
                """
                insert into product_price_history
                  (store_id, product_id, field, old_value, new_value, reason, changed_by)
                values ($1, $2, 'selling_price', $3, $4, $5, $6)
                """,
                store_id,
                product_id,
                old_price,
                new_price,
                payload.get("reason") or "AI-prepared price change approved by merchant",
                user_id,
            )
            await conn.execute(
                """
                insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
                values ($1, $2, 'PRICE_CHANGED', 'product', $3, $4::jsonb, $5)
                """,
                store_id,
                user_id,
                product_id,
                json.dumps({"old_price": old_price, "new_price": new_price}),
                f"Price changed for {cur['name']}: ₹{old_price} → ₹{new_price}",
            )
    return {"reference_id": product_id, "old_price": old_price, "new_price": new_price,
            "changed": True}


async def _execute_inventory_adjust(store_id: str, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from app.routers import inventory as inv_router

    body = inv_router.ProductAdjustIn(
        change=int(payload["change"]),
        reason=(payload.get("reason") or "AI prepared adjustment")[:80],
        expiry_date=payload.get("expiry_date"),
        unit_cost=payload.get("unit_cost"),
    )
    result = await inv_router.adjust_product_inventory(
        product_id=payload["product_id"], body=body, user={"store_id": store_id, "id": user_id}
    )
    return {"reference_id": payload["product_id"], "adjustment": result}


async def cancel_action(store_id: str, user_id: str, action_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        "update ai_actions set status='cancelled' where id=$1 and store_id=$2 and status='prepared' returning id",
        action_id,
        store_id,
    )
    if not row:
        raise ActionError("Prepared action not found (it may already be executed)", "bad_status")
    await db.execute(
        """
        insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, message)
        values ($1, $2, 'AI_ACTION_CANCELLED', 'ai_action', $3, $4)
        """,
        store_id,
        user_id,
        action_id,
        "Prepared action cancelled by merchant",
    )
    return {"action_id": action_id, "status": "cancelled"}


async def get_action(store_id: str, action_id: str) -> dict[str, Any]:
    row = await db.fetchrow(
        "select * from ai_actions where id = $1 and store_id = $2", action_id, store_id
    )
    if not row:
        raise ActionError("Action not found", "not_found")
    d = dict(row)
    d["id"] = str(d["id"])
    d["payload"] = _jsonb(d.get("payload"), {})
    d["preview"] = _jsonb(d.get("preview"), {})
    d["state_fingerprint"] = _jsonb(d.get("state_fingerprint"), {})
    return _j(d)


async def list_actions(store_id: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """
        select id, action_type, status, payload, preview, error, created_at, executed_at,
               recommendation_id, executed_reference_id
        from ai_actions where store_id = $1
        order by created_at desc limit $2
        """,
        store_id,
        max(1, min(int(limit), 200)),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["payload"] = _jsonb(d.get("payload"), {})
        d["preview"] = _jsonb(d.get("preview"), {})
        out.append(_j(d))
    return out
