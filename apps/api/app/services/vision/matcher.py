"""ProductMatcher + confidence engine (spec §14, §15, §55, master-prompt §37).

Evidence priority is fixed and never reversed:
    BARCODE (exact) > visual+OCR agreement > strong visual > OCR-only > manual.

Multi-signal scoring is DETERMINISTIC (no LLM decides identity):
    base      = single_signal_floor + visual_weight * visual_similarity
    ocr bonus = ocr_weight * ocr_score          (when OCR text agrees)
    meta bonus= metadata_weight * (0.5*brand_match + 0.5*pack_size_match)

Weights and the confidence thresholds are configuration-driven (env) — never
hardcoded into logic. A weak match never silently becomes a bill item: the
confidence engine assigns REVIEW_REQUIRED / UNRESOLVED and the UI asks the
merchant. Thresholds are NOT tuned for demo appearance (§39): visual-only
evidence stays below AUTO_ADD until a real evaluation dataset justifies it.

Every result carries an explainable evidence breakdown (§4/§38): a 0.93 score
is a similarity/confidence score, never "93% accuracy".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from app.services.vision.ocr_evidence import OcrEvidence, normalize_pack_units, ocr_score
from app.services.vision.types import BarcodeReading, Candidate, IdentityMethod, MatchResult


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class MatchScoringConfig:
    """All matching constants in one place (env-tunable, §55, §70)."""

    # Weights — barcode dominates by construction (exact match short-circuits).
    # Fusion: base = floor + visual_weight * similarity, plus ADDITIVE bonuses
    # for OCR agreement and brand/pack-size agreement (single-signal paths
    # must stay above the review threshold; multi-signal agreement pushes a
    # match toward auto-add, §55).
    visual_weight: float = 0.65
    ocr_weight: float = 0.25
    metadata_weight: float = 0.10
    # Floor applied to the visual-only signal so a single-signal match can
    # reach the configured review threshold (the ceiling is visual_weight
    # without it, which would make every OCR-less deployment UNRESOLVED).
    single_signal_floor: float = 0.30
    # Barcode wins outright when exact catalog match exists (§14 priority 1).
    barcode_confidence: float = 0.99
    # Thresholds (§15): configurable, validated via /health/counter exposure.
    # CHANGING THESE REQUIRES EVIDENCE FROM A REAL EVALUATION DATASET (§39).
    auto_add_threshold: float = 0.95
    review_threshold: float = 0.70
    # Similar-SKU discrimination (§5): when two candidates are visually
    # near-tied within this window, OCR pack-size agreement re-ranks them;
    # if the winner still lacks pack evidence it is treated as AMBIGUOUS and
    # penalized below the auto-add threshold (never silently add an
    # uncertain variant of a visually-similar family). NOTE: real same-family
    # variants differ by ~0.001 in DINOv2 similarity; genuinely different
    # products differ by ~0.05+. The window must separate those regimes.
    ambiguity_window: float = 0.02
    # runner-up must itself be a PLAUSIBLE match (same-packaging-family
    # territory) before the variant-ambiguity penalty applies — a weak
    # cross-product runner-up (~0.67 on a two-product counter) is not a
    # tied SKU variant, and capping the winner over it is a false alarm.
    ambiguity_min_runner: float = 0.75
    ambiguity_penalty: float = 0.30

    @staticmethod
    def from_env() -> "MatchScoringConfig":
        return MatchScoringConfig(
            visual_weight=_env_float("COUNTER_MATCH_VISUAL_WEIGHT", 0.65),
            ocr_weight=_env_float("COUNTER_MATCH_OCR_WEIGHT", 0.25),
            metadata_weight=_env_float("COUNTER_MATCH_METADATA_WEIGHT", 0.10),
            single_signal_floor=_env_float("COUNTER_MATCH_SINGLE_SIGNAL_FLOOR", 0.30),
            barcode_confidence=_env_float("COUNTER_MATCH_BARCODE_CONFIDENCE", 0.99),
            auto_add_threshold=_env_float("COUNTER_AUTO_ADD_THRESHOLD", 0.95),
            review_threshold=_env_float("COUNTER_REVIEW_THRESHOLD", 0.70),
            ambiguity_window=_env_float("COUNTER_MATCH_AMBIGUITY_WINDOW", 0.02),
            ambiguity_min_runner=_env_float("COUNTER_MATCH_AMBIGUITY_MIN_RUNNER", 0.75),
            ambiguity_penalty=_env_float("COUNTER_MATCH_AMBIGUITY_PENALTY", 0.30),
        )


def _brand_match(brand: Optional[str], ocr_evidence: Optional[OcrEvidence]) -> bool:
    """Deterministic brand agreement: the product's brand token appears in
    the OCR text (normalized)."""
    if not brand or not ocr_evidence or not ocr_evidence.tokens:
        return False
    b = normalize_pack_units((brand or "").lower()).strip()
    if len(b) < 3:
        return False
    return any(b == t or (len(b) > 3 and b in t) for t in ocr_evidence.tokens)


def _pack_match(meta_pack: Optional[str], ocr_evidence: Optional[OcrEvidence]) -> bool:
    if not meta_pack or not ocr_evidence or not ocr_evidence.pack_size:
        return False
    return ocr_evidence.pack_size == normalize_pack_units(meta_pack.lower())


class ProductMatcher:
    """Fuses barcode + visual candidates + OCR/brand/pack evidence into a
    MatchResult with an explainable per-candidate evidence breakdown."""

    def __init__(self, config: Optional[MatchScoringConfig] = None) -> None:
        self.config = config or MatchScoringConfig.from_env()

    def match(
        self,
        barcode: Optional[BarcodeReading],
        barcode_product_id: Optional[str],
        visual_candidates: list[Candidate],
        ocr_evidence: Optional[OcrEvidence],
        product_names: dict[str, str],
        product_meta: Optional[dict[str, dict]] = None,
    ) -> MatchResult:
        """
        barcode            — reading from the crop, if any
        barcode_product_id — catalog product resolved from the barcode by the
                             caller (DB lookup), or None when unknown
        visual_candidates  — top-K from store-scoped embedding retrieval
        ocr_evidence       — parsed OCR evidence for the crop
        product_names      — {product_id: name} for OCR scoring of candidates
        product_meta       — optional {product_id: {brand, pack_size}} used
                             for brand/pack-size agreement signals (§37)
        """
        cfg = self.config
        product_meta = product_meta or {}

        # ---- 1) Exact barcode match dominates (§14 priority 1) ------------
        if barcode and barcode_product_id:
            return MatchResult(
                product_id=barcode_product_id,
                confidence=cfg.barcode_confidence,
                method=IdentityMethod.BARCODE,
                reasons=[f"exact barcode {barcode.value} in store catalog"],
                barcode=barcode.value,
                evidence={
                    "visual_similarity": None,
                    "ocr_similarity": None,
                    "brand_match": None,
                    "pack_size_match": None,
                    "barcode_match": True,
                },
            )

        # ---- 2) Multi-signal scoring (visual base + additive agreements) ---
        scored: list[tuple[Candidate, float, list[str], dict]] = []
        for cand in visual_candidates:
            name = product_names.get(cand.product_id, "")
            meta = product_meta.get(cand.product_id, {})
            visual_score = cand.similarity
            base = min(1.0, cfg.single_signal_floor + cfg.visual_weight * visual_score)
            ocr_s = ocr_score(name, ocr_evidence) if ocr_evidence else 0.0
            brand_ok = _brand_match(meta.get("brand"), ocr_evidence)
            pack_ok = _pack_match(meta.get("pack_size"), ocr_evidence)
            meta_bonus = cfg.metadata_weight * (0.5 * brand_ok + 0.5 * pack_ok)
            final = min(1.0, base + cfg.ocr_weight * ocr_s + meta_bonus)

            reasons = [f"visual similarity {visual_score:.3f}"]
            if ocr_s > 0:
                reasons.append(f"ocr agreement {ocr_s:.2f}")
            if brand_ok:
                reasons.append(f"brand '{meta.get('brand')}' in OCR")
            if pack_ok:
                reasons.append(f"pack size {ocr_evidence.pack_size} matches")
            evidence = {
                "visual_similarity": round(visual_score, 4),
                "ocr_similarity": round(ocr_s, 4) if ocr_evidence else None,
                "brand_match": brand_ok if ocr_evidence else None,
                "pack_size_match": pack_ok if ocr_evidence else None,
                "barcode_match": False,
            }
            scored.append((cand, final, reasons, evidence))

        if scored:
            scored.sort(key=lambda t: -t[1])
            reasons = list(scored[0][2])

            # Similar-SKU discrimination (§5): OCR pack-size evidence re-ranks
            # visually near-tied candidates (Dairy Milk 24g/55g/110g family).
            if ocr_evidence and ocr_evidence.pack_size and len(scored) > 1:
                best = scored[0]
                if not best[3]["pack_size_match"]:
                    challenger = next(
                        (
                            s
                            for s in scored[1:]
                            if best[1] - s[1] <= cfg.ambiguity_window
                            and s[3]["pack_size_match"]
                        ),
                        None,
                    )
                    if challenger is not None:
                        scored.remove(challenger)
                        scored.insert(0, challenger)
                        reasons = list(challenger[2])
                        reasons.append(
                            f"pack-size evidence re-ranked candidate ({ocr_evidence.pack_size})"
                        )

            best, best_score, _r, best_evidence = scored[0]
            alternatives = [c for c, _, _, _ in scored[1:]]

            # Ambiguity penalty: a visually near-tied winner WITHOUT pack
            # evidence must not auto-add — push it below the auto-add
            # threshold so the merchant decides (never fake certainty).
            if len(scored) > 1:
                runner_up = scored[1]
                # compare RAW similarity to RAW similarity (the winner's final
                # score includes the base+offset mapping and OCR bonus, so
                # subtracting finals from raws produced an always-true tie)
                visually_tied = (
                    best.similarity - runner_up[0].similarity <= cfg.ambiguity_window
                    and runner_up[0].similarity >= cfg.ambiguity_min_runner
                )
                if visually_tied and not best_evidence["pack_size_match"]:
                    best_score = min(best_score, cfg.auto_add_threshold - cfg.ambiguity_penalty)
                    best_score = max(best_score, 0.0)
                    reasons.append(
                        "ambiguous variant — pack size not confirmed; merchant review required"
                    )

            method = (
                IdentityMethod.COMBINED
                if best_evidence["ocr_similarity"] or best_evidence["brand_match"] or best_evidence["pack_size_match"]
                else IdentityMethod.VISUAL
            )

            # Shared-evidence cap (real finding from the 10-product catalog):
            # when the runner-up carries the SAME OCR/brand/pack agreement as
            # the winner (e.g. Lays Classic vs Lays Magic Masala — both "Lays",
            # both 52g), those bonuses prove nothing discriminative and must
            # not push the winner into auto-add territory. OCR *text* remains
            # valid evidence only when it is winner-specific.
            if len(scored) > 1:
                ru_evidence = scored[1][3]
                shared_evidence = (
                    best_evidence["brand_match"] is True
                    and ru_evidence["brand_match"] is True
                    and best_evidence["pack_size_match"] is True
                    and ru_evidence["pack_size_match"] is True
                )
                if shared_evidence and best_evidence["ocr_similarity"] is not None:
                    ru_ocr = ru_evidence["ocr_similarity"] or 0.0
                    be_ocr = best_evidence["ocr_similarity"] or 0.0
                    if abs(be_ocr - ru_ocr) <= 0.05:
                        best_score = min(best_score, cfg.review_threshold - 0.05)
                        best_score = max(best_score, 0.0)
                        reasons.append(
                            "shared evidence — same brand+pack family cannot "
                            "discriminate variants; merchant review required"
                        )

            # Ambiguity note (honest UI): near-tied candidates are flagged,
            # the confidence gate still decides the status.
            if len(scored) > 1 and best_score - scored[1][1] < 0.03:
                reasons.append(
                    f"ambiguous: close to {product_names.get(scored[1][0].product_id, 'another candidate')}"
                )
            return MatchResult(
                product_id=best.product_id,
                confidence=min(1.0, best_score),
                method=method,
                reasons=reasons,
                alternatives=alternatives,
                barcode=barcode.value if barcode else None,
                evidence=best_evidence,
            )

        return MatchResult(
            product_id=None,
            confidence=0.0,
            method=IdentityMethod.VISUAL,
            reasons=["no catalog candidates matched"],
            barcode=barcode.value if barcode else None,
        )


@dataclass(frozen=True)
class ConfidenceDecision:
    status: str  # IDENTIFIED | REVIEW_REQUIRED | UNRESOLVED
    auto_addable: bool
    requires_review: bool


class ConfidenceEngine:
    """Configurable thresholds (§15). Never hardcodes 0.95 in logic paths."""

    def __init__(self, config: Optional[MatchScoringConfig] = None) -> None:
        self.config = config or MatchScoringConfig.from_env()

    def decide(self, confidence: float) -> ConfidenceDecision:
        cfg = self.config
        if confidence >= cfg.auto_add_threshold:
            return ConfidenceDecision("IDENTIFIED", auto_addable=True, requires_review=False)
        if confidence >= cfg.review_threshold:
            return ConfidenceDecision("REVIEW_REQUIRED", auto_addable=False, requires_review=True)
        return ConfidenceDecision("UNRESOLVED", auto_addable=False, requires_review=False)
