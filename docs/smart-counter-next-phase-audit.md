# Smart Counter — Next-Phase Audit (Phase A)

Audit performed before any modification, per directive §2. Current verified
state (Iterations 11–13) treated as fact; nothing here is re-implemented.

## What already exists (DO NOT change)

| Component | Location | Status |
|---|---|---|
| Detector seam | `app/services/vision/providers/base.py` (`RetailDetector`) | provider-agnostic; nvidia / yolo / fullframe / mock registered |
| Pipeline (N-detection capable) | `app/services/vision/pipeline.py` | loops over `detections[]`; crop→barcode→embed→OCR→match per detection; product-level candidate dedup; global-index merge |
| OCR | `providers/production.py` (`RapidOcrEngine`) | live, Apache-2.0, positioned text blocks |
| Multi-signal matcher | `matcher.py` | evidence breakdown; thresholds 0.95/0.70 env-driven; pack-size re-rank; ambiguity cap |
| Embeddings store | `embeddings_store.py` | product-level retrieval, 384-dim pgvector, global VERIFIED merge |
| Global brain | `global_catalog.py` + migration 016 | canonical products/barcodes/images/embeddings, links, consent-gated contributions, recognition_events |
| Feedback ledger | `counter.py` `/events/{id}/feedback` | confirm/correct/reject, idempotent, hard negatives, failure_reason context |
| Tests | `scripts/e2e_counter_real_vision.py` 53/53, `e2e_counter_learning.py` 49/49, `e2e_counter_vision.py` 25/25 | live-API suites |

## What must NOT change

- Barcode exact-identity dominance; DB as sole price/stock authority.
- Atomic checkout/inventory (`create_sale` RPC path).
- Thresholds 0.95/0.70 without dataset evidence.
- RLS deny-by-default posture; store scoping from JWT.
- Feedback semantics (checkout ≠ ground truth).
- All currently passing suites.

## What must change (this phase)

| Area | Change | Files |
|---|---|---|
| Detector | Add `RtDetrRetailDetector` (Apache-2.0 RT-DETR via transformers) as the first REAL multi-object provider; keep fullframe; keep yolo adapter but marked NON-PRODUCTION (AGPL) | `providers/production.py`, `registry.py` |
| Embedder | Batch `embed_batch(crops)` with order preservation, per-item failure isolation, max-batch + OOM fallback | `providers/base.py`, `providers/production.py`, `pipeline.py` |
| Pipeline | Batch embedding + timings per stage for N products | `pipeline.py` |
| Dataset engine | Quality-gated builder from recognition_events + consent; dedup (exact + perceptual hash); leakage-safe splits; sample schema | new `scripts/dataset/`, `app/services/training/` |
| Model registry | `model_registry` table + promotion state machine | migration 017 |
| Kaggle integration | Reproducible training package (train/evaluate/resume), checkpoint-resume, credential-free (token via env) | new `training/` |
| UI | N-detected-products candidate list with per-detection states | `smart-counter/page.tsx`, `use-vision-recognize.ts` |
| Docs | datasets, training, model-registry, resumable-training | `docs/` |

## Dataset license audit (researched, recorded in manifest format)

| Dataset | Task | License | Commercial | Decision |
|---|---|---|---|---|
| SKU-110K | dense retail detection | CC BY-NC 4.0 (dataset) | NO | research/benchmark only, NON-PRODUCTION |
| RPC | checkout scene detection | CC BY-NC-SA 4.0 | NO | research/benchmark only, NON-PRODUCTION |
| RP2K | fine-grained recognition | research-only request/license (non-commercial) | NO | research only, NON-PRODUCTION |
| Products-10K | fine-grained recognition | custom research license | REVIEW | research only, NON-PRODUCTION |
| RT-DETR (model, Apache-2.0) | detection | Apache-2.0 | YES | PRODUCTION candidate (weights: RT-DETRv2/resnet checkpoints, Apache-2.0) |
| DINOv2 (Apache-2.0) | embeddings | Apache-2.0 | YES | production baseline (unchanged) |
| RapidOCR (Apache-2.0) | OCR | Apache-2.0 | YES | production (unchanged) |

Every downloaded dataset gets a machine-readable manifest with
`commercial_use_status` and `LICENSE_REVIEW_REQUIRED` flags where unclear
(`scripts/dataset/manifests/*.json`). Unclear-license data never enters a
production training artifact.

## Risks

1. **GPU memory** (RTX 4050 6GB): RT-DETR-50 is heavy; use RT-DETRv2-S/R18
   or ResNet-50 inference with fp16; batch embedding capped by env.
2. **Kaggle new KGAT token**: kagglehub 1.0.2 doesn't accept it; REST API
   with `Authorization: Bearer` verified working → dataset acquisition uses
   a thin REST client (kagglehub left for future versions).
3. **Non-commercial datasets**: used only to TRAIN/evaluate research
   checkpoints (fine for non-commercial research use); production deployment
   ships Apache-2.0 models; dataset licenses are marked NON-PRODUCTION in
   the registry, and the promotion gate enforces `license_status`.
4. **Kaggle runtime limits**: never defeated — checkpoint/terminate/resume.
