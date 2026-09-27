"""Model registry — load vision providers once, report health honestly (§43).

Provider selection (env, all optional):
  COUNTER_DETECTOR_PROVIDER   = rtdetr | nvidia | yolo | fullframe | mock
  COUNTER_BARCODE_PROVIDER    = pyzbar | zxing | mock
  COUNTER_EMBEDDING_PROVIDER  = torch | mock
  COUNTER_OCR_PROVIDER        = rapidocr | paddleocr | mock

`mock` requires COUNTER_ALLOW_MOCK_VISION=1 AND COUNTER_ENV=test
(test-only, spec §6); anything else raises MockVisionNotAllowed.

Construction is lazy and cached; `health()` returns ProviderInfo per stage
so /health/counter and the recognition pipeline can degrade honestly. A
provider whose runtime is missing reports available=False and the pipeline
skips that stage — nothing is faked (spec §77).
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional

from app.services.vision.providers.base import (
    BarcodeReader,
    ProductEmbedder,
    ProviderInfo,
    RetailDetector,
    VisionProviderError,
)
from app.services.vision.providers import production as prod

logger = logging.getLogger("kirana.vision")

_MOCK_ENV = "COUNTER_ALLOW_MOCK_VISION"
_TEST_ENV = "COUNTER_ENV"


class MockVisionNotAllowed(VisionProviderError):
    def __init__(self) -> None:
        super().__init__(
            "mock vision providers are test-only; set COUNTER_ALLOW_MOCK_VISION=1 "
            "and COUNTER_ENV=test to enable",
            "VISION_NOT_CONFIGURED",
        )


def _provider_name(env_key: str) -> str:
    return os.environ.get(env_key, "").strip().lower()


def _mock_allowed() -> bool:
    return (
        os.environ.get(_MOCK_ENV, "").strip() == "1"
        and os.environ.get(_TEST_ENV, "").strip() == "test"
    )


def _guard_mock(name: str) -> None:
    if name == "mock" and not _mock_allowed():
        raise MockVisionNotAllowed()


@dataclass
class VisionHealth:
    detector: ProviderInfo
    barcode: ProviderInfo
    embedder: ProviderInfo
    ocr: ProviderInfo
    pgvector: bool
    allow_mock: bool

    def as_dict(self) -> dict:
        return {
            "detector": {
                "name": self.detector.name,
                "available": self.detector.available,
                "detail": self.detector.detail,
            },
            "barcode": {
                "name": self.barcode.name,
                "available": self.barcode.available,
                "detail": self.barcode.detail,
            },
            "embedder": {
                "name": self.embedder.name,
                "available": self.embedder.available,
                "detail": self.embedder.detail,
            },
            "ocr": {
                "name": self.ocr.name,
                "available": self.ocr.available,
                "detail": self.ocr.detail,
            },
            "pgvector": self.pgvector,
            "allow_mock": self.allow_mock,
        }

    def recognition_ready(self) -> bool:
        """Visual recognition needs detection + embeddings + pgvector.
        Barcode is an optional accelerator stage — its absence does not
        block the visual path (spec §8: skip expensive visual work when a
        barcode is present, not 'no recognition without barcode')."""
        return self.detector.available and self.embedder.available and self.pgvector


class VisionRegistry:
    """Lazy, cached, thread-safe provider registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, Optional[object]] = {}
        self._error: dict[str, str] = {}

    # -- construction -------------------------------------------------------
    def _construct(self, key: str):
        name = _provider_name(self._env_key(key))
        if not name:
            return None
        try:
            if key == "detector":
                if name == "nvidia":
                    return prod.NvidiaRetailDetector()
                if name == "yolo":
                    return prod.YoloRetailDetector()
                if name == "rtdetr":
                    return prod.RtDetrRetailDetector()
                if name == "fullframe":
                    return prod.FullFrameRegionDetector()
                if name == "mock":
                    _guard_mock(name)
                    from app.services.vision.providers.mock import MockRetailDetector

                    return MockRetailDetector()
            elif key == "barcode":
                if name == "pyzbar":
                    return prod.PyzbarBarcodeReader()
                if name == "zxing":
                    return prod.ZxingBarcodeReader()
                if name == "mock":
                    _guard_mock(name)
                    from app.services.vision.providers.mock import MockBarcodeReader

                    return MockBarcodeReader()
            elif key == "embedder":
                if name == "torch":
                    return prod.DinoV2Embedder()
                if name == "mock":
                    _guard_mock(name)
                    from app.services.vision.providers.mock import MockProductEmbedder

                    return MockProductEmbedder()
            elif key == "ocr":
                if name == "paddleocr":
                    return prod.PaddleOcrEngine()
                if name == "rapidocr":
                    return prod.RapidOcrEngine()
                if name == "mock":
                    _guard_mock(name)
                    from app.services.vision.providers.mock import MockOcrEngine

                    return MockOcrEngine()
            logger.warning("Unknown vision provider '%s' for %s", name, key)
            return None
        except VisionProviderError as exc:
            self._error[key] = str(exc)
            return None

    @staticmethod
    def _env_key(key: str) -> str:
        return {
            "detector": "COUNTER_DETECTOR_PROVIDER",
            "barcode": "COUNTER_BARCODE_PROVIDER",
            "embedder": "COUNTER_EMBEDDING_PROVIDER",
            "ocr": "COUNTER_OCR_PROVIDER",
        }[key]

    # -- access -------------------------------------------------------------
    def get(self, key: str) -> Optional[object]:
        """Cached provider instance or None (unconfigured / failed init)."""
        with self._lock:
            if key not in self._cache:
                self._cache[key] = self._construct(key)
            return self._cache[key]

    def get_ready(self, key: str):
        """Provider that is configured AND available, else None."""
        provider = self.get(key)
        if provider is None:
            return None
        return provider if provider.info().available else None

    def error_for(self, key: str) -> str:
        return self._error.get(key, "")

    # -- health -------------------------------------------------------------
    @staticmethod
    async def _check_pgvector() -> bool:
        """True when the vector extension is installed (async, shared pool)."""
        try:
            from app.database import db

            if not db.pool:
                return False
            return bool(
                await db.fetchval("select 1 from pg_extension where extname='vector'")
            )
        except Exception:  # noqa: BLE001 — DB down => report honestly
            return False

    async def health(self) -> VisionHealth:
        detector = self.get("detector") or _NullProvider("detector")
        barcode = self.get("barcode") or _NullProvider("barcode")
        embedder = self.get("embedder") or _NullProvider("embedder")
        ocr = self.get("ocr") or _NullProvider("ocr")
        return VisionHealth(
            detector=detector.info(),
            barcode=barcode.info(),
            embedder=embedder.info(),
            ocr=ocr.info(),
            pgvector=await self._check_pgvector(),
            allow_mock=_mock_allowed(),
        )


class _NullProvider:
    """Stand-in for an unconfigured stage so health() always has .info()."""

    def __init__(self, stage: str) -> None:
        self.stage = stage

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="none",
            available=False,
            detail=f"{self.stage} provider not configured",
        )
