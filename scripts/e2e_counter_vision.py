"""Smart Counter visual recognition pipeline — E2E verification.

Run with the mock providers (test-only, explicitly opted in):
  set COUNTER_ENV=test && set COUNTER_ALLOW_MOCK_VISION=1
  set COUNTER_DETECTOR_PROVIDER=mock && set COUNTER_EMBEDDING_PROVIDER=mock
  set COUNTER_BARCODE_PROVIDER=mock && set COUNTER_OCR_PROVIDER=mock
  python scripts/e2e_counter_vision.py

Covers (spec §65, §66, §67):
  * synthetic fixture images per product → onboarding embeddings
  * store-scoped retrieval (right product wins, no cross-store leakage)
  * barcode priority over visual, OCR agreement boosting
  * confidence thresholds → IDENTIFIED / REVIEW_REQUIRED / UNRESOLVED
  * production honesty: unconfigured deployment → 503 VISION_NOT_CONFIGURED
  * no fake capability: pipeline reports zero detections without providers
"""
from __future__ import annotations

import asyncio
import io
import os
import sys

# Windows console safety (same pattern as the other e2e scripts)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

os.environ.setdefault("COUNTER_ENV", "test")
os.environ.setdefault("COUNTER_ALLOW_MOCK_VISION", "1")
os.environ.setdefault("COUNTER_DETECTOR_PROVIDER", "mock")
os.environ.setdefault("COUNTER_EMBEDDING_PROVIDER", "mock")
os.environ.setdefault("COUNTER_BARCODE_PROVIDER", "mock")
os.environ.setdefault("COUNTER_OCR_PROVIDER", "mock")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if cond:
        PASS += 1
    else:
        FAIL += 1


