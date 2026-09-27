"""Deterministic OCR evidence normalization + parsing (spec §13, §56).

Pure string logic — no LLM, no guessing:
  * lowercase / punctuation / whitespace normalization
  * unit normalization: 55 G, 55g, 55 gm, 55 grams -> 55g; 750 ML -> 750ml
  * MRP extraction with anchors (MRP, M.R.P., Rs, ₹, INR) + plausibility band

OCR output is EVIDENCE for catalog matching. It is never the selling price:
the DB price (fetched at cart/checkout time) remains authoritative (§2).
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass

from app.services.vision.types import OcrResult


@dataclass(frozen=True)
class OcrEvidence:
    """Structured evidence extracted from one product crop's OCR text."""

    normalized_text: str
    tokens: list[str]
    mrp: Optional[float] = None
    pack_size: Optional[str] = None
    mrp_source_text: Optional[str] = None


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


_WS_RE = re.compile(r"\s+")
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9₹.]+")
_UNIT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(g|gm|gram|grams|kg|ml|l|ltr|litre|liter)\b", re.IGNORECASE)
_CANON_UNITS = {
    "g": "g", "gm": "g", "gram": "g", "grams": "g",
    "kg": "kg", "ml": "ml", "l": "l", "ltr": "l", "litre": "l", "liter": "l",
}
_MRP_ANCHOR_RE = re.compile(
    r"(?:m\.?r\.?p\.?|rs\.?|rs\.|inr|₹)\s*[:\-]?\s*(₹|rs\.?|inr)?\s*(\d{1,5}(?:[.,]\d{1,2})?)",
    re.IGNORECASE,
)


def normalize_text(raw: str) -> str:
    """lowercase, strip accents/punctuation noise, collapse whitespace."""
    text = unicodedata.normalize("NFKC", raw or "").lower()
    text = text.replace("₹", " rs ")
    text = re.sub(r"[^a-z0-9.\s]", " ", text)
    return _WS_RE.sub(" ", text).strip()


def normalize_tokens(raw: str) -> list[str]:
    tokens = []
    for tok in _TOKEN_SPLIT_RE.split(normalize_text(raw)):
        if not tok:
            continue
        tokens.append(tok)
    return tokens


def normalize_pack_units(raw: str) -> str:
    """'55 G'/'55 gm' -> '55g'; '750 ML' -> '750ml' (canonical, compact)."""
    def repl(m: re.Match) -> str:
        num, unit = m.group(1), m.group(2).lower()
        canon = _CANON_UNITS.get(unit, unit)
        num_clean = num.rstrip(".0").rstrip(".") if "." in num else num
        return f"{num_clean}{canon}"

    return _UNIT_RE.sub(repl, raw.lower())


def extract_mrp(blocks_text: str) -> Optional[float]:
    """MRP with anchors + a plausibility band (₹2–₹10000, configurable).

    Deliberately conservative: '₹120' with no anchor is NOT MRP.
    """
    lo = _env_float("COUNTER_OCR_MRP_MIN", 2.0)
    hi = _env_float("COUNTER_OCR_MRP_MAX", 10000.0)
    for m in _MRP_ANCHOR_RE.finditer(blocks_text):
        try:
            val = float(m.group(2).replace(",", "."))
        except (TypeError, ValueError):
            continue
        if lo <= val <= hi:
            return round(val, 2)
    return None


def extract_pack_size(blocks_text: str) -> Optional[str]:
    """Canonical pack size token, e.g. '55g', '1l', '750ml'."""
    m = _UNIT_RE.search(blocks_text)
    if not m:
        return None
    return normalize_pack_units(m.group(0))


def build_evidence(ocr: OcrResult) -> OcrEvidence:
    """Full evidence object from an OCR result (deterministic)."""
    raw = ocr.joined_text()
    normalized = normalize_pack_units(normalize_text(raw))
    tokens = normalize_tokens(raw)
    return OcrEvidence(
        normalized_text=normalized,
        tokens=normalize_pack_units(" ".join(tokens)).split(),
        mrp=extract_mrp(raw),
        pack_size=extract_pack_size(raw),
        mrp_source_text=next(
            (b.text for b in ocr.text_blocks if b.text and "mrp" in b.text.lower()),
            None,
        ),
    )


def ocr_score(candidate_name: str, evidence: OcrEvidence) -> float:
    """Score how well a catalog product name matches OCR evidence (0..1).

    Deterministic token overlap with pack-size bonus. Pure lexical — the LLM
    never participates in identity (§86).

    Overlap is measured as RECALL over the OCR's own tokens
    (len(overlap)/len(evidence_tokens)) — NOT against the name. Measuring
    against the name made the score depend on name length: "Lays Classic 52g"
    and "Lays Magic Masala 52g" got different scores from identical pack text
    ("LAYS 52 g"), so OCR appeared to discriminate variants it cannot see.
    Variant words count only when OCR actually reads them.
    """
    name_tokens = set(normalize_tokens(candidate_name))
    if not name_tokens:
        return 0.0
    evidence_tokens = set(evidence.tokens)
    overlap = name_tokens & evidence_tokens
    if not overlap:
        return 0.0
    base = len(overlap) / len(evidence_tokens)
    bonus = 0.0
    cand_pack = extract_pack_size(candidate_name)
    if evidence.pack_size and cand_pack and evidence.pack_size == cand_pack:
        bonus = 0.25
    return min(1.0, base + bonus)
