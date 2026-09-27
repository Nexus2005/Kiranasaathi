-- KiranaSaathi AI — 008 Phase 3: AI agent foundation.
-- Extends ai_recommendations (priority, evidence, dedup, actions), adds
-- ai_actions (prepared/approved/executed action records with idempotency),
-- widens recommendation lifecycle with EXECUTING, adds ai_conversations for
-- session-scoped assistant history.

-- ---------- recommendation schema upgrade ----------
alter table ai_recommendations
  add column if not exists summary text,
  add column if not exists priority integer not null default 0
    check (priority >= 0 and priority <= 100),
  add column if not exists priority_reason text,
  add column if not exists data_sources jsonb not null default '[]'::jsonb,
  add column if not exists reasoning_summary text,
  add column if not exists proposed_action jsonb not null default '{}'::jsonb,
  add column if not exists estimated_impact text,
  add column if not exists risk text,
  add column if not exists dedup_key text,
  add column if not exists updated_state jsonb,
  add column if not exists reviewed_at timestamptz,
  add column if not exists resolved_at timestamptz;

-- One OPEN (NEW/REVIEWED/APPROVED/EXECUTING) recommendation per dedup key.
update ai_recommendations r
set status = 'DISMISSED',
    resolved_at = now(),
    outcome = 'superseded by dedup migration'
from (
  select id, row_number() over (
    partition by store_id, dedup_key
    order by created_at desc, id desc
  ) as rn
  from ai_recommendations
  where dedup_key is not null
    and status in ('NEW','REVIEWED','APPROVED','EXECUTING')
) d
where r.id = d.id and d.rn > 1;

create unique index if not exists uq_reco_open_dedup
  on ai_recommendations (store_id, dedup_key)
  where status in ('NEW','REVIEWED','APPROVED','EXECUTING')
    and dedup_key is not null;

create index if not exists idx_reco_store_status
  on ai_recommendations (store_id, status, priority desc, created_at desc);

-- ---------- action lifecycle ----------
alter table ai_recommendations
  drop constraint if exists ai_recommendations_status_check;

alter table ai_recommendations
  add constraint ai_recommendations_status_check
  check (status in ('NEW','REVIEWED','APPROVED','EXECUTING','EXECUTED','COMPLETED','FAILED','DISMISSED','REJECTED'));

create table if not exists ai_actions (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  recommendation_id uuid references ai_recommendations(id) on delete set null,
  action_type text not null
    check (action_type in ('create_purchase','price_change','inventory_adjust')),
  payload jsonb not null,
  preview jsonb not null default '{}'::jsonb,
  state_fingerprint jsonb not null default '{}'::jsonb,
  status text not null default 'prepared'
    check (status in ('prepared','approved','executing','executed','failed','cancelled')),
  idempotency_key text not null,
  executed_reference_id uuid,
  error text,
  executed_at timestamptz,
  created_by uuid references users(id) on delete set null,
  created_at timestamptz not null default now(),
  unique (store_id, idempotency_key)
);

create index if not exists idx_ai_actions_store on ai_actions (store_id, created_at desc);

-- ---------- assistant conversations ----------
create table if not exists ai_conversations (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  user_id uuid references users(id) on delete set null,
  question text not null,
  answer text not null,
  tools_used jsonb not null default '[]'::jsonb,
  recommendations_created integer not null default 0,
  engine text not null default 'deterministic-rules-v1',
  created_at timestamptz not null default now()
);

create index if not exists idx_ai_conv_store on ai_conversations (store_id, created_at desc);
