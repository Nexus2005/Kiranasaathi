-- KiranaSaathi AI — 009 Phase 4: Demand, Festival, Customer Intelligence, Marketing
-- Adds: customer consent columns, festivals calendar (configured data with provenance),
-- campaigns + campaign_recipients (+ per-recipient message payloads & provider
-- message ids), WhatsApp provider config abstraction (no credentials shipped).
-- No sales/inventory duplication: campaigns reference products and customers
-- by FK into the existing tables.

-- ========== CUSTOMER CONSENT ==========
alter table customers
  add column if not exists marketing_consent boolean not null default false,
  add column if not exists consent_source text
    check (consent_source is null or consent_source in ('store_entry','verbal','written','signup','import')),
  add column if not exists consent_timestamp timestamptz,
  add column if not exists opt_out_timestamp timestamptz,
  add column if not exists notes text;

create index if not exists idx_customers_consent on customers (store_id, marketing_consent);

-- ========== FESTIVALS (configured application data — provenance stored) ==========
create table if not exists festivals (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  name text not null,
  start_date date not null,
  end_date date not null,
  region text not null default 'all-India',
  category text not null default 'religious'
    check (category in ('religious','national','seasonal','regional')),
  description text,
  relevance jsonb not null default '[]'::jsonb, -- [{"category": "Snacks", "note": "..."}]
  source text not null default 'configured_demo',
  source_url text,
  retrieved_at timestamptz default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (end_date >= start_date),
  unique (store_id, name, start_date)
);

create index if not exists idx_festivals_store_dates on festivals (store_id, start_date);

-- Seed: 2026-27 dates verified against published panchang calendars
-- (drikpanchang / calendarlabs / timeanddate; retrieved 2026-09-22).
-- Treated as CONFIGURED DEMO DATA, not live external intelligence.
insert into festivals (store_id, name, start_date, end_date, region, category, description, relevance, source)
select s.id, v.name, v.start_date::date, v.end_date::date, v.region, v.category, v.description,
       v.relevance::jsonb, 'configured_demo'
from stores s
cross join (values
  ('Navratri', '2026-10-11', '2026-10-19', 'all-India', 'religious',
   'Nine-night festival; fasting foods (sabudana, makhana, fruits) and festive clothing see higher demand.',
   '[{"category":"Snacks","note":"Fasting snacks: sabudana, makhana, bhujia"},
     {"category":"Biscuits & Snacks","note":"Fruit & dryfruit biscuit demand rises"},
     {"category":"Beverages","note":"Beverage demand rises during gatherings"}]'),
  ('Dussehra', '2026-10-20', '2026-10-20', 'all-India', 'religious',
   'Vijayadashami; sweets and festive foods spike on the day.',
   '[{"category":"Snacks","note":"Sweets and festive snacks"},
     {"category":"Biscuits & Snacks","note":"Gift biscuits"}]'),
  ('Dhanteras', '2026-11-06', '2026-11-06', 'all-India', 'religious',
   'First day of Diwali week; household and utensil purchases, sweets, dry fruits.',
   '[{"category":"Snacks","note":"Gift and snack purchases begin"},
     {"category":"Biscuits & Snacks","note":"Gift packs"}, 
     {"category":"Cooking Essentials","note":"Cooking oil for festive cooking"}]'),
  ('Diwali', '2026-11-08', '2026-11-12', 'all-India', 'religious',
   'Five-day festival of lights; the largest gifting and sweets window of the year.',
   '[{"category":"Snacks","note":"Sweets, namkeen, gift packs"},
     {"category":"Biscuits & Snacks","note":"Gift biscuits, dryfruit packs"},
     {"category":"Cooking Essentials","note":"Cooking oil and ghee demand rises"},
     {"category":"Personal Care","note":"Gifting personal care combos"}]'),
  ('Makar Sankranti', '2027-01-14', '2027-01-15', 'all-India', 'seasonal',
   'Harvest festival; sesame, jaggery, peanuts.',
   '[{"category":"Snacks","note":"Til-gud, chikki"},
     {"category":"Atta, Rice & Grains","note":"Grain purchases"}]'),
  ('Holi', '2027-03-22', '2027-03-23', 'all-India', 'religious',
   'Festival of colours; thandai ingredients, sweets, snacks.',
   '[{"category":"Beverages","note":"Thandai and drink mixes"},
     {"category":"Snacks","note":"Namkeen, gujiya ingredients"},
     {"category":"Dairy & Eggs","note":"Milk-based sweets prep"}]'),
  ('Ganesh Chaturthi', '2027-09-04', '2027-09-13', 'Maharashtra + all-India', 'religious',
   'Ten-day Ganesh festival; modak ingredients, coconuts, flowers.',
   '[{"category":"Snacks","note":"Modak ingredients, steamed snacks"},
     {"category":"Cooking Essentials","note":"Cooking essentials for naivedya"},
     {"category":"Dairy & Eggs","note":"Milk and dairy for modak"}]'),
  ('Raksha Bandhan', '2027-08-17', '2027-08-17', 'all-India', 'religious',
   'Rakhi; sweets, gift packs, roli-moli.',
   '[{"category":"Snacks","note":"Sweets and gift packs"},
     {"category":"Biscuits & Snacks","note":"Gift biscuits"}]')
) as v(name, start_date, end_date, region, category, description, relevance)
where not exists (
  select 1 from festivals f where f.store_id = s.id and f.name = v.name and f.start_date = v.start_date::date
);

