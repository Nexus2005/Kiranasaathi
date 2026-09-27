# Smart Counter — Camera Scan Implementation (end-to-end)

Status: **Phase 1 delivered — camera barcode scanning wired end-to-end into the existing
commerce flow.** Visual (non-barcode) product recognition is deliberately NOT faked; see
§7 Limitations.

---

## 1. Codebase audit (discovered, not assumed)

| Area | What exists | Where |
|---|---|---|
| Frontend | Next.js 16 (App Router), React 19, Tailwind 4, lucide-react | `apps/web` |
| Backend | FastAPI + asyncpg, modular monolith, routers under `app/routers`, services under `app/services` | `apps/api` |
| Database | Supabase PostgreSQL via session pooler; 14 migrations applied by `scripts/migrate.py` | `database/migrations` |
| Auth | JWT bearer → `get_current_user` resolves `user → merchant → store`; `store_id` ALWAYS derived server-side from the JWT | `apps/api/app/security.py` |
| Product schema | `products(store_id, name, category, sku, barcode, unit, mrp, selling_price, purchase_price, …)` + `inventory`, `inventory_batches` (FEFO), `inventory_movements` | `001_phase1_foundation.sql` |
| Commerce | `orders` state machine (Phase 6) → checkout → **atomic `create_sale()` RPC** (stock check, price floor, FEFO deduction, movements, SALE_CREATED activity, LOW_STOCK alerts) | `002_create_sale_rpc.sql`, `services/orders.py` |
| Barcode lookup | `GET /api/products/barcode/{code}` — handles FOUND / MULTIPLE_MATCHES / INACTIVE_PRODUCT / 404 UNKNOWN_BARCODE explicitly | `routers/products.py` |
| Existing Smart Counter | Search + typed barcode + cart + bargain modal + customer + discount + payment method → `POST /api/orders` → `/checkout/[orderId]` (revalidation, manual payment provider, split payments, receipt) | `apps/web/src/app/smart-counter/page.tsx` |
| Idempotency | `idempotency_keys` table + `retail.idempotent_result`; payments idempotent; `create_sale` atomic | Phase 7 |
| Events | `order_events`, `activity_logs` (SALE_CREATED, ORDER_COMPLETED, INVENTORY movements) | Phase 6/7 |
| Security posture | RLS enabled deny-by-default on all tables, zero grants to anon/authenticated (migration 013); verified by `scripts/verify_security.py` | — |
| Honest-limitations policy | PROGRESS.md Iterations 8–9: "not implemented" ≠ "broken"; camera/OCR explicitly listed as NOT implemented — never faked | `PROGRESS.md` |

**Decision: reuse everything.** No new tables, no new auth, no second sale engine, no new
state library. The camera is an *input device* for the existing counter.

## 2. What this phase adds

The counter gains a third input mode — **camera** — alongside search and typed barcode:

```
Camera (getUserMedia, rear camera preferred)
  → throttled frame capture (~450 ms, never 30fps upload)
  → barcode detection (native BarcodeDetector; ZXing/wasm fallback)
  → same-plane debounce (one add per physical scan; 2.5 s repeat lockout)
  → GET /api/products/barcode/{code}          (existing, store-scoped)
  → merchant-visible notice + auto-add to cart (stock-checked client-side,
     re-validated server-side at order creation and again at checkout)
  → POST /api/orders                          (existing)
  → /checkout/[orderId] → payment → atomic create_sale → inventory deduction
```

Camera frames never leave the device. Barcode detection runs fully in the browser —
no image upload endpoint exists, so there is nothing to secure or rate-limit on that path.

## 3. Files

**New**
- `apps/web/src/lib/use-camera.ts` — camera lifecycle: permission states
  (`idle → requesting → ready / denied / not_found / unavailable / insecure`),
  device enumeration + switching, facingMode `environment` on mobile, strict
  track-stop on unmount/mode change (no leaks).
