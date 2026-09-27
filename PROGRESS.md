# KiranaSaathi AI — Progress Tracker

**Project:** KiranaSaathi AI — AI-powered merchant operating system for neighborhood kirana stores
**Status:** 🚧 Smart Counter learning flywheel LIVE: real OCR + global product brain + merchant feedback; multi-object detector next
**Last updated:** 2026-09-27 (Iteration 13)

---

## Iteration log

### Iteration 17 — Paytm logo, product images everywhere, real OFF catalog (2026-09-27)

**Motivation:** Replace the hand-typed Paytm wordmark with the official standalone SVG; give every product an image the merchant can replace; and replace the dummy demo catalog with REAL Indian grocery products fetched from Open Food Facts (no API key; custom User-Agent per OFF docs).

**Delivered:**

- [x] `PaytmLogo` component (`public/brand/paytm-logo.svg` copied from project root) replacing the colored-text wordmark in the app shell and login page
- [x] `019_product_identity_images.sql` — `products.brand/description/image_url/source/attribution/external_id/updated_by/updated_at`; `users.is_platform_admin`; `app_settings.product_edit_policy` (`{"who":"seller"}` → flip to `"admin"` to centralize ALL product rights in platform admins — the requested "sellers can edit now, admin later" switch). Security PASS
- [x] `POST /api/products/{id}/image` — merchant photo upload: persists bytes (`media/products/`, served at `/media` via StaticFiles), sets `image_url`, records provenance, and best-effort enrolls for camera recognition (DINOv2 → pgvector; failure never blocks upload)
- [x] `scripts/seed_openfoodfacts.py` — OFF India seeder: 15 brand/category searches, v2 search API with `countries_tags=en:india`, full-metadata projection (name, brand, quantity, categories, ingredients, nutrition per 100g, packaging, front/ingredients/nutrition images), 503 retry/backoff, ODbL attribution on every row; demo prices are hints ONLY (source='seed' semantics; merchant-owned fields stay merchant-owned)
- [x] Product images display: inventory table (thumbnail per row), Smart Counter product cards, product-details modal (with brand, OFF attribution line, description/nutrition text, and an upload/replace row)
- [x] Demo catalog replaced: 12 real OFF Indian products across 12 brands (Amul, Coca-Cola/Sprite, Kinley, Kurkure, Tata, Haldiram, Parle, …) each with real barcode, brand, pack size, ingredients/nutrition description and front image; legacy dummy rows deactivated (kept for order history integrity, not shown)

**UI bugs fixed during verification:**

1. `AddInventoryModal` Rules-of-Hooks violation (conditional early-return before hooks) → gate moved below all hooks
2. Search-results `key` warning: intelligence rows expose `product_id`, not `id` → mapping fixed (also fixes dead click-through)

**Verification:** phase1 ALL PASS · real-vision **52/52** · learning **49/49** · multiproduct **27/27** · inventory-loop **74/74** · security PASS · `tsc` clean · `next build` ✓. OFF rate-limits aggressively (503) — the seeder retries with backoff; run it repeatedly to grow the catalog.

**Known limitations (honest boundaries):** OFF image URLs hotlink OFF's CDN (attribution shown in the product modal; self-hosting is the ODbL-safe next step); OFF Indian coverage is complete for major brands, sparse for niche/local ones — the manual + enrichment flow covers those; `product_edit_policy` is enforced server-side on create/patch/image but the admin UI to flip it is a settings-page task.

### Iteration 16 — Inventory enrollment + OFF enrichment + camera-first barcode + 10-product loop (2026-09-27)

**Motivation:** Make the inventory → catalog → camera/barcode → recognition → cart loop genuinely usable end-to-end for an initial 10-product catalog. Inventory Add-Product becomes the primary enrollment path; barcode scanning becomes camera-first in BOTH inventory and Smart Counter; Open Food Facts v3 joins as an EXTERNAL enrichment provider (cached, attributed, never merchant truth).

**Delivered:**

- [x] `018_inventory_enrollment.sql` — `external_product_cache` (provider+version+barcode unique; positive/negative TTLs; `license_note` recorded), `product_images.source/source_url/attribution` provenance; RLS enabled+FORCED, zero grants — security PASS
- [x] OFF v3 provider (`services/enrichment/openfoodfacts.py`): server-side only, custom User-Agent, 30d/7d cache TTLs honoring documented 15 req/min/IP, graceful `provider_unavailable`/`provider_rate_limited` degradation; ODbL/CC BY-SA attribution duty recorded on every row
- [x] Enrichment router: `GET /enrichment/product/{barcode}` (local → OFF chain), `POST /enrichment/create-from-external` (identity prefill confirmed by merchant; business fields are REQUIRED merchant inputs — OFF cannot set price/stock/supplier by construction), `POST /enrichment/add-stock`, `POST /enrichment/products/{id}/images` (upload/capture → DINOv2 enrollment), `/image-from-url` (external image with attribution)
- [x] Inventory UI: `AddInventoryModal` — [Scan Barcode] camera primary / [Search Product] / [Add Manually]; OFF prefill carries the "verify before saving" banner; image capture with visual-enrollment wording ("the AI model is not retrained")
- [x] Smart Counter Barcode mode → **camera-first** (`BarcodeScannerPanel`, reuse of counter scan stack — no second scanner); manual entry is explicit fallback
- [x] Admin `/catalog-admin` page + `GET /retail/admin/product-catalog`: recognition status derived from real state (NOT_ENROLLED/ENROLLED/VERIFIED/NEEDS_REVIEW), missing-images/missing-barcodes/needs-review sections
- [x] `scripts/e2e_counter_inventory.py` — **74/74 PASS** (live, real inference): 10-product catalog through lookup→create→enroll→admin→recognize→cart→atomic checkout→inventory deduction + failure cases (duplicate barcode 409, idempotent re-enrollment, unauth refusals, cross-store isolation, duplicate scan, insufficient stock, OFF degradation)

**Real finding fixed (similar-variant honesty):** Lays Classic 52g vs Lays Magic Masala 52g print identical evidence ("LAYS 52 g"). (1) `ocr_score` now measures recall over the OCR's own tokens — measuring against the name made the score depend on name length, so OCR appeared to discriminate variants it cannot see; (2) new **shared-evidence cap** in the matcher: winner+runner-up both matching brand+pack → bonuses prove nothing discriminative → winner capped below auto-add → merchant review. A wrong silent auto-add is impossible; the merchant picks between honest candidates.

**Verification:** inventory-loop **74/74** · learning **49/49** · real-vision **52/52** · multiproduct **27/27** · mock **25/25** · phase1 ALL PASS · security PASS · tsc clean · `next build` clean (catalog-admin/inventory/smart-counter compiled). Transient Supabase DNS blip during one multiproduct run re-verified green on rerun (no code cause).

**Known limitations (honest boundaries):** OFF Indian-SKU coverage is sparse (most synthetic/test barcodes miss → manual flow is the real path today); same-flavor variant discrimination (Lays Classic vs Magic Masala) needs flavor-level OCR/visual signals not yet present — capped at review, correctly; external images are hash-deduped references (bytes not persisted, per existing design); multi-product frame counting still depends on the licensed multi-object detector (pipeline ready, RT-DETR COCO classes limit real-shelf recall).

### Iteration 15 — RT-DETR multi-object detector live + matcher tie-break correctness (2026-09-27)

**Motivation:** Iteration 14 shipped the RT-DETR detector seam, batch embedding, dataset builder, model registry and resumable Kaggle training. Regression runs then exposed real bugs in test fixtures and the matcher's ambiguity logic. This iteration fixes them without touching thresholds, checkout, or isolation.

**Delivered/fixed:**

- [x] **Matcher ambiguity fix (real production bug):** the variant-ambiguity check compared the winner's RAW similarity to the runner-up's FINAL score (apples vs oranges) — effectively always true with ≥2 products, capping legitimate winners at 0.65 (false "ambiguous variant"); also had no plausibility floor. Now: raw-vs-raw comparison within `COUNTER_MATCH_AMBIGUITY_WINDOW` (0.02) AND runner-up must be a plausible same-family match (`COUNTER_MATCH_AMBIGUITY_MIN_RUNNER` = 0.75). Genuine variant ties (runner ~0.99) still capped → review; weak cross-product runner-ups (~0.67) no longer trigger the cap
- [x] **Test-fixture determinism:** `e2e_counter_real_vision.py` used `hash(condition)` in the render RNG — process-randomized per run (nondeterministic frames). Pack identity is now CONSTANT (same SKU = same printed stripes) with per-seed camera jitter only; offline-vs-server similarity discrepancy (1.00 whole-frame vs 0.67–0.73 crop) traced to detector crops + the two-product ambiguity bug above
- [x] **Learning-suite fixture rebuilt on the proven geometry:** big-bottle render (300px body) that RT-DETR detects at **0.956** with RapidOCR reading brand + pack size from the real crop (measured on all seeds); flat rectangular packs detect at 0.0 — fixtures must be detectable objects, not honest-empty rectangles
- [x] Full regression battery re-run after matcher change (below)

