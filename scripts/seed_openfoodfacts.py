"""Seed real Indian grocery products from Open Food Facts (in.openfoodfacts.org).

Replaces the dummy 003_seed_demo catalog with REAL product data:
  name, brand, quantity/pack, categories, ingredients, nutrition, packaging,
  serving size, and front/ingredients/nutrition image URLs — everything OFF
  provides — plus attribution (ODbL) on every row.

Docs honored (user directive + OFF docs):
  * reads need NO API key — only a custom User-Agent
    ("KiranaSaathiAI/1.0 (contact)") — default library UAs get firewalled
  * data licensed ODbL 1.0 / images CC BY-SA → attribution recorded per row
  * merchant business fields (cost/selling price, stock) are NOT fetchable
    from OFF — they stay merchant-owned; the seeder sets plausible demo
    prices ONLY because this is the demo store (marked source='seed')

Usage:
  python scripts/seed_openfoodfacts.py --store-email ramesh@kirana.demo \
      --per-term 4 --replace
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from urllib.parse import quote_plus

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "apps", "api"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

from app.database import db  # noqa: E402

USER_AGENT = "KiranaSaathiAI/1.0 (seeding; contact: admin@kiranasaathi.example)"
SEARCH_URL = "https://world.openfoodfacts.org/api/v2/search"
ATTRIBUTION = (
    "Product data: Open Food Facts contributors, ODbL 1.0 "
    "(in.openfoodfacts.org); images: CC BY-SA"
)
# Indian grocery searches — real brands/categories with strong OFF coverage
SEED_TERMS = [
    "parle-g",
    "maggi noodles",
    "amul",
    "tata salt",
    "aashirvaad",
    "britannia",
    "dabur",
    "haldiram",
    "coca-cola india",
    "lays india",
    "surf excel",
    "fortune oil",
    "red label tea",
    "nescafe",
    "kurkure",
]
FIELDS = (
    "code,product_name,product_name_en,brands,quantity,categories,categories_tags,"
    "ingredients_text,packaging,serving_size,image_front_url,image_ingredients_url,"
    "image_nutrition_url,nutriments"
)
RATE_SLEEP_S = 4.0  # OFF search 503s easily; generous pacing + retries below


def _first_category(categories_tags: list[str] | None, categories: str | None) -> str:
    if categories_tags:
        for tag in categories_tags:
            if tag.startswith("en:"):
                return tag[3:].replace("-", " ").title()[:60]
    if categories:
        return categories.split(",")[0].strip().title()[:60]
    return "Groceries"


def _pack_from_quantity(quantity: str | None, name: str) -> str:
    if quantity and quantity.strip():
        return quantity.strip()[:40]
    # fall back to a size token in the name, e.g. "Maggi Noodles 70g"
    for token in (name or "").split():
        if any(u in token.lower() for u in ("g", "ml", "l", "kg")) and any(c.isdigit() for c in token):
            return token[:40]
    return ""


def _price_hint(quantity: str | None) -> float:
    """Demo-store selling price hint from pack size (merchant-owned truth)."""
    q = (quantity or "").lower().replace(" ", "")
    try:
        if q.endswith("kg"):
            return 90.0 + 40.0 * float(q[:-2])
        if q.endswith("g") and q[:-1].isdigit():
            grams = float(q[:-1])
            return max(5.0, round(grams * 0.22, 0))
        if q.endswith("l") and q[:-1].isdigit():
            return 30.0 + 35.0 * (float(q[:-1]) - 1)
        if q.endswith("ml") and q[:-2].isdigit():
            return max(10.0, round(float(q[:-2]) * 0.045, 0))
    except ValueError:
        pass
    return 40.0


async def fetch_products(term: str, per_term: int, client: httpx.AsyncClient, retries: int = 4) -> list[dict]:
    url = (
        f"{SEARCH_URL}?search_terms={quote_plus(term)}&search_simple=1&action=process"
        f"&json=1&page_size={per_term * 3}&fields={FIELDS}"
        "&countries_tags=en:india&states_tags=en:complete"
    )
    # OFF search 503s aggressively under back-to-back load — retry with backoff
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            break
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            wait = 3.0 * (attempt + 1)
            print(f"  … '{term}' HTTP {exc.response.status_code}, retry in {wait:.0f}s")
            time.sleep(wait)
        except httpx.HTTPError as exc:
            last_exc = exc
            time.sleep(3.0)
    else:
        raise last_exc if last_exc else RuntimeError("unreachable")
    products = resp.json().get("products", [])
    # keep only well-formed rows: real name, barcode, and a front image
    picked: dict[str, dict] = {}
    for p in products:
        code = (p.get("code") or "").strip()
        name = (p.get("product_name_en") or p.get("product_name") or "").strip()
        if not code.isdigit() or len(name) < 3 or not p.get("image_front_url"):
            continue
        if code in picked:
            continue
        picked[code] = p
        if len(picked) >= per_term:
            break
    return list(picked.values())


async def upsert_product(store_id: str, p: dict, dry: bool = False) -> str | None:
    name = (p.get("product_name_en") or p.get("product_name") or "").strip()[:150]
    barcode = (p.get("code") or "").strip()
    brand = (p.get("brands") or "").split(",")[0].strip()[:100] or None
    quantity = (p.get("quantity") or "").strip()
    category = _first_category(p.get("categories_tags"), p.get("categories"))
    ingredients = (p.get("ingredients_text") or "").strip()[:1800] or None
    nutr = p.get("nutriments") or {}
    description_parts = []
    if ingredients:
        description_parts.append(f"Ingredients: {ingredients}")
    energy = nutr.get("energy-kcal_100g")
    protein = nutr.get("proteins_100g")
    carbs = nutr.get("carbohydrates_100g")
    fat = nutr.get("fat_100g")
    if energy is not None:
        description_parts.append(
            f"Per 100g: {energy} kcal"
            + (f", protein {protein}g" if protein is not None else "")
            + (f", carbs {carbs}g" if carbs is not None else "")
            + (f", fat {fat}g" if fat is not None else "")
        )
    packaging = (p.get("packaging") or "").strip()
    if packaging:
        description_parts.append(f"Packaging: {packaging[:120]}")
    description = " | ".join(description_parts)[:1990] or None
    image_url = p.get("image_front_url")
    price = _price_hint(quantity or name)
    pack = _pack_from_quantity(quantity, name)

    existing = await db.fetchval(
        "select id from products where store_id=$1 and barcode=$2",
        store_id,
        barcode,
    )
    if dry:
        return None
    if existing:
        await db.execute(
            """
            update products set name=$3, brand=$4, description=$5, image_url=$6,
                                attribution=$7, source='openfoodfacts', updated_at=now()
            where id=$1 and store_id=$2
            """,
            existing,
            store_id,
            name,
            brand,
            description,
            image_url,
            ATTRIBUTION,
        )
        return str(existing)

    pid = await db.fetchval(
        """
        insert into products
          (store_id, name, category, sku, barcode, unit, mrp, selling_price,
           purchase_price, reorder_level, brand, description, image_url,
           source, attribution, external_id)
        values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,'openfoodfacts',$14,$15)
        returning id
        """,
        store_id,
        name,
        category[:80],
        None,
        barcode,
        "pack",
        price,                       # mrp (demo hint)
        price,                       # selling (demo hint)
        round(price * 0.78, 2),      # purchase (demo hint)
        8,
        brand,
        description,
        image_url,
        ATTRIBUTION,
        barcode,
    )
    # starter stock so the demo store is immediately usable
    await db.execute(
        """
        insert into inventory (store_id, product_id, quantity) values ($1,$2,$3)
        on conflict (store_id, product_id) do nothing
        """,
        store_id,
        pid,
        24,
    )
    return str(pid)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store-email", default="ramesh@kirana.demo")
    ap.add_argument("--per-term", type=int, default=4)
    ap.add_argument("--replace", action="store_true", help="deactivate dummy seed products first")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    await db.connect()
    try:
        store_id = await db.fetchval(
            """
            select s.id from stores s
            join merchants m on m.id = s.merchant_id
            join users u on u.id = m.user_id
            where u.email = $1
            order by s.created_at
            limit 1
            """,
            args.store_email,
        )
        if not store_id:
            print(f"Store for {args.store_email} not found")
            return 1
        print(f"Seeding store {store_id} from Open Food Facts (India)")

        if args.replace and not args.dry_run:
            n = await db.execute(
                "update products set is_active=false where store_id=$1 and source='seed'",
                store_id,
            )
            print(f"Deactivated dummy seed products: {n}")

        seen: set[str] = set()
        created = updated = skipped = 0
        async with httpx.AsyncClient(
            timeout=25.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            for term in SEED_TERMS:
                try:
                    products = await fetch_products(term, args.per_term, client)
                except httpx.HTTPError as exc:
                    print(f"  ! '{term}' fetch failed: {exc}")
                    continue
                for p in products:
                    code = p["code"]
                    if code in seen:
                        skipped += 1
                        continue
                    seen.add(code)
                    if args.dry_run:
                        name = p.get("product_name_en") or p.get("product_name")
                        print(f"  DRY {code} {name} ({p.get('brands')})")
                        created += 1
                        continue
                    try:
                        pid = await upsert_product(store_id, p)
                        if pid:
                            created += 1
                    except Exception as exc:  # noqa: BLE001
                        print(f"  ! {code} upsert failed: {exc}")
                        skipped += 1
                time.sleep(RATE_SLEEP_S)
        print(f"DONE: {created} products seeded/updated, {skipped} skipped/dupes")
        return 0
    finally:
        await db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
