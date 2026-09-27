"""Recognition pipeline orchestrator (spec §3, §12, §14, §15).

One frame in → per-detection identity evidence out:
  validate frame → detect product regions → crop each region →
  [barcode | embedding retrieval | OCR evidence] → match → confidence.

The pipeline NEVER returns prices, stock, or sale totals — identity only.
Every stage degrades honestly: an unconfigured provider is skipped (or the
endpoint returns 503 VISION_NOT_CONFIGURED); nothing is invented (§77).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from app.services.vision import embeddings_store as store
from app.services.vision import global_catalog as gcat
from app.services.vision.matcher import ConfidenceEngine, ProductMatcher
from app.services.vision.ocr_evidence import build_evidence
from app.services.vision.preprocessing import (
    clamp_dimensions,
    crop_detection,
    decode,
    to_jpeg,
)
from app.services.vision.providers.base import VisionProviderError
from app.services.vision.registry import VisionRegistry
from app.services.vision.types import (
    Candidate,
    Detection,
    DetectionStatus,
    Embedding,
    MatchResult,
    new_id,
)

logger = logging.getLogger("kirana.vision")


@dataclass
class RecognitionResult:
    """Per-detection recognition outcome returned to the frontend (§36)."""

    detection: Detection
    match: MatchResult
    status: DetectionStatus
    auto_addable: bool
    requires_review: bool
    latency_ms: int
    ocr_mrp: Optional[float] = None  # evidence only — NEVER the charged price
    event_id: Optional[str] = None   # recognition_events row (feedback ledger)

    def as_dict(self) -> dict:
        return {
            "detection": self.detection.as_dict(),
            "match": self.match.as_dict(),
            "status": self.status.value,
            "auto_addable": self.auto_addable,
            "requires_review": self.requires_review,
            "latency_ms": self.latency_ms,
            "ocr_mrp_evidence": self.ocr_mrp,
            "recognition_event_id": self.event_id,
        }


@dataclass
class FrameRecognition:
    frame_id: str
    timestamp: int
    results: list[RecognitionResult] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # stage -> why
    timings: dict[str, int] = field(default_factory=dict)  # stage -> ms
    latency_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "frame_id": self.frame_id,
            "timestamp": self.timestamp,
            "detections": [r.as_dict() for r in self.results],
            "skipped_stages": self.skipped,
            "timings_ms": self.timings,
            "latency_ms": self.latency_ms,
        }


class RecognitionPipeline:
    def __init__(self, registry: VisionRegistry) -> None:
        self.registry = registry
        self.matcher = ProductMatcher()
        self.confidence = ConfidenceEngine(self.matcher.config)

    # -- stage helpers ------------------------------------------------------
    def _detect(self, img) -> list[Detection]:
        detector = self.registry.get_ready("detector")
        if detector is None:
            raise VisionProviderError(
                "detector provider not configured/available", "VISION_NOT_CONFIGURED"
            )
        return detector.detect(to_jpeg(clamp_dimensions(img)))

    def _read_barcode(self, crop_jpeg: bytes):
        reader = self.registry.get_ready("barcode")
        if reader is None:
            return None
        try:
            readings = reader.read(crop_jpeg)
            return readings[0] if readings else None
        except VisionProviderError as exc:
            logger.info("barcode stage skipped: %s", exc)
            return None

    def _embed(self, crop_jpeg: bytes) -> Optional[Embedding]:
        embedder = self.registry.get_ready("embedder")
        if embedder is None:
            return None
        return embedder.embed(crop_jpeg)

    def _embed_batch(self, crop_jpegs: list[bytes]) -> list[Optional[Embedding]]:
        """Batch embedding (directive §11): one forward pass per chunk when
        the provider supports it; order preserved; per-item failures return
        None without corrupting other crops."""
        if not crop_jpegs:
            return []
        embedder = self.registry.get_ready("embedder")
        if embedder is None:
            return [None] * len(crop_jpegs)
        if hasattr(embedder, "embed_batch"):
            return embedder.embed_batch(crop_jpegs)
        results: list[Optional[Embedding]] = []
        for c in crop_jpegs:
            try:
                results.append(embedder.embed(c))
            except VisionProviderError:
                results.append(None)
        return results

    def _ocr(self, crop_jpeg: bytes):
        ocr = self.registry.get_ready("ocr")
        if ocr is None:
            return None
        try:
            return ocr.extract_text(crop_jpeg)
        except VisionProviderError as exc:
            logger.info("ocr stage skipped: %s", exc)
            return None

    # -- main entry ---------------------------------------------------------
    async def recognize_frame(self, frame_jpeg: bytes, store_id: str) -> FrameRecognition:
        started = time.monotonic()
        frame = FrameRecognition(frame_id=new_id("frame"), timestamp=int(time.time() * 1000))

        img = decode(frame_jpeg)
        img = clamp_dimensions(img)
        timings = frame.timings

        # Stage 1: detection (required for the visual pipeline)
        t0 = time.monotonic()
        try:
            detections = self._detect(img)
        except VisionProviderError as exc:
            # Detection is the entry stage: without it there is no pipeline.
            frame.skipped["detector"] = str(exc)
            return frame
        timings["detect_ms"] = int((time.monotonic() - t0) * 1000)

        if not detections:
            return frame  # honestly empty — no products seen

        frame_img = img  # crops come from the clamped frame
        frame_fallback_used = False

        # Stage 1b: batch crop extraction + batch embedding (directive §10/§11).
        # Multi-product frames embed in one forward pass per chunk instead of
        # serializing every crop through the model.
        det_started_all = time.monotonic()
        crop_jpegs: list[bytes] = []
        crop_ok: list[bool] = []
        for det in detections:
            try:
                crop_jpegs.append(crop_detection(frame_img, det.bbox))
                crop_ok.append(True)
            except VisionProviderError:
                crop_jpegs.append(to_jpeg(frame_img))  # degenerate box: fall back to frame
                crop_ok.append(False)
        t0 = time.monotonic()
        embeddings = self._embed_batch(crop_jpegs)
        timings["embed_ms"] = int((time.monotonic() - t0) * 1000)

        for det_idx, det in enumerate(detections):
            det_started = time.monotonic()
            crop_jpeg = crop_jpegs[det_idx]

            # Stage 2a: barcode (highest identity priority)
            t0 = time.monotonic()
            barcode = self._read_barcode(crop_jpeg)
            timings["barcode_ms"] = timings.get("barcode_ms", 0) + int(
                (time.monotonic() - t0) * 1000
            )
            barcode_pid = None
            if barcode:
                barcode_pid = await store.find_product_by_barcode(store_id, barcode.value)

            # Stage 2b: visual retrieval (skipped when barcode already exact — §8).
            # Embeddings come from the batch pass above (order preserved).
            # If the crop embeds to zero candidates (box clipped the pack, etc.),
            # retry ONCE with the full frame — only in single-product frames, so
            # a multi-product scene never borrows a neighbour's identity.
            # The STORE index is merged with the GLOBAL VERIFIED index (shared
            # product knowledge) via the store's product links — no merchant
            # business data crosses the boundary (master-prompt §28).
            candidates = []
            emb = embeddings[det_idx] if det_idx < len(embeddings) else None
            if barcode_pid is None:
                if emb is not None:
                    t0 = time.monotonic()
                    candidates = await store.search_similar(store_id, emb)
                    timings["vector_search_ms"] = timings.get("vector_search_ms", 0) + int(
                        (time.monotonic() - t0) * 1000
                    )

                    # Global knowledge merge: VERIFIED global embeddings map to
                    # this store's products through product_global_links.
                    try:
                        gres = await gcat.search_verified_global(emb, top_k=3)
                    except Exception as exc:  # noqa: BLE001 — global index optional
                        gres = []
                        logger.info("global index skipped: %s", exc)
                    seen = {c.product_id for c in candidates}
                    for g in gres:
                        try:
                            local_pid = await gcat.link_global_to_store_product(
                                store_id, g["global_product_id"]
                            )
                        except Exception:  # noqa: BLE001
                            local_pid = None
                        if local_pid and local_pid not in seen:
                            seen.add(local_pid)
                            candidates.append(
                                Candidate(local_pid, g["similarity"], len(seen))
                            )
                            frame.skipped.setdefault(
                                "global_merge",
                                "global VERIFIED embedding matched via store link",
                            )
                    if candidates:
                        candidates.sort(key=lambda c: -c.similarity)
                        candidates = [
                            Candidate(c.product_id, c.similarity, i + 1, c.embedding_id)
                            for i, c in enumerate(candidates)
                        ]

                    if not candidates and len(detections) == 1 and not frame_fallback_used:
                        frame_fallback_used = True
                        t0 = time.monotonic()
                        emb_full = self._embed(to_jpeg(frame_img))
                        timings["embed_ms"] += int((time.monotonic() - t0) * 1000)
                        if emb_full is not None:
                            t0 = time.monotonic()
                            candidates = await store.search_similar(store_id, emb_full)
                            timings["vector_search_ms"] += int(
                                (time.monotonic() - t0) * 1000
                            )
                            if candidates:
                                frame.skipped.setdefault(
                                    "crop_fallback",
                                    "crop matched nothing; full frame embedded instead",
                                )
                else:
                    frame.skipped.setdefault("embedder", "embedding provider unavailable")

            # Stage 2c: OCR evidence (supporting only)
            t0 = time.monotonic()
            ocr_res = self._ocr(crop_jpeg)
            timings["ocr_ms"] = timings.get("ocr_ms", 0) + int(
                (time.monotonic() - t0) * 1000
            )
            evidence = build_evidence(ocr_res) if ocr_res else None

            # Stage 3: match + confidence (multi-signal, with product meta for
            # brand/pack-size agreement)
            t0 = time.monotonic()
            cand_ids = [c.product_id for c in candidates] + (
                [barcode_pid] if barcode_pid else []
            )
            names = await store.product_names_for(cand_ids)
            meta = await store.product_meta_for(cand_ids)
            match = self.matcher.match(barcode, barcode_pid, candidates, evidence, names, meta)
            decision = self.confidence.decide(match.confidence)
            timings["match_ms"] = timings.get("match_ms", 0) + int(
                (time.monotonic() - t0) * 1000
            )

            status = (
                DetectionStatus.IDENTIFIED
                if decision.auto_addable
                else (DetectionStatus.REVIEW_REQUIRED if decision.requires_review else DetectionStatus.UNRESOLVED)
            )

            # Recognition event ledger: prediction first (action NULL); the
            # merchant's confirm/correct/reject updates the same row later.
            # Feedback NEVER influences identity, price, stock, or checkout.
            event_id: Optional[str] = None
            try:
                emb_provider = self.registry.get("embedder")
                model_version = getattr(emb_provider, "model_name", None) if emb_provider else None
                best_sim = next(
                    (
                        c.similarity
                        for c in candidates
                        if c.product_id == match.product_id
                    ),
                    None,
                )
                ocr_s = (match.evidence or {}).get("ocr_similarity")
                event_id = await gcat.record_event(
                    store_id=store_id,
                    product_id=match.product_id,
                    detection_id=det.detection_id,
                    frame_id=frame.frame_id,
                    predicted_confidence=match.confidence,
                    recognition_method=match.method.value,
                    visual_similarity=best_sim,
                    ocr_score=ocr_s,
                    barcode_match=bool(barcode_pid),
                    model_version=model_version,
                    user_action=None if status != DetectionStatus.UNRESOLVED else "UNRESOLVED",
                )
            except Exception as exc:  # noqa: BLE001 — analytics must never break recognition
                logger.warning("recognition event not recorded: %s", exc)

            status = (
                DetectionStatus.IDENTIFIED
                if decision.auto_addable
                else (DetectionStatus.REVIEW_REQUIRED if decision.requires_review else DetectionStatus.UNRESOLVED)
            )
            frame.results.append(
                RecognitionResult(
                    detection=det,
                    match=match,
                    status=status,
                    auto_addable=decision.auto_addable,
                    requires_review=decision.requires_review,
                    latency_ms=int((time.monotonic() - det_started) * 1000),
                    ocr_mrp=evidence.mrp if evidence else None,
                    event_id=event_id,
                )
            )

        frame.latency_ms = int((time.monotonic() - started) * 1000)
        return frame
