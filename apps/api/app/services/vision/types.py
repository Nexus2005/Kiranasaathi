"""Vision pipeline data contracts (Smart Counter visual recognition).

These types are the boundary between the FastAPI layer, the provider
adapters (detector / recognizer / OCR / barcode), and the matcher.
Vision NEVER returns prices, stock, or totals — only identity evidence.
The database remains the sole commercial authority.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class IdentityMethod(str, Enum):
    """How a product candidate was identified. Barcode > Visual > OCR > Manual."""

    BARCODE = "BARCODE"
    VISUAL = "VISUAL"
    OCR = "OCR"
    MANUAL = "MANUAL"
    COMBINED = "COMBINED"


class DetectionStatus(str, Enum):
    """Lifecycle of one detection, from pixels to a cart decision (spec §36)."""

    DETECTED = "DETECTED"
    IDENTIFYING = "IDENTIFYING"
    IDENTIFIED = "IDENTIFIED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNRESOLVED = "UNRESOLVED"
    ADDED = "ADDED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class BBox:
    """Axis-aligned bounding box in pixels of the source frame."""

    x: float
    y: float
    width: float
    height: float

    def as_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class Detection:
    """One detected physical product region ('where is a product?')."""

    detection_id: str
    bbox: BBox
    confidence: float
    class_name: str = "object"
    crop: Optional[bytes] = None  # JPEG bytes of the cropped region

    def as_dict(self) -> dict:
        return {
            "detection_id": self.detection_id,
            "bbox": self.bbox.as_dict(),
            "confidence": round(self.confidence, 4),
            "class_name": self.class_name,
        }


@dataclass(frozen=True)
class BarcodeReading:
    value: str
    format: str
    confidence: float

    def as_dict(self) -> dict:
        return {
            "value": self.value,
            "format": self.format,
            "confidence": round(self.confidence, 4),
        }


@dataclass(frozen=True)
class TextBlock:
    """One OCR text region with its position and confidence."""

    text: str
    confidence: float
    bbox: Optional[BBox] = None

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 4),
            "bbox": self.bbox.as_dict() if self.bbox else None,
        }


@dataclass(frozen=True)
class OcrResult:
    text_blocks: list[TextBlock] = field(default_factory=list)
    duration_ms: int = 0

    def joined_text(self) -> str:
        return " ".join(b.text for b in self.text_blocks).strip()

    def as_dict(self) -> dict:
        return {
            "text_blocks": [b.as_dict() for b in self.text_blocks],
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class Embedding:
    """A visual embedding vector with full model traceability (spec §44)."""

    vector: list[float]
    model: str
    version: str
    dimensions: int

    @staticmethod
    def create(vector: list[float], model: str, version: str) -> "Embedding":
        return Embedding(
            vector=vector, model=model, version=version, dimensions=len(vector)
        )


@dataclass(frozen=True)
class Candidate:
    """One catalog candidate from embedding retrieval (spec §9)."""

    product_id: str
    similarity: float
    rank: int
    embedding_id: Optional[str] = None


@dataclass
class MatchResult:
    """The matcher's verdict for one detection (spec §14) with an explainable
    multi-signal evidence breakdown (development/debug + UI explanation;
    a confidence score is never presented as 'accuracy')."""

    product_id: Optional[str]
    confidence: float
    method: IdentityMethod
    reasons: list[str] = field(default_factory=list)
    alternatives: list[Candidate] = field(default_factory=list)
    barcode: Optional[str] = None
    evidence: Optional[dict] = None  # visual_similarity/ocr/brand/pack/barcode

    def as_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "confidence": round(self.confidence, 4),
            "method": self.method.value,
            "reasons": self.reasons,
            "alternatives": [
                {
                    "product_id": c.product_id,
                    "similarity": round(c.similarity, 4),
                    "rank": c.rank,
                }
                for c in self.alternatives
            ],
            "barcode": self.barcode,
            "evidence": self.evidence,
        }


def new_id(prefix: str) -> str:
    """Short traceable id, e.g. det_1a2b3c4d."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def now_ms() -> int:
    return int(time.time() * 1000)
