-- ============================================================
-- KiranaSaathi AI — 017 Model Registry + Training Runs (Level-3 prep)
--
-- Tracks trained model versions and their promotion state. A model NEVER
-- becomes PRODUCTION merely because training finished: it must pass the
-- documented evaluation gate (license, offline metrics, unknown rejection,
-- similar-SKU, latency, regression vs the current baseline).
--
-- Security posture (013) preserved: RLS deny-by-default, no grants.
-- ============================================================

create table if not exists model_registry (
  id uuid primary key default gen_random_uuid(),
  model_name text not null,                 -- e.g. rtdetr_v2_r50vd_retail
  model_version text not null,              -- e.g. exp-2026-09-27-a
  task text not null check (task in ('detection', 'embedding', 'ocr')),
  artifact_location text not null,          -- training/artifacts/<run_id>/
  dataset_version text,
  dataset_manifest_hash text,
  -- EXPERIMENTAL -> CANDIDATE -> VALIDATED -> PRODUCTION | REJECTED | RETIRED
  status text not null default 'EXPERIMENTAL'
    check (status in ('EXPERIMENTAL', 'CANDIDATE', 'VALIDATED', 'PRODUCTION', 'REJECTED', 'RETIRED')),
  license_status text not null default 'LICENSE_REVIEW_REQUIRED'
    check (license_status in ('APACHE_2.0', 'MIT', 'COMMERCIAL_OK', 'NON_PRODUCTION_RESEARCH_ONLY', 'LICENSE_REVIEW_REQUIRED')),
  metrics jsonb,                            -- top1/top5/unknown-rejection/latency...
  latency_ms jsonb,                         -- p50/p95 per stage
  model_card jsonb,                         -- training config, data provenance
  promoted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (model_name, model_version)
);

create table if not exists training_runs (
  id uuid primary key default gen_random_uuid(),
  run_id text not null unique,              -- human-readable: train_20260927_001
  model_name text not null,
  model_version text not null,
  dataset_name text not null,
  dataset_version text not null,
  dataset_manifest_hash text,
  -- QUEUED | RUNNING | CHECKPOINTED | INTERRUPTED | RESUMABLE | COMPLETED
  -- | FAILED | EVALUATING | PROMOTED | REJECTED
  status text not null default 'QUEUED',
  config jsonb,                             -- hyperparameters (no secrets)
  checkpoint_location text,                 -- latest checkpoint artifact path
  best_metric jsonb,
  metrics jsonb,                            -- final evaluation metrics
  hardware text,                            -- e.g. kaggle-p100, local-rtx4050
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_training_runs_status on training_runs (status);
create index if not exists idx_model_registry_status on model_registry (status, task);

-- ---------- SECURITY POSTURE (013/015/016) ----------
do $$
declare t text;
begin
  for t in select unnest(array['model_registry', 'training_runs'])
  loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
  end loop;
end $$;

do $$
declare t text; r text;
begin
  for t in select unnest(array['model_registry', 'training_runs'])
  loop
    for r in select rolname from pg_roles where rolname in ('anon', 'authenticated')
    loop
      execute format('revoke all privileges on %I from %I', t, r);
    end loop;
  end loop;
end $$;
