"""Kaggle dataset discovery + download via the REST API (Bearer token).

Uses the NEW Kaggle token format (KGAT_*) through
`Authorization: Bearer <KAGGLE_API_TOKEN>` — verified working; kagglehub
1.0.2 does not yet accept the new token flow.

CREDENTIAL SAFETY (directive §5):
  * the token is read from the environment (KAGGLE_API_TOKEN) or the local
    Kaggle.txt setup file — never from source control
  * it is NEVER printed, logged, or written to generated artifacts
  * manifests record dataset LICENSES, never credentials
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Optional

API_BASE = "https://www.kaggle.com/api/v1"


def _token() -> str:
    tok = os.environ.get("KAGGLE_API_TOKEN", "").strip()
    if tok:
        return tok
    # Local setup file (git-ignored); never copied anywhere else.
    local = Path(__file__).resolve().parents[2] / "Kaggle.txt"
    if local.exists():
        m = re.search(r"KGAT_[A-Za-z0-9]+", local.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(0)
    raise RuntimeError(
        "Kaggle credentials unavailable: set KAGGLE_API_TOKEN (or provide the "
        "local Kaggle.txt token file). Do not fabricate credentials."
    )


def _get(path: str, params: Optional[dict] = None) -> Any:
    url = f"{API_BASE}{path}"
    if params:
        from urllib.parse import urlencode

        url += "?" + urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {_token()}")
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read().decode())


def _download(url: str, dest: Path) -> Path:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {_token()}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=600) as res, open(dest, "wb") as f:
        while True:
            chunk = res.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return dest


def search_datasets(query: str, max_size_bytes: int = 5_000_000_000, limit: int = 10) -> list[dict]:
    rows = _get("/datasets/list", {"search": query, "maxSize": max_size_bytes, "pageSize": limit})
    out = []
    for d in rows or []:
        out.append(
            {
                "ref": d.get("ref"),
                "title": d.get("title"),
                "size_bytes": d.get("totalBytes") or 0,
                "license_name": (d.get("licenseName") or "").strip(),
                "usability": d.get("usabilityRating"),
            }
        )
    return out


def dataset_files(ref: str, page_size: int = 50) -> list[dict]:
    """File listing (paginated datasetFiles)."""
    rows = _get(f"/datasets/list/{ref}") or {}
    files = rows.get("datasetFiles") or []
    return [
        {"name": f.get("name") or f.get("fileName"), "size": f.get("totalBytes")}
        for f in files[:page_size]
    ]


def dataset_license(ref: str) -> dict:
    """License files of a dataset (name + text when available)."""
    files = dataset_files(ref, page_size=100)
    lic = [f for f in files if str(f.get("name", "")).lower() in ("license.txt", "license", "licensing.txt")]
    return {"files": [f["name"] for f in files][:50], "license_files": [f["name"] for f in lic]}


def download_file(ref: str, file_name: str, dest_dir: str | Path) -> Path:
    """Download ONE file from a dataset (for small annotations/metadata;
    heavy image payloads stay Kaggle-side at training time)."""
    safe = file_name.replace("/", "__")
    dest = Path(dest_dir) / safe
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    return _download(f"{API_BASE}/datasets/download/{ref}/{file_name}", dest)


def download_dataset(ref: str, dest_dir: str | Path) -> Path:
    """Download the latest dataset archive as <owner>_<name>.zip."""
    dest = Path(dest_dir) / (ref.replace("/", "_") + ".zip")
    if dest.exists() and dest.stat().st_size > 0:
        return dest  # already downloaded
    return _download(f"{API_BASE}/datasets/download/{ref}", dest)


def write_manifest(
    ref: str,
    meta: dict,
    out_path: str | Path,
    extra: Optional[dict] = None,
) -> Path:
    """Machine-readable dataset manifest (directive §4). Credentials never
    appear here — licenses and provenance only."""
    manifest = {
        "dataset_name": meta.get("title") or ref,
        "source_url": f"https://www.kaggle.com/datasets/{ref}",
        "source_platform": "kaggle",
        "license": meta.get("license_name") or "LICENSE_REVIEW_REQUIRED",
        "license_url": meta.get("license_url") or "",
        "commercial_use_status": meta.get("commercial_use_status", "LICENSE_REVIEW_REQUIRED"),
        "redistribution_status": meta.get("redistribution_status", "LICENSE_REVIEW_REQUIRED"),
        "size_bytes": meta.get("size_bytes") or 0,
        "annotation_format": meta.get("annotation_format", ""),
        "task": meta.get("task", ""),
        "country_domain": meta.get("country_domain", ""),
        "recommended_use": meta.get("recommended_use", "research"),
        "notes": meta.get("notes", ""),
        "downloaded_at": meta.get("downloaded_at"),
        "files": meta.get("files", []),
    }
    if extra:
        manifest.update(extra)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Known candidate datasets (directive §4) with researched license status.
# Every entry is explicit about commercial usability — nothing is assumed.
# ---------------------------------------------------------------------------
CANDIDATES = {
    "sku110k": {
        "refs": ["thedatasith/sku110k-annotations"],
        "task": "retail_object_detection",
        "license": "CC BY-NC-SA 3.0 IGO (verified via Kaggle API)",
        "commercial_use_status": "NON_PRODUCTION_RESEARCH_ONLY",
        "size_bytes": 14_123_100_000,
        "notes": "Dense retail shelf detection, 11,762 images. Non-commercial: research/benchmark only. Mount inside Kaggle at training time; never redistribute.",
    },
    "rpc": {
        "refs": ["diyer22/retail-product-checkout-dataset"],
        "task": "checkout_scene_detection",
        "license": "CC BY-NC-SA 4.0 (verified via Kaggle API)",
        "commercial_use_status": "NON_PRODUCTION_RESEARCH_ONLY",
        "size_bytes": 27_205_200_000,
        "notes": "30k checkout scenes, 200 fine-grained SKUs. Non-commercial: research only. Mount inside Kaggle.",
    },
    "rp2k": {
        "refs": [],
        "task": "fine_grained_recognition",
        "license": "research license (request-based, non-commercial)",
        "commercial_use_status": "NON_PRODUCTION_RESEARCH_ONLY",
        "notes": "Fine-grained retail classification; access by request — not on Kaggle directly.",
    },
    "products10k": {
        "refs": [],
        "task": "fine_grained_recognition",
        "license": "custom research license (TenCent/JP pesquisas)",
        "commercial_use_status": "LICENSE_REVIEW_REQUIRED",
        "notes": "10k classes; hosted outside Kaggle (github products-10k).",
    },
    "indian_groceries": {
        "refs": ["akashram/indian-grocery-dataset"],
        "task": "indian_fmcg_detection",
        "license": "LICENSE_REVIEW_REQUIRED",
        "commercial_use_status": "LICENSE_REVIEW_REQUIRED",
        "notes": "Indian grocery images; verify license on the dataset page before any training use.",
    },
}


if __name__ == "__main__":
    import sys

    print("Kaggle dataset search (license-aware discovery)")
    for name, spec in CANDIDATES.items():
        print(f"\n== {name} [{spec['commercial_use_status']}] ==")
        if not spec["refs"]:
            print("   (not hosted on Kaggle; request/license review needed)")
            continue
        for ref in spec["refs"]:
            try:
                owner, slug = ref.split("/")
                rows = _get("/datasets/list", {"search": slug, "pageSize": 5})
                for d in rows or []:
                    if d.get("ref") == ref or slug in str(d.get("ref", "")):
                        print(f"   {d.get('ref')} | {d.get('title')} | "
                              f"{round((d.get('totalBytes') or 0)/1e6, 1)} MB | "
                              f"license: {d.get('licenseName')}")
                        break
            except Exception as exc:  # noqa: BLE001
                print(f"   lookup failed: {type(exc).__name__} {str(exc)[:120]}")