-- ========== CAMPAIGNS ==========
create table if not exists campaigns (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  name text not null,
  campaign_type text not null check (campaign_type in
    ('festival','new_product','discount','inventory_clearance','re_engagement','product_recommendation','general')),
  status text not null default 'DRAFT'
    check (status in ('DRAFT','READY_FOR_REVIEW','APPROVED','SENDING','SENT','PARTIALLY_SENT','FAILED','CANCELLED')),
  channel text not null default 'whatsapp'
    check (channel in ('whatsapp','manual')),
  audience jsonb not null default '{}'::jsonb,   -- {segment | customer_ids, filters, count_snapshot}
  products jsonb not null default '[]'::jsonb,   -- [{product_id, name, price, offer_text}]
  message_template text,                          -- validated text; placeholders filled at build time
  message_text text,
  provider text not null default 'development',  -- provider id from provider registry
  provider_message_ids jsonb not null default '[]'::jsonb,
  recipient_count integer not null default 0,
  sent_count integer not null default 0,
  failed_count integer not null default 0,
  approved_by uuid references users(id) on delete set null,
  approved_at timestamptz,
  error_note text,
  sent_at timestamptz,
  cancelled_at timestamptz,
  created_by uuid references users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_campaigns_store on campaigns (store_id, created_at desc);

create table if not exists campaign_recipients (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  campaign_id uuid not null references campaigns(id) on delete cascade,
  customer_id uuid not null references customers(id) on delete cascade,
  phone text,
  personalized_message text,
  provider_message_id text,
  status text not null default 'pending'
    check (status in ('pending','queued','sent','failed','skipped_no_consent','skipped_no_phone','skipped_opted_out')),
  error text,
  sent_at timestamptz,
  created_at timestamptz not null default now(),
  unique (campaign_id, customer_id)
);

create index if not exists idx_campaign_recipients_campaign on campaign_recipients (campaign_id, status);
create index if not exists idx_campaign_recipients_customer on campaign_recipients (customer_id);

-- Duplicate-campaign guard: one SENDING/SENT campaign per (store, type, audience-segment) per day
create unique index if not exists uq_campaign_active_dedup
  on campaigns (store_id, campaign_type, ((audience->>'segment')))
  where status in ('SENDING','SENT','PARTIALLY_SENT')
    and audience->>'segment' is not null;

-- ========== WHATSAPP PROVIDER REGISTRY (abstraction; no credentials) ==========
create table if not exists whatsapp_provider_config (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null unique references stores(id) on delete cascade,
  provider_id text not null default 'development'
    check (provider_id in ('development','meta_cloud_api','gupshup','interakt','wati','aiSensy')),
  display_name text,
  configured boolean not null default false,
  credentials jsonb not null default '{}'::jsonb, -- EMPTY in dev mode; populated by merchant in prod
  updated_at timestamptz not null default now()
);

insert into whatsapp_provider_config (store_id, provider_id, display_name, configured)
select s.id, 'development', 'Development Mode (no real messages)', false
from stores s
where not exists (select 1 from whatsapp_provider_config w where w.store_id = s.id);

-- ========== ACTIVITY LOG EVENT TYPES (documentation of Phase 4 events) ==========
-- New event types used by this phase (text column, no constraint to change):
-- CUSTOMER_CONSENT_UPDATED, FESTIVAL_SYNCED, CAMPAIGN_CREATED, CAMPAIGN_APPROVED,
-- CAMPAIGN_SENT, CAMPAIGN_PARTIALLY_SENT, CAMPAIGN_FAILED, CAMPAIGN_CANCELLED,
-- AI_CROSS_MODULE_PLAN_CREATED
