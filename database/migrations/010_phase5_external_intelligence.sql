-- KiranaSaathi AI — 010 Phase 5: External Intelligence + Evidence Layer
-- Three-way separation enforced by schema:
--   MERCHANT DATA   = existing tables (sales, inventory, ...)  [SOURCE A]
--   EXTERNAL DATA   = evidence_items (raw + normalized)        [SOURCE B]
--   AI INFERENCE    = ai_recommendations / ai_conversations    [never stored as fact]
--
-- evidence_items is append-only. external_signals are derived views over
-- verified evidence and never feed pricing/inventory math. Store isolation
-- via store_id FK on every table.

-- ========== SOURCE REGISTRY ==========
create table if not exists external_sources (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  source_key text not null,                 -- 'rss:<host>' | 'manual:<name>' | 'gov:<domain>'
  name text not null,
  kind text not null check (kind in ('rss','web_page','manual','api')),
  base_url text not null,
  trust_tier text not null default 'UNVERIFIED'
    check (trust_tier in ('OFFICIAL','ESTABLISHED','UNVERIFIED')),
  region text not null default 'all-India',
  enabled boolean not null default true,
  last_fetched_at timestamptz,
  created_at timestamptz not null default now(),
  unique (store_id, source_key)
);

-- ========== EVIDENCE ITEMS (append-only) ==========
create table if not exists evidence_items (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  source_id uuid references external_sources(id) on delete set null,
  source_kind text not null default 'manual',
  source_name text not null,
  source_url text,
  title text not null,
  summary text,
  raw_payload jsonb not null default '{}'::jsonb,
  published_at timestamptz,
  retrieved_at timestamptz not null default now(),
  region text,
  category text,
  verification_status text not null default 'UNVERIFIED'
    check (verification_status in ('UNVERIFIED','VERIFIED','REJECTED','EXPIRED')),
  verified_at timestamptz,
  verification_notes text,
  trust_tier text not null default 'UNVERIFIED'
    check (trust_tier in ('OFFICIAL','ESTABLISHED','UNVERIFIED')),
  relevance jsonb not null default '[]'::jsonb,   -- matched store categories
  dedup_hash text not null,
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  unique (store_id, dedup_hash)
);

create index if not exists idx_evidence_store on evidence_items (store_id, retrieved_at desc);
create index if not exists idx_evidence_status on evidence_items (store_id, verification_status);
create index if not exists idx_evidence_category on evidence_items (store_id, category);

-- ========== DERIVED SIGNALS (clearly-labelled external) ==========
create table if not exists external_signals (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  signal_type text not null check (signal_type in ('demand_trend','category_note','price_mention','event_context')),
  title text not null,
  statement text not null,                    -- the claim, quoted from evidence
  category text,
  region text,
  confidence text not null default 'LOW'
    check (confidence in ('HIGH','MEDIUM','LOW')),
  evidence_ids jsonb not null default '[]'::jsonb,  -- supporting evidence_item ids
  created_at timestamptz not null default now(),
  expires_at timestamptz
);

create index if not exists idx_signals_store on external_signals (store_id, created_at desc);

-- ========== CONFIGURED STARTER SOURCES (provenance explicit) ==========
-- Manual/demo sources ship enabled; RSS/web sources ship disabled until the
-- merchant (or a later iteration) enables them — no background scraping by default.
insert into external_sources (store_id, source_key, name, kind, base_url, trust_tier, region, enabled)
select s.id, v.source_key, v.name, v.kind, v.base_url, v.trust_tier, v.region, v.enabled
from stores s
cross join (values
  ('manual:merchant-observations', 'Merchant observations', 'manual', 'local', 'OFFICIAL', 'all-India', true),
  ('rss:pib-gov-in', 'Press Information Bureau (Government of India)', 'rss', 'https://pib.gov.in', 'OFFICIAL', 'all-India', false),
  ('rss:agri-welcome-gov-in', 'Agri Marketing (Dept. of Agriculture)', 'rss', 'https://agriwelcome.gov.in', 'OFFICIAL', 'all-India', false)
) as v(source_key, name, kind, base_url, trust_tier, region, enabled)
where not exists (
  select 1 from external_sources e where e.store_id = s.id and e.source_key = v.source_key
);
