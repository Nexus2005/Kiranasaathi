"""Phase 8 — quick-commerce provider abstraction.

Architecture:
    KiranaSaathi -> QuickCommerceProvider (protocol) -> provider adapter -> real API

Truthful status rules (Part 14):
    * No configuration            -> NOT_CONFIGURED
    * Demo mode (no credentials)  -> DEMO / SIMULATED  (exercises the REAL
      internal order pipeline, clearly labeled, never shown as live)
    * Live credentials configured -> only possible when a real adapter exists.
      No Blinkit/Zepto/Swiggy/ONDC adapter ships in this codebase because no
      provider credentials or API agreements exist — configuring "live" mode
      is therefore refused honestly instead of faked.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from typing import Any, Optional, Protocol

from app.database import db
from app.services.commerce import CommerceError

PROVIDERS = ("BLINKIT", "ZEPTO", "SWIGGY", "ONDC", "OTHER")


class QuickCommerceProvider(Protocol):
    """Contract every real adapter must implement (Part 13)."""

    name: str

    async def get_catalog(self, store_id: str) -> dict[str, Any]: ...
    async def sync_catalog(self, store_id: str, products: list[dict[str, Any]]) -> dict[str, Any]: ...
    async def update_inventory(self, store_id: str, listings: list[dict[str, Any]]) -> dict[str, Any]: ...
    async def update_price(self, store_id: str, listings: list[dict[str, Any]]) -> dict[str, Any]: ...
    async def acknowledge_order(self, store_id: str, external_order_id: str) -> dict[str, Any]: ...
    async def update_order_status(self, store_id: str, external_order_id: str, status: str) -> dict[str, Any]: ...
    async def cancel_order(self, store_id: str, external_order_id: str) -> dict[str, Any]: ...
    async def get_order_status(self, store_id: str, external_order_id: str) -> dict[str, Any]: ...


class DemoAdapter:
    """Demo adapter: implements the protocol against local state only.

    It never calls an external API — catalog/listing/inventory/price syncs
    exercise KiranaSaathi's own tables and are labelled DEMO in the UI.
    """

    name = "demo"

    async def get_catalog(self, store_id: str) -> dict[str, Any]:
        rows = await db.fetch(
            """
            select p.id, p.name, p.sku, p.selling_price,
                   sellable_stock($1, p.id) as sellable, l.sync_status
            from products p
            left join qc_listings l on l.product_id = p.id
              and l.provider_id = (select id from qc_providers
                                   where store_id=$1 and mode='demo' limit 1)
            where p.store_id = $1 and p.is_active
            order by p.name limit 200
            """,
            store_id,
        )
        return {"products": [dict(r) for r in rows], "mode": "demo"}

    async def sync_catalog(self, store_id: str, products: list[dict[str, Any]]) -> dict[str, Any]:
        return {"synced": len(products), "mode": "demo"}

    async def update_inventory(self, store_id: str, listings: list[dict[str, Any]]) -> dict[str, Any]:
        return {"updated": len(listings), "mode": "demo"}

    async def update_price(self, store_id: str, listings: list[dict[str, Any]]) -> dict[str, Any]:
        return {"updated": len(listings), "mode": "demo"}

    async def acknowledge_order(self, store_id: str, external_order_id: str) -> dict[str, Any]:
        return {"acknowledged": external_order_id, "mode": "demo"}

    async def update_order_status(self, store_id: str, external_order_id: str, status: str) -> dict[str, Any]:
        return {"external_order_id": external_order_id, "status": status, "mode": "demo"}

    async def cancel_order(self, store_id: str, external_order_id: str) -> dict[str, Any]:
        return {"cancelled": external_order_id, "mode": "demo"}

    async def get_order_status(self, store_id: str, external_order_id: str) -> dict[str, Any]:
        return {"external_order_id": external_order_id, "mode": "demo"}


def get_adapter(provider: str, mode: str) -> QuickCommerceProvider:
    """Adapter factory. Live adapters exist ONLY for providers with real,
    verified integrations — none are configured in this deployment."""
    if mode == "demo":
        return DemoAdapter()
    raise CommerceError(
        f"No live adapter is implemented for {provider} in this deployment. "
        "Demo mode is the only available integration until provider "
        "credentials and API access are obtained.",
        "ADAPTER_NOT_IMPLEMENTED", 409,
    )


# ==================================================================
# Provider registry
# ==================================================================
async def list_providers(store_id: str) -> list[dict[str, Any]]:
    rows = await db.fetch(
        "select id, provider, status, mode, connected_account, last_sync_at, "
        "last_error, created_at from qc_providers where store_id=$1 order by provider",
        store_id,
    )
    known = {r["provider"] for r in rows}
    # Surface the standard provider slots even when never configured
    out = [dict(r) for r in rows]
    for p in PROVIDERS:
        if p not in known:
            out.append({"provider": p, "status": "NOT_CONFIGURED", "mode": "unconfigured",
                        "id": None, "connected_account": None, "last_sync_at": None,
                        "last_error": None})
    return out


async def configure_provider(store_id: str, provider: str, mode: str,
                             connected_account: Optional[str] = None) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise CommerceError("Unknown provider.", "UNKNOWN_PROVIDER", 404)
    if mode not in ("demo", "live", "unconfigured"):
        raise CommerceError("Mode must be demo, live or unconfigured.", "INVALID_MODE")

    if mode == "live":
        # Honest refusal: no verified provider agreement/credentials exist.
        get_adapter(provider, "live")

    webhook_secret = secrets.token_urlsafe(24)
    status = "DEMO" if mode == "demo" else "NOT_CONFIGURED"
    existing = await db.fetchval(
        "select id from qc_providers where store_id=$1 and provider=$2", store_id, provider
    )
    if existing:
        await db.execute(
            """
            update qc_providers set mode=$3, status=$4,
                   config = case when $3 = 'demo'
                       then jsonb_set(coalesce(config, '{}'::jsonb), '{webhook_secret}',
                                      to_jsonb($5::text), true)
                       else config end,
                connected_account=$6, last_error=null, updated_at=now()
            where id=$1 and store_id=$2
            """,
            existing, store_id, mode, status, webhook_secret, connected_account,
        )
        pid = existing
    else:
        pid = await db.fetchval(
            """
            insert into qc_providers (store_id, provider, mode, status, config, connected_account)
            values ($1, $2, $3, $4,
                    case when $3 = 'demo'
                        then jsonb_build_object('webhook_secret', $5::text)
                        else '{}'::jsonb end,
                    $6)
            returning id
            """,
            store_id, provider, mode, status, webhook_secret, connected_account,
        )
    await db.execute(
        "insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, "
        "new_state, message) values ($1, null, 'QC_PROVIDER_CONFIGURED', 'qc_provider', $2, $3::jsonb, $4)",
        store_id, str(pid), json.dumps({"provider": provider, "mode": mode, "status": status}),
        f"{provider} integration set to {status}",
    )
    row = await db.fetchrow(
        "select id, provider, status, mode, connected_account from qc_providers where id=$1", pid
    )
    d = dict(row)  # type: ignore[arg-type]
    if mode == "demo":
        d["note"] = "DEMO / SIMULATED — exercises internal order pipeline; no external API is called."
    return d


async def sync_catalog(store_id: str, provider: str, user_id: Optional[str]) -> dict[str, Any]:
    """Map internal products to provider listings (Part 15).

    DEMO mode: creates/refreshes qc_listings for every active product with
    deterministic demo external ids and marks them SYNCED (labelled demo).
    Live mode: requires a real adapter — refused honestly when absent.
    """
    prow = await db.fetchrow(
        "select * from qc_providers where store_id=$1 and provider=$2", store_id, provider
    )
    if not prow or prow["mode"] == "unconfigured":
        raise CommerceError(f"{provider} is not configured.", "NOT_CONFIGURED", 409)

    products = await db.fetch(
        "select id, sku, selling_price from products where store_id=$1 and is_active order by name",
        store_id,
    )
    adapter = get_adapter(provider, prow["mode"])
    await adapter.sync_catalog(store_id, [dict(p) for p in products])

    synced = errored = 0
    for p in products:
        # Price integrity for the QUICK_COMMERCE channel: never below the
        # policy floor; products violating it are skipped with an error note.
        floor_row = await db.fetchrow(
            "select min_acceptable_price($1, $2) as floor", store_id, p["id"]
        )
        floor = float(floor_row["floor"]) if floor_row and floor_row["floor"] is not None else 0.0
        price = float(p["selling_price"])
        if price < floor:
            await db.execute(
                """
                insert into qc_listings (store_id, provider_id, product_id, internal_sku,
                                         external_product_id, sync_status, last_error, last_synced_at)
                values ($1, $2, $3, $4, $5, 'ERROR', $6, now())
                on conflict (provider_id, product_id) do update
                  set sync_status='ERROR', last_error=excluded.last_error, last_synced_at=now()
                """,
                store_id, prow["id"], p["id"], p["sku"],
                f"{provider.lower()}-{str(p['id'])[:8]}",
                f"Selling price ₹{price:g} is below the minimum acceptable ₹{floor:g}; "
                "not listed until the price is corrected.",
            )
            errored += 1
            continue
        await db.execute(
            """
            insert into channel_prices (store_id, product_id, channel, price)
            values ($1, $2, 'QUICK_COMMERCE', $3)
            on conflict (store_id, product_id, channel)
            do update set price = excluded.price, active = true, updated_at = now()
            """,
            store_id, p["id"], price,
        )
        await db.execute(
            """
            insert into qc_listings (store_id, provider_id, product_id, internal_sku,
                                     external_product_id, external_sku, sync_status, last_synced_at)
            values ($1, $2, $3, $4, $5, $6, 'SYNCED', now())
            on conflict (provider_id, product_id) do update
              set internal_sku=excluded.internal_sku,
                  external_product_id=excluded.external_product_id,
                  external_sku=excluded.external_sku,
                  sync_status='SYNCED', last_error=null, last_synced_at=now()
            """,
            store_id, prow["id"], p["id"], p["sku"],
            f"{provider.lower()}-{str(p['id'])[:8]}", p["sku"],
        )
        synced += 1

    await db.execute(
        "update qc_providers set last_sync_at=now(), last_error=null, updated_at=now() where id=$1",
        prow["id"],
    )
    await db.execute(
        "insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, "
        "new_state, message) values ($1, $2, 'QC_CATALOG_SYNCED', 'qc_provider', $3, $4::jsonb, $5)",
        store_id, user_id, str(prow["id"]),
        json.dumps({"provider": provider, "synced": synced, "errors": errored}),
        f"{provider} catalog sync: {synced} synced, {errored} errors",
    )
    return {"provider": provider, "synced": synced, "errors": errored, "mode": prow["mode"]}


async def sync_inventory(store_id: str, provider: str) -> dict[str, Any]:
    """Push current sellable stock as channel availability (demo adapter)."""
    prow = await db.fetchrow(
        "select * from qc_providers where store_id=$1 and provider=$2", store_id, provider
    )
    if not prow or prow["mode"] == "unconfigured":
        raise CommerceError(f"{provider} is not configured.", "NOT_CONFIGURED", 409)
    adapter = get_adapter(provider, prow["mode"])
    listings = await db.fetch(
        """
        select l.id, l.product_id, sellable_stock($1, l.product_id) as available
        from qc_listings l where l.provider_id=$2 and l.sync_status='SYNCED'
        """,
        store_id, prow["id"],
    )
    result = await adapter.update_inventory(store_id, [dict(l) for l in listings])
    await db.execute(
        "update qc_providers set last_sync_at=now(), updated_at=now() where id=$1", prow["id"]
    )
    return {"provider": provider, "listings": len(listings), "mode": prow["mode"], **result}


# ==================================================================
# Webhook ingestion (signature + dedup)
# ==================================================================
async def ingest_webhook(store_id: str, provider: str, signature: Optional[str],
                         raw_body: bytes) -> dict[str, Any]:
    prow = await db.fetchrow(
        "select * from qc_providers where store_id=$1 and provider=$2", store_id, provider
    )
    if not prow or prow["mode"] != "demo":
        raise CommerceError(
            f"{provider} is not accepting webhooks (provider must be in DEMO mode in this deployment).",
            "NOT_CONFIGURED", 404,
        )
    # asyncpg returns jsonb as str by default — decode before reading secrets
    config = prow["config"]
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except ValueError:
            config = {}
    secret = (config or {}).get("webhook_secret") if isinstance(config, dict) else None
    if not secret:
        raise CommerceError("Webhook secret missing for provider.", "NOT_CONFIGURED", 409)
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature.strip()):
        await db.execute(
            "insert into qc_events (store_id, provider, event_type, external_event_id, status, note) "
            "values ($1, $2, 'UNKNOWN', $3, 'REJECTED', 'invalid signature')",
            store_id, provider, f"unsigned-{uuid.uuid4().hex[:8]}",
        )
        raise CommerceError("Invalid webhook signature.", "INVALID_SIGNATURE", 401)

    try:
        payload = json.loads(raw_body.decode() or "{}")
    except ValueError:
        raise CommerceError("Webhook body is not valid JSON.", "INVALID_PAYLOAD", 400)
    event_type = payload.get("event_type") or "ORDER_CREATED"
    external_event_id = payload.get("event_id") or str(uuid.uuid4())

    # Idempotency: the same external event can only be processed once
    try:
        await db.execute(
            """
            insert into qc_events (store_id, provider, event_type, external_event_id, payload)
            values ($1, $2, $3, $4, $5::jsonb)
            """,
            store_id, provider, event_type, external_event_id,
            json.dumps(payload),
        )
    except Exception:  # unique violation -> duplicate delivery
        return {"processed": False, "duplicate": True, "event_id": external_event_id}

    if event_type == "ORDER_CREATED":
        from app.services import commerce as commerce_service
        cart = payload.get("items") or []
        result = await commerce_service.simulate_qc_order(
            store_id, provider, cart, external_order_id=payload.get("external_order_id"),
        )
        return {"processed": True, "duplicate": False, "order_id": str(result["order"]["id"]),
                "event_id": external_event_id}
    await db.execute(
        "update qc_events set status='REJECTED', note='unsupported event type' "
        "where store_id=$1 and provider=$2 and external_event_id=$3",
        store_id, provider, external_event_id,
    )
    return {"processed": False, "reason": f"unsupported event type {event_type}"}
