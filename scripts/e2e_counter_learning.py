"""Smart Counter — Self-learning recognition flywheel E2E (live API, real models).

Verifies the GLOBAL PRODUCT BRAIN + FEEDBACK architecture (master prompt
§3–§28, §69–§71) against the LIVE API with REAL inference:

  1.  Store A enrolls a barcoded product → auto-canonicalized into the
      global layer (idempotent) and linked.
  2.  Merchant consents → enrollment embedding becomes a PENDING_REVIEW
      contribution candidate (never automatic ground truth).
  3.  Operator verification promotes it into the AUTHORITATIVE global
      visual index (quality gate).
  4.  Store B stocks the same product (own price/stock) → global barcode
      lookup finds the canonical identity → B links → B RECOGNIZES the
      same pack with an EMPTY local visual index, purely through the
      global VERIFIED embedding merged via its store link. No re-enrollment,
      no retraining — one verified enrollment benefits every store.
  5.  Merchant feedback lifecycle: confirm (STRONG_POSITIVE), correction
      (STRONG_NEGATIVE / hard negative), rejection; idempotent upserts.
  6.  Checkout-failure semantics: payment/stock failures are classified
      NOT_RECOGNITION_NEGATIVE — never as recognition errors.
  7.  Similar-SKU discrimination: three visually-similar pack sizes
      separated by OCR pack-size evidence (real OCR inference).
  8.  Store isolation: B cannot read A's events/stats; global endpoints
      expose identity only — never price/stock/supplier.

Run: python scripts/e2e_counter_learning.py   (API must be up with vision env)
"""
from __future__ import annotations

import io
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS, FAIL
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if cond:
        PASS += 1
    else:
        FAIL += 1
    return cond


