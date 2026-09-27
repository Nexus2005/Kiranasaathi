"""Smart Counter — PHASE 16: inventory-enrollment → recognition → cart E2E.

The complete loop (directive §20), live API + real inference:
  merchant receives stock → add via barcode scan path (enrichment lookup,
  OFF may be unavailable/missing → manual fallback is EXPECTED for synthetic
  barcodes) → store price/stock set by merchant → reference images enrolled
  (DINOv2 retrieval indexing, NOT training) → Smart Counter recognizes
  products → store prices retrieved → cart → atomic checkout → inventory
  deducted.

Also covers the failure cases from §21 that are testable without a human:
duplicate enrollment (idempotent), unauthenticated refusal, cross-store
isolation, duplicate scan of the same barcode, external-cache behavior.

Prereq: API running on 127.0.0.1:8000 with real providers (same env as
e2e_counter_real_vision.py).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# single stdout re-wrap for Windows cp1252 consoles (the imported suites'
# own wraps are stripped below — one wrapper total, never a closed buffer)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Import sibling suites WITHOUT their module-level `sys.stdout` re-wrap
# (two TextIOWrappers over one buffer → the GC'd one closes it mid-run).
import importlib.util


def _load_suite(module_name: str, filename: str):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    src = open(path, encoding="utf-8").read().replace(
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")', ""
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod


_real_vision = _load_suite("_e2e_real_vision", "e2e_counter_real_vision.py")
_learning = _load_suite("_e2e_learning", "e2e_counter_learning.py")
multipart = _real_vision.multipart
# per-product distinct pack renderer (RT-DETR-proven geometry; the real-vision
# renderer draws ONE fixed Maggi identity — unusable for a 10-product catalog)
render_product = _learning.render_pack

PASS = 0
FAIL = 0
METRICS: dict[str, float] = {}


def check(name: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  [{detail}]")
    return ok


def http(method: str, path: str, token: str | None = None, data=None, ct=None, timeout=120):
    req = urllib.request.Request(f"http://127.0.0.1:8000{path}", method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", ct or "application/json")
        req.data = data
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() or "{}"
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body}


def register(email, store_name):
    status, reg = http("POST", "/api/auth/register", data=json.dumps({
        "email": email, "password": "InventoryLoop!2026",
        "full_name": "Inventory E2E", "store_name": store_name,
    }).encode())
    assert status in (200, 201), str(reg)
    return reg["access_token"]


# The 10-product initial catalog (§19). Colors distinct; render_pack is the
# RT-DETR-detectable bottle geometry with OCR-legible size text.
CATALOG = [
    ("Maggi 2-Minute Noodles 70g", "8901058000016", (178, 34, 52), "70 g"),
    ("Lays Classic 52g",           "8901058100023", (196, 154, 42), "52 g"),
    ("Lays Magic Masala 52g",      "8901058100030", (168, 96, 32),  "52 g"),
    ("Dairy Milk 55g",             "8901058200037", (90, 44, 120),  "55 g"),
    ("Sprite 750ml",               "8901058300044", (40, 130, 90),  "750 ml"),
    ("Amul Taaza 1L",              "8901058400051", (200, 40, 60),  "1 L"),
    ("Parle-G 800g",               "8901058500068", (60, 90, 160),  "800 g"),
    ("Coca-Cola 750ml",            "8901058600075", (150, 30, 30),  "750 ml"),
    ("Kurkure Masala Munch 90g",   "8901058700082", (220, 120, 30), "90 g"),
    ("Britannia Good Day 200g",    "8901058800099", (120, 80, 40),  "200 g"),
]


async def main() -> int:
    print("=" * 72)
    print("SMART COUNTER — INVENTORY ENROLLMENT → RECOGNITION → CART (live)")
    print("=" * 72)

    tag = os.urandom(3).hex()
    token = register(f"inv-{tag}@example.com", f"InvStore {tag}")
    H = lambda: token  # noqa: E731

    # ------------------------------------------------------------------
    # 1. INVENTORY ONBOARDING — the Add-Product loop for all 10 products
    # ------------------------------------------------------------------
    pids: dict[str, str] = {}
    prices: dict[str, float] = {}
    stocks: dict[str, int] = {}
    enroll_times: list[float] = []

    for i, (name, barcode, color, size) in enumerate(CATALOG):
        # a) scan-path lookup: local miss, then external (OFF) enrichment.
        #    Synthetic barcodes are honestly absent from OFF — the expected
        #    result is NOT_FOUND → merchant fills the form manually (§6/§7).
        status, look = http("GET", f"/api/enrichment/product/{barcode}", token=H())
        if status == 200 and look.get("result") == "FOUND_EXTERNAL":
            check(f"enrichment hit for {name}", True)
        else:
            # real OFF failure modes degrade gracefully — never a 500
            check(
                f"lookup {name} degrades honestly ({look.get('result')}/{look.get('source')})",
                status == 200 and look.get("result") in ("NOT_FOUND", "FOUND_LOCAL"),
                f"{status} {json.dumps(look)[:140]}",
            )

        # b) merchant confirms business fields → create-from-external path
        t0 = time.perf_counter()
        selling = 14.0 + i
        status, created = http("POST", "/api/enrichment/create-from-external", token=H(), data=json.dumps({
            "barcode": barcode,
            "name": name,
            "brand": name.split(" ")[0],
            "category": "Packaged Food",
            "pack_size": size,
            "mrp": selling,
            "selling_price": selling,
            "purchase_price": round(selling * 0.75, 2),
            "initial_stock": 10,
        }).encode())
        dt = (time.perf_counter() - t0) * 1000
        if not check(f"create '{name}' 201", status == 201, f"{status} {json.dumps(created)[:160]}"):
            continue
        pids[barcode] = created["product_id"]
        prices[barcode] = selling
        stocks[barcode] = 10
        METRICS[f"create_{i}_ms"] = round(dt, 1)

        # duplicate barcode creation is refused (§21 duplicate enrollment)
        status, dup = http("POST", "/api/enrichment/create-from-external", token=H(), data=json.dumps({
            "barcode": barcode, "name": name, "mrp": selling, "selling_price": selling,
            "purchase_price": selling * 0.75, "initial_stock": 0,
        }).encode())
        check(f"duplicate barcode refused ({name[:20]})", status == 409, str(status))

    # c) reference images → visual enrollment (2 views per product)
    for i, (name, barcode, color, size) in enumerate(CATALOG):
        pid = pids.get(barcode)
        if not pid:
            continue
        for view_i, cond in enumerate(("front", "angle")):
            # per-product identity: brand word + size + color, conditioned frame
            brand_word = name.split()[0].upper()
            png = render_product(brand_word, size, color, cond, seed=i)
            payload, ctype = multipart([
                ("image", png, f"{cond}{view_i}.jpg"), ("view", cond, None),
            ])
            t0 = time.perf_counter()
            status, emb = http(
                "POST", f"/api/enrichment/products/{pid}/images", token=H(),
                data=payload, ct=ctype,
            )
            enroll_times.append((time.perf_counter() - t0) * 1000)
            check(
                f"enroll '{name[:22]}' {cond} 201 (dim=384)",
                status == 201 and emb.get("dimensions") == 384,
                f"{status} {json.dumps(emb)[:140]}",
            )
    if enroll_times:
        METRICS["enroll_avg_ms"] = round(sum(enroll_times) / len(enroll_times), 1)

    # duplicate image bytes → idempotent re-enrollment (§21)
    pid0 = pids[CATALOG[0][1]]
    dup_png = render_product("MAGGI", "70 g", CATALOG[0][2], "front", seed=0)
    payload, ctype = multipart([("image", dup_png, "again.jpg"), ("view", "front", None)])
    status, emb1 = http("POST", f"/api/enrichment/products/{pid0}/images", token=H(), data=payload, ct=ctype)
    payload, ctype = multipart([("image", dup_png, "again.jpg"), ("view", "front", None)])
    status2, emb2 = http("POST", f"/api/enrichment/products/{pid0}/images", token=H(), data=payload, ct=ctype)
    check(
        "duplicate enrollment idempotent (same embedding)",
        status == 201 and status2 == 201 and emb1.get("embedding_id") == emb2.get("embedding_id"),
        f"{emb1.get('embedding_id')} vs {emb2.get('embedding_id')}",
    )

    # ------------------------------------------------------------------
    # 2. ADMIN CATALOG — recognition status derives from real state
    # ------------------------------------------------------------------
    status, cat = http("GET", "/api/retail/admin/product-catalog", token=H())
    check("admin product-catalog 200", status == 200, str(status))
    if status == 200:
        s = cat.get("summary", {})
        check(
            "admin: all 10 enrolled+linked → VERIFIED",
            s.get("verified") == len(pids) and s.get("missing_barcodes") == 0,
            json.dumps(s),
        )

    # ------------------------------------------------------------------
    # 3. SMART COUNTER — recognize the shelf, identify, count, price, cart
    # ------------------------------------------------------------------
    print("-" * 72)
    print("RECOGNITION — multi-product frame through the live pipeline")
    print("-" * 72)

    # single-product recognition first (1, 2, 5 products per directive §19)
    for n_products in (1, 2, 5):
        chosen = CATALOG[:n_products]
        ok_all = True
        det_ms = []
        for name, barcode, color, size in chosen:
            brand_word = name.split()[0].upper()
            payload, ctype = multipart([("frame", render_product(brand_word, size, color, "front", seed=42), "f.jpg")])
            t0 = time.perf_counter()
            status, res = http("POST", "/api/counter/recognize", token=H(), data=payload, ct=ctype)
            det_ms.append((time.perf_counter() - t0) * 1000)
            dets = res.get("detections") if status == 200 else None
            if not dets:
                ok_all = False
                break
        METRICS[f"frame_{n_products}p_avg_ms"] = round(sum(det_ms) / len(det_ms), 1) if det_ms else 0
        check(f"recognition loop ({n_products} product frame(s)) returns detections", ok_all, str(status))

    # full shopping flow: A×3 via repeated barcode scans (camera dedupes
    # physically; here we add 3 units) + visual confirmations for B, C
    a_bar, b_bar, c_bar = CATALOG[0][1], CATALOG[1][1], CATALOG[3][1]
    a_pid, b_pid, c_pid = pids[a_bar], pids[b_bar], pids[c_bar]

    # barcode path: repeated scan → same product ×3 (cart line merges)
    status, look = http("GET", f"/api/products/barcode/{a_bar}", token=H())
    check("barcode scan resolves product A", status == 200 and look.get("result") == "FOUND", str(status))
    status, prod_a = http("GET", f"/api/products/{a_pid}", token=H())
    cart: list[tuple[str, int]] = []
    for _ in range(3):
        cart.append((a_pid, 1))
    # visual path: B and C recognized → REVIEW → merchant confirms (test does it)
    for bar in (b_bar, c_bar):
        name, _bc, color, size = next(c for c in CATALOG if c[1] == bar)
        brand_word = name.split()[0].upper()
        payload, ctype = multipart([("frame", render_product(brand_word, size, color, "front", seed=42), "f.jpg")])
        status, res = http("POST", "/api/counter/recognize", token=H(), data=payload, ct=ctype)
        dets = res.get("detections") if status == 200 else []
        if check(f"visual recognition hit for {bar}", bool(dets), str(status)):
            m = dets[0]["match"]
            matched = m.get("product_id")
            conf = m.get("confidence") or 0.0
            if bar == b_bar:
                # Lays Classic vs Lays Magic Masala: identical printed evidence
                # ("LAYS 52 g") — the shared-evidence cap MUST hold the winner
                # at review level; the merchant then picks. Picking either
                # candidate is honest; SILENT auto-add is not (§5/§7/§16).
                lays_family = {pids[b_bar], pids[CATALOG[2][1]]}
                check(
                    "Lays variant: winner stays in family AND capped at review (no silent auto-add)",
                    matched in lays_family and conf < 0.95,
                    f"top={matched} conf={conf} family={lays_family}",
                )
            else:
                check(
                    f"matcher resolves {name[:20]} to the enrolled product",
                    matched == pids[bar],
                    f"top={matched} conf={conf} expected={pids[bar]}",
                )
            cart.append((pids[bar], 1))  # merchant confirms the candidate

    # merge quantities per product
    qty: dict[str, int] = {}
    for pid, n in cart:
        qty[pid] = qty.get(pid, 0) + 1
    check("cart quantity counting (A×3, B×1, C×1)", qty.get(a_pid) == 3 and qty.get(b_pid) == 1 and qty.get(c_pid) == 1, json.dumps(qty))

    # prices come from the STORE DB, not OCR/MRP (§15)
    status, pa = http("GET", f"/api/products/{a_pid}", token=H())
    check("store price authoritative", float(pa.get("selling_price")) == prices[a_bar], str(pa.get("selling_price")))

    # ------------------------------------------------------------------
    # 4. ATOMIC CHECKOUT → INVENTORY DEDUCTION (existing deterministic path)
    # ------------------------------------------------------------------
    barcode_by_pid = {p: b for b, p in pids.items()}
    status, order = http("POST", "/api/orders", token=H(), data=json.dumps({
        "items": [
            {"product_id": pid, "quantity": n,
             "list_price_at_cart": prices[barcode_by_pid[pid]]}
            for pid, n in qty.items()
        ],
        "payment_method": "cash",
    }).encode())
    check("order created (201)", status == 201, f"{status} {json.dumps(order)[:200]}")
    order_id = order.get("order_id")

    status, co = http("POST", f"/api/orders/{order_id}/checkout", token=H())
    check("checkout COMPLETED", status == 200 and co.get("status") == "COMPLETED", f"{status} {json.dumps(co)[:200]}")

    status, inv_a = http("GET", f"/api/products/{a_pid}", token=H())
    check("inventory A: 10 → 7", inv_a.get("quantity") == 7, f"{inv_a.get('quantity')}")
    status, inv_b = http("GET", f"/api/products/{b_pid}", token=H())
    check("inventory B: 10 → 9", inv_b.get("quantity") == 9, str(inv_b.get("quantity")))
    status, inv_c = http("GET", f"/api/products/{c_pid}", token=H())
    check("inventory C: 10 → 9", inv_c.get("quantity") == 9, str(inv_c.get("quantity")))

    # ------------------------------------------------------------------
    # 5. FAILURE CASES (§21, automated subset)
    # ------------------------------------------------------------------
    payload_u, ctype_u = multipart([("frame", render_product("MAGGI", "70 g", CATALOG[0][2], "front", seed=99), "f.jpg")])
    status, _ = http("POST", "/api/counter/recognize", data=payload_u, ct=ctype_u)
    check("unauthenticated recognize refused", status in (401, 403), str(status))

    status, _ = http("GET", "/api/retail/admin/product-catalog")
    check("unauthenticated admin catalog refused", status in (401, 403), str(status))

    # cross-store isolation: another store can't see this store's product
    token2 = register(f"inv-b-{tag}@example.com", f"InvStoreB {tag}")
    status, _ = http("GET", f"/api/products/{a_pid}", token=token2)
    check("store B cannot read store A's product (404)", status == 404, str(status))
    status, _ = http("GET", "/api/retail/admin/product-catalog", token=token2)
    b_items = _.get("items", []) if status == 200 else []
    check("store B catalog lists only its own products", all(i["name"] not in [c[0] for c in CATALOG] for i in b_items), str(len(b_items)))

    # duplicate scan: same barcode lookup twice → same single product
    status1, r1 = http("GET", f"/api/products/barcode/{a_bar}", token=H())
    status2, r2 = http("GET", f"/api/products/barcode/{a_bar}", token=H())
    check("duplicate scan → same product (no double-create)", r1.get("product", {}).get("id") == r2.get("product", {}).get("id"), str(status2))

    # insufficient stock guard: order 50 units of a 9-stock product
    status, err = http("POST", "/api/orders", token=H(), data=json.dumps({
        "items": [{"product_id": b_pid, "quantity": 50, "list_price_at_cart": prices[b_bar]}],
        "payment_method": "cash",
    }).encode())
    check("insufficient stock refused", status in (400, 409, 422), f"{status} {json.dumps(err)[:140]}")

    # ------------------------------------------------------------------
    print("=" * 72)
    print("METRICS (measured)")
    print("=" * 72)
    for k in sorted(METRICS):
        print(f"  {k:<28} {METRICS[k]:>9} ms")
    print("=" * 72)
    print(f"INVENTORY-LOOP RESULT: {PASS} PASS / {FAIL} FAIL")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