- `apps/web/src/lib/use-barcode-scanner.ts` — detection engine:
  `BarcodeDetector` when available (Chrome/Android/Edge), `@zxing/browser`
  `BrowserMultiFormatReader` fallback (all 1-D retail formats + QR), 450 ms
  throttle, same-value debounce with `lastValue + lastAt` lockout, paused while
  a lookup is in flight (prevents double-fire on one frame).
- `apps/web/src/components/camera-workspace.tsx` — viewport + overlay
  (scan reticle, "last scan" chip), all 12 UI states from the spec:
  idle / requesting / denied / no-camera / insecure / ready / detecting /
  product-found / not-in-catalog / lookup-error / switching / stopping.

**Modified**
- `apps/web/src/app/smart-counter/page.tsx` — `InputModeSelector`
  (Camera | Barcode | Search — exactly the fallback hierarchy from the spec),
  camera mode wired to `resolveBarcode()` (same path the typed scanner uses),
  camera permission error mapping, notices for found/not-found states.
- `apps/web/package.json` — `@zxing/browser` dependency.
- `.env.example` — counter scan tunables (documented; consumed client-side via
  `NEXT_PUBLIC_*` so the browser can read them).

**Not modified (deliberately)**
- No backend changes: barcode resolution endpoint, orders, payments, sale RPC,
  inventory are untouched — they already satisfy the data/integrity contract.
- No schema migration: barcode lives on `products.barcode` (+ `product_barcodes`
  multi-barcode table from Phase 7, resolvable via `/retail/scan`).

## 4. Business rules honored (unchanged)

- Camera/barcode only ever answers *"which product?"* — never price, stock, tax,
  or totals. Those come from the DB at order creation (`orders` service) and
  again inside `create_sale()`.
- Detection never creates a sale. It can only add a *candidate line* to the
  in-memory cart; the merchant still reviews and proceeds to checkout.
- Unknown barcode → `UNKNOWN_BARCODE` 404 → UI offers Search manually / Add
  product; nothing is auto-created from a scan without merchant action.
- Out-of-stock / inactive / duplicate matches are surfaced explicitly (existing
  endpoint contract) and the client refuses the add with the same messages the
  typed path already used.

## 5. Verification plan

1. `tsc --noEmit` + `next build` in `apps/web` — clean.
2. Manual E2E (requires a physical camera + product packaging or a barcode
   displayed on a phone screen): Counter → Camera → allow → scan → cart →
   checkout → confirm → receipt; verify inventory deduction in DB.
3. Failure cases: deny permission, no camera device, scan unknown barcode,
   out-of-stock item, duplicate scan storm (debounce), navigating away
   mid-scan (track cleanup).

## 6. Security

- Store scoping: unchanged (JWT-derived; barcode lookup joins `store_id`).
- No new attack surface: no image upload, no new endpoints, no DB grants.
- RLS posture untouched (013 intact; `verify_security.py` still passes by
  construction — this phase ships no SQL).
- Camera permission is device-level; the stream is local-only and torn down on
  unmount and on tab-hidden pause.

## 7. Known limitations (honest boundaries)

- **Visual product recognition (object detection + embedding matching + OCR)
  is NOT implemented.** It requires a hosted ML inference runtime (YOLO-style
  detector + DINOv2-style embeddings + pgvector) that this environment cannot
  run. Per the project's no-fake-capability rule, the UI claims nothing:
  the camera mode is labelled **"Scan barcode"** and does exactly that. The
  `InputModeSelector` and component boundaries are ready for a
  `RetailDetector`/`ProductMatcher` backend when the inference service exists.
- Barcode-only: a product whose barcode is hidden/damaged must be entered via
  search (existing fallback) — by design, barcode is the highest-confidence
  identity in the hierarchy.
- `BarcodeDetector` availability varies by browser; ZXing/wasm fallback covers
  the rest at slightly higher CPU cost.
- Requires secure context (https or localhost) — enforced by the browser and
  surfaced as a first-class `insecure` state in the UI.
