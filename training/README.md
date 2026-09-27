# KiranaSaathi Training Package (Level-3 preparation)

Reproducible training/evaluation runs, locally or on Kaggle. Production
Smart Counter NEVER depends on Kaggle — this is offline experimentation
infrastructure only (directive §26).

## Layout

```
training/
  train.py        # resumable fine-tuning (checkpoint every N epochs)
  evaluate.py     # retrieval metrics + promotion gate
  resume.py       # checkpoint inspector (proves resumability)
  runs/<exp>/     # config.json, checkpoint_epoch_*.pt, metrics.json, evaluation.json
scripts/dataset/
  kaggle_client.py    # license-aware discovery + manifests (REST, Bearer)
  manifests/*.json    # per-dataset license/usage manifests
```

## Workflow

```
LOCAL
  → build dataset (scripts/dataset: collect→dedup→grouped splits→manifest hash)
  → license gate (train.py REFUSES unclear/non-commercial data unless the
    explicit --allow-nonproduction-research research flag is passed)
  → train (checkpoint/terminate/resume; never fight Kaggle's time limit)
  → evaluate (top1/top5, unknown rejection; must beat the production baseline)
  → model_registry: EXPERIMENTAL → CANDIDATE → VALIDATED → PRODUCTION
```

## Kaggle usage (thin runner, no logic in notebooks)

```python
# notebook cell: upload training/ as a Kaggle Dataset, then
!python /kaggle/working/training/train.py --data-root /kaggle/working/data \
    --run-dir /kaggle/working/runs/exp1 --epochs 10 --checkpoint-every 1
```

Sessions die — that is fine: the next run detects `checkpoint_epoch_*.pt`
(model+optimizer+scheduler+epoch+config+manifest hash) and resumes. Copy
`runs/` out (Kaggle Dataset / persistent storage) between sessions.

## Credential safety

`KAGGLE_API_TOKEN` (new KGAT_ format) is read from the environment or the
git-ignored local `Kaggle.txt` — never hard-coded, printed, logged, or
written into manifests/artifacts. Dataset manifests record LICENSES and
provenance only.

## License gate (enforced in code)

| dataset | license | allowed |
|---|---|---|
| SKU-110K | CC BY-NC-SA 3.0 IGO | research only (`--allow-nonproduction-research`) |
| RPC | CC BY-NC-SA 4.0 | research only |
| RP2K / Products-10K | research licenses | request/review |
| production models (RT-DETR, DINOv2, RapidOCR) | Apache-2.0 | production |

NON-PRODUCTION outputs are marked in `model_card.json` and can never pass
the promotion gate (`license_ok` is part of `evaluate.py`'s gate set).
