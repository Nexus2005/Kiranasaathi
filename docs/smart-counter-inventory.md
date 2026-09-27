# Smart Counter — Inventory Enrollment & External Enrichment (Iteration 16)

Inventory onboarding is now the **primary product-enrollment path**. The
merchant experiences "add stock"; visual identity happens behind the scenes.

## The flow

```
INVENTORY → Add products
  ├─ [Scan Barcode]      ← camera (primary; reuses the counter scan stack)
  ├─ [Search Product]    ← existing catalog
  └─ [Add Manually]      ← fallback

barcode → local store lookup
   ├─ FOUND      → confirm/adjust stock → (optionally) add reference images
   └─ NOT FOUND  → Open Food Facts (backend, cached)
        ├─ FOUND      → PREFILL identity + "verify before saving" banner
        └─ NOT FOUND  → manual form (unbarcoded local products OK)

merchant confirms → store price/cost/stock (SOURCE: Store)
                  → image (capture | upload | attributed external)
                  → DINOv2 embedding → product_visual_embeddings
                  → recognition-ready in Smart Counter
```

## Source-of-truth rules (enforced in code, not convention)

| Data | Source | Enforced by |
|---|---|---|
| name / brand / pack / category | OFF prefill **only after merchant edit+confirm** | prefill goes through the form, never straight to DB |
| selling price / cost / stock / reorder | **Store (merchant)** | `CreateFromExternalIn` — business fields are required inputs; OFF payload has no such fields |
| reference image provenance | `product_images.source` = `merchant` \| `external` | external images always carry `source_url` + `attribution` (CC BY-SA duty) |
| checkout price | store DB at checkout | unchanged deterministic path |

## Open Food Facts integration

* **API v3** (`/api/v3/product/{code}.json`), v2 deprecated upstream.
* Custom User-Agent `KiranaSaathiAI/1.0 (enrichment; …)`; reads need no auth.
* **Server-side only** — the browser never talks to OFF.
* **Cached** in `external_product_cache` (provider, version, barcode unique):
  positive rows 30 days, negative (not-found) rows 7 days — protects the
  documented 15 read req/min/IP limit and keeps failures honest.
* **License posture:** OFF database is ODbL 1.0, images CC BY-SA — recorded in
  `license_note` on every cache row and shown in the UI banner. Enrichment
  only; nothing is relicensed as proprietary.

## Similar-variant honesty (new matcher rule)

Real finding from the 10-product catalog: **Lays Classic 52g vs Lays Magic
Masala 52g** print identical evidence ("LAYS 52 g"). Two changes:

1. `ocr_score` now measures recall over the OCR's own tokens — measuring
   against the name made score depend on name length, so OCR appeared to
   discriminate variants it cannot see.
2. **Shared-evidence cap:** when winner AND runner-up both match brand+pack,
   those bonuses prove nothing discriminative → winner is capped below
   auto-add and goes to merchant review. Either candidate may be right;
   **silent auto-add is not.**

Liveness/dark-path behaviors (both exercised in tests): OFF unavailable →
`provider_unavailable`, manual flow unaffected; unknown barcode → manual form;
duplicate barcode → 409; duplicate image bytes → same embedding (idempotent).

## Admin catalog

`/catalog-admin` (store-scoped) — recognition status derives from REAL state:
`NOT_ENROLLED` (no embeddings) / `ENROLLED` (embeddings, no global link) /
`VERIFIED` (embeddings + global link) / `NEEDS_REVIEW` (merchant corrections
on record). Sections: recognition ready, missing images, missing barcodes,
needs review. "Missing images" is the actionable list for visual coverage.

## Smart Counter input modes

* **Camera** — visual recognition + barcode in one view (unchanged).
* **Barcode** — now **camera-first** (`BarcodeScannerPanel`), typed entry is
  the explicit "Enter barcode manually" fallback. Same `resolveBarcode` code
  path for camera hits, typed input, and USB wedge.
* **Search** — unchanged fallback.
