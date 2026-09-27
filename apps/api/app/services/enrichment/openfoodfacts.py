"""Open Food Facts — EXTERNAL product enrichment (never merchant truth).

License posture (recorded, not hidden):
  * OFF database: ODbL 1.0 — contributions must keep the shared/attributed
    provenance; we do NOT relicense or resell it as proprietary data.
  * Product images: CC BY-SA (and possibly other rights per image) — stored
    with attribution + source_url on product_images so the duty survives.
  * We cache responses server-side (documented 15 read req/min/IP for read
    endpoints) and never expose OFF as authoritative for price/stock/supplier.

API: v3 (`/api/v3/product/{code}.json`) — v2 is deprecated upstream.
Auth: none for reads; custom User-Agent required by upstream etiquette.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import httpx

from app.database import db

logger = logging.getLogger("uvicorn.error")

USER_AGENT = "KiranaSaathiAI/1.0 (enrichment; +https://kiranasaathi.example)"
BASE_URL = "https://world.openfoodfacts.org/api/v3/product/{code}.json"
PROVIDER = "openfoodfacts"
PROVIDER_VERSION = "v3"
# Attribution duty recorded on every row (§22)
LICENSE_NOTE = (
    "Open Food Facts data: ODbL 1.0 (database) / CC BY-SA (images); "
    "external enrichment only — verify before saving"
)
# Negative-cache TTL (product not found) vs positive TTL
NEG_TTL_S = 7 * 86400
POS_TTL_S = 30 * 86400
TIMEOUT_S = 8.0

# Normalized payload keys returned to the frontend
NORMALIZED_FIELDS = (
    "product_name",
    "brands",
    "quantity",
    "categories",
    "image_front_url",
    "image_url",
    "ingredients_text",
)


def _normalize(product: dict[str, Any]) -> dict[str, Any]:
    """Project OFF's large payload to the fields the form prefills."""
    out: dict[str, Any] = {"source": "openfoodfacts", "source_version": PROVIDER_VERSION}
    for key in NORMALIZED_FIELDS:
        val = product.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    # first category tag, de-prefixed ("en:Confectionary" → "Confectionary")
    cats = product.get("categories")
    if isinstance(cats, str) and cats:
        first = cats.split(",")[0].strip()
        if ":" in first:
            first = first.split(":", 1)[1]
        out["category_hint"] = first[:64]
    return out


async def _cached(barcode: str) -> Optional[dict[str, Any]]:
    row = await db.fetchrow(
        """
        select found, payload, retrieved_at, source_url
        from external_product_cache
        where provider=$1 and provider_version=$2 and barcode=$3
        """,
        PROVIDER,
        PROVIDER_VERSION,
        barcode,
    )
    if row is None:
        return None
    ttl = POS_TTL_S if row["found"] else NEG_TTL_S
    if time.time() - row["retrieved_at"].timestamp() > ttl:
        return None  # stale → refetch
    return {
        "cached": True,
        "found": row["found"],
        "product": row["payload"] if row["found"] else None,
        "source_url": row["source_url"],
        "license_note": LICENSE_NOTE,
    }


async def lookup(barcode: str) -> dict[str, Any]:
    """Barcode → enrichment payload. Cached; network errors degrade to
    `provider_unavailable` (never blocks the manual flow)."""
    code = barcode.strip()
    if not code.isdigit() or not (6 <= len(code) <= 14):
        return {"found": False, "error": "invalid_barcode", "product": None}

    hit = await _cached(code)
    if hit is not None:
        return hit

    url = BASE_URL.format(code=code)
    found = False
    payload: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            resp = await client.get(url)
        if resp.status_code == 200:
            body = resp.json()
            result_id = (body.get("result") or {}).get("id")
            product = body.get("product") or {}
            if result_id == "product_found" and product:
                found = True
                payload = _normalize(product)
        elif resp.status_code == 429:
            return {
                "found": False,
                "error": "provider_rate_limited",
                "product": None,
                "license_note": LICENSE_NOTE,
            }
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("OFF lookup failed for %s: %s", code, exc)
        return {
            "found": False,
            "error": "provider_unavailable",
            "product": None,
            "license_note": LICENSE_NOTE,
        }

    await db.execute(
        """
        insert into external_product_cache
          (provider, provider_version, barcode, found, payload, source_url, license_note)
        values ($1, $2, $3, $4, $5::jsonb, $6, $7)
        on conflict (provider, provider_version, barcode)
        do update set found=excluded.found, payload=excluded.payload,
                      retrieved_at=now(), source_url=excluded.source_url
        """,
        PROVIDER,
        PROVIDER_VERSION,
        code,
        found,
        json.dumps(payload),
        url,
        LICENSE_NOTE,
    )
    return {
        "cached": False,
        "found": found,
        "product": payload if found else None,
        "source_url": url,
        "license_note": LICENSE_NOTE,
    }