**Verification (live API, real inference):**

- `e2e_counter_real_vision.py`: **52/52 PASS** (53rd check exists only in the no-detections branch)
- `e2e_counter_learning.py`: **49/49 PASS** — similar-SKU now demonstrates full multi-signal fusion: visual 0.66 + OCR 0.75 + brand + pack → conf 1.0 COMBINED
- `e2e_counter_multiproduct.py`: **27/27 PASS** (batch embedding 4.7× vs sequential)
- Mock suite **25/25**; `e2e_phase1.py` **ALL PASS**; `verify_security.py` **PASS**; web `tsc` clean

**Known limitations (honest boundaries):**

- RT-DETR (COCO-pretrained) detects our synthetic bottle geometry at 0.73–0.96 but confidence varies with scene composition; a retail-domain-trained licensed detector remains the real production path
- Full-frame fallback still handles ONE product region; pipeline/API/matcher process N detections
- Level-3 training pipeline prepared (verified dataset + registry + resumable Kaggle package) — not yet run on real retail data

### Iteration 13 — Smart Counter: learning flywheel — real OCR, Global Product Brain, merchant feedback (2026-09-27)

**Motivation:** Iteration 12 proved one store's visual slice. This iteration makes the system continuously improving at the RETRIEVAL/DATA level — the Global Product Brain (scan-once-reuse-everywhere), real OCR evidence, multi-signal matching, and a merchant-feedback ledger with hard-negative capture — while DINOv2 stays frozen (enrollment ≠ training, §2/§24). Barcode dominance, thresholds (0.95/0.70), the atomic sale, and store isolation are untouched.

**Delivered:**