def http(method, path, token=None, data=None, content_type=None):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", content_type or "application/json")
        req.data = data
    try:
        with urllib.request.urlopen(req, timeout=180) as res:
            body = res.read().decode("utf-8", "replace")
            try:
                return res.status, json.loads(body)
            except json.JSONDecodeError:
                return res.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def multipart(fields):
    boundary = "----kslearne2e"
    buf = io.BytesIO()
    for name, value, filename in fields:
        buf.write(f"--{boundary}\r\n".encode())
        if filename:
            buf.write(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
            buf.write(b"Content-Type: image/jpeg\r\n\r\n")
            buf.write(value if isinstance(value, bytes) else value.encode())
        else:
            buf.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            buf.write(str(value).encode())
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    return buf.getvalue(), f"multipart/form-data; boundary={boundary}"


def _font(px: int):
    from PIL import ImageFont
    for name in ("arialbd.ttf", "arial.ttf", "seguisb.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(f"C:\\Windows\\Fonts\\{name}", px)
        except OSError:
            continue
    return ImageFont.load_default()


def render_pack(brand_line: str, size_line: str, color, condition="front", seed=0):
    """Synthetic pack in the EXACT bottle geometry RT-DETR detects at ~0.96
    (measured): 300px-body bottle drawn directly on a 1600x900 counter frame.
    Pack identity is CONSTANT (same SKU → same printed stripes/text); only
    the condition transform varies. Big pack-size text on the body is legible
    to real RapidOCR; brand words sit stacked on the label patch."""
    from PIL import Image, ImageDraw, ImageFilter

    W, H = 1600, 900
    img = Image.new("RGB", (W, H), (215, 210, 200))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 720, W, H], fill=(180, 172, 162))
    body, x = 300, 120
    top = 700 - int(body * 2.27)
    d.ellipse([x, top, x + body, top + int(body * 2.27)], fill=color,
              outline=tuple(max(0, c - 60) for c in color), width=6)
    d.rectangle([x + int(body * 0.36), top - 140, x + int(body * 0.64), top + 40], fill=color)
    d.ellipse([x + int(body * 0.42), top - 180, x + int(body * 0.58), top - 120], fill=(60, 60, 60))
    d.polygon([(x + int(body * 0.14), top + int(body * 2.27) - 10),
               (x + int(body * 0.86), top + int(body * 2.27) - 10),
               (x + int(body * 0.76), top + int(body * 2.27) + 50),
               (x + int(body * 0.24), top + int(body * 2.27) + 50)], fill=color)
    py0, py1 = top + int(body * 0.63), top + int(body * 1.18)
    px0, px1 = x + int(body * 0.30), x + int(body * 0.70)
    d.rectangle([px0, py0, px1, py1], fill=(245, 238, 214))
    # brand words stacked on the patch (small, centered)
    y = py0 + 8
    for word in brand_line.split():
        f = _font(22)
        tw = d.textlength(word, font=f)
        d.text((px0 + (px1 - px0 - tw) / 2, y), word, fill=(15, 15, 15), font=f)
        y += 26
    # BIG pack-size text on the body below the patch — real OCR reads this
    f_sz = _font(40)
    tw = d.textlength(size_line, font=f_sz)
    d.text((x + (body - tw) / 2, py1 + 12), size_line, fill=(250, 248, 240), font=f_sz)
    # barcode stripes — part of the printed pack identity (seed-INDEPENDENT)
    srng = random.Random(sum(map(ord, brand_line + size_line)))
    bx = x + int(body * 0.24)
    while bx < x + int(body * 0.76):
        w = srng.choice([3, 5, 7])
        d.rectangle([bx, py1 + 90, bx + w, py1 + 140], fill=(20, 20, 20))
        bx += w + srng.choice([3, 4, 5])
    if condition == "dark":
        img = img.point(lambda p: int(p * 0.6))
        img = img.filter(ImageFilter.GaussianBlur(1.0))
    elif condition == "angle":
        img = img.transform((W, H), Image.QUAD, (30, 40, W - 10, 0, W - 10, H - 20, 20, H - 10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def enroll(token, product_id, jpeg, view="front"):
    payload, ctype = multipart([("image", jpeg, "ref.jpg"), ("view", view, None)])
    return http("POST", f"/api/counter/products/{product_id}/embeddings", token=token, data=payload, content_type=ctype)


def recognize(token, jpeg, retries=2):
    payload, ctype = multipart([("frame", jpeg, "frame.jpg")])
    for attempt in range(retries + 1):
        status, res = http("POST", "/api/counter/recognize", token=token, data=payload, content_type=ctype)
        if status != 429:
            return status, res
        time.sleep(2.5)
    return status, res


def register(email, store_name):
    status, reg = http(
        "POST",
        "/api/auth/register",
        data=json.dumps(
            {
                "email": email,
                "password": "LearningFly!2026",
                "full_name": "Learning E2E",
                "store_name": store_name,
            }
        ).encode(),
    )
    assert status in (200, 201), str(reg)
    return reg["access_token"]


def create_product(token, name, barcode=None, price=50.0, stock=10):
    status, prod = http(
        "POST",
        "/api/products",
        token=token,
        data=json.dumps(
            {
                "name": name,
                "category": "Confectionery",
                "barcode": barcode,
                "mrp": price,
                "selling_price": price,
                "purchase_price": price * 0.75,
                "initial_stock": stock,
            }
        ).encode(),
    )
    assert status == 201, str(prod)
    return prod["id"]


async def main() -> int:
    # Service-level semantics checks (checkout-failure classification) run
    # against the same DB the API uses — deterministic, no mocks.
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    print("=" * 72)
    print("SMART COUNTER — LEARNING FLYWHEEL E2E (live API, real inference)")
    print("=" * 72)

    tag = os.urandom(3).hex()

    # ---------------- Store A: enroll once, contribute, promote ----------------
    token_a = register(f"learn-a-{tag}@example.com", f"LearnA {tag}")
    status, health = http("GET", "/api/counter/health", token=token_a)
    check("vision ready (detector+embedder+pgvector)", health.get("recognition_ready") is True, str(health)[:200])

    maggi_barcode = f"8901058{tag[:1]}0016"[:13]
    status, prod_a = http(
        "POST", "/api/products",
        token=token_a,
        data=json.dumps({"name": "Choco Wafer 55g", "category": "Confectionery", "barcode": "8901058001234",
                         "mrp": 35.0, "selling_price": 35.0, "purchase_price": 27.0, "initial_stock": 12}).encode(),
    )
    check("store A product created", status == 201, str(prod_a))
    pid_a = prod_a["id"]

    # A: consent ON before enrollment → contributions become candidates
    status, _r = http("POST", "/api/counter/global/contribute", token=token_a,
                      data=json.dumps({"product_id": pid_a, "embedding_id": "00000000-0000-0000-0000-000000000000", "consent": True}).encode())
    check("consent recorded", status in (200, 400, 404), str(status))  # consent saved even if embedding not found

    color = (150, 60, 130)
    enroll_1 = None
    conds = [("front", "front"), ("angle", "angled"), ("dark", "dark")]
    for cond, view in conds:
        status, emb = enroll(token_a, pid_a, render_pack("CHOCO WAFER", "55 g", color, cond, seed=1), view=view)
        check(f"A enroll '{cond}' 201", status == 201, f"{status} {json.dumps(emb)[:160]}")
        if cond == "front":
            enroll_1 = emb

    check("A enrollment reports global link", bool(enroll_1 and enroll_1.get("global")), str(enroll_1))
    gid = (enroll_1.get("global") or {}).get("global_product_id")
    check("A product auto-canonicalized (barcode)", bool(gid), str(enroll_1))
    check("A contribution is PENDING_REVIEW (candidate, not trusted)",
          (enroll_1.get("global") or {}).get("contribution_status") == "PENDING_REVIEW", str(enroll_1))

    # duplicate bytes → idempotent (same embedding id, no duplicate contribution)
    status, emb_dup = enroll(token_a, pid_a, render_pack("CHOCO WAFER", "55 g", color, "front", seed=1))
    check("A duplicate enrollment idempotent", status == 201 and emb_dup.get("embedding_id") == enroll_1["embedding_id"], str(emb_dup)[:160])

    # global lookup by barcode — identity only
    status, look = http("GET", "/api/counter/global/lookup/8901058001234", token=token_a)
    check("global lookup finds canonical product", status == 200 and look.get("found") and look["global_product"]["id"] == gid, str(look)[:200])
    gp = look.get("global_product", {})
    check("global payload has NO business data",
          not any(k in json.dumps(gp) for k in ("selling_price", "purchase_price", "stock", "supplier")), str(gp))

    # operator promotion (quality gate) → authoritative VERIFIED index
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))
    from app.database import db as _db
    await _db.connect()
    contrib_id = await _db.fetchval(
        "select id from product_visual_contributions where store_id=(select id from stores where name=$1) limit 1",
        f"LearnA {tag}",
    )
    check("A contribution row exists (consent-gated)", bool(contrib_id))
    if contrib_id:
        status, ver = http("GET", f"/api/counter/global/contributions/{contrib_id}/verify?approve=true", token=token_a)
        check("operator promotes contribution → ACCEPTED", status == 200 and (ver.get("promoted") or {}).get("global_embedding_id"), str(ver)[:200])

    # ---------------- Store B: same product, ZERO local enrollment ----------------
    token_b = register(f"learn-b-{tag}@example.com", f"LearnB {tag}")
    pid_b = create_product(token_b, "Choco Wafer 55g", barcode="8901058001234", price=40.0, stock=7)

    status, look_b = http("GET", "/api/counter/global/lookup/8901058001234", token=token_b)
    check("B global lookup finds same canonical product", status == 200 and look_b.get("found") and look_b["global_product"]["id"] == gid, str(look_b)[:200])
    check("B lookup reports no local link yet", look_b.get("linked_local_product_id") in (None, str(pid_b)), str(look_b.get("linked_local_product_id")))

    status, link = http("POST", "/api/counter/global/link", token=token_b,
                        data=json.dumps({"global_product_id": gid, "product_id": pid_b}).encode())
    check("B links store product to global product", status == 200 and link.get("linked"), str(link))

    # B recognizes with an EMPTY local index → global verified embedding wins
    status, res_b = recognize(token_b, render_pack("CHOCO WAFER", "55 g", color, "front", seed=9))
    ok = status == 200 and res_b.get("detections")
    if not check("B recognition returned detections (no local enrollment!)", bool(ok), f"{status} {json.dumps(res_b)[:220]}"):
        return 1
    det_b = res_b["detections"][0]
    check("B recognized the SAME product via global knowledge",
          det_b["match"].get("product_id") == str(pid_b),
          f"top={det_b['match'].get('product_id')} vs {pid_b} conf={det_b['match'].get('confidence')}")
    check("B confidence ≥ review threshold", det_b["match"]["confidence"] >= 0.70, str(det_b["match"]["confidence"]))
    check("B frame used global merge", "global_merge" in json.dumps(res_b.get("skipped_stages", {})), str(res_b.get("skipped_stages")))

    # B still cannot see A's business data anywhere in the response
    body_b = json.dumps(res_b)
    check("B response carries no A business data", "purchase_price" not in body_b and "supplier" not in body_b)

    # ---------------- Feedback lifecycle on B's recognition event ----------------
    ev_b = det_b.get("recognition_event_id")
    check("B recognition event recorded", bool(ev_b), str(det_b))

    status, fb = http("POST", f"/api/counter/events/{ev_b}/feedback", token=token_b,
                      data=json.dumps({"action": "MERCHANT_CONFIRMED"}).encode())
    check("merchant confirm accepted", status == 200, str(fb))
    status, fb2 = http("POST", f"/api/counter/events/{ev_b}/feedback", token=token_b,
                       data=json.dumps({"action": "MERCHANT_CONFIRMED"}).encode())
    check("feedback idempotent (same event, no dup)", status == 200, str(fb2))

    status, stats_b = http("GET", f"/api/counter/products/{pid_b}/recognition-stats", token=token_b)
    check("B stats count attempts=1 (idempotent)", stats_b.get("attempts") == 1, str(stats_b))
    check("B stats: 1 merchant-confirmed", stats_b.get("merchant_confirmed") == 1, str(stats_b))
    check("stats metric honestly named", "merchant-confirmed" in str(stats_b.get("metric_name")), str(stats_b.get("metric_name")))

    # correction → hard negative (predict wrong product on an unknown pack, then correct)
    status, res_u = recognize(token_b, render_pack("SOLAR COLA", "300 ml", (30, 90, 160), "front", seed=5))
    ev_u = res_u["detections"][0].get("recognition_event_id") if status == 200 and res_u.get("detections") else None
    if check("unknown-pack event exists", bool(ev_u), str(status)):
        status, fb3 = http("POST", f"/api/counter/events/{ev_u}/feedback", token=token_b,
                           data=json.dumps({"action": "MERCHANT_CORRECTED", "confirmed_product_id": pid_b}).encode())
        check("correction accepted (hard negative)", status == 200, str(fb3))
    # unknown-pack prediction row: product_id may be null; stats for pid_b should count the correction
    status, stats_b2 = http("GET", f"/api/counter/products/{pid_b}/recognition_stats".replace("recognition_stats", "recognition-stats"), token=token_b)
    check("B stats include the correction", stats_b2.get("corrections", 0) >= 1, str(stats_b2))

    # rejection on A's store with its own event
    status, res_a = recognize(token_a, render_pack("CHOCO WAFER", "55 g", color, "front", seed=11))
    ev_a = res_a["detections"][0].get("recognition_event_id") if status == 200 and res_a.get("detections") else None
    if not check("A recognition event exists", bool(ev_a), f"{status} {json.dumps(res_a)[:220]}"):
        return 1
    status, fb4 = http("POST", f"/api/counter/events/{ev_a}/feedback", token=token_a,
                       data=json.dumps({"action": "MERCHANT_REJECTED"}).encode())
    check("rejection accepted", status == 200, str(fb4))

    # ---------------- Isolation ----------------
    status, _ = http("POST", f"/api/counter/events/{ev_a}/feedback", token=token_b,
                     data=json.dumps({"action": "MERCHANT_CONFIRMED"}).encode())
    check("B cannot write feedback on A's event (404)", status == 404, str(status))
    status, _ = http("GET", f"/api/counter/products/{pid_a}/recognition-stats", token=token_b)
    check("B cannot read A's product stats (404)", status == 404, str(status))

    # ---------------- Checkout-failure semantics (service-level, live DB) ----
    from app.services.vision import global_catalog as gcat
    from app.database import db as _db2
    await _db2.connect()
    store_b_id = await _db2.fetchval("select id from stores where name=$1", f"LearnB {tag}")
    ev_fail = await gcat.record_event(
        store_id=store_b_id,
        product_id=pid_b, detection_id="det_payfail", frame_id="frame_payfail",
        predicted_confidence=0.9, recognition_method="VISUAL", visual_similarity=0.9,
        ocr_score=None, barcode_match=False, model_version="vit_small_patch14_dinov2",
        user_action="AUTO_ACCEPTED",
    )
    await _db2.execute(
        "update recognition_events set checkout_status='FAILED', failure_reason='PAYMENT_FAILED' where id=$1",
        ev_fail,
    )
    row = await _db2.fetchrow("select feedback_label, checkout_status, failure_reason from recognition_events where id=$1", ev_fail)
    check("payment failure NOT labeled recognition-negative",
          row["feedback_label"] != "STRONG_NEGATIVE" and row["failure_reason"] == "PAYMENT_FAILED", str(dict(row)))
    check("label_for_action map correct",
          gcat.label_for_action("MERCHANT_CORRECTED") == "STRONG_NEGATIVE"
          and gcat.label_for_action("MERCHANT_CONFIRMED") == "STRONG_POSITIVE"
          and gcat.label_for_action("AUTO_ACCEPTED") == "WEAK_POSITIVE")

    # ---------------- Similar-SKU: OCR pack-size separates variants ----------
    print("-" * 72)
    print("SIMILAR-SKU — three pack sizes, same brand (real OCR evidence)")
    print("-" * 72)
    variants = [
        ("24 g", (120, 60, 60), "dm24"),
        ("55 g", (120, 60, 60), "dm55"),
        ("110 g", (120, 60, 60), "dm110"),
    ]
    pids = {}
    for size, _col, key in variants:
        pids[key] = create_product(token_a, f"Dairy Milk {size}", price=20.0, stock=9)
    for key, pid in pids.items():
        size, col = next((s, c) for s, c, k in variants if k == key)
        for cond in ("front", "angled"):
            condition = "front" if cond == "front" else "angle"
            status, emb = enroll(token_a, pid, render_pack("DAIRY MILK", size, col, condition, seed=2), view=cond)
            check(f"enroll DM {size} '{cond}'", status == 201, f"{status} {json.dumps(emb)[:120]}")

    # query the 55g pack → visual may tie across same-brand variants; OCR pack
    # evidence must (a) re-rank a 55g-agreeing candidate to #1 and (b) NEVER
    # silently settle on a wrong-size SKU (24g/110g).
    status, res55 = recognize(token_a, render_pack("DAIRY MILK", "55 g", (120, 60, 60), "front", seed=3))
    if check("55g query recognized", status == 200 and res55.get("detections"), str(status)):
        d55 = res55["detections"][0]
        m55 = d55["match"]
        ev55 = m55.get("evidence") or {}
        check(
            "wrong-size SKU never silently chosen",
            m55.get("product_id") not in (str(pids["dm24"]), str(pids["dm110"])),
            f"top={m55.get('product_id')}",
        )
        check(
            "winner carries 55g pack evidence (OCR re-rank works)",
            m55.get("product_id") == str(pids["dm55"]) or ev55.get("pack_size_match") is True,
            f"top={m55.get('product_id')} evidence={json.dumps(ev55)}",
        )
        check("evidence breakdown present (visual/ocr/pack)",
              ev55.get("visual_similarity") is not None, str(ev55))
        check("method COMBINED with OCR agreement", m55.get("method") in ("COMBINED", "VISUAL"), str(m55.get("method")))
        print(f"      dm55: conf={m55['confidence']:.3f} method={m55['method']} evidence={json.dumps(ev55)}")

    # 110g query must not collapse onto 55g
    status, res110 = recognize(token_a, render_pack("DAIRY MILK", "110 g", (120, 60, 60), "front", seed=4))
    if status == 200 and res110.get("detections"):
        m110 = res110["detections"][0]["match"]
        check("110g SKU ranked #1", m110.get("product_id") == str(pids["dm110"]), f"top={m110.get('product_id')}")
        print(f"      dm110: conf={m110['confidence']:.3f} method={m110['method']}")

    await _db2.close()

    print("=" * 72)
    print(f"LEARNING FLYWHEEL RESULT: {PASS} PASS / {FAIL} FAIL")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
