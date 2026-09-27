"""KiranaSaathi — checkpoint resume inspector (directive §25).

Reports the newest checkpoint in a run dir and the exact state that would be
restored — used by the session-interruption test to PROVE resumability
without eyeballing tensor dumps.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch


def inspect(run_dir: Path) -> dict:
    ckpts = sorted(Path(run_dir).glob("checkpoint_epoch_*.pt"))
    if not ckpts:
        return {"resumable": False, "checkpoints": 0}
    latest = ckpts[-1]
    state = torch.load(latest, map_location="cpu", weights_only=False)
    return {
        "resumable": True,
        "checkpoints": len(ckpts),
        "latest": latest.name,
        "epoch": state["epoch"],
        "best_metric": state.get("best_metric"),
        "has_optimizer": state.get("optimizer") is not None,
        "has_scheduler": state.get("scheduler") is not None,
        "config_model": (state.get("config") or {}).get("model"),
        "dataset_manifest_hash": (state.get("dataset_manifest_hash") or "")[:16],
        "saved_at": state.get("saved_at"),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    print(json.dumps(inspect(Path(args.run_dir)), indent=2))
