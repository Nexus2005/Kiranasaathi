# Smart Counter — Learning Flywheel & Global Product Brain

How recognition improves over time WITHOUT retraining models per scan, and
without ever letting predictions reinforce themselves.

## The one-sentence rule

**Product onboarding creates store-specific visual reference embeddings used
for retrieval; it does not retrain the underlying vision model.**

## Two knowledge layers, never mixed

```
GLOBAL PRODUCT KNOWLEDGE                 STORE PRODUCT KNOWLEDGE
(merchant-agnostic identity)             (merchant business data)
├── canonical product ID                 ├── store_id + product_id
├── canonical_name, brand, category      ├── selling_price, cost
├── pack_size (+ normalized)             ├── stock, tax
├── barcodes (global_product_barcodes)   ├── supplier, reorder level
├── reference images                     ├── local reference images
├── verified visual embeddings           ├── local visual embeddings
└── verification states                  └── recognition_events (store-scoped)
```

- Store A NEVER sees Store B price/cost/stock/supplier/customers/sales (§28).
- The shared layer contains identity only. `product_global_links` associates
  a store product with the canonical product (`barcode_match` |
  `merchant_confirmed` | `auto_matched`).
- DINOv2 is frozen at inference. No scan ever trains a model.

## Scan-once, reuse-everywhere (verified e2e)

```
Store A enrolls "Choco Wafer 55g" (barcode 8901058001234)
  → auto-canonicalized into global_products (idempotent on name+pack)
  → barcode registered as CANDIDATE
Store B scans the same barcode
  → GET /api/counter/global/lookup/{barcode} → canonical product found
  → B links its own product (its own price/stock)
  → B RECOGNIZES the pack with an EMPTY local index:
        crop → DINOv2 → global VERIFIED embeddings → link → store product
```

No re-enrollment, no retraining — retrieval quality is shared.

## Consent-gated contributions (never automatic ground truth)

```
merchant enrollment (with prior explicit consent)
  → product_visual_contributions row = PENDING_REVIEW
  → NEVER searchable by recognition (only ACCEPTED embeddings are)
  → operator quality gate → promote → global_product_embeddings ACCEPTED
```

- `store_settings.visual_contribution_consent` (NULL = undecided = NOT
  consented). Merchant images are never silently shared (§44).
- `promote` is the ONLY path into the authoritative global index; it is an
  explicit operator action (`GET /global/contributions/{id}/verify?approve=`),
  idempotent, and it preserves provenance (`source_store_id`).

## Recognition events + feedback semantics

Every detection writes a `recognition_events` row (prediction first,
`user_action` NULL). The merchant's action UPDATES the same row — idempotent
per (frame_id, detection_id):

| user_action          | feedback_label    | meaning |
|---|---|---|
| MERCHANT_CONFIRMED   | STRONG_POSITIVE   | explicit "Confirm" — strongest signal |
| AUTO_ACCEPTED        | WEAK_POSITIVE     | accepted without interaction |
| MERCHANT_CORRECTED   | STRONG_NEGATIVE   | hard negative: predicted X, actual Y |
| MERCHANT_REJECTED    | STRONG_NEGATIVE   | "Wrong product", no replacement |
| REMOVED / MANUAL_REPLACEMENT / UNRESOLVED | UNCERTAIN | contextual, not automatically an error |

Non-negotiables (§48, §71):

- `checkout_success` is NEVER `recognition_correct`. Feedback labels derive
  from merchant ACTION, not transaction outcome.
- Payment/stock/network failures are transaction failures. A FAILED checkout
  row keeps its label; `failure_reason` records context
  (PAYMENT_FAILED ≠ recognition error).
- Feedback never modifies stock/price/tax, never creates sales, never blocks
  checkout. It is analytical data only (§46).
- No self-reinforcement: a prediction is never stored as ground truth
  (§24). Only merchant-verified actions are eligible learning signals.

## Multi-signal matching (deterministic, no LLM)

```
base       = 0.30 (single_signal_floor) + 0.65 × visual_similarity
ocr bonus  = 0.25 × ocr_score            (additive, when OCR agrees)
meta bonus = 0.10 × (½·brand_match + ½·pack_size_match)
```

- Exact barcode still short-circuits everything (0.99).
- Thresholds UNCHANGED by this work: AUTO_ADD 0.95 / REVIEW 0.70 (§39) —
  changing them requires a real evaluation dataset.
- Evidence breakdown returned per match (visual/ocr/brand/pack/barcode) for
  UI explanation and debugging — a 0.93 score is a similarity score, never
  "93% accuracy" (§38).

### Similar-SKU discrimination (Dairy Milk 24g/55g/110g problem)

Real DINOv2 similarity between same-brand size variants is ~0.99 (gap ≈
0.001), while genuinely different products differ by ~0.05+:

1. When candidates are near-tied within `COUNTER_MATCH_AMBIGUITY_WINDOW`
   (0.02, raw similarity vs raw similarity), OCR pack-size agreement
   re-ranks the winner.
2. The variant-ambiguity penalty applies ONLY when the runner-up is itself
   plausible — same-packaging-family territory
   (`COUNTER_MATCH_AMBIGUITY_MIN_RUNNER` = 0.75). A weak cross-product
   runner-up (~0.67 on a two-product counter) is not a tied SKU variant;
   capping the winner over it would be a false alarm.
3. A visually-tied winner WITHOUT pack evidence is penalized below AUTO_ADD
   → merchant review instead of a silent wrong-size bill line.
4. Retrieval is product-level: one candidate per product (best embedding),
   so a product's five reference images never masquerade as "variants".

## Three learning levels (§23)

| Level | What happens | Frequency |
|---|---|---|
| 1. Retrieval enrichment | merchant-confirmed observation becomes eligible reference data | continuous |
| 2. Global catalog enrichment | verified, consented contributions enter the shared index | gated |
| 3. Model improvement | offline training/eval/promotion from the verified dataset | future, periodic |

Level 3 is NOT implemented. Architecture prepared: verified event ledger +
hard negatives + provenance = future training dataset with no leakage
(splits must respect store/session boundaries, §40/§72).

## Product-level statistics (honest naming)

`GET /api/counter/products/{id}/recognition-stats` reports attempts,
merchant-confirmed, corrections, uncertain, avg similarity/confidence,
barcode/OCR-assisted counts — explicitly labelled **"merchant-confirmed
recognition rate (not model accuracy)"** (§26/§51).

## Security posture

Migration 016 preserves the 013/015 deny-by-default posture: RLS enabled +
FORCED on all seven new tables, zero grants to anon/authenticated, EXECUTE
closed. `verify_security.py` PASS. All store-scoped queries constrain
`store_id` from the JWT; global endpoints expose identity only.

## Limitations (honest)

- Full-frame detector still handles ONE product region per frame (§29–30):
  the pipeline, API contract, and matcher already process N detections;
  a licensed multi-object detector plugs in without pipeline changes.
- No batch embedding yet (§61) — sequential crops, ~50ms each on the RTX 4050.
- No automatic promotion: operator verification is manual by design until a
  quality-gate service exists.
- Level-3 (model training) pipeline: dataset export only, not implemented.
