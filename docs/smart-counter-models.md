# Smart Counter — Models & Vision Providers (spec §76)

## Model selection & licensing

| Stage | Provider option | Model / source | License | Runtime requirement | Notes |
|---|---|---|---|---|---|
| Detection | `nvidia` | NVIDIA TAO `retail_object_detection` (DINO-based) | NVIDIA NGC model license — **verify terms for your deployment** before commercial use | Deployed Triton/DeepStream endpoint (GPU host or Jetson); adapter calls its HTTP infer endpoint via `COUNTER_NVIDIA_URL` | Closest to the checkout-counter problem (spec: detects retail objects on a counter). Output: boxes + confidence; identity is NOT its job |
| Detection | `yolo` | Ultralytics YOLO with local weights (SKU-110K-finetuned recommended) | AGPL-3.0 for the `ultralytics` package — **AGPL compliance requires either open-sourcing your app or an Ultralytics commercial license**; SKU-110K dataset is CC BY-NC 4.0 (non-commercial) — fine-tuned weights inherit restrictions for commercial use | `ultralytics` + weights file on GPU/CPU host | Simplest local path for development; license check REQUIRED before production. **Not wired as a production dependency** — the detector interface is provider-agnostic so a licensed retail detector can replace it without touching the pipeline |
| Detection | `fullframe` | none — geometric single-product region (center 80% of frame) | none (no weights, no external terms) | CPU | Single-product counter mode for the vertical slice; identity comes from barcode/embeddings, never from the detector. Replace with a licensed retail detector (nvidia/yolo) for multi-product scenes — no pipeline changes required |
| Barcode | `zxing` | zxing-cpp | Apache-2.0 | self-contained wheels (Windows/macOS/Linux) | EAN-13/8, UPC-A/E, Code128/39, ITF, QR, DataMatrix; no external shared library — dependable default where pyzbar's zbar DLL chain is fragile (e.g. Windows VC++ runtime) |
| Barcode | `pyzbar` | zbar | LGPL-2.1 (zbar) / pyzbar MIT | zbar shared library | EAN-13/8, UPC-A/E, Code128/39, ITF, QR; exact decodes |
| Embeddings | `torch` | DINOv2 ViT-S/14 (`vit_small_patch14_dinov2` via timm; facebookresearch weights) | Apache-2.0 — **safe for commercial use** | `torch` + `timm` (+ GPU strongly recommended; CPU works but slowly) | Retrieval features without fine-tuning; 142M-image pretraining; embeddings + pgvector = store-specific recognition. timm naming differs from the original repo (`dinov2_vits14` → `vit_small_patch14_dinov2`); input size is configurable (`COUNTER_EMBEDDING_INPUT_SIZE`, default 224 — native checkpoint is 518px) |
| OCR | `rapidocr` | RapidOCR — PP-OCR det/rec models via ONNX Runtime | Apache-2.0 | `rapidocr-onnxruntime` (pip wheels, no paddle binary) | LIVE default. EVIDENCE ONLY — never the price source (§2, §12). Returns positioned text blocks (text, confidence, bbox) |
| OCR | `paddleocr` | PaddleOCR PP-OCR (Hindi/Marathi capable) | Apache-2.0 | `paddleocr` + `paddlepaddle` | Alternative lineage; same model family |
| Vector search | — | pgvector | PostgreSQL License | Supabase extension (enabled by migration 015) | Store-scoped cosine search; ANN index when a dimension is registered |

**Do not mix licenses blindly (§76):** DINOv2 + pgvector + PaddleOCR are the
commercially safe default. YOLO/SKU-110K needs a license review; NVIDIA TAO
models need NGC terms review. Document your choice here before shipping.

## Runtime requirements (§40, §42)

- The FastAPI app runs fine with NO vision packages installed — every adapter
  probes availability at construction and reports honestly; `/api/counter/health`
  shows what is missing. No fake output is ever produced (§77).
- GPU inference must NOT run inside Vercel serverless (§74). Deployment shapes:
  - **Dev (this repo default):** API without model runtimes → camera barcode
    scanning works, visual recognition returns 503 `VISION_NOT_CONFIGURED`.
  - **Inference host:** separate machine/container with GPU (e.g. RTX 4050
    laptop for experiments) running the same FastAPI app with
    `COUNTER_DETECTOR_PROVIDER=yolo|nvidia`, `COUNTER_EMBEDDING_PROVIDER=torch`,
    weights downloaded once at deploy (never per-request, §43).
  - Device auto-detect: `cuda if available else cpu`; slow-CPU configurations
    must be surfaced via `/health/counter` latency, not silently accepted (§42).

## Environment variables (§75)

```
COUNTER_DETECTOR_PROVIDER=      # nvidia | yolo | fullframe | (empty = not configured)
COUNTER_BARCODE_PROVIDER=       # zxing | pyzbar | (empty)
COUNTER_EMBEDDING_PROVIDER=     # torch | (empty)
COUNTER_OCR_PROVIDER=           # rapidocr | paddleocr | (empty)
COUNTER_MATCH_AMBIGUITY_WINDOW=0.02   # visual tie window (raw vs raw) for pack-size re-rank
COUNTER_MATCH_AMBIGUITY_PENALTY=0.30  # ambiguous variant → below auto-add
COUNTER_MATCH_AMBIGUITY_MIN_RUNNER=0.75 # runner-up must be plausible (same-family) before the penalty fires
COUNTER_NVIDIA_URL=             # Triton infer endpoint (nvidia provider)
COUNTER_YOLO_WEIGHTS=           # path to .pt weights (yolo provider)
COUNTER_EMBEDDING_MODEL=vit_small_patch14_dinov2
COUNTER_EMBEDDING_INPUT_SIZE=224
COUNTER_FULLFRAME_COVERAGE=0.8
COUNTER_DEVICE=                 # cuda | cpu | (empty = auto)
COUNTER_TOP_K=5
COUNTER_AUTO_ADD_THRESHOLD=0.95
COUNTER_REVIEW_THRESHOLD=0.70
COUNTER_MAX_IMAGE_MB=6
COUNTER_MAX_IMAGE_WIDTH=1920
COUNTER_MAX_IMAGE_HEIGHT=1920
COUNTER_MAX_DETECTIONS=12
COUNTER_DETECTION_MIN_CONFIDENCE=0.5
COUNTER_OCR_LANG=en             # en | hi | mr ...
# test-only:
COUNTER_ENV=test COUNTER_ALLOW_MOCK_VISION=1   # both required for mocks
```

## How the store gets "trained" (§10, §21, §54)

**Product onboarding creates store-specific visual reference embeddings used
for retrieval; it does not retrain the underlying vision model.** No global
retraining, ever — DINOv2 is frozen at inference:

```
Add product → (optional) scan barcode → merchant captures 3–10 reference photos
  → POST /api/counter/products/{id}/embeddings (per photo; view=front/back/side/angled)
  → DINOv2 embedding stored per image (model+version stamped, §44)
  → product becomes searchable by camera via store-scoped retrieval
```

Onboarding IS catalog enrollment/indexing, not training. Recognition improves
as reference coverage grows (more views/conditions per SKU); merchant
corrections (replacing a wrong candidate) are logged with the original
prediction for later hard-negative mining (§52–53).