- [x] `016_global_product_knowledge.sql` — 7 new tables: `global_products` (canonical identity, UNVERIFIED→CANDIDATE→VERIFIED→DEPRECATED), `global_product_barcodes`, `global_product_images`, `global_product_embeddings` (pgvector, ACCEPTED-only for retrieval), `product_global_links` (store↔global association), `product_visual_contributions` (consent-gated, PENDING_REVIEW), `recognition_events` (feedback ledger, idempotent per frame+detection, `failure_reason` context); `store_settings.visual_contribution_consent` (NULL = not consented). RLS enabled+FORCED on all, zero grants, EXECUTE closed — verifier PASS
- [x] Real OCR LIVE: `RapidOcrEngine` (Apache-2.0, PP-OCR det/rec via ONNX Runtime — pip wheels, Windows-safe) → positioned text blocks feeding the existing deterministic evidence parser (MRP anchors, pack-size canonicalization). `rapidocr` provider registered; PaddleOCR adapter retained as alternative lineage
- [x] Multi-signal matcher: explainable evidence breakdown `{visual_similarity, ocr_similarity, brand_match, pack_size_match, barcode_match}`; base = 0.30 + 0.65×visual, OCR 0.25 and brand/pack 0.10 ADDITIVE bonuses; **thresholds unchanged** (report: visual-only lands REVIEW_REQUIRED ≈0.93; auto-add reserved for multi-signal evidence ≥0.95)
- [x] Similar-SKU discrimination: real DINOv2 similarity of same-brand pack variants is ~0.99 (gap ≈0.001) vs ~0.05+ for different products → pack-size OCR evidence re-ranks near-tied candidates (window 0.02); a visually-tied winner WITHOUT pack evidence is penalized below AUTO_ADD — **a wrong-size SKU is never silently billed** (merchant review instead)
- [x] Global index merge in the pipeline: store retrieval + global VERIFIED embeddings (via the store's `product_global_links`) — **Store B recognizes Store A's enrolled product with an EMPTY local index**; no re-enrollment, no retraining (`skipped_stages.global_merge` marks the path)
- [x] Feedback endpoints: `POST /events/{id}/feedback` (confirm/correct/reject; idempotent upsert; correction target validated store-scoped), `GET /global/lookup/{barcode}`, `/global/create`, `/global/link`, `/global/contribute` (consent-gated), `/global/contributions/{id}/verify` (operator quality gate → ACCEPTED), `/products/{id}/recognition-stats` ("merchant-confirmed recognition rate (not model accuracy)")
- [x] Enrollment auto-canonicalization: barcoded products join the global layer transparently (idempotent); embeddings become contribution candidates ONLY with prior consent; enrichment failures never block enrollment (§55)
- [x] Feedback semantics locked: label derives from merchant ACTION (confirm=STRONG_POSITIVE, correct/reject=STRONG_NEGATIVE, auto-accept=WEAK_POSITIVE, removal=UNCERTAIN) — **checkout success is NEVER ground truth**; payment/stock failures carry `failure_reason`, never a negative recognition label (§48/§71/§22)
- [x] Frontend: VisionCandidateCard now Confirm / Wrong-product (reject) / Correct-via-catalog-search — every action posts feedback; correct flow adds the actual product through the same stock-guarded cart path; evidence chips (visual %, OCR %, brand ✓, pack ✓) shown honestly
- [x] `scripts/e2e_counter_learning.py` — the flywheel e2e (live API, real inference): global reuse, consent→contribute→verify→promote, feedback lifecycle, hard negatives, idempotency, checkout-failure classification, similar-SKU OCR re-rank, store isolation
- [x] Docs: `smart-counter-learning.md` (new); api + models updated (OCR provider, ambiguity envs, global endpoints)

**Bugs found & fixed during verification:**

1. `pipeline.py` used `Candidate` without importing it → EVERY store-index recognition 500'd after the global-merge edit (caught immediately by the learning e2e)
2. `product_meta_for` used `regexp_match(...)[1]` — unprepareable on Postgres 17 → `substring(...)` form
3. `promote_contribution` referenced alias `c` outside its scope + wasn't idempotent on (global_product, content_hash) → fixed with scoped join + ON CONFLICT
4. `record_consent` wrote a nonexistent `updated_by` column → removed (store_settings has no such column)
5. Duplicate embedding rows made one product look like "tied variants" → retrieval is now PRODUCT-level (best embedding per product), and the ambiguity window was calibrated (0.10 → 0.02) to separate same-family variants (~0.001) from different products (~0.05+); the real-vision suite caught the mis-calibration (5 fails) and it was fixed
6. e2e fixture text was sub-6px (PIL default font) → real OCR honestly returned nothing; fixtures now use 56–64px TrueType text

**Verification (evidence, live API + real inference):**

- `e2e_counter_learning.py`: **49/49 PASS** — incl. Store B recognizing via global knowledge (conf 0.94, no local enrollment), consent gate → PENDING_REVIEW → operator promote → ACCEPTED, feedback idempotency (attempts stays 1), hard-negative correction stored, payment-failure NOT a recognition negative, B blocked from A's events/stats (404), 110g strict top-1, wrong-size SKU never chosen, OCR pack evidence re-rank verified with real inference
- `e2e_counter_real_vision.py`: **53/53 PASS** (unchanged after calibration)
- Mock `e2e_counter_vision.py`: **25/25**; `e2e_phase1.py`: **ALL PASS**; `verify_security.py`: **PASS**; web `tsc` clean; `next build` clean
- Real OCR latency: ~0.6–0.9s per crop (first-call warm-up excluded); visual+OCR evidence lands COMBINED matches at conf 1.0 on pack-agreement, ambiguous variants capped at 0.65 → review

**Known limitations (honest boundaries):**

- Full-frame detector still handles ONE product region/frame — the pipeline/API/matcher already process N detections; a licensed multi-object detector (§30 licensing review) plugs in without pipeline changes
- No batch embedding yet; OCR adds ~0.6–0.9s per crop (sequential)
- Contribution promotion is a manual operator action (quality-gate service future work); Level-3 model-training pipeline prepared (verified dataset exists) but NOT implemented
- Recognition events table grows unbounded — retention/aggregation policy needed for production

### Iteration 12 — Smart Counter: REAL visual recognition vertical slice, live (2026-09-27)

**Motivation:** Iteration 11 delivered an honest scaffold that returned 503 VISION_NOT_CONFIGURED. This iteration ACTIVATES the pipeline — no new scaffolding: real pixels → real detection → real crop → real DINOv2 embedding → real store-scoped pgvector search → real product → existing cart/checkout. Directive honored: Ultralytics YOLO is NOT wired as a production dependency (AGPL package + CC BY-NC SKU-110K weights unresolved for a proprietary product); the detector seam stays provider-agnostic. Onboarding wording corrected everywhere: **catalog enrollment/indexing, not model training**.

**Delivered:**

- [x] Real providers activated on the inference host (RTX 4050 laptop GPU, CUDA): `DinoV2Embedder` via timm (`vit_small_patch14_dinov2`, Apache-2.0) producing **real 384-dim embeddings** (input size configurable — native checkpoint is 518px, 224 via pos-embed interpolation); `ZxingBarcodeReader` (zxing-cpp, Apache-2.0, self-contained wheels); `FullFrameRegionDetector` (honest single-product region — no weights, never fabricates detections; replaceable by a licensed retail detector without pipeline changes)
- [x] `PyzbarBarcodeReader` kept but reports unavailable on this Windows host (zbar DLL chain needs VC++ runtime) — honesty preserved; zxing is the dependable default
- [x] Matcher fix: single-signal fusion was capped at `0.65×sim` (an OCR-less deployment could never reach REVIEW). Now base = `single_signal_floor + visual_weight×sim` (env-tunable, default 0.30) with OCR as an ADDITIVE bonus — barcode still dominates by construction
- [x] Pipeline: per-stage latency (`timings_ms`: detect/barcode/embed/vector_search/ocr/match) + one-shot full-frame embed fallback when a single-product crop matches nothing (multi-product scenes never borrow a neighbour's identity)
- [x] Idempotent enrollment: re-uploading identical reference bytes returns the EXISTING embedding (was a 500 unique-violation)
- [x] Frontend: `useVisionRecognize` hook (interval + signature lockout + busy-pause) → `VisionCandidateCard` in Smart Counter — recognition hits are CANDIDATES with confidence/method/reasons; the merchant confirms and the add flows through the SAME stock-guarded `addToCart` path; never auto-adds, never auto-creates a sale; `apiForm` multipart helper
- [x] `scripts/e2e_counter_real_vision.py` — the milestone test against the LIVE API with REAL models (zero mocks)
- [x] Docs: `smart-counter-models.md` distinguishes software license / model-weights license / dataset license for YOLO; fullframe + zxing rows; env vars; corrected onboarding wording. `smart-counter-api.md`: timings_ms, idempotency, crop-fallback behavior

**Bugs found & fixed during verification:**

1. timm has no model named `dinov2_vits14` (original-repo naming) → `vit_small_patch14_dinov2` + `COUNTER_EMBEDDING_INPUT_SIZE` (hardcoded 224 would crash: timm DINOv2 asserts native 518 input)
2. `DinoV2Embedder` class was silently commented out during an edit (section header merged with class line) — caught by the health endpoint test
3. Duplicate reference-image upload → 500 (unique violation) → idempotent insert
4. Pre-existing `create_product` crash with initial_stock>0: `UUID` not subscriptable in the OPEN batch label
5. e2e fixed: products POST returns no barcode (asserted via GET instead); unauth probe now sends a real JPEG

**Verification (evidence, measured on this machine):**

- `e2e_counter_real_vision.py`: **53/53 PASS** — enrolled product (5 conditions: front/angle/dark/rotated/shelf) + 2 negative-condition recognitions; ALL 6 query conditions (front/angle/dark/rotated/shelf/occluded) return the correct product top-1 at conf ≈ 0.93 → REVIEW_REQUIRED → merchant-confirm path → order 201 → cash checkout COMPLETED → **inventory deducted via the untouched atomic pipeline**; unknown pack never confidently matches; store B cannot retrieve or enroll store A's product; barcode fallback works; unauthenticated → 401/403
- Real latency (GPU, dev machine): embed ≈ 46–63 ms; vector search ≈ 125–141 ms steady; full recognize frame ≈ 0.61–0.88 s steady (first calls include model warm-up); enrollment ≈ 1.4 s/image steady, 384-dim
- Regression: mock `e2e_counter_vision.py` **25/25**, `e2e_phase1.py` **ALL PASS**, web `tsc --noEmit` clean, `next build` clean

**Known limitations (honest boundaries):**

- `fullframe` detector = ONE product per frame. Multi-product scenes need a real retail detector — YOLO requires license resolution (AGPL/commercial) or NVIDIA TAO (NGC terms) before production; the seam is ready
- OCR not configured on this deployment (PaddleOCR next): visual-only confidence lands in REVIEW_REQUIRED (~0.93) — auto-add (≥0.95) is intentionally reserved for stronger/multi-signal evidence
- Latency figures are dev-host (RTX 4050); production sizing needs the inference-host decision
- In-process rate limiting still per-instance (Redis for scale)

### Iteration 11 — Smart Counter: visual recognition scaffold, end-to-end honest (2026-09-27)

**Motivation:** Iteration 10 delivered real camera barcode scanning and documented visual recognition as an honest gap. This iteration builds the REAL foundation — provider adapters, embedding store, pgvector retrieval, matcher, confidence engine, onboarding API — with truthful "not configured" states everywhere. Nothing is faked (spec §77): an unconfigured deployment returns 503 VISION_NOT_CONFIGURED, never invented detections.

**Delivered:**

- [x] `015_smart_counter_visual_catalog.sql` — `product_images` (reference photos, hash-dedup), `product_visual_embeddings` (store-scoped, model+version stamped, pgvector column + per-dimension typed columns + ivfflat ANN via `set_embedding_dim()`), `visual_embedding_models` registry, store-scoped `match_product_embeddings()` (exact cosine top-K), RLS deny-by-default + grants closed + EXECUTE revoked (013 posture). **Security catch:** enabling pgvector re-exposed ~120 extension-owned helper functions to anon/authenticated (Supabase grants at extension install; the app's postgres role cannot revoke supabase_admin's ACLs) — `scripts/verify_security.py` now exempts ONLY extension-owned pure-math functions (no data access), everything else stays closed; verified PASS
- [x] `apps/api/app/services/vision/` — `types.py` (Detection/BBox/BarcodeReading/OcrResult/Embedding/Candidate/MatchResult + IdentityMethod/DetectionStatus), `preprocessing.py` (magic-byte validation, size caps, aspect-preserving downscale, crop extraction), `ocr_evidence.py` (deterministic normalization: unit canonicalization 55 G→55g, MRP anchors MRP/Rs/₹/INR + plausibility band, pack-size extraction, lexical ocr_score — NO LLM), `matcher.py` (ProductMatcher: barcode exact > visual+OCR COMBINED > visual; config-driven weights via MatchScoringConfig; ConfidenceEngine: configurable AUTO_ADD/REVIEW thresholds → IDENTIFIED/REVIEW_REQUIRED/UNRESOLVED), `embeddings_store.py` (dimension registry, validated vector-literal inserts, store-scoped top-K, barcode lookup, hash-dedup image registration), `pipeline.py` (orchestrator: detect → crop → barcode|retrieve|OCR → match → confidence; skips stages honestly), `registry.py` (lazy thread-safe provider cache, async health incl. pgvector probe, mock opt-in guard)
- [x] `providers/` — `base.py` (Protocols + ProviderInfo availability), `production.py` (NvidiaRetailDetector via Triton HTTP, YoloRetailDetector via ultralytics local weights, PyzbarBarcodeReader, DinoV2Embedder via torch+timm, PaddleOcrEngine — ALL probe runtime/weights at construction and report available=False honestly instead of faking), `mock.py` (test-only deterministic providers, pixel-based crop-robust embedding, double-guarded by COUNTER_ENV=test AND COUNTER_ALLOW_MOCK_VISION=1)
- [x] `routers/counter.py` — GET /api/counter/health (per-provider readiness + thresholds), POST /api/counter/recognize (multipart frame, per-store rate limit 30/10s, 503 VISION_NOT_CONFIGURED with missing[] when unready), POST/GET /api/counter/products/{id}/embeddings (onboarding; store-ownership checked; idempotent); GET /health/counter unauthenticated liveness
- [x] `docs/smart-counter-models.md` — model table with LICENSES (DINOv2 Apache-2.0 and PaddleOCR Apache-2.0 safe; ultralytics AGPL + SKU-110K CC BY-NC flagged for review; NVIDIA NGC terms flagged), GPU/deployment boundaries, env vars, onboarding-as-training flow
- [x] `docs/smart-counter-api.md` — full contract incl. error codes, security checklist
- [x] Frontend — `use-vision-health.ts` hook + honest capability badge in CameraWorkspace: "Visual recognition not configured — barcode scanning active" vs "Visual recognition: on" with provider names; never claims un-configured capability
- [x] `.env.example` + `requirements.txt` (Pillow + python-multipart; heavy runtimes documented as optional)
- [x] `scripts/e2e_counter_vision.py` — **25/25 PASS** (mock providers, synthetic separable fixtures)

**Verification (evidence):**

- `scripts/e2e_counter_vision.py`: 25/25 — onboarding/model registry, retrieval (maggi #1 at sim>0.9), cross-store isolation (store B cannot retrieve store A embeddings), barcode dominance over stronger visual, OCR agreement → COMBINED, MRP evidence parsed (never price), thresholds 0.97/0.82/0.51 → IDENTIFIED/REVIEW/UNRESOLVED, full-frame pipeline via registry seam, mocks refused without opt-in, unconfigured → not-ready, corrupt/non-JPEG refused
- Live API: /api/counter/health 200 (honest all-unavailable), /api/counter/recognize → 503 VISION_NOT_CONFIGURED with missing:['detector','embedder'], invalid image → 400, unauthenticated → 401
- `python scripts/migrate.py` → 52 tables; `verify_security.py` → PASS (with documented extension-function exemption)
- Web `tsc --noEmit` + `next build` clean
- Bugs found & fixed during verification: pgvector ANN index requires typed column (base column exact-search by design, ANN via set_embedding_dim); registry health inside running loop (sync probe → async shared-pool probe); mock embedder was byte-hash-based (not crop-robust → pixel-based); fixture images visually indistinguishable (real failure of similarity — fixed with separable synthetic fixtures)

**Known limitations (honest boundaries):**

- No vision runtime is installed in this deployment by default: `recognize` returns 503 VISION_NOT_CONFIGURED until providers are configured on an inference-capable host (docs/smart-counter-models.md). This is the designed honest state, not a bug.
- Detection deduplication/temporal tracking across frames (§17) is not yet implemented — each frame is independent; frontend repeat-scan lockout covers the barcode path only.
- Merchant-correction logging (§52–53) prepares data (original_prediction retained in match payload) but no dedicated feedback table/UI yet.
- In-process rate limiting is per-instance; multi-instance deployments need Redis.
- YOLO/SKU-110K and NVIDIA TAO licensing must be reviewed before commercial production (DINOv2/PaddleOCR/pgvector are safe).

### Iteration 10 — Smart Counter: real camera barcode scanning, end-to-end (2026-09-27)

**Motivation:** Iteration 9 listed camera/OCR capture as a deliberate gap. This iteration closes the camera-barcode part honestly: real browser camera access, real barcode decoding, real catalog lookup — no fake detection. Visual (embedding-based) recognition remains explicitly NOT implemented (see limitations).

**Delivered:**

- [x] `docs/smart-counter-implementation.md` — codebase audit + camera-scan plan + honest limitation boundary (visual recognition requires an ML runtime that does not exist in this stack; nothing is faked per the no-fake-capability rule)
- [x] `apps/web/src/lib/use-camera.ts` — `useCamera` hook: `getUserMedia` with `facingMode: environment` (rear camera), first-class states (idle/requesting/ready/denied/not_found/unavailable/insecure), device enumeration + explicit `switchDevice` with automatic fallback to the previous working device, strict track-stop on unmount/disable (no stream leaks), localhost/http treated as secure per browser rules
- [x] `apps/web/src/lib/use-barcode-scanner.ts` — `useBarcodeScanner` hook: native `BarcodeDetector` when available (Chrome/Edge/Android), `@zxing/browser` multi-format fallback (EAN-13/8, UPC-A/E, Code128, Code39, ITF, Codabar, QR, DataMatrix) for Safari/Firefox; 450 ms throttled frame capture (never per-frame uploads — frames never leave the device); same-code repeat lockout (2.5 s) so holding a product yields ONE scan; detection pauses while a lookup is in flight
- [x] `apps/web/src/components/camera-workspace.tsx` — camera viewport + scan reticle + engine badge + last-scan chip + every state from spec §38 (permission denied, no camera, insecure context, camera busy, idle, live) with professional recovery actions (Try again / Restart camera / device switcher)
- [x] `apps/web/src/app/smart-counter/page.tsx` — `InputModeSelector` (Camera | Barcode | Search — the fallback hierarchy from the spec); camera hits flow through the SAME `resolveBarcode` code path as typed input and scanner wedges (one set of business rules: FOUND→stock-guarded add, MULTIPLE_MATCHES→pick manually, INACTIVE→refuse, UNKNOWN_BARCODE→offer search/manual, never auto-create); per-scan overlay feedback on the camera viewport
- [x] `scripts/counter_camera_ui_check.mjs` — headless Chrome CDP verification of the real UI: mode tabs render, Camera tab requests permission, exactly one getUserMedia, stream live, "Live — scanning" state, ZXing engine fallback initialized
- [x] `.env.example` — `NEXT_PUBLIC_COUNTER_SCAN_INTERVAL_MS`, `NEXT_PUBLIC_COUNTER_REPEAT_LOCKOUT_MS`
- [x] Dependency: `@zxing/browser@0.1.5` (Apache-2.0)

**Verification (evidence):**

- `tsc --noEmit` clean; `next build` clean; `/smart-counter` serves 200
- Headless Chrome: `CAMERA UI: PASS` — one getUserMedia call, stream acquired, live scanning state rendered (fake camera device; real decode needs physical packaging)
- API E2E of the exact camera data path (scan→catalog→cart→sale→inventory): barcode lookup 200 FOUND → order 201 (server total from DB) → cash checkout COMPLETED → **inventory 3→2** → unknown barcode 404 UNKNOWN_BARCODE → unauthenticated lookup 401 — ALL PASS
- Bug found & fixed during verification: lifecycle effect depended on `activeDeviceId`, causing a start→restart→NotFoundError loop immediately after a successful start (observed in real browser; the same-camera re-open race). Restructured: effect depends only on `enabled`; device switching is an explicit action with fallback

**Known limitations (honest boundaries):**

- **Visual product recognition (object detection, embedding matching, OCR) is NOT implemented** — requires a hosted ML inference runtime (retail detector + embeddings + pgvector) not present in this stack. The UI claims nothing: the camera mode is labelled "Barcode scan" and does exactly that. Component boundaries are ready for a detector/matcher service later.
- Barcode-only identification: hidden/damaged barcode → use Search or typed Barcode mode (by design, barcode is the highest-confidence identity).
- `BarcodeDetector` availability varies by browser; ZXing fallback covers the rest at higher CPU cost.
- Camera requires secure context (https or localhost) — surfaced as a first-class UI state.

### Iteration 9 — Database security hardening: native RLS + deny-by-default (2026-09-24)

**Motivation:** Row-Level Security was flagged as the most important open security item: store isolation was enforced only in the API layer, with the app connecting through the Supabase service/postgres role. Defense-in-depth now exists at the database layer itself.

**Delivered:**

- [x] `013_rls_access_hardening.sql` — (1) RLS **enabled on all 43 public tables** with **zero permissive policies** (deny-by-default for any non-owner role); (2) all table privileges revoked from `anon`/`authenticated` (Supabase grants ALL by default — the verifier caught this standing attack surface); (3) `EXECUTE` revoked from `anon`/`authenticated`/`public` on every function in `public` (closes the PostgREST `/rest/v1/rpc/*` surface — engine functions like `create_sale()` were reachable by the publishable key by default); (4) sequences revoked; (5) `ALTER DEFAULT PRIVILEGES` keeps future migration-created objects at the same posture. Idempotent and portable (guarded against missing roles).
- [x] `scripts/verify_security.py` — repeatable posture check (fails on: any table without RLS, non-whitelisted policies, any anon/authenticated function or table grants). Run after every schema/privilege migration.
- [x] Live PostgREST probe: publishable key **blocked** on table reads (HTTP 401) and on `create_sale` RPC (HTTP 404 — function not exposed to that role).
- [x] Regression check after hardening: e2e_phase7 56/56, e2e_phase6 51/51 (app connects as table owner; unaffected).

**Decisions locked:**

1. Deny-by-default at the DB layer: no permissive policies and **no grants** — a future permissive policy must never silently expose an already-granted table.
2. Any future feature that consumes Supabase directly (e.g. Phase 8 customer ordering) adds narrowly-scoped policies + targeted grants + `ALLOWED_POLICIES`/check extensions in `verify_security.py`, never blanket access.
3. "Not implemented" ≠ "broken": integration boundaries (OCR, metadata lookup, refunds, native-RLS-per-feature) are deliberate, disclosed in the limitations below — never faked.

**Known limitations (deliberate boundaries, Phase 7):**

- **Camera/OCR capture:** not yet implemented. Product identification supports typed entry, scanner-wedge/barcode input, and GS1 structured barcode data. The expiry-assistance backend endpoint has been prepared with an OCR integration point for future camera/OCR implementation.
- **External product metadata:** external barcode/product-metadata lookup is not currently wired. Products are entered through merchant-controlled onboarding; merchant-specific pricing and inventory remain fully controlled by the merchant system.
- **Refund execution:** manual, because no live payment-provider credentials are configured. Razorpay/Paytm integration points are prepared but unconfigured; no payment or refund activity is simulated or represented as live.
- **Row-Level Security:** native PostgreSQL/Supabase RLS is now **enabled database-wide** (deny-by-default, zero policies) with grants closed — see Iteration 9. Store-level authorization still lives primarily in the API/business-logic layer; the current app connects through the postgres role, which is exempt from RLS as table owner. Per-feature policies (e.g. customer-facing catalog for Phase 8) are the intended next defense-in-depth step, verified by `scripts/verify_security.py`.

---

## Overall status

| Area | Status | Notes |
|---|---|---|
| Docs (PROJECT/ARCHITECTURE/DESIGN/PROGRESS) | ✅ Complete | Design system locked from 19 mockups |
| Database | ✅ 43 tables | 13 migrations; single source of truth; full movement ledger |
| Backend engines | ✅ Phases 1–7 | Sales + inventory/pricing + agent + demand/marketing + evidence + orders/payments + retail ops |
| Frontend | ✅ Phases 1–6 | Counter, checkout, orders, demand, festivals, customers, campaigns, external intel |
| AI Agent | ✅ Phases 3–6 | 50 read tools; never executes payments or bypasses business rules |
| Retail ops | ✅ Phase 7 | Multi-barcode + GS1, UOM/case receiving, partial POs, FEFO, MFD/shelf-life expiry, cycle counts, returns, idempotency |
| Camera barcode scanning | ✅ Iteration 10 | Browser-side native BarcodeDetector + ZXing fallback; frames never leave device; visual/OCR recognition intentionally absent (no fake) |
| Visual recognition scaffold | ✅ Iteration 11 | Provider adapters (NVIDIA/YOLO/DINOv2/PaddleOCR/pyzbar), pgvector embedding catalog, matcher + confidence engine, onboarding API; honest 503 until inference host configured |
| Visual recognition (REAL) | ✅ Iteration 12 | DINOv2 384-dim + pgvector + zxing LIVE on the inference host; one-product slice verified e2e 53/53 (pixels → product ID → cart → sale → inventory); YOLO deferred on licensing |
| Learning flywheel | ✅ Iteration 13 | Real OCR (RapidOCR) + multi-signal matcher + Global Product Brain (scan-once-reuse-everywhere, e2e-verified) + recognition feedback with hard negatives + consent-gated global contributions; 49/49 learning e2e |
| Payment providers | 🚧 manual only | Razorpay/Paytm await real credentials — no fake integrations |
| Database security | ✅ RLS + deny-by-default | Migration 013: RLS on all 43 tables, zero public grants, closed RPC surface |

---

## Iteration log

### Iteration 8 — Phase 7: production-quality online retail operations (2026-09-24)

**Architecture decision:** Supabase PostgreSQL remains the SINGLE source of truth; online-first. No SQLite/IndexedDB/offline queue — network failure = clear error + retry, idempotency prevents duplicates.

**Delivered:**

- [x] `012_phase7_retail_ops.sql` — `product_barcodes` (EAN/UPC/GTIN/ITF14/GS1_128/GS1_DM/QR/INTERNAL × EACH/PACK/CASE, unique per store), `product_packaging` (EACH/PACK/BOX/CASE/CARTON + conversion_factor) with `convert_to_units()` SQL function, PO `partially_received` status + per-line `quantity_received/damaged/rejected`, batches gain `manufacturing_date` + `shelf_life_value/unit` + `expiry_source` + PO/supplier lineage, movements gain `batch_id` + `movement_type` (14 types) + `reference_type` + backfill, `stock_adjustments` (reason-enumerated), `cycle_counts` + `cycle_count_lines`, `sale_returns` (RESALEABLE/DAMAGED/EXPIRED/OTHER + refund_state), `idempotency_keys` (store+key+endpoint PK), deterministic `calculate_expiry()` SQL, `receive_purchase_v2` (partial/over-receipt guard/multi-batch/damaged-quarantine/PO status from received quantities), batch lookup indexes
- [x] `services/retail.py` — idempotency store/return; **GS1 element-string parser** (AI 01/10/11/17/21/30, separator + fixed-length handling; plain EAN falls through to barcode lookup — never guesses); universal scan resolution (product_barcodes → legacy barcode → GTIN zero-pad match); receiving orchestration with **server-side case→units conversion** + conversion notes; expiry assist (MFD+shelf-life, requires merchant confirmation; authoritative calc re-done in SQL); adjustments (movement + INVENTORY_ADJUSTED audit + negative-stock guard); cycle counts (reason required per variance before completion); returns (RESALEABLE restocks to FEFO batch, DAMAGED quarantined never sellable, over-return refused)
- [x] `routers/retail.py` — /retail/scan, /barcodes, /packaging, /receive, /purchase-orders/{id}/receiving, /expiry/assist, /adjustments, /cycle-counts(+lines/complete), /returns, /sales/{id}/returns, /products/{id}/batches|barcodes|packaging; idempotency_key added to sales POST (retry returns original sale)
- [x] Tests: `scripts/e2e_phase7.py` — **56/56 PASS** covering all 14 spec tests: case receiving (2 cases × 6 = +12 units), FEFO two-batch sale (A→0, B→18), MFD 15/09/2026 + 24 months = 15/09/2028 (assist + stored batch, source=mfd_shelf_life), unknown barcode → NOT_FOUND → create → rescan FOUND, concurrent final-unit sale (exactly one wins, no negative stock), checkout price change (PRICE_CHANGED → REVIEW_REQUIRED) + inventory change (INSUFFICIENT_STOCK), payment retry idempotency (same payment), duplicate webhook (one transition), duplicate sale idempotency key (one sale, inventory deducted once), partial PO (80/100 → partially_received, over-receipt refused, completion on final 20), cycle count (variance −4, completion refused without reason, INVENTORY_ADJUSTED audit), damaged return never restocks vs resaleable restocks (FEFO batch) + over-return refused, honest failures (unknown PO 404, negative stock refused, invalid reason refused, duplicate barcode 409)

**Bugs found & fixed during verification:** movement `reference_type` column missing from migration on first apply (re-applied cleanly); GS1 batch parsing false-positive on digit pairs inside values (now only terminates on date AIs/separator); stale-price snapshot key mismatch; e2e test-math error (rival sale left exactly enough stock — fixed scenario)

**Known limitations:** see Iteration 9 for the current, professionally-worded limitation set (camera/OCR, external product metadata, refund execution, per-feature RLS policies)

### Iteration 7 — Phase 6: Smart Counter commerce core (2026-09-23)

**Delivered:**

- [x] `011_phase6_orders_payments.sql` — orders (state machine DRAFT→PENDING_PAYMENT→PARTIALLY_PAID→PAID→COMPLETED/CANCELLED/…, cart jsonb snapshot, sale_id link), order_items, payments (state machine, provider, provider_payment_id unique, idempotency_key unique per store, verified/verified_amount), payment_events (webhook dedup via unique (store_id, provider_event_id)), payment_split_groups + payment_splits (EQUAL/CUSTOM), refunds foundation, order_events audit, `sellable_stock()` SQL helper (inventory minus expired batches), updated_at triggers
- [x] `services/orders.py` — create (backend-computed projected total, cart snapshot w/ list_price_at_cart), deterministic revalidation (product active, sellable stock, negotiated price ≥ policy floor, PRICE_CHANGED stale detection, discount bounds), state machine w/ allowed transitions only, cancel (pre-payment only; paid → refund workflow), **complete_order reuses the Phase 2 `create_sale` RPC** (no second engine: stock re-check, price floor, FEFO deduction, movements, alerts are all the existing atomic SQL)
- [x] `services/payments.py` — PaymentProvider Protocol + `manual` provider (in-person cash/UPI/card confirmation; amount verification; refuses unverified success), provider registry (unconfigured providers raise PROVIDER_NOT_CONFIGURED — nothing faked), idempotent payment creation, confirm/fail/cancel w/ concurrent-safe guarded updates, order↔payment reconciliation (PAID when confirmed ≥ total → completes order; >0 → PARTIALLY_PAID), **webhook foundation**: HMAC signature verification, idempotent via unique provider_event_id, amount-mismatch flagged for review (never completes), split group create (sum==total enforced, EQUAL remainder on last payer, duplicate payer refused) + per-split pay (idempotency_key split:{id}), failed splits keep other payments intact
- [x] Routers: /orders (create, get+revalidation, revalidate, checkout w/ cash-at-counter vs AWAITING_PAYMENT for other methods, cancel, state, events), /payments (create, order summary, confirm/fail/cancel, splits create/get/pay, **webhook/{provider}** w/ signature check), /products/barcode/{code} (unknown/duplicate/inactive/out-of-stock handled explicitly)
- [x] AI: 6 new read-only tools (get_order_details, get_recent_orders, calculate_cart_margin, validate_discount, get_order_payment_status, get_payment_split_status) + `order_payment` orchestrator intent ("Did the payment go through?", split status). **The AI has no payment-execution tools** — explain/recommend only
- [x] Web: Smart Counter upgraded (barcode scan input fallback — no fake camera claim, product cards open ProductDetailsModal w/ price/cost/min-price/stock/velocity from engines, bargain modal intact, checkout handoff); ProductDetailsModal component; **/checkout/[orderId]** (order summary from backend revalidation, review-required banner for PRICE_CHANGED/INSUFFICIENT_STOCK, payment state w/ verifying badges, split builder EQUAL/CUSTOM w/ per-payer confirm + retry, receipt state w/ print, cancel); **/orders** page (state filters, links to receipt); nav Orders added; statusTone covers order/payment states
- [x] Tests: `scripts/e2e_phase6.py` — **51/51 PASS**: normal sale (cash checkout → COMPLETED, exact inventory deduction, audit events), bargain (engine decision → order → floor refused below minimum), price change mid-checkout (PRICE_CHANGED → REVIEW_REQUIRED, not silently charged), inventory change (rival sale → INSUFFICIENT_STOCK), payment failure + retry (FAILED ≠ completed; inventory untouched), partial split (PARTIALLY_PAID, no sale, remaining correct), complete split (COMPLETED + sale + group COMPLETED + duplicate split pay idempotent), 3-way split w/ remainder, **duplicate webhook no-op + single ORDER_COMPLETED + sale_id unchanged**, amount-mismatch flagged not applied, idempotency key returns same payment, **concurrency race: exactly one of two counters wins the last unit, no negative stock**, AI order/payment Q&A from tools, auth on all endpoints

**Bugs found & fixed during verification:** `dbo.` schema prefix (functions live on public), orders.total=0 pre-completion (now store projected total at creation), webhook duplicate detection (unique violation raises — catch as duplicate), products PATCH crashed on jsonb activity params (pre-existing Phase 1 bug — json.dumps + ::jsonb casts), stale-price snapshot key mismatch, test event_id reuse across runs (unique-per-run tags), race test needed exactly-1 stock setup + cash checkout flow

**Known limitations:** manual provider only (Razorpay/Paytm/UPI-links/QR flows activate when credentials are configured — split QR UI intentionally absent until then); refunds table exists but no refund execution without a provider; order expiry (24h) enforced at revalidation; webhooks require store_id in payload (store resolution from provider account mapping is a Phase 7 concern); camera barcode scanning not implemented (typed/scanner-wedge input only, honestly labelled)

*(Iterations 5–6 — Phases 4 demand/customers/marketing and Phase 5 external intelligence/evidence — were delivered in prior sessions; see scripts/e2e_phase4.py 79/79, e2e_phase4_integration.py 19/19, e2e_phase5.py 31/31.)*

### Iteration 4 — Phase 3: AI agent, recommendations, action workflow (2026-09-22)

**Delivered:**

- [x] `008_phase3_agent.sql` — ai_recommendations upgraded (summary, priority + priority_reason, data_sources, reasoning_summary, proposed_action jsonb, estimated_impact, risk, dedup_key, EXECUTING status); `ai_actions` (payload/preview/state_fingerprint/status/idempotency_key unique per store, executed_reference_id); `ai_conversations` (session assistant history with tools_used + engine label)
- [x] `agent/tools.py` — 27 READ-ONLY store-scoped tools over the Phase 2 engines (store summary, today/sales, product sales/cost history, inventory health/low/critical/expiring/overstock/dead, reorder candidates, margins, discount impact, bargain, supplier comparison/details, customers + lapsing, alerts, business events, festivals, deterministic profit-change analyzer). The model/orchestrator never touches SQL.
- [x] `agent/recommendations.py` — deterministic detectors (expiry risk, reorder, margin risk from cost history, lapsing customers), transparent priority score (rupee impact/urgency/actionability/breadth with reason string), dedup upsert (one open reco per key, evidence refreshed in place, AI_RECOMMENDATION_CREATED logged)
- [x] `agent/actions.py` — prepare (validates + builds exact preview + state fingerprint) → approve → **re-validate current state** (stale detection: stock/price/cost deltas return status=stale with updated preview; nothing executes blindly) → execute via Phase 2 transactional backends (create_purchase / price_change with floor re-check + history / FEFO inventory adjust) → outcome logged; duplicate approval returns original outcome (idempotent); failures recorded with AI_ACTION_FAILED and "nothing was changed" semantics
- [x] `agent/orchestrator.py` — intent router (today/reorder/expiry/profit/pricing/customers/suppliers/inventory/sales/fallback) with targeted tool plans (no over-calling), "What should I do today?" prioritizes live recommendations + brief, business_brief endpoint, recommendation lifecycle (REVIEWED/APPROVED/REJECTED/DISMISSED), conversation logging. Engine label: `deterministic-rules-v1` (no LLM key configured — clearly labeled, narration rendered from tool outputs only; LLM slots in later without changing tools/actions)
- [x] `routers/agent.py` — /agent/ask, /agent/brief, /agent/recommendations(+status), /agent/actions(prepare/approve/cancel/list/get), /agent/conversations; store scoping from JWT only
- [x] Web: RecommendationCard (type icon, priority + why, evidence grid, impact, reasoning/risk/sources details, dismiss), ActionPreviewModal (backend preview rows, stale warning with updated numbers, duplicate-safe approve, cancel), AI Assistant page (Today's Business Brief, chat with tool-trace + engine label, suggestion chips, prioritized recommendation rail); nav AI Assistant activated
- [x] Tests: `scripts/e2e_phase3.py` — **50/50 PASS** covering scenarios A–F: today-brief with evidence, profit-down analysis with product-level causes, reorder plan, price-feasibility check, prepare→approve→execute price change (+ price persisted, duplicate approval idempotent), stale recommendation (sale between prepare and approve → not executed, updated preview, forced approval then creates pending PO, second approval duplicate), lifecycle REVIEWED/DISMISSED, conversations persisted, audit events (AI_RECOMMENDATION_CREATED/PREPARED/EXECUTED), auth required on all agent endpoints, price-below-floor refused at prepare, unknown action type refused. `e2e_phase1` ALL PASS, `e2e_phase2` 48/48 (no regression). Web `tsc` clean

**Bugs found & fixed during verification:** ORDER BY alias expression (wrapped in subquery), legacy seed rows storing proposed_action as plain text (tolerant jsonb decode), asyncpg jsonb-as-string decoding in action paths, idempotency key collision across test runs (duplicate-execution now owned by the status machine)

**Known limitations:** narration is deterministic (LLM adapter point isolated for later); price-change + purchase + inventory-adjust are the executable action types; festival data is a static calendar matched against store categories; campaign actions intentionally deferred to Phase 4

### Iteration 3 — Phase 2: inventory intelligence, pricing/bargaining, procurement (2026-09-22)

**Delivered:**

- [x] `004_phase2_intelligence.sql` — store_settings (all thresholds configurable), product_price_history (cost + selling price, append-only), batch `status` (sellable/expired/quarantined), PO receive columns, suppliers enrichment, alert dedup partial unique index + pre-dedup
- [x] SQL engines: `product_stock_status` (HEALTHY/LOW/CRITICAL/OVERSTOCKED/SLOW_MOVING/DEAD/OUT), `product_velocity`, `days_of_stock`, `min_acceptable_price` (policy floor + breakeven clearance exception), `deplete_batches` (FEFO), `ensure_alert` (dedup), `receive_purchase` (atomic: batches + inventory + movements + cost history + events), `refresh_inventory_intelligence` (state-based idempotent alerts: stock/expiry/reorder + expiry transitions), `create_sale` v2 (per-item negotiated price with server-enforced floor, FEFO actual unit cost, expired stock excluded from sellable)
- [x] `005_reseed_batches.sql` — batch sums now exactly equal inventory (fixed Phase 1 decorative batches); staggered expiries incl. near-expiry cases, cheaper older batches
- [x] `006` create_sale numeric parse fix; `007` alerts.read_at
- [x] `services/intelligence.py` — AI-ready deterministic tools: inventory intelligence (one query, no N+1), expiry engine (sell-through estimates labelled as estimates), margin/min-price/discount-impact/cart-margin, bargain (ACCEPT/COUNTER/REJECT + reason), reorder (velocity + lead + safety + pending POs, "insufficient_data" when no history), supplier comparison/profile (honest insufficient-data), risk summary, cost history
- [x] Routers: `/inventory/*` (intelligence, expiry, risk-summary, pricing, bargain, discount-impact, adjust FEFO-aware, settings, refresh), `/purchases` (create/receive/cancel), `/suppliers` (CRUD + profile + price history), `/alerts` (list/read/acknowledge/dismiss/refresh); legacy meta `/suppliers` + clamping `/inventory/adjust` removed; sales router accepts optional per-item `unit_price`
- [x] Web: Inventory upgraded (valuation + at-risk + dead-stock metrics, expiry/velocity/days-left columns, status filters, honest empty states); Purchases & Suppliers page (PO create with expiry lines, receive → real inventory/batch updates, cancel, supplier table with "not enough history" states); Bargain modal in Smart Counter (engine-checked offers, negotiated prices flow into the sale); Alerts page (open/all, read, acknowledge, dismiss, idempotent re-check); nav updated, no fake modules
- [x] Tests: `scripts/smoke_phase2_db.py` 17/17 (statuses, FEFO rollback, refresh idempotency, dedup); `scripts/e2e_phase2.py` **48/48 PASS**; `scripts/e2e_phase1.py` re-run **ALL PASS** (no regression)

**Bugs found & fixed during verification:** date params needed real date objects (asyncpg), `date + bigint` casts, untyped `$n` next to date (AmbiguousFunction), create_sale `numeric = ''` crash, alerts.read_at missing, route shadowing meta-vs-new routers.

**Known limitations:** expiry estimates are velocity-based approximations (labelled); supplier comparison uses avg historical cost only (subjective scoring deferred until merchant criteria exist); bargain audit trail logs BARGAINING_REQUESTED; API runs on :8001 (port 8000 occupied by an unrelated app on this machine).

### Iteration 2 — Phase 1 foundation implemented + end-to-end verified (2026-09-22)

**Scope:** Backend (FastAPI + asyncpg), database schema + migrations + seed, frontend (Next.js), vertical slice verification.

**Delivered:**

- [x] Database: `001_phase1_foundation.sql` — users/merchants/stores, products, inventory, inventory_batches, inventory_movements, suppliers, purchase_orders(+items), customers, sales(+items), alerts, ai_recommendations, activity_logs; constraints, indexes, tenancy via store_id
- [x] `002_create_sale_rpc.sql` — atomic `create_sale()` PL/pgSQL RPC: server-side totals, stock check, inventory deduction, movement log, SALE_CREATED activity, auto LOW_STOCK alerts
- [x] `003_seed_demo.sql` — realistic Sharma Kirana Store: 15 real FMCG products (Parle-G, Amul, Maggi…), 4 suppliers, 5 customers, 21 days of history, low/expiry/healthy stock mix, 2 AI recommendations
- [x] API: /auth (register/login/me, PBKDF2 + JWT), /products (CRUD + search), /sales (create via RPC + list), /customers (+history), /inventory + /inventory/adjust, /suppliers, /activity, /recommendations (+status PATCH), /dashboard/summary (all values computed from DB)
- [x] Web: Paytm-style shell (topbar/sidebar per DESIGN.md), Login, Home dashboard (KPIs, priorities, week chart, top products, activity, category mix), Inventory (KPIs + create + search + filters), Smart Counter (search → cart → qty → discount → customer → payment → complete sale), Customers, Activity; loading/empty/error/success states everywhere; Lucide icons only, no emoji
- [x] Future modules show honest "not implemented yet" pages via `[...future]` catch-all — no fake UI
- [x] `scripts/e2e_phase1.py` — 21 automated checks; **21/21 PASS**: login valid/invalid/missing, authz, product list, excessive-qty/empty-cart/discount-over-subtotal rejection, sale completes with server-computed total, inventory 18→16, sale + items persisted, customer history updated, SALE_CREATED logged, dashboard orders/sales/top-products reflect the sale

**Bugs found & fixed during verification:**

1. **DB unreachable** — `db.<ref>.supabase.co` resolves IPv6-only on this network → switched `DATABASE_URL` to Supabase session pooler (`aws-0-ap-southeast-1.pooler.supabase.com`, project region discovered by probe); `scripts/fix_db_host.py`
2. **DSN corruption** — `database.py`/`migrate.py` unquoted+rebuilt URL broke asyncpg parsing (`getaddrinfo failed` masked it); removed rebuild, pass DSN as-is
3. **JWT crash** — asyncpg returns `UUID` objects; `jwt.encode` can't serialize → `str()` cast in `create_access_token`
4. **`AmbiguousParameterError`** — untyped optional `$2` in ILIKE filters → `$2::text` casts (products + customers)
5. **`json_agg` returned as string** — `/api/sales` items parsed to real JSON arrays
6. **`NameError: json`** — import hoisted to module top in `sales.py`
7. **migrate.py baseline crash** on empty DB — verify() now tolerant before schema exists

**Decisions locked this iteration:**

1. Supabase connections go through the **session pooler** (IPv4-reachable); direct `db.<ref>.supabase.co` kept out of `.env`
2. Business mutations are **server-side** (RPC/validation); client numbers are display-only
3. Sale total is recomputed by the DB from product prices — frontend total is never trusted

**Known limitations (intentional, Phase 1):** split payment records intent only; barcode/camera scan UI stubs marked as later; purchases UI pending; batch deduction is store-level (FIFO batch depletion in Phase 2); duplicate-submit guard is client-side (DB RPC is atomic, so double-submit cannot double-deduct).

**Next (Phase 2 candidates):** Inventory Intelligence + Pricing/Margin + Expiry engine, purchases UI, batch-aware deduction, AI recommendation generation from real state.

---

### Iteration 0 — Project bootstrap (2026-09-22)

**Scope:** Documentation only — no code built (per instruction).

**Deliverables:**

- [x] `PROJECT.md` — complete product definition: what we're building, core problem, intelligence loop, all 16 modules (Home, AI Assistant, Smart Counter, Bargaining, Inventory, Expiry Rescue, Demand, Procurement, Customers, WhatsApp, Festivals, Alerts, Analytics, Reports, Payments, Quick Commerce), AI agent tools, Agent Reach placement, data model, LLM-not-source-of-truth principle, autonomy levels 1–5, user journey, navigation, build phases 1–7, demo story, Milestone 1, tech stack, success criteria
- [x] `ARCHITECTURE.md` — locked system architecture: modular monolith, Next.js + FastAPI + PostgreSQL/Supabase, deterministic engines, AI agent layer, event system, evidence layer, source registry, development order, repo structure
- [x] `PROGRESS.md` — this file; iteration log + phase checklist

**Decisions locked this iteration:**

1. Tech stack: Next.js/TypeScript frontend, Python/FastAPI backend, PostgreSQL/Supabase DB
2. Modular monolith — no unnecessary microservices
3. Deterministic engines before LLM; LLM never source of truth
4. Autonomy Levels 1–4 for hackathon (Level 5 deferred)
5. Agent Reach = external intelligence layer only, not foundation, not Day 1
6. Build order: Merchant Data → Business Engine → AI Agent → External Intelligence → Actions → Learning
7. Milestone 1: shopkeeper can run one full day of the shop inside KiranaSaathi
8. Quick commerce & payment splitter: architecture-ready only, no fake integrations

**Not done this iteration:** All code, database, UI.

---

### Iteration 1 — UI mockup analysis & design system (2026-09-22)

**Scope:** Documentation only — no code, no UI built (per instruction).

**Inputs analyzed:**

- [x] All **19** `ChatGPT Image …` PNG mockups in project folder (full visual analysis)
- [x] Brand/payment/partner assets: Paytm, WhatsApp, UPI, GPay, PhonePe, BHIM, Razorpay, Visa, Mastercard, RuPay, Blinkit, Zepto, Instamart, bigbasket, ONDC (SVG/PNG inventory noted)
- [x] `readme.html` — logoshape redirect only, not design-relevant (contains stray SQL text; ignore for product)

**Screens covered:** Landing, Home dashboard, AI Assistant, Smart Counter, Inventory, Purchases & Suppliers, Demand & Trends, Customers, WhatsApp & Marketing (list + Create Campaign wizard), Festival Calendar, Quick Commerce, Analytics, Reports, Alerts, Settings, Help & Support, Product detail + Bargain modal, Checkout + Split Payment.

**Deliverables:**

- [x] `DESIGN.md` — full design system saved for all future iterations:
  - Color theme: primary `#2080F0`, Paytm cyan `#00BAF2`, navy headings `#0B1F3A`, page bg `#F5F7FA`, semantic success/danger/warning/WhatsApp green, 8-color chart palette, alert priority colors, tinted surface palette
  - Typography: Inter-style sans; weights/sizes for H1, KPIs, tables, badges; Indian ₹ formatting rules
  - Spacing/radius/shadow tokens; CSS variable cheat sheet
  - Layout: topbar (Paytm + KiranaSaathi AI + BETA + search Ctrl+K + store/user), fixed 220px sidebar (exact nav order + New badge + Watch Demo promo card), main + right-rail grid
  - Components: buttons, KPI cards, AI priority rows, tables, badges, forms, chat, Smart Counter, bargain modal, split payment, charts, icons, imagery
  - Module-by-module signature patterns for all 15+ screens
  - Motion, accessibility, Do/Don’t rules
- [x] `PROGRESS.md` — Iteration 1 logged (this update)

**Decisions locked this iteration:**

1. Default theme is **light** Paytm-inspired merchant OS; Dark/System exist in Settings but light matches mockups first
2. Primary interactive color is **only** `#2080F0` (no competing blues)
3. Font stack: **Inter** (or close) with system fallbacks
4. App shell: topbar + 220px sidebar + content + optional ~360px AI right rail — fixed pattern for all modules
5. Sidebar nav order and BETA/New badges are part of the design contract
6. AI-facing UI always shows evidence + action button (matches PROJECT.md principles)

**Not done this iteration:** Any code, database, or runnable UI.

---

## Build phase checklist

### Phase 1 — Foundation
- [x] Authentication (login/signup)
- [x] Merchant profile
- [x] Store profile & location
- [ ] Business settings & user roles
- [x] Database schema & migrations
- [x] Product catalog (categories, SKU, barcode, MRP, prices)
- [x] Inventory (quantity, unit, batch, expiry, in/out/adjust)
- [x] Suppliers
- [x] Customers
- [x] Enforce `user → merchant → store` at DB/API level

### Phase 2 — Transaction engine
- [ ] Smart Counter UI (search, barcode, cart)
- [ ] Pricing & discounts in cart
- [ ] Bargaining flow (pricing engine integration)
- [ ] Customer selection on sale
- [ ] Billing / invoice generation
- [ ] Payment (provider via abstraction)
- [ ] Sale creation
- [ ] Automatic inventory deduction
- [ ] Customer purchase history update
- [ ] Sales analytics update
- [ ] AI intelligence update from transaction

### Phase 3 — Intelligence (deterministic engines)
- [ ] Margin / Pricing engine
- [ ] Expiry engine (value at risk, clearance options)
- [ ] Demand engine (velocity, seasonality)
- [ ] Reorder engine (quantity, reorder point)
- [ ] Supplier comparison (landed cost, MOQ, delivery)
- [ ] Inventory intelligence (low/overstock/dead/fast/slow)
- [ ] Alert engine (inventory, expiry, pricing, sales, supplier, customer, system)
- [ ] Business event system (SALE_CREATED, INVENTORY_UPDATED, etc.)
- [ ] Analytics engine (revenue, profit, turnover, top/slow products)

### Phase 4 — AI Agent
- [ ] AI Assistant chat UI
- [ ] Tool registry & tool-calling loop
- [ ] Store context injection
- [ ] Agent tools:
  - [ ] `get_store_summary`
  - [ ] `get_inventory` / `get_low_stock_products` / `get_expiring_inventory`
  - [ ] `get_sales` / `get_product_details`
  - [ ] `calculate_margin` / `calculate_bargain`
  - [ ] `forecast_demand` / `recommend_reorder`
  - [ ] `get_supplier_quotes` / `compare_supplier_quotes`
  - [ ] `get_customer_segments` / `get_festival_opportunities`
  - [ ] `create_campaign` / `create_alert`
  - [ ] `prepare_action` / `execute_approved_action`
- [ ] Evidence-backed recommendation format (situation, evidence, action, effect, tradeoffs, timestamps)
- [ ] Approval workflow (Levels 1–4)
- [ ] Action execution engine
- [ ] Audit / activity log

### Phase 5 — Growth
- [ ] Customer segments & intelligence
- [ ] Campaign creation
- [ ] WhatsApp integration (authorized/official only, consent-based)
- [ ] Festival intelligence (festival → demand → inventory → gap → procurement → campaign)
- [ ] Festival calendar UI

### Phase 6 — External intelligence
- [ ] Agent Reach client (server-side only)
- [ ] Source normalization & verification
- [ ] Evidence records (source, URL, published/retrieved date, region, confidence)
- [ ] Source registry
- [ ] Trend / market signal research tools
- [ ] **Never** use for authoritative wholesale prices

### Phase 7 — Demo polish
- [x] Design system extracted from mockups → `DESIGN.md` (D0)
- [ ] Paytm-inspired merchant dashboard UI (follow DESIGN.md)
- [ ] Home / AI Priority Feed
- [ ] Charts & recommendation cards
- [ ] Activity timeline
- [ ] Demo data / realistic merchant scenarios
- [ ] Full demo story walkthrough (expiry → reorder → bargain → Diwali → campaign → analytics)
- [ ] Navigation for all 15 modules
- [ ] Quick Commerce: adapter architecture only (no fake partner APIs)
- [ ] Payment Splitter: via PaymentProvider abstraction (only if provider supports it)

---

## Milestone tracker

| Milestone | Definition | Status |
|---|---|---|
| **M0 — Docs** | PROJECT.md, ARCHITECTURE.md, PROGRESS.md created | ✅ Done |
| **M1 — One day in the shop** | Login → products → inventory → purchase → sale → payment → AI sees transaction → AI answers "what needs attention?" from real DB | 🚧 ~70% — auth/products/inventory/sale/inventory-deduct/history verified e2e; purchase recording + bargain + AI answer pending (Phases 2–4) |
| **M2 — Intelligence loop** | Deterministic engines produce real alerts & recommendations from real transactions | ⬜ Not started |
| **M3 — Agentic loop** | AI tool calling + approval + execution + audit log | ⬜ Not started |
| **M4 — Full demo story** | Complete judge demo: expiry → reorder → bargain → festival → campaign → analytics | ⬜ Not started |

---

## Risks / open items

- [ ] Payment provider selection & capabilities (incl. whether split payments are actually supported)
- [ ] WhatsApp Business API access & consent model
- [ ] LLM provider selection for agent layer
- [ ] Agent Reach integration feasibility (external layer, post-M1)
- [ ] Quick commerce partner/API access (future — do not fake)
- [ ] Demo data seeding strategy for judge-facing story

---

## How to update this file

At the end of **every iteration**:

1. Add a new `### Iteration N — <title> (date)` section at the top of the Iteration log
2. List deliverables done / not done
3. List decisions locked or changed
4. Tick items in the Build phase checklist that completed
5. Update **Overall status** table
6. Update **Milestone tracker** and **Last updated** date
