"""Vision provider contracts — the seams where real models plug in.

Every adapter implements a tiny Protocol. Production providers require
external model runtimes (NVIDIA TAO/DeepStream, Ultralytics YOLO, PaddleOCR
paddlepaddle, pyzbar/zbar). When the runtime or weights are not installed,
the registry reports the provider as UNAVAILABLE and the pipeline answers
honestly — the UI never sees invented detections (spec §77).

The Mock* providers exist ONLY for tests (spec §6): they require an explicit
opt-in env var and are refused in production mode.
"""

from __future__ import annotations

import importlib.util
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from app.services.vision.types import BarcodeReading, Detection, Embedding, OcrResult

logger = logging.getLogger("kirana.vision")


class VisionProviderError(Exception):
    """Provider-level failure (runtime missing, model load failed, etc.)."""

    def __init__(self, message: str, code: str = "VISION_PROVIDER_ERROR"):
        super().__init__(message)
        self.code = code


def module_available(module: str) -> bool:
    """True when the module is importable (runtime/weights may still fail)."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    available: bool
    detail: str  # why unavailable, or version info when available


# ---------------------------------------------------------------------------
# Detector — "Where are the products?"
# ---------------------------------------------------------------------------
class RetailDetector(ABC):
    @abstractmethod
    def info(self) -> ProviderInfo: ...

    @abstractmethod
    def detect(self, image: bytes) -> list[Detection]:
        """Detect product regions in a JPEG/PNG frame."""


# ---------------------------------------------------------------------------
# BarcodeReader — highest-priority identity evidence
# ---------------------------------------------------------------------------
class BarcodeReader(ABC):
    @abstractmethod
    def info(self) -> ProviderInfo: ...

    @abstractmethod
    def read(self, image: bytes) -> list[BarcodeReading]:
        """Read barcodes from a JPEG/PNG frame (or crop)."""


# ---------------------------------------------------------------------------
# ProductEmbedder — visual representation for retrieval
# ---------------------------------------------------------------------------
class ProductEmbedder(ABC):
    @abstractmethod
    def info(self) -> ProviderInfo: ...

    @abstractmethod
    def embed(self, image: bytes) -> Embedding:
        """Embed one product crop for catalog similarity search."""

    def embed_batch(self, images: list[bytes]) -> list[Optional[Embedding]]:
        """Embed many crops, preserving order. Default: sequential fallback.
        Providers with real batch inference override this (one forward pass
        per chunk). Items that fail return None at their position — one bad
        crop never corrupts the rest of the batch (directive §11)."""
        results: list[Optional[Embedding]] = []
        for image in images:
            try:
                results.append(self.embed(image))
            except VisionProviderError:
                results.append(None)
        return results


# ---------------------------------------------------------------------------
# OCR — supporting evidence only (never the price authority)
# ---------------------------------------------------------------------------
class OcrEngine(ABC):
    @abstractmethod
    def info(self) -> ProviderInfo: ...

    @abstractmethod
    def extract_text(self, image: bytes) -> OcrResult:
        """Extract positioned text blocks from a product crop."""
