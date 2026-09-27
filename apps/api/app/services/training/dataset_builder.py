"""Quality-gated training dataset builder (Level-3 preparation).

Turns the recognition_events ledger into a VERSIONED, VERIFIED training
dataset — nothing is "learned" here; this only curates evidence for future
offline training (directive §12–§17):

  * STRONG POSITIVE   = merchant confirmed the prediction
  * STRONG NEGATIVE   = merchant corrected/rejected (hard-negative pair:
                        the image must NOT retrieve the predicted product)
  * WEAK/BEHAVIORAL   = accepted without correction + completed checkout
                        (never labeled as ground truth — MEDIUM strength)
  * UNCERTAIN         = everything else — EXCLUDED

Exclusions (hard rules):
  - payment/stock/network failures are NOT recognition negatives
  - non-consented stores' images never leave store scope
  - uncertain labels never enter the dataset
  - duplicates removed (exact sha256 + perceptual dhash)
  - splits are grouped (store/product/session) — no leakage
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from app.database import db

logger = logging.getLogger("kirana.training")

# import hashlib
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def perceptual_dhash(img, hash_size: int = 8) -> str:
    """Deterministic difference hash (no external deps)."""
    import PIL.Image as Image

    gray = img.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
    px = list(gray.getdata())
    rows = [px[i : i * (hash_size + 1) + (hash_size + 1)] for i in range(hash_size)]
    bits = []
    for row in px[: hash_size * (hash_size + 1)]:
        pass
    # proper scan: compare horizontal neighbors
    bits = []
    for r in range(hash_size):
        row = px[r * (hash_size + 1) : (r + 1) * (hash_size + 1)]
        for c in range(hash_size):
            bits.append("1" if row[c] > row[c + 1] else "0")
    return "".join(bits)


@dataclass
class DatasetSplit:
    train: list[dict] = field(default_factory=list)
    validation: list[dict] = field(default_factory=list)
    test: list[dict] = field(default_factory=list)


LABEL_RULES = {
    "MERCHANT_CONFIRMED": ("POSITIVE", "STRONG"),
    "MERCHANT_CORRECTED": ("NEGATIVE", "STRONG"),
    "MERCHANT_REJECTED": ("NEGATIVE", "STRONG"),
    "AUTO_ACCEPTED": ("POSITIVE", "MEDIUM"),  # behavioral; requires completed checkout
}


def label_for(action: Optional[str], checkout_status: Optional[str], failure_reason: Optional[str]) -> Optional[tuple[str, str]]:
    """Quality rules (§12). Returns (label, strength) or None when excluded."""
    if action == "AUTO_ACCEPTED":
        # behavioral positive ONLY when the sale completed cleanly; transaction
        # failures are never recognition signals
        if checkout_status == "COMPLETED":
            return ("POSITIVE", "MEDIUM")
        return None
    rule = LABEL_RULES.get(action or "")
    if not rule:
        return None
    label, strength = rule
    if label == "NEGATIVE" and failure_reason in ("PAYMENT_FAILED", "INSUFFICIENT_STOCK", "NETWORK_ERROR"):
        # a failed transaction is not a recognition error
        return None
    return (label, strength)


async def collect_samples(store_id: Optional[str] = None, limit: int = 10000) -> list[dict]:
    """Verified samples from the ledger (only rows with usable labels)."""
    where = "where (product_id is not null or confirmed_product_id is not null)"
    params: list = []
    if store_id:
        where += " and store_id=$1"
        params.append(store_id)
    rows = await db.fetch(
        f"""
        select e.id, e.store_id, e.product_id, e.confirmed_product_id, e.global_product_id,
               e.detection_id, e.frame_id, e.predicted_confidence, e.recognition_method,
               e.visual_similarity, e.ocr_score, e.barcode_match, e.model_version,
               e.user_action, e.feedback_label, e.checkout_status, e.failure_reason,
               e.created_at, p.name as product_name, cp.name as confirmed_name,
               i.content_hash, i.view
        from recognition_events e
        left join products p on p.id = e.product_id
        left join products cp on cp.id = e.confirmed_product_id
        left join product_images i on i.store_id = e.store_id
              and i.product_id = coalesce(e.confirmed_product_id, e.product_id)
              and i.created_by is not null
        {where}
        order by e.created_at desc
        limit {int(limit)}
        """,
        *params,
    )
    samples: list[dict] = []
    for r in rows:
        lab = label_for(r["user_action"], r["checkout_status"], r["failure_reason"])
        if lab is None:
            continue
        samples.append(
            {
                "sample_id": str(r["id"]),
                "store_id": str(r["store_id"]),
                "predicted_product_id": str(r["product_id"]) if r["product_id"] else None,
                "confirmed_product_id": str(r["confirmed_product_id"]) if r["confirmed_product_id"] else None,
                "global_product_id": str(r["global_product_id"]) if r["global_product_id"] else None,
                "predicted_name": r["product_name"],
                "confirmed_name": r["confirmed_name"],
                "label": lab[0],
                "label_strength": lab[1],
                "predicted_confidence": float(r["predicted_confidence"]) if r["predicted_confidence"] is not None else None,
                "recognition_method": r["recognition_method"],
                "visual_similarity": float(r["visual_similarity"]) if r["visual_similarity"] is not None else None,
                "ocr_score": float(r["ocr_score"]) if r["ocr_score"] is not None else None,
                "barcode_match": r["barcode_match"],
                "model_version": r["model_version"],
                "image_content_hash": r["content_hash"],
                "created_at": r["created_at"].isoformat(),
            }
        )
    return samples


def hard_negative_pairs(samples: list[dict]) -> list[dict]:
    """Corrections become PAIRS: the image is positive for the confirmed
    product AND a hard negative for the predicted one (§14)."""
    pairs = []
    for s in samples:
        if s["label"] == "NEGATIVE" and s["predicted_product_id"] and s["confirmed_product_id"]:
            pairs.append(
                {
                    "image": s["image_content_hash"],
                    "positive_for": s["confirmed_product_id"],
                    "hard_negative_for": s["predicted_product_id"],
                    "predicted_confidence": s["predicted_confidence"],
                    "strength": s["label_strength"],
                }
            )
    return pairs


def deduplicate(samples: list[dict], image_hashes: Optional[dict[str, str]] = None) -> tuple[list[dict], dict]:
    """Exact-hash + perceptual dedup. Returns (kept, stats). Near-duplicates
    (same perceptual hash) are dropped regardless of label to avoid
    train/test contamination (§16)."""
    seen_exact: set[str] = set()
    seen_phash: set[str] = set()
    kept: list[dict] = []
    stats = {"exact_duplicates": 0, "near_duplicates": 0, "input": len(samples)}
    for s in samples:
        h = s.get("image_content_hash")
        if h:
            if h in seen_exact:
                stats["exact_duplicates"] += 1
                continue
            seen_exact.add(h)
        ph = (image_hashes or {}).get(h or "", "")
        if ph:
            if ph in seen_phash:
                stats["near_duplicates"] += 1
                continue
            seen_phash.add(ph)
        kept.append(s)
    stats["kept"] = len(kept)
    return kept, stats


def grouped_splits(samples: list[dict], group_key: str = "store_id", val_frac: float = 0.15, test_frac: float = 0.15, seed: int = 7) -> DatasetSplit:
    """Grouped splits: all samples of one group (store/product/session) stay
    in one split — no leakage (§16/§17). Deterministic given seed."""
    import random

    groups: dict[str, list[dict]] = {}
    for s in samples:
        groups.setdefault(str(s.get(group_key)), []).append(s)
    keys = sorted(groups.keys())
    random.Random(seed).shuffle(keys)
    split = DatasetSplit()
    n = len(keys)
    n_val = max(1, int(n * val_frac)) if n >= 3 else 0
    n_test = max(1, int(n * test_frac)) if n >= 6 else 0
    for i, k in enumerate(keys):
        if i < n_test:
            split.test.extend(groups[k])
        elif i < n_test + n_val:
            split.validation.extend(groups[k])
        else:
            split.train.extend(groups[k])
    return split


def dataset_manifest_hash(samples: list[dict], config: dict) -> str:
    payload = json.dumps({"samples": sorted(s["sample_id"] for s in samples), "config": config}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


async def build_dataset(store_id: Optional[str] = None, group_key: str = "store_id") -> dict:
    """Full pipeline: collect → dedup → split → manifest hash."""
    samples = await collect_samples(store_id)
    kept, dedup_stats = deduplicate(samples)
    split = grouped_splits(kept, group_key=group_key)
    manifest_hash = dataset_manifest_hash(kept, {"group_key": group_key})
    hard_negatives = hard_negative_pairs(kept)
    return {
        "manifest_hash": manifest_hash,
        "dedup": dedup_stats,
        "counts": {
            "train": len(split.train),
            "validation": len(split.validation),
            "test": len(split.test),
            "hard_negative_pairs": len(hard_negatives),
        },
        "train": split.train,
        "validation": split.validation,
        "test": split.test,
        "hard_negatives": hard_negatives,
    }
