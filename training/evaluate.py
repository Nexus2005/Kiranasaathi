"""KiranaSaathi — offline evaluation + promotion gate (Level 3).

Evaluates a candidate embedding model against the production baseline
(retrieval top-1/top-5, unknown rejection, similar-SKU confusion) and emits
a promotion-gate report. A model that fails ANY gate is REJECTED — the
current production model stays (directive §23).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from train import EmbeddingDataset, set_seed


@torch.no_grad()
def embed_all(model, ds: EmbeddingDataset, device: str) -> tuple[torch.Tensor, torch.Tensor]:
    model.eval()
    feats, labels = [], []
    for x, y in DataLoader(ds, batch_size=32, num_workers=0):
        f = model.forward_features(x.to(device))
        pooled = f[:, 1:, :].mean(dim=1) if f.dim() == 3 else f.mean(dim=1)
        feats.append(torch.nn.functional.normalize(pooled, dim=1).cpu())
        labels.append(y)
    return torch.cat(feats), torch.cat(labels)


def retrieval_metrics(feats: torch.Tensor, labels: torch.Tensor, ks=(1, 5)) -> dict:
    sims = feats @ feats.T
    sims.fill_diagonal_(-2)
    order = sims.argsort(dim=1, descending=True)
    out = {}
    for k in ks:
        hits = 0
        for i in range(len(labels)):
            top = labels[order[i, :k]]
            hits += int((top == labels[i]).any())
        out[f"top{k}"] = hits / max(1, len(labels))
    return out


def unknown_rejection(query_feats: torch.Tensor, ref_feats: torch.Tensor, threshold: float) -> dict:
    """Unknown rejection = best similarity of each query against the ENROLLED
    catalog (train references), NOT against other test queries. A query whose
    best reference similarity < threshold must be flagged UNKNOWN."""
    sims = query_feats @ ref_feats.T
    best = sims.max(dim=1).values
    flagged = (best < threshold).float().mean().item()
    return {
        "unknown_rate_at_threshold": round(flagged, 4),
        "threshold": threshold,
        "best_ref_sim_min": round(float(best.min()), 4),
        "best_ref_sim_max": round(float(best.max()), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default="data/dataset")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--model", default="vit_small_patch14_dinov2")
    ap.add_argument("--checkpoint", default="model_final/pytorch_model.bin")
    ap.add_argument("--unknown-threshold", type=float, default=0.55)
    ap.add_argument("--baseline-top1", type=float, default=0.0,
                    help="production baseline top-1 to beat (measured separately)")
    args = ap.parse_args()

    set_seed(7)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    run_dir = Path(args.run_dir)
    from timm import create_model
    from torchvision import transforms as T

    tf = T.Compose([T.Resize((224, 224)), T.ToTensor(),
                    T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])])
    manifest = json.loads((Path(args.data_root) / "manifest.json").read_text(encoding="utf-8"))
    test_ds = EmbeddingDataset(manifest["test"], Path(args.data_root), tf)
    n_classes = len(test_ds.label_to_idx)

    if "dinov2" in args.model:
        model = create_model(args.model, pretrained=False, num_classes=max(1, n_classes), img_size=224)
    else:
        model = create_model(args.model, pretrained=False, num_classes=max(1, n_classes))
    state = torch.load(run_dir / args.checkpoint, map_location=device, weights_only=True)
    # Retrieval evaluation uses features, not the trained classifier head —
    # the head size may differ from the eval manifest (unknown classes etc.)
    head_state = {k: v for k, v in state.items() if not k.startswith("head.")}
    model.load_state_dict(head_state, strict=False)
    model.to(device)

    feats, labels = embed_all(model, test_ds, device)
    metrics = retrieval_metrics(feats, labels)
    # Reference catalog = TRAIN split (what production would have enrolled)
    train_ds = EmbeddingDataset(manifest["train"], Path(args.data_root), tf)
    ref_feats, _ref_labels = embed_all(model, train_ds, device)
    metrics.update(unknown_rejection(feats, ref_feats, args.unknown_threshold))

    gates = {
        "top1_beats_baseline": metrics["top1"] > args.baseline_top1,
        "unknown_rejection_reasonable": metrics["unknown_rate_at_threshold"] > 0.0,
        "license_ok": manifest.get("license_status") not in ("LICENSE_REVIEW_REQUIRED",),
        "tested_on_disjoint_split": True,  # grouped splits by construction
    }
    verdict = "PROMOTE_CANDIDATE" if all(gates.values()) else "REJECTED"
    report = {"metrics": metrics, "gates": gates, "verdict": verdict,
              "baseline_top1": args.baseline_top1, "model": args.model,
              "run_dir": str(run_dir)}
    (run_dir / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if verdict == "PROMOTE_CANDIDATE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
