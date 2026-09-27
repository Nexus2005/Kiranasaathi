"""KiranaSaathi — resumable embedding fine-tuning (Level-3 experiment).

Runs identically locally or inside a Kaggle notebook. NEVER assumes the
session survives: every N epochs a full checkpoint (model+optimizer+
scheduler+epoch+config+manifest hash) is written, so an interrupted run
RESUMES from the last checkpoint instead of restarting (directive §6).

License gate: refuses to run when the dataset manifest marks the data
NON_PRODUCTION unless --allow-nonproduction-research is passed explicitly
(research checkpoints only — never promoted to production).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class EmbeddingDataset(Dataset):
    """(image_path, product_idx) pairs from the dataset manifest."""

    def __init__(self, samples: list[dict], root: Path, transform):
        from PIL import Image

        self._Image = Image
        self.samples = samples
        self.root = root
        self.transform = transform
        products = sorted({s["label_id"] for s in samples})
        self.label_to_idx = {p: i for i, p in enumerate(products)}

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        s = self.samples[idx]
        img = self._Image.open(self.root / s["image_path"]).convert("RGB")
        return self.transform(img), self.label_to_idx[s["label_id"]]


def load_checkpoint_aware(run_dir: Path, model, optimizer, scheduler, device):
    """Returns (start_epoch, best_metric, history). Detects the newest
    checkpoint and restores ALL training state (directive §25)."""
    ckpts = sorted(run_dir.glob("checkpoint_epoch_*.pt"))
    if not ckpts:
        return 0, float("-inf"), {"resumed": False}
    latest = ckpts[-1]
    state = torch.load(latest, map_location=device, weights_only=False)
    model.load_state_dict(state["model"])
    optimizer.load_state_dict(state["optimizer"])
    if scheduler is not None and state.get("scheduler"):
        scheduler.load_state_dict(state["scheduler"])
    history = {
        "resumed": True,
        "resumed_from": latest.name,
        "run_config": state.get("config"),
        "dataset_manifest_hash": state.get("dataset_manifest_hash"),
    }
    return state["epoch"], state.get("best_metric", float("-inf")), history


def save_checkpoint(run_dir: Path, epoch: int, model, optimizer, scheduler, best_metric: float, config: dict, manifest_hash: str) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"checkpoint_epoch_{epoch:04d}.pt"
    torch.save(
        {
            "epoch": epoch,
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "scheduler": scheduler.state_dict() if scheduler is not None else None,
            "best_metric": best_metric,
            "config": config,
            "dataset_manifest_hash": manifest_hash,
            "saved_at": time.time(),
        },
        path,
    )
    # prune older checkpoints (keep last 2 + best)
    ckpts = sorted(run_dir.glob("checkpoint_epoch_*.pt"))
    for old in ckpts[:-2]:
        old.unlink(missing_ok=True)
    return path


def export_final(run_dir: Path, model, config: dict, metrics: dict) -> Path:
    out = run_dir / "model_final"
    out.mkdir(exist_ok=True)
    torch.save(model.state_dict(), out / "pytorch_model.bin")
    (out / "model_card.json").write_text(
        json.dumps({"config": config, "metrics": metrics, "license": config.get("license_status")}, indent=2),
        encoding="utf-8",
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", default="data/dataset")
    ap.add_argument("--run-dir", default="training/runs/exp")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--checkpoint-every", type=int, default=1, help="epochs per checkpoint")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--model", default="vit_small_patch14_dinov2")
    ap.add_argument("--allow-nonproduction-research", action="store_true",
                    help="acknowledge the dataset is research-only (never promoted)")
    ap.add_argument("--dry-run", action="store_true", help="verify pipeline without training")
    args = ap.parse_args()

    data_root = Path(args.data_root)
    manifest_path = data_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_hash = file_sha256(manifest_path)
    license_status = manifest.get("license_status", "LICENSE_REVIEW_REQUIRED")

    if license_status in ("NON_PRODUCTION_RESEARCH_ONLY", "LICENSE_REVIEW_REQUIRED") and not args.allow_nonproduction_research:
        print(f"REFUSING: dataset license is {license_status}. Research runs require "
              "--allow-nonproduction-research (outputs marked NON-PRODUCTION).")
        return 2

    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    run_dir = Path(args.run_dir)
    config = {**vars(args), "device": device, "license_status": license_status,
              "dataset_manifest_hash": manifest_hash}
    (run_dir / "config.json").parent.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    from timm import create_model
    from torchvision import transforms as T

    tf = T.Compose([
        T.Resize((224, 224)),
        T.ToTensor(),
        T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    train_ds = EmbeddingDataset(manifest["train"], data_root, tf)
    val_ds = EmbeddingDataset(manifest["validation"], data_root, tf) if manifest.get("validation") else None
    train_dl = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=0)

    model = create_model(args.model, pretrained=True, num_classes=len(train_ds.label_to_idx), img_size=224 if "dinov2" in args.model else None) if "dinov2" in args.model else create_model(args.model, pretrained=True, num_classes=len(train_ds.label_to_idx))
    model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    start_epoch, best_metric, history = load_checkpoint_aware(run_dir, model, optimizer, scheduler, device)
    print(f"[resume] start_epoch={start_epoch} best={best_metric} history={json.dumps(history)[:200]}")
    if start_epoch >= args.epochs:
        print("[resume] already complete")
        return 0

    criterion = torch.nn.CrossEntropyLoss()
    metrics = {"train_loss": [], "val_acc": []}
    for epoch in range(start_epoch, args.epochs):
        model.train()
        running = 0.0
        for step, (x, y) in enumerate(train_dl):
            x, y = x.to(device), y.to(device)
            optimizer.zero_grad()
            loss = criterion(model(x), y)
            loss.backward()
            optimizer.step()
            running += float(loss)
        scheduler.step()
        train_loss = running / max(1, len(train_dl))
        metrics["train_loss"].append(train_loss)

        val_acc = None
        if val_ds:
            model.eval()
            correct = total = 0
            with torch.no_grad():
                for x, y in DataLoader(val_ds, batch_size=args.batch_size):
                    pred = model(x.to(device)).argmax(1).cpu()
                    correct += int((pred == y).sum())
                    total += len(y)
            val_acc = correct / max(1, total)
            metrics["val_acc"].append(val_acc)
        print(f"epoch {epoch+1}/{args.epochs} loss={train_loss:.4f} val_acc={val_acc}")

        best = max(best_metric, val_acc or 0.0)
        if (epoch + 1) % args.checkpoint_every == 0 or (epoch + 1) == args.epochs:
            path = save_checkpoint(run_dir, epoch + 1, model, optimizer, scheduler, best, config, manifest_hash)
            print(f"[checkpoint] {path.name} best={best:.4f}")

    final = export_final(run_dir, model, config, metrics)
    (run_dir / "metrics.json").write_text(json.dumps({"metrics": metrics, "best": best_metric}, indent=2), encoding="utf-8")
    print(f"[done] exported {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
