"""Mock vision providers — TEST-ONLY (spec §6, §66).

They exist so the pipeline, matcher, confidence engine, retrieval and API
layers can be integration-tested without GPU/model downloads. They are
refused outside tests: `COUNTER_ALLOW_MOCK_VISION=1` must be set explicitly
AND settings.vision_allow_mock must be true. Production mode (the default)
raises ProviderUnavailableError.

The mock detector draws deterministic regions; the mock embedder produces
deterministic, clusterable vectors from image bytes so synthetic fixture
images actually retrieve the right catalog entries in tests.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from app.services.vision.providers.base import (
    BarcodeReader,
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

MOCK_EMBEDDING_DIM = 64  # 8x8 pixel grid


def _pixel_embedding(data: bytes, dim_side: int = 8) -> list[float]:
    """Deterministic, crop-robust pseudo-embedding from IMAGE PIXELS.

    Decodes the image, downsamples to dim_side×dim_side grayscale, and
    L2-normalizes. Robust to JPEG re-encoding and to crops of a mostly-
    uniform fixture (like real embeddings, unlike raw byte hashing).
    """
    import io as _io

    from PIL import Image

    img = Image.open(_io.BytesIO(data)).convert("L").resize(
        (dim_side, dim_side)
    )
    vec = [px / 255.0 for px in img.tobytes()]
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


class MockRetailDetector(RetailDetector):
    """Deterministic region detector for pipeline tests.

    Returns ONE box covering most of the frame (deterministic) so fixture
    frames exercise the whole pipeline: crop ≈ frame, the mock barcode
    sentinel survives re-encode, and the crop embedding matches the fixture
    embedding the catalog was onboarded with. Real detectors return many
    boxes; region logic is theirs to own."""

    def info(self) -> ProviderInfo:
        return ProviderInfo("mock_detector", True, "test-only; deterministic regions")

    def detect(self, image: bytes) -> list[Detection]:
        if not image:
            raise VisionProviderError("empty image", "INVALID_IMAGE")
        return [
            Detection(
                detection_id=f"mock_{len(image) % 9973}",
                bbox=BBox(x=4.0, y=4.0, width=232.0, height=232.0),
                confidence=0.97,
            )
        ]


class MockBarcodeReader(BarcodeReader):
    """Reads a barcode 'printed' into fixture bytes via a sentinel prefix."""

    SENTINEL = b"KS-BARCODE:"

    def info(self) -> ProviderInfo:
        return ProviderInfo("mock_barcode", True, "test-only; sentinel-prefixed fixtures")

    def read(self, image: bytes) -> list[BarcodeReading]:
        if self.SENTINEL not in image:
            return []
        idx = image.index(self.SENTINEL)
        tail = image[idx + len(self.SENTINEL):]
        value = tail[:13].split(b"\x00")[0].decode("ascii", "ignore").strip()
        if not value.isdigit():
            return []
        return [BarcodeReading(value=value, format="EAN_13", confidence=0.99)]


class MockProductEmbedder(ProductEmbedder):
    """Deterministic embedding from image bytes (see _hash_to_unit_vector)."""

    MODEL = "mock-embedding"
    VERSION = "v1"

    def info(self) -> ProviderInfo:
        return ProviderInfo("mock_embedder", True, "test-only; sha256 pseudo-embedding")

    def embed(self, image: bytes) -> Embedding:
        return Embedding.create(
            _pixel_embedding(image), self.MODEL, self.VERSION
        )


class MockOcrEngine:
    """Returns a small fixed text block so OCR normalization paths run."""

    def info(self) -> ProviderInfo:
        return ProviderInfo("mock_ocr", True, "test-only; fixed text")

    def extract_text(self, image: bytes) -> OcrResult:
        text = "MRP Rs 55  NET WT 55g"
        return OcrResult(
            text_blocks=[TextBlock(text=text, confidence=0.9, bbox=None)],
            duration_ms=1,
        )