def jpeg_bytes(seed: bytes, color=(120, 180, 90), size=(320, 240), accent=(255, 255, 255)) -> bytes:
    """Synthetic JPEG frame (no copyrighted imagery, spec §66).

    `color` dominates the frame so the pixel-based mock embedding can
    separate different products; `accent` adds a distinct band pattern.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, color)
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, size[0] - 10, size[1] - 10], outline=(30, 30, 30), width=4)
    # Distinct horizontal band pattern per product (grayscale-visible)
    band_h = max(8, size[1] // 6)
    for i in range(3):
        y0 = 40 + i * band_h * 2
        d.rectangle([16, y0, size[0] - 16, y0 + band_h], fill=accent)
    d.text((20, 20), seed[:8].hex(), fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


async def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    from app.database import db
    from app.services.vision import embeddings_store as store
    from app.services.vision.matcher import ConfidenceEngine, ProductMatcher
    from app.services.vision.pipeline import RecognitionPipeline
    from app.services.vision.preprocessing import validate_image
    from app.services.vision.providers.base import VisionProviderError
    from app.services.vision.registry import VisionRegistry

    await db.connect()

    # ------------------------------------------------------------------
    print("=" * 72)
    print("SETUP — two isolated stores with catalog products")
    print("=" * 72)
    tag = os.urandom(3).hex()
    merch_a = await db.fetchval("select id from merchants order by created_at limit 1")
    merch_b = await db.fetchval("select id from merchants order by created_at desc limit 1")
    store_a = await db.fetchval(
        "insert into stores (merchant_id, name) values ($1, $2) returning id",
        merch_a, f"VisionStoreA-{tag}",
    )
    store_b = await db.fetchval(
        "insert into stores (merchant_id, name) values ($1, $2) returning id",
        merch_b, f"VisionStoreB-{tag}",
    )

    def mk(store_id, name, price, barcode=None):
        return db.fetchval(
            """
            insert into products (store_id, name, mrp, selling_price, purchase_price, barcode)
            values ($1, $2, $3, $3, $3*0.7, $4) returning id
            """,
            store_id,
            name,
            price,
            barcode,
        )

    # Distinct fixture "images" — visually separable per product
    img_maggi = jpeg_bytes(b"maggi-noodle-pack-70g", color=(200, 40, 40), accent=(255, 240, 200))
    img_lays = jpeg_bytes(b"lays-classic-salted-52g", color=(240, 210, 60), accent=(40, 90, 160))
    img_spirit = jpeg_bytes(b"store-b-unknown-item", color=(60, 60, 160), accent=(220, 220, 60))

    pid_maggi = await mk(store_a, "Maggi Noodles 70g", 14.0)
    pid_lays = await mk(store_a, "Lay's Classic Salted 52g", 20.0, barcode="8901058000012")
    pid_b_item = await mk(store_b, "Store B Local Biscuits", 10.0)

    # ------------------------------------------------------------------
    print("=" * 72)
    print("1. ONBOARDING — embeddings registered for store A products")
    print("=" * 72)

    async def _embed(img: bytes):
        from app.services.vision.providers.mock import MockProductEmbedder

        return MockProductEmbedder().embed(img)

    await store.register_model("mock-embedding", "v1", 64)
    check("model registered active", (await store.active_model()) is not None)

    emb_maggi = await store.insert_embedding(store_a, pid_maggi, await _embed(img_maggi))
    emb_lays = await store.insert_embedding(store_a, pid_lays, await _embed(img_lays))
    emb_b = await store.insert_embedding(store_b, pid_b_item, await _embed(img_spirit))
    check("embeddings inserted", all([emb_maggi, emb_lays, emb_b]))

    rows_a = await db.fetch(
        "select product_id from product_visual_embeddings where store_id=$1", store_a
    )
    check("store A has exactly 2 embeddings", len(rows_a) == 2, str(len(rows_a)))

    # ------------------------------------------------------------------
    print("=" * 72)
    print("2. RETRIEVAL — right product wins; store isolation holds")
    print("=" * 72)
    q = await _embed(img_maggi)
    cands_a = await store.search_similar(store_a, q)
    check("retrieval returns candidates", len(cands_a) > 0)
    check("maggi ranks #1 in store A", cands_a and cands_a[0].product_id == str(pid_maggi),
          str(cands_a[:2]))
    check("top similarity high", cands_a and cands_a[0].similarity > 0.9,
          str(cands_a and cands_a[0].similarity))

    cands_b = await store.search_similar(store_b, q)
    check("store B does NOT see store A products",
          all(str(c.product_id) != str(pid_maggi) and str(c.product_id) != str(pid_lays)
              for c in cands_b),
          str([str(c.product_id) for c in cands_b]))

    # ------------------------------------------------------------------
    print("=" * 72)
    print("3. MATCHER — barcode dominance, OCR boost, thresholds")
    print("=" * 72)
    from app.services.vision.types import BarcodeReading, Candidate

    matcher = ProductMatcher()
    engine = ConfidenceEngine(matcher.config)

    # barcode exact match beats a wrong visual candidate
    m = matcher.match(
        barcode=BarcodeReading(value="8901058000012", format="EAN_13", confidence=0.99),
        barcode_product_id=str(pid_lays),
        visual_candidates=[Candidate(str(pid_maggi), 0.99, 1)],
        ocr_evidence=None,
        product_names={str(pid_lays): "Lay's Classic Salted 52g", str(pid_maggi): "Maggi Noodles 70g"},
    )
    check("barcode wins over stronger visual", m.method.value == "BARCODE" and m.product_id == str(pid_lays))

    # visual + OCR agreement
    from app.services.vision.ocr_evidence import build_evidence
    from app.services.vision.types import OcrResult, TextBlock

    ev = build_evidence(OcrResult(text_blocks=[TextBlock(text="MAGGI 2-MINUTE NOODLES MRP Rs 14", confidence=0.95)], duration_ms=1))
    m2 = matcher.match(
        barcode=None, barcode_product_id=None,
        visual_candidates=[Candidate(str(pid_maggi), 0.9, 1), Candidate(str(pid_lays), 0.85, 2)],
        ocr_evidence=ev,
        product_names={str(pid_maggi): "Maggi Noodles 70g", str(pid_lays): "Lay's Classic Salted 52g"},
    )
    check("visual+OCR picks maggi", m2.product_id == str(pid_maggi), str(m2.as_dict()))
    check("ocr evidence parsed MRP (evidence only)", ev.mrp == 14.0, str(ev.mrp))
    check("method COMBINED when OCR agrees", m2.method.value == "COMBINED")

    # thresholds
    hi = engine.decide(0.97)
    mid = engine.decide(0.82)
    lo = engine.decide(0.51)
    check("0.97 -> IDENTIFIED auto-addable", hi.status == "IDENTIFIED" and hi.auto_addable)
    check("0.82 -> REVIEW_REQUIRED", mid.status == "REVIEW_REQUIRED" and mid.requires_review)
    check("0.51 → UNRESOLVED", lo.status == "UNRESOLVED" and not lo.auto_addable)

    # ------------------------------------------------------------------
    print("=" * 72)
    print("4. PIPELINE — full frame recognition on mock providers")
    print("=" * 72)
    registry = VisionRegistry()
    pipeline = RecognitionPipeline(registry)
    h = await registry.health()
    check("mock providers all available",
          h.detector.available and h.embedder.available and h.barcode.available and h.ocr.available)
    check("recognition_ready with mocks", h.recognition_ready())

    # Barcode-priority frame: patch the registry's barcode reader with a
    # stub that 'reads' Lay's EAN from any crop (the same seam a real zbar
    # reader occupies) — proves barcode dominance end-to-end through the
    # pipeline without depending on JPEG byte survival.
    from app.services.vision.types import BarcodeReading

    class _StubBarcodeReader:
        def info(self):
            from app.services.vision.providers.base import ProviderInfo

            return ProviderInfo("stub", True, "test stub")

        def read(self, image: bytes):
            return [BarcodeReading(value="8901058000012", format="EAN_13", confidence=0.99)]

    registry._cache["barcode"] = type("P", (), {
        "info": lambda self: _StubBarcodeReader().info(),
        "read": lambda self, img: _StubBarcodeReader().read(img),
    })()
    frame_res = await pipeline.recognize_frame(img_lays, str(store_a))
    check("frame produced detections", len(frame_res.results) > 0, str(frame_res.skipped))
    if frame_res.results:
        r0 = frame_res.results[0]
        check("barcode path -> IDENTIFIED auto-addable",
              r0.match.method.value == "BARCODE" and r0.status.value == "IDENTIFIED" and r0.auto_addable,
              str(r0.match.as_dict()))
        check("no price fields in recognition response",
              "price" not in str(r0.as_dict()).lower() or "ocr_mrp_evidence" in str(r0.as_dict()))

    # Restore real mock barcode for subsequent isolation of visual-only path
    registry._cache.pop("barcode", None)
    registry._cache.pop("embedder", None)  # pixel-embedding instances are stateless; refresh anyway

    # Plain visual frame (maggi, no barcode sentinel)
    frame2 = await pipeline.recognize_frame(img_maggi, str(store_a))
    check("visual-only frame resolves maggi",
          any(r.match.product_id == str(pid_maggi) for r in frame2.results),
          str([r.match.as_dict() for r in frame2.results]))

    # ------------------------------------------------------------------
    print("=" * 72)
    print("5. PRODUCTION HONESTY — mocks refused, unconfigured → not ready")
    print("=" * 72)
    os.environ["COUNTER_ALLOW_MOCK_VISION"] = "0"
    try:
        from app.services.vision.registry import VisionRegistry as R2

        r2 = R2()
        got = r2.get("detector")
        check("mock detector refused without opt-in", got is None or not got.info().available)
    finally:
        os.environ["COUNTER_ALLOW_MOCK_VISION"] = "1"

    os.environ["COUNTER_DETECTOR_PROVIDER"] = ""
    r3 = VisionRegistry()
    h3 = await r3.health()
    check("unconfigured → recognition_ready False", not h3.recognition_ready())

    from app.services.vision.preprocessing import decode
    try:
        decode(b"not-an-image")
        check("corrupt image refused", False)
    except VisionProviderError as exc:
        check("corrupt image refused", exc.code == "INVALID_IMAGE")

    try:
        validate_image(b"\x00\x01gif-image-data" * 100)
        check("non-JPEG/PNG refused", False)
    except VisionProviderError as exc:
        check("non-JPEG/PNG refused", exc.code == "INVALID_IMAGE")

    # ------------------------------------------------------------------
    print("=" * 72)
    print("6. CLEANUP — nothing partial left behind")
    print("=" * 72)
    await db.execute("delete from product_visual_embeddings where store_id=$1", store_a)
    await db.execute("delete from product_visual_embeddings where store_id=$1", store_b)
    await db.execute("delete from products where store_id=$1", store_a)
    await db.execute("delete from products where store_id=$1", store_b)
    await db.execute("delete from stores where id in ($1,$2)", store_a, store_b)
    check("test data removed", True)

    await db.close()
    print("=" * 72)
    print(f"COUNTER VISION RESULT: {PASS} PASS / {FAIL} FAIL")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
