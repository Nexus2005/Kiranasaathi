# Smart Counter — Vision API (spec §35–37, §45–47)

All endpoints require `Authorization: Bearer <JWT>`. The store is resolved
server-side from the token — never from the request (§46). Store scoping is
enforced in every query AND by the store-scoped SQL match function.

## GET /api/counter/health

Provider readiness for the authenticated merchant (§45).

```json
{
  "detector":  { "name": "none", "available": false, "detail": "detector provider not configured" },
  "barcode":   { "name": "none", "available": false, "detail": "..." },
  "embedder":  { "name": "none", "available": false, "detail": "..." },
  "ocr":       { "name": "none", "available": false, "detail": "..." },
  "pgvector":  true,
  "recognition_ready": false,
  "allow_mock": false,
  "thresholds": { "auto_add": 0.95, "review": 0.70 }
}
```

`recognition_ready = detector.available && embedder.available && pgvector`.
The UI displays a truthful "Visual recognition not configured" badge when false.

## POST /api/counter/recognize

`multipart/form-data` with `frame` (JPEG/PNG, ≤ COUNTER_MAX_IMAGE_MB).
Rate-limited per store (30 frames / 10 s in-process; move to Redis for scale).

Response (§36):

```json
{
  "frame_id": "frame_1a2b3c4d",
  "timestamp": 1730000000000,
  "detections": [
    {
      "detection": { "detection_id": "det_…", "bbox": {"x":120,"y":80,"width":210,"height":320}, "confidence": 0.96, "class_name": "object" },
      "match": {
        "product_id": "…", "confidence": 0.94, "method": "BARCODE",
        "reasons": ["exact barcode 8901234567890 in store catalog"],
        "alternatives": [{"product_id": "…", "similarity": 0.71, "rank": 2}],
        "barcode": "8901234567890"
      },
      "status": "IDENTIFIED",
      "auto_addable": true,
      "requires_review": false,
      "latency_ms": 143,
      "ocr_mrp_evidence": 14.0
    }
  ],
  "skipped_stages": {},
  "latency_ms": 210
}
```

- `status`: `IDENTIFIED` (≥ auto_add threshold) | `REVIEW_REQUIRED` | `UNRESOLVED`
- `method`: `BARCODE` | `VISUAL` | `OCR` | `MANUAL` | `COMBINED`
- `timings_ms`: per-stage latency (detect/barcode/embed/vector_search/ocr/match)
- `ocr_mrp_evidence` is EVIDENCE ONLY — the charged price always comes from
  the DB via the existing order/checkout path (§2, §25).
- If the single crop matches nothing in one-product frames, the pipeline
  retries once with the full frame (`skipped_stages.crop_fallback` records
  when this path was used) — multi-product scenes never do this.
- Detection NEVER creates a bill line (§16): the frontend adds candidates to
  the cart; the merchant confirms; `POST /api/orders` revalidates everything.

## POST /api/counter/products/{product_id}/embeddings

Onboarding (§21): `multipart/form-data` with `image` and optional `view`
(front|back|side|angled|shelf). Product must belong to the caller's store.
Idempotent on identical bytes (hash-dedup). Registers the model dimension on
first use (adds a typed pgvector column + ivfflat ANN index).

```json
{ "embedding_id": "…", "image_id": "…", "model": "vit_small_patch14_dinov2", "version": "v1", "dimensions": 384 }
```

Re-uploading identical bytes returns the EXISTING embedding id (idempotent
enrollment) — the pipeline never duplicates reference vectors.

## GET /api/counter/products/{product_id}/embeddings

Lists stored embeddings for a product (model/version/dimension traceability, §44).

## Recognition events + merchant feedback (learning flywheel)

Every detection records a `recognition_events` row (prediction first,
`user_action` NULL). The response carries `recognition_event_id` per
detection. Merchant actions UPDATE the same row — idempotent per
(frame_id, detection_id):

```
POST /api/counter/events/{event_id}/feedback
  { "action": "MERCHANT_CONFIRMED" | "MERCHANT_CORRECTED" | "MERCHANT_REJECTED",
    "confirmed_product_id": "<uuid when correcting>" }
```

Labels derive from the ACTION (confirm=STRONG_POSITIVE, correct/reject=
STRONG_NEGATIVE hard negatives) — never from checkout success. Payment/
stock failures are not recognition failures. Feedback never touches
stock/price/checkout.

## Global Product Brain (identity-only, shared across stores)

| Endpoint | Purpose |
|---|---|
| `GET /api/counter/global/lookup/{barcode}` | barcode → canonical product (id, name, brand, pack, verification_status) + local link if any. NO business data |
| `POST /api/counter/global/create` | canonicalize a store product (idempotent on name+pack) + link |
| `POST /api/counter/global/link` | link store product ↔ global product |
| `POST /api/counter/global/contribute` | consent-gated contribution → PENDING_REVIEW (never automatic ground truth) |
| `GET /api/counter/global/contributions/{id}/verify?approve=` | operator quality gate → promotes into the AUTHORITATIVE global index (ACCEPTED) |
| `GET /api/counter/products/{id}/recognition-stats` | per-product "merchant-confirmed recognition rate (not model accuracy)" |

Recognition merges the store index with the global VERIFIED index via the
store's `product_global_links` — Store B recognizes Store A's enrolled
product with an empty local index (`skipped_stages.global_merge` records
when this path fired). No merchant business data ever crosses stores.

Enrollment auto-canonicalizes barcoded products into the global layer
(idempotent) and records a contribution candidate ONLY when the merchant's
`visual_contribution_consent` is set (NULL = not consented).

## GET /health/counter (unauthenticated)

Liveness summary for orchestrators only: `{"status", "database", "counter"}` —
no provider names, no model paths, no sensitive detail.

## Error contract (§37)

| HTTP | code | when |
|---|---|---|
| 400 | `INVALID_IMAGE` | not JPEG/PNG (magic-byte check), corrupt, too small |
| 400 | `IMAGE_TOO_LARGE` | exceeds COUNTER_MAX_IMAGE_MB |
| 401 | — | missing/invalid token |
| 404 | — | product not in this store |
| 429 | — | frame/upload rate limit exceeded |
| 503 | `VISION_NOT_CONFIGURED` | providers absent / pgvector missing — honest, with `missing: [...]` |
| 503 | `INFERENCE_SERVICE_UNAVAILABLE` | configured provider failed at call time |
| 503 | `EMBEDDING_DIMENSION_MISMATCH` | embedding doesn't match registered model |

Never a raw stack trace (§37): `unhandled` returns a generic 500; provider
failures map to the structured codes above.

## Security (§46–48, §78)

- Store resolved from JWT; every query + the SQL match function constrain `store_id`.
- RLS deny-by-default (013 posture) preserved on the new tables; grants closed.
- pgvector's extension-owned helper functions are the ONLY EXECUTE surface for
  anon/authenticated — pure math, no data access (documented in 015 + verifier).
- No raw camera images persisted by recognition (hashes only in `product_images`).
- Rate limits per store on both frame inference and onboarding uploads.
- Logs carry ids, latencies, model versions, confidences — never secrets, never
  raw images without explicit consent (§48).
