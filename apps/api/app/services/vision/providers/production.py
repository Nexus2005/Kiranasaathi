"""Production vision providers — real adapters; honest when runtimes are absent.

Each adapter checks its runtime/weights AT CONSTRUCTION and reports
availability truthfully. `info().available == False` means the pipeline will
skip that stage (or the API returns 503 VISION_NOT_CONFIGURED) — the adapter
never fabricates output (spec §77).

Supported production adapters (choose via env, see config.py):
  * COUNTER_DETECTOR_PROVIDER=nvidia   — NVIDIA TAO retail_object_detection
        (deployed via Triton/DeepStream; this adapter calls its HTTP gRPC-
        exposed REST endpoint). Requires the service URL.
  * COUNTER_DETECTOR_PROVIDER=yolo     — Ultralytics YOLO local weights
        (GPU or CPU). Requires `ultralytics` + weights file.
  * COUNTER_BARCODE_PROVIDER=pyzbar    — zbar shared library + pyzbar.
  * COUNTER_EMBEDDING_PROVIDER=torch   — DINOv2 (facebookresearch) via
        torch/timm. GPU strongly recommended; CPU works but slowly.
  * COUNTER_OCR_PROVIDER=paddleocr     — PaddleOCR (PP-OCRv5, Hindi/Marathi
        capable). Requires paddlepaddle + paddleocr packages.

Weights licensing is documented in docs/smart-counter-models.md (spec §76).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from abc import ABC
from typing import Any, Optional

from app.services.vision.providers.base import (
    BarcodeReader,
    module_available,
    ProductEmbedder,
    ProviderInfo,
    RetailDetector,
    VisionProviderError,
)
from app.services.vision.types import (
    BarcodeReading,
    BBox,
    Detection,
    Embedding,
    OcrResult,
    TextBlock,
)

logger = logging.getLogger("kirana.vision")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _decode_image(image: bytes):
    """Decode JPEG/PNG bytes to a PIL RGB image; raises on invalid input."""
    if not image:
        raise VisionProviderError("empty image", "INVALID_IMAGE")
    if len(image) > _env_int("COUNTER_MAX_IMAGE_BYTES", 6_000_000):
        raise VisionProviderError("image too large", "IMAGE_TOO_LARGE")
    if not module_available("PIL"):
        raise VisionProviderError(
            "Pillow required for image preprocessing", "VISION_RUNTIME_MISSING"
        )
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(image))
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise VisionProviderError("invalid image data", "INVALID_IMAGE") from exc
    if img.width < 16 or img.height < 16:
        raise VisionProviderError("image too small", "INVALID_IMAGE")
    return img.convert("RGB")


def _jpeg_bytes(img, quality: int = 88) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


# ===========================================================================
# DETECTORS
# ===========================================================================
class NvidiaRetailDetector(RetailDetector):
    """NVIDIA TAO `retail_object_detection` (DINO-based) via a deployed
    Triton/DeepStream HTTP endpoint. The endpoint receives the JPEG frame
    and returns bounding boxes; this adapter maps them to `Detection`s.

    Deploy model: ngc registry model nvidia/tao/retail_object_detection
    Triton HTTP endpoint expected at COUNTER_NVIDIA_URL (e.g.
    http://jetson-orin:8000/v2/models/retail_object_detection/infer).
    """

    def __init__(self) -> None:
        self.url = _env("COUNTER_NVIDIA_URL")
        self.model = _env("COUNTER_NVIDIA_MODEL", "retail_object_detection")
        self.timeout = _env_float("COUNTER_NVIDIA_TIMEOUT_S", 4.0)
        self.min_conf = _env_float("COUNTER_DETECTION_MIN_CONFIDENCE", 0.5)
        self.max_det = _env_int("COUNTER_MAX_DETECTIONS", 12)
        self._available = bool(self.url)
        self._detail = (
            f"endpoint={self.url}" if self._available else "COUNTER_NVIDIA_URL not set"
        )
        if self._available:
            import urllib.request

            self._urllib = urllib.request

    def info(self) -> ProviderInfo:
        return ProviderInfo("nvidia", self._available, self._detail)

    def detect(self, image: bytes) -> list[Detection]:
        if not self._available:
            raise VisionProviderError(
                "NVIDIA detector endpoint not configured", "VISION_NOT_CONFIGURED"
            )
        img = _decode_image(image)
        w, h = img.size
        payload = json.dumps(
            {
                "inputs": [
                    {
                        "name": "INPUT",
                        "shape": [1],
                        "datatype": "BYTES",
                        "data": [base64.b64encode(_jpeg_bytes(img)).decode()],
                    }
                ]
            }
        ).encode()
        req = self._urllib.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._urllib.urlopen(req, timeout=self.timeout) as res:
                body = json.loads(res.read().decode() or "{}")
        except Exception as exc:  # noqa: BLE001
            raise VisionProviderError(
                f"NVIDIA inference failed: {exc}", "INFERENCE_SERVICE_UNAVAILABLE"
            ) from exc

        outputs: list[dict[str, Any]] = body.get("outputs", [])
        boxes: list[Detection] = []
        for out in outputs:
            for det in out.get("data", []):
                conf = float(det.get("confidence", 0))
                if conf < self.min_conf:
                    continue
                boxes.append(
                    Detection(
                        detection_id=f"nv_{len(boxes)}_{int(conf * 10000)}",
                        bbox=BBox(
                            x=float(det.get("x", 0)) * w,
                            y=float(det.get("y", 0)) * h,
                            width=float(det.get("width", 0)) * w,
                            height=float(det.get("height", 0)) * h,
                        ),
                        confidence=conf,
                        class_name=str(det.get("class", "object")),
                    )
                )
                if len(boxes) >= self.max_det:
                    break
        boxes.sort(key=lambda d: -d.confidence)
        return boxes[: self.max_det]


class RtDetrRetailDetector(RetailDetector):
    """Real multi-object retail detector: RT-DETR (REAList-Time Detection
    Transformer, Apache-2.0) via HuggingFace transformers. Detects the
    COCO 'bottle' class plus generic objects on a counter scene; returns N
    boxes — identity comes from the catalog, never from the detector.

    Production license: Apache-2.0 (unlike Ultralytics AGPL / SKU-110K
    CC BY-NC weights). Runs on CUDA when available, CPU otherwise.
    """

    def __init__(self) -> None:
        self.model_name = _env("COUNTER_DETECTOR_MODEL", "PekingU/rtdetr_v2_r50vd")
        self.device = _env("COUNTER_DEVICE") or None
        self.min_conf = _env_float("COUNTER_DETECTION_MIN_CONFIDENCE", 0.5)
        self.max_det = _env_int("COUNTER_MAX_DETECTIONS", 12)
        self._model: Any = None
        self._processor: Any = None
        self._torch: Any = None
        self._detail = ""
        if not module_available("transformers"):
            self._detail = "transformers package not installed"
        else:
            try:
                import torch
                from transformers import AutoModelForObjectDetection, AutoImageProcessor

                self._torch = torch
                self._processor = AutoImageProcessor.from_pretrained(self.model_name)
                self._model = AutoModelForObjectDetection.from_pretrained(self.model_name)
                self._model.eval()
                if self.device:
                    self._model.to(self.device)
                elif torch.cuda.is_available():
                    self._model.to("cuda")
                    self.device = "cuda"
                self._detail = f"{self.model_name} loaded ({self.device or 'cpu'})"
            except Exception as exc:  # noqa: BLE001 — weights download blocked etc.
                self._model = None
                self._detail = f"RT-DETR load failed: {exc}"
        self._available = self._model is not None

    def info(self) -> ProviderInfo:
        return ProviderInfo("rtdetr", self._available, self._detail)

    def detect(self, image: bytes) -> list[Detection]:
        if not self._available or self._model is None:
            raise VisionProviderError(
                "RT-DETR detector not available", "VISION_NOT_CONFIGURED"
            )
        import time as _t

        started = _t.monotonic()
        img = _decode_image(image)
        w, h = img.size
        inputs = self._processor(images=img, return_tensors="pt")
        if self.device:
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with self._torch.no_grad():
            outputs = self._model(**inputs)
        target_sizes = self._torch.tensor([[h, w]])
        if self.device:
            target_sizes = target_sizes.to(self.device)
        results = self._processor.post_process_object_detection(
            outputs, threshold=self.min_conf, target_sizes=target_sizes
        )[0]
        boxes: list[Detection] = []
        scores = results["scores"].tolist()
        labels = results["labels"].tolist()
        xyxy = results["boxes"].tolist()
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        id2label = getattr(self._model.config, "id2label", {})
        for i in order[: self.max_det]:
            x1, y1, x2, y2 = xyxy[i]
            boxes.append(
                Detection(
                    detection_id=f"rtdetr_{len(boxes)}_{int(scores[i] * 10000)}_{int((_t.monotonic() - started) * 1000)}",
                    bbox=BBox(
                        x=float(x1),
                        y=float(y1),
                        width=max(1.0, float(x2 - x1)),
                        height=max(1.0, float(y2 - y1)),
                    ),
                    confidence=float(scores[i]),
                    class_name=str(id2label.get(labels[i], "object")),
                )
            )
        return boxes


class FullFrameRegionDetector(RetailDetector):
    """Single-product counter mode (no trained weights, spec §77-honest).

    Treats the central capture region of the frame as ONE product crop.
    Identity still comes from the catalog (barcode/embeddings) — this
    provider only answers 'where'. It exists so the detection seam can be
    exercised end-to-end before a licensed retail detector (NVIDIA TAO or
    an approved YOLO build) is deployed; it never fabricates detections of
    products it cannot see.
    """

    def __init__(self) -> None:
        self.coverage = min(1.0, max(0.1, _env_float("COUNTER_FULLFRAME_COVERAGE", 0.8)))
        self._available = True
        self._detail = f"single-product region ({int(self.coverage * 100)}% of frame)"

    def info(self) -> ProviderInfo:
        return ProviderInfo("fullframe", self._available, self._detail)

    def detect(self, image: bytes) -> list[Detection]:
        if not self._available:
            raise VisionProviderError(
                "fullframe detector not available", "VISION_NOT_CONFIGURED"
            )
        img = _decode_image(image)
        w, h = img.size
        bw = w * self.coverage
        bh = h * self.coverage
        return [
            Detection(
                detection_id="ff_0_10000",
                bbox=BBox(
                    x=(w - bw) / 2.0,
                    y=(h - bh) / 2.0,
                    width=bw,
                    height=bh,
                ),
                confidence=1.0,  # geometric region, not a model probability
                class_name="counter_region",
            )
        ]


class YoloRetailDetector(RetailDetector):
    """Ultralytics YOLO with local weights (SKU-110K-finetuned weights are a
    good fit: they detect dense retail 'object' boxes, spec §7). One class:
    product regions — identity comes from the catalog, not the detector."""

    def __init__(self) -> None:
        self.weights = _env("COUNTER_YOLO_WEIGHTS")
        self.min_conf = _env_float("COUNTER_DETECTION_MIN_CONFIDENCE", 0.5)
        self.iou = _env_float("COUNTER_DETECTION_IOU", 0.45)
        self.max_det = _env_int("COUNTER_MAX_DETECTIONS", 12)
        self.device = _env("COUNTER_DEVICE") or None  # auto: cuda if available
        self._model: Any = None
        self._detail = ""
        if not module_available("ultralytics"):
            self._detail = "ultralytics package not installed"
        elif not self.weights:
            self._detail = "COUNTER_YOLO_WEIGHTS not set"
        else:
            try:
                from ultralytics import YOLO

                self._model = YOLO(self.weights)
                names = getattr(self._model, "names", {})
                self._detail = f"weights={os.path.basename(self.weights)} classes={len(names)}"
            except Exception as exc:  # noqa: BLE001
                self._model = None
                self._detail = f"weights load failed: {exc}"
        self._available = self._model is not None

    def info(self) -> ProviderInfo:
        return ProviderInfo("yolo", self._available, self._detail)

    def detect(self, image: bytes) -> list[Detection]:
        if not self._available or self._model is None:
            raise VisionProviderError(
                "YOLO detector not available", "VISION_NOT_CONFIGURED"
            )
        img = _decode_image(image)
        results = self._model.predict(
            source=img,
            conf=self.min_conf,
            iou=self.iou,
            max_det=self.max_det,
            device=self.device,
            verbose=False,
        )
        boxes: list[Detection] = []
        for r in results:
            for b in r.boxes:
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
                cls_id = int(b.cls[0]) if b.cls is not None and len(b.cls) else 0
                boxes.append(
                    Detection(
                        detection_id=f"yolo_{len(boxes)}_{int(float(b.conf[0]) * 10000)}",
                        bbox=BBox(x=x1, y=y1, width=max(1.0, x2 - x1), height=max(1.0, y2 - y1)),
                        confidence=float(b.conf[0]),
                        class_name=str(r.names.get(cls_id, "object")),
                    )
                )
        boxes.sort(key=lambda d: -d.confidence)
        return boxes[: self.max_det]


# ===========================================================================
# BARCODE
# ===========================================================================
class PyzbarBarcodeReader(BarcodeReader):
    """zbar via pyzbar — EAN-13/8, UPC-A/E, Code128/39, ITF, QR and more."""

    def __init__(self) -> None:
        self._available = module_available("pyzbar")
        self._detail = (
            "pyzbar installed (zbar shared lib must also be present)"
            if self._available
            else "pyzbar not installed"
        )
        if self._available:
            try:
                from pyzbar import pyzbar as _pz  # noqa: F401 — forces lib load

                self._pz = _pz
                self._detail = "pyzbar + zbar ready"
            except Exception as exc:  # noqa: BLE001 — missing zbar shared lib
                self._available = False
                self._detail = f"zbar library unavailable: {exc}"

    def info(self) -> ProviderInfo:
        return ProviderInfo("pyzbar", self._available, self._detail)

    def read(self, image: bytes) -> list[BarcodeReading]:
        if not self._available:
            raise VisionProviderError(
                "pyzbar barcode reader not available", "VISION_NOT_CONFIGURED"
            )
        img = _decode_image(image)
        found = self._pz.decode(img)
        readings: list[BarcodeReading] = []
        for sym in found:
            data = sym.data.decode("ascii", "ignore").strip()
            if data:
                readings.append(
                    BarcodeReading(
                        value=data,
                        format=str(sym.type),
                        confidence=0.99,  # zbar returns exact decodes
                    )
                )
        return readings


class ZxingBarcodeReader(BarcodeReader):
    """zxing-cpp (Apache-2.0) — EAN-13/8, UPC-A/E, Code128/39, ITF, QR,
    DataMatrix and more. Self-contained wheels (no external shared library),
    which makes it the dependable default on Windows hosts where pyzbar's
    zbar DLL chain is fragile."""

    def __init__(self) -> None:
        self._available = module_available("zxingcpp")
        self._detail = (
            "zxing-cpp ready" if self._available else "zxingcpp not installed"
        )
        if self._available:
            try:
                import zxingcpp  # noqa: F401 — forces native module load

                self._zx = zxingcpp
            except Exception as exc:  # noqa: BLE001 — broken native module
                self._available = False
                self._detail = f"zxing-cpp unavailable: {exc}"

    def info(self) -> ProviderInfo:
        return ProviderInfo("zxing", self._available, self._detail)

    def read(self, image: bytes) -> list[BarcodeReading]:
        if not self._available:
            raise VisionProviderError(
                "zxing barcode reader not available", "VISION_NOT_CONFIGURED"
            )
        img = _decode_image(image)
        import numpy as np

        arr = np.array(img)[:, :, ::-1]  # RGB -> BGR for zxing-cpp
        try:
            found = self._zx.read_barcodes(arr)
        except Exception as exc:  # noqa: BLE001 — decode-level failure
            raise VisionProviderError(
                f"zxing decode failed: {exc}", "INFERENCE_SERVICE_UNAVAILABLE"
            ) from exc
        return [
            BarcodeReading(value=r.text, format=r.format.name, confidence=0.99)
            for r in found
            if r.text.strip()
        ]


# ===========================================================================
# EMBEDDINGS (DINOv2)
# ===========================================================================
class DinoV2Embedder(ProductEmbedder):
    """DINOv2 visual embeddings (facebookresearch weights via timm;
    Apache-2.0). Produces retrieval-ready features without fine-tuning
    (spec §6). Crops are resized to the model input size, normalized with
    ImageNet stats, and mean-pooled over patch tokens.

    Model naming: the original facebookresearch name is `dinov2_vits14`;
    timm ships the same weights as `vit_small_patch14_dinov2` (and the
    _b14/_l14 variants). The default here is the timm name; weights are
    downloaded once at first construction (never per request, §43).
    """

    def __init__(self) -> None:
        self.model_name = _env("COUNTER_EMBEDDING_MODEL", "vit_small_patch14_dinov2")
        self.input_size = _env_int("COUNTER_EMBEDDING_INPUT_SIZE", 224)
        self.device = _env("COUNTER_DEVICE") or None
        self._model: Any = None
        self._transform: Any = None
        self._detail = ""
        if not (module_available("torch") and module_available("timm")):
            self._detail = "torch + timm required for DINOv2 embeddings"
        else:
            try:
                import torch
                from timm import create_model

                self._torch = torch
                # img_size is required for DINOv2 in timm: the pretrained
                # checkpoint is native 518px; 224 works via pos-embed
                # interpolation (standard retrieval practice, ~2x faster).
                kwargs: dict[str, Any] = {"pretrained": True}
                if "dinov2" in self.model_name:
                    kwargs["img_size"] = self.input_size
                self._model = create_model(self.model_name, **kwargs)
                self._model.eval()
                if self.device:
                    self._model.to(self.device)
                elif self._torch.cuda.is_available():
                    self._model.to("cuda")
                    self.device = "cuda"
                from torchvision import transforms

                self._transform = transforms.Compose(
                    [
                        transforms.Resize(
                            (self.input_size, self.input_size)
                        ),
                        transforms.ToTensor(),
                        transforms.Normalize(
                            mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
                        ),
                    ]
                )
                self._detail = (
                    f"{self.model_name} loaded ({self.device or 'cpu'}, "
                    f"input={self.input_size})"
                )
            except Exception as exc:  # noqa: BLE001 — weights download blocked etc.
                self._model = None
                self._detail = f"DINOv2 load failed: {exc}"
        self._available = self._model is not None

    def info(self) -> ProviderInfo:
        return ProviderInfo("dinov2", self._available, self._detail)

    def embed(self, image: bytes) -> Embedding:
        if not self._available:
            raise VisionProviderError(
                "DINOv2 embedder not available", "VISION_NOT_CONFIGURED"
            )
        img = _decode_image(image)
        tensor = self._transform(img).unsqueeze(0)
        if self.device:
            tensor = tensor.to(self.device)
        with self._torch.no_grad():
            out = self._model.forward_features(tensor)
            # timm DINOv2: forward_features returns patch tokens (B, N, C);
            # pool tokens (CLS + patches) with mean, then L2-normalize.
            pooled = out[:, 1:, :].mean(dim=1) if out.dim() == 3 else out.mean(dim=1)
            vec = self._torch.nn.functional.normalize(pooled, dim=1)[0]
        return Embedding.create(
            [float(v) for v in vec.tolist()], self.model_name, "v1"
        )

    def embed_batch(self, images: list[bytes]) -> list[Optional[Embedding]]:
        """True batch inference: one forward pass per chunk (preserves order;
        per-item failure isolation; OOM-safe chunk halving, directive §11)."""
        if not self._available:
            raise VisionProviderError(
                "DINOv2 embedder not available", "VISION_NOT_CONFIGURED"
            )
        max_batch = max(1, _env_int("COUNTER_EMBED_BATCH_SIZE", 8))
        results: list[Optional[Embedding]] = [None] * len(images)

        def run_chunk(idxs: list[int]) -> None:
            # Per-item decode isolation: one corrupt crop becomes None at its
            # position; it never aborts the chunk (directive §11).
            tensors: list = []
            valid: list[int] = []
            for i in idxs:
                try:
                    tensors.append(self._transform(_decode_image(images[i])))
                    valid.append(i)
                except VisionProviderError as exc:
                    logger.warning("crop %d failed to decode: %s", i, exc)
                    results[i] = None
            if not valid:
                return
            try:
                batch = self._torch.stack(tensors).to(self.device)
                with self._torch.no_grad():
                    out = self._model.forward_features(batch)
                    pooled = out[:, 1:, :].mean(dim=1) if out.dim() == 3 else out.mean(dim=1)
                    vecs = self._torch.nn.functional.normalize(pooled, dim=1)
                for pos, i in enumerate(valid):
                    results[i] = Embedding.create(
                        [float(v) for v in vecs[pos].tolist()], self.model_name, "v1"
                    )
            except Exception as exc:  # noqa: BLE001 — OOM or transient failure
                if len(valid) == 1:
                    logger.warning("embedding failed for 1 crop: %s", exc)
                    return  # item stays None (isolated failure)
                mid = len(valid) // 2
                run_chunk(valid[:mid])
                run_chunk(valid[mid:])

        pending = [i for i in range(len(images))]
        for start in range(0, len(pending), max_batch):
            run_chunk(pending[start : start + max_batch])
        return results


# ===========================================================================
# OCR (RapidOCR / PaddleOCR)
# ===========================================================================
class RapidOcrEngine:
    """RapidOCR (Apache-2.0): PP-OCR detection+recognition models served via
    ONNX Runtime — pip-installable, no paddle binary dependency, Windows-safe.
    Same model lineage as PaddleOCR; evidence only, never the price source.
    Returns positioned text blocks for the deterministic evidence parser."""

    def __init__(self) -> None:
        self.max_side = _env_int("COUNTER_OCR_MAX_SIDE", 960)
        self.min_conf = _env_float("COUNTER_OCR_MIN_CONFIDENCE", 0.5)
        self._engine: Any = None
        self._detail = ""
        if not module_available("rapidocr_onnxruntime"):
            self._detail = "rapidocr_onnxruntime package not installed"
        else:
            try:
                from rapidocr_onnxruntime import RapidOCR

                self._engine = RapidOCR()
                self._detail = "RapidOCR ready (PP-OCR ONNX)"
            except Exception as exc:  # noqa: BLE001 — runtime/model load failed
                self._engine = None
                self._detail = f"RapidOCR init failed: {exc}"
        self._available = self._engine is not None

    def info(self) -> ProviderInfo:
        return ProviderInfo("rapidocr", self._available, self._detail)

    def extract_text(self, image: bytes) -> OcrResult:
        if not self._available:
            raise VisionProviderError(
                "RapidOCR not available", "VISION_NOT_CONFIGURED"
            )
        import time as _t

        started = _t.monotonic()
        img = _decode_image(image)
        if max(img.size) > self.max_side:
            ratio = self.max_side / max(img.size)
            img = img.resize(
                (max(16, int(img.width * ratio)), max(16, int(img.height * ratio)))
            )
        import numpy as np

        try:
            result, _elapse = self._engine(np.array(img))
        except Exception as exc:  # noqa: BLE001 — inference-level failure
            raise VisionProviderError(
                f"RapidOCR inference failed: {exc}", "INFERENCE_SERVICE_UNAVAILABLE"
            ) from exc
        blocks: list[TextBlock] = []
        for line in result or []:
            try:
                box_pts, text, conf = line[0], line[1], float(line[2])
            except (IndexError, TypeError, ValueError):
                continue
            text = (text or "").strip()
            if not text or conf < self.min_conf:
                continue
            xs = [float(p[0]) for p in box_pts]
            ys = [float(p[1]) for p in box_pts]
            blocks.append(
                TextBlock(
                    text=text,
                    confidence=conf,
                    bbox=BBox(
                        x=min(xs), y=min(ys),
                        width=max(xs) - min(xs), height=max(ys) - min(ys),
                    ),
                )
            )
        return OcrResult(
            text_blocks=blocks,
            duration_ms=int((_t.monotonic() - started) * 1000),
        )
class PaddleOcrEngine:
    """PaddleOCR PP-OCR (multilingual incl. Hindi/Marathi; Apache-2.0).
    Evidence only — extracted text feeds the matcher, never the price."""

    def __init__(self) -> None:
        self.lang = _env("COUNTER_OCR_LANG", "en")
        self.max_side = _env_int("COUNTER_OCR_MAX_SIDE", 960)
        self._ocr: Any = None
        self._detail = ""
        if not (module_available("paddleocr") and module_available("paddle")):
            self._detail = "paddleocr + paddlepaddle packages not installed"
        else:
            try:
                from paddleocr import PaddleOCR

                self._ocr = PaddleOCR(use_angle_cls=True, lang=self.lang, show_log=False)
                self._detail = f"PP-OCR ready (lang={self.lang})"
            except Exception as exc:  # noqa: BLE001
                self._ocr = None
                self._detail = f"PaddleOCR init failed: {exc}"
        self._available = self._ocr is not None

    def info(self) -> ProviderInfo:
        return ProviderInfo("paddleocr", self._available, self._detail)

    def extract_text(self, image: bytes) -> OcrResult:
        if not self._available:
            raise VisionProviderError(
                "PaddleOCR not available", "VISION_NOT_CONFIGURED"
            )
        import time as _t

        started = _t.monotonic()
        img = _decode_image(image)
        if max(img.size) > self.max_side:
            ratio = self.max_side / max(img.size)
            img = img.resize(
                (max(16, int(img.width * ratio)), max(16, int(img.height * ratio)))
            )
        import numpy as np

        result = self._ocr.ocr(np.array(img), cls=True)
        blocks: list[TextBlock] = []
        for page in result or []:
            for line in page or []:
                try:
                    box_pts, (text, conf) = line[0], line[1]
                except (IndexError, TypeError, ValueError):
                    continue
                text = (text or "").strip()
                if not text or conf < 0.5:
                    continue
                xs = [p[0] for p in box_pts]
                ys = [p[1] for p in box_pts]
                blocks.append(
                    TextBlock(
                        text=text,
                        confidence=float(conf),
                        bbox=BBox(
                            x=min(xs), y=min(ys),
                            width=max(xs) - min(xs), height=max(ys) - min(ys),
                        ),
                    )
                )
        return OcrResult(
            text_blocks=blocks,
            duration_ms=int((_t.monotonic() - started) * 1000),
        )
