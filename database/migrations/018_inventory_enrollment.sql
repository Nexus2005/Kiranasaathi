-- ============================================================
-- KiranaSaathi AI — 018 Inventory Enrollment + External Enrichment
--
-- Inventory onboarding becomes the primary product-enrollment path:
--   scan barcode → local lookup → external enrichment (Open Food Facts,
--   EXTERNAL enrichment only, never merchant truth) → merchant confirms →
--   store price/stock → image → visual embedding.
--
-- This migration adds ONLY infrastructure:
--   * external_product_cache  — provider responses cached server-side
--     (rate-limit friendly; provence + license metadata recorded)
--   * product_images.source_url / attribution — provenance for externally
--     sourced reference images (ODbL / CC BY-SA attribution duty)
--
-- NO business behavior changes. Price/stock stay store-authoritative.
-- ============================================================

-- 1. External product cache ------------------------------------------------
create table if not exists external_product_cache (
  id uuid primary key default gen_random_uuid(),
  provider text not null,                     -- 'openfoodfacts' (extensible)
  provider_version text not null default 'v3',
  barcode text not null,
  retrieved_at timestamptz not null default now(),
  found boolean not null,                     -- cache misses too (negative cache)
  payload jsonb not null,                     -- normalized enrichment payload
  source_url text,                            -- the exact API URL used
  license_note text,                          -- ODbL / CC BY-SA attribution duty
  created_at timestamptz not null default now()
);
create unique index if not exists uq_external_cache
  on external_product_cache(provider, provider_version, barcode);
create index if not exists idx_external_cache_retrieved
  on external_product_cache(provider, retrieved_at desc);

-- 2. Image provenance for externally sourced reference images ---------------
alter table product_images add column if not exists source_url text;
alter table product_images add column if not exists attribution text;
alter table product_images add column if not exists source text
  not null default 'merchant';               -- 'merchant' | 'external' | 'seed'

-- 3. RLS: cache is server-side infra; store data stays store-scoped ---------
alter table external_product_cache enable row level security;
alter table external_product_cache force row level security;

-- No per-store rows here (provider+barcode keyed), and NO policies are
-- created: the service role uses the server only; client roles get nothing
-- (consistent with 013/016 zero-grant posture). verify_security.py checks
-- RLS is enabled + forced.
