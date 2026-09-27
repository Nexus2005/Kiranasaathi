-- ============================================================
-- KiranaSaathi AI — 016 Global Product Knowledge + Recognition Feedback
--
-- Two distinct knowledge layers (never mixed):
--   GLOBAL:  canonical product identity — name/brand/pack/barcodes/
--            verified reference images + embeddings. NO merchant
--            business data (price/cost/stock/supplier) ever lives here.
--   STORE:   existing products/inventory tables remain the source of
--            truth for merchant business data; product_global_links
--            associates a store product with a canonical global product.
--
-- Recognition events record what was predicted and what the merchant
-- did about it. Feedback strength comes from merchant interaction:
--   * explicit confirm            -> STRONG_POSITIVE
--   * accepted without correction -> POSITIVE_BEHAVIOR
--   * explicit correction         -> STRONG_NEGATIVE (hard negative)
-- Checkout success alone is NEVER ground truth; payment/stock/network
-- failures are transaction failures, not recognition failures.
--
-- Security posture (013/015) preserved: RLS enabled deny-by-default,
-- no grants to anon/authenticated, EXECUTE revoked from public.
-- ============================================================

-- ---------- GLOBAL PRODUCTS (canonical identity only) ----------
create table if not exists global_products (
  id uuid primary key default gen_random_uuid(),
  -- canonical_name + pack identify the SKU; brand/category aid retrieval/OCR
  canonical_name text not null,
  brand text,
  category text,
  pack_size text,
  pack_size_normalized text,
  -- UNVERIFIED -> CANDIDATE -> VERIFIED -> DEPRECATED (only VERIFIED may be
  -- used as authoritative identity evidence for other stores)
  verification_status text not null default 'CANDIDATE'
    check (verification_status in ('UNVERIFIED', 'CANDIDATE', 'VERIFIED', 'DEPRECATED')),
  status text not null default 'active' check (status in ('active', 'retired')),
  country text default 'IN',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists uq_global_products_identity
  on global_products (lower(canonical_name), coalesce(pack_size_normalized, ''));
create index if not exists idx_global_products_brand on global_products (lower(brand));

-- ---------- GLOBAL BARCODES (strongest shared identifier) ----------
create table if not exists global_product_barcodes (
  id uuid primary key default gen_random_uuid(),
  global_product_id uuid not null references global_products(id) on delete cascade,
  barcode text not null,
  barcode_type text,          -- EAN13 | UPC | GTIN | ...
  verification_status text not null default 'UNVERIFIED'
    check (verification_status in ('UNVERIFIED', 'CANDIDATE', 'VERIFIED', 'DEPRECATED')),
  created_at timestamptz not null default now(),
  unique (barcode, barcode_type)
);
create index if not exists idx_gpb_barcode on global_product_barcodes (barcode);

-- ---------- GLOBAL REFERENCE IMAGES (canonical views) ----------
create table if not exists global_product_images (
  id uuid primary key default gen_random_uuid(),
  global_product_id uuid not null references global_products(id) on delete cascade,
  image_url text,
  view text not null default 'front',
  -- provenance: which store contributed it (null = platform/manufacturer)
  source text not null default 'store_contribution',
  source_store_id uuid references stores(id) on delete set null,
  quality_score numeric check (quality_score >= 0 and quality_score <= 1),
  verification_status text not null default 'PENDING'
    check (verification_status in ('PENDING', 'ACCEPTED', 'REJECTED', 'NEEDS_REVIEW')),
  content_hash text,
  created_at timestamptz not null default now(),
  unique (global_product_id, content_hash)
);
create index if not exists idx_gpi_product on global_product_images (global_product_id);

-- ---------- GLOBAL EMBEDDINGS (verified visual memory, pgvector) ----------
create table if not exists global_product_embeddings (
  id uuid primary key default gen_random_uuid(),
  global_product_id uuid not null references global_products(id) on delete cascade,
  image_id uuid references global_product_images(id) on delete cascade,
  embedding_model text not null,
  embedding_version text not null,
  dimensions integer,
  source text not null default 'store_contribution',
  source_store_id uuid references stores(id) on delete set null,
  quality_score numeric check (quality_score >= 0 and quality_score <= 1),
  verification_status text not null default 'PENDING'
    check (verification_status in ('PENDING', 'ACCEPTED', 'REJECTED', 'NEEDS_REVIEW')),
  created_by uuid references users(id) on delete set null,
  created_at timestamptz not null default now(),
  unique (global_product_id, image_id, embedding_model, embedding_version, dimensions)
);
create index if not exists idx_gpe_product on global_product_embeddings (global_product_id);

-- pgvector column on the global index too (portable if extension missing)
do $$
begin
  if exists (select 1 from pg_extension where extname = 'vector') then
    execute 'alter table global_product_embeddings add column if not exists embedding vector';
  end if;
end $$;

-- ---------- STORE PRODUCT <-> GLOBAL PRODUCT LINK ----------
-- products.* remains the merchant business record (price/stock/tax).
create table if not exists product_global_links (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  product_id uuid not null references products(id) on delete cascade,
  global_product_id uuid not null references global_products(id) on delete cascade,
  linked_by uuid references users(id) on delete set null,
  -- barcode_match | merchant_confirmed | auto_matched
  link_method text not null default 'merchant_confirmed',
  created_at timestamptz not null default now(),
  unique (store_id, product_id, global_product_id)
);
create index if not exists idx_pgl_global on product_global_links (global_product_id);
create index if not exists idx_pgl_store on product_global_links (store_id);

-- ---------- MERCHANT CONTRIBUTIONS (consent-gated enrichment) ----------
-- A store's verified observation MAY enrich the global index — only when the
-- merchant opted in (contribution_consent) AND it passes the quality gate.
create table if not exists product_visual_contributions (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  product_id uuid not null references products(id) on delete cascade,
  global_product_id uuid references global_products(id) on delete set null,
  image_id uuid references product_images(id) on delete cascade,
  embedding_id uuid references product_visual_embeddings(id) on delete cascade,
  -- STORE_ONLY | PENDING_REVIEW | GLOBAL_APPROVED | REJECTED
  contribution_status text not null default 'PENDING_REVIEW'
    check (contribution_status in ('STORE_ONLY', 'PENDING_REVIEW', 'GLOBAL_APPROVED', 'REJECTED')),
  quality_score numeric check (quality_score >= 0 and quality_score <= 1),
  content_hash text,
  created_by uuid references users(id) on delete set null,
  created_at timestamptz not null default now(),
  unique (store_id, product_id, embedding_id)
);
create index if not exists idx_pvc_status on product_visual_contributions (contribution_status);

-- ---------- RECOGNITION EVENTS (feedback ledger) ----------
create table if not exists recognition_events (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  product_id uuid references products(id) on delete set null,      -- predicted
  confirmed_product_id uuid references products(id) on delete set null, -- actual (corrections)
  global_product_id uuid references global_products(id) on delete set null,
  detection_id text,
  frame_id text,
  predicted_confidence numeric check (predicted_confidence >= 0 and predicted_confidence <= 1),
  recognition_method text,        -- BARCODE | VISUAL | COMBINED | ...
  visual_similarity numeric,
  ocr_score numeric,
  barcode_match boolean,
  model_version text,
  detector_version text,
  ocr_version text,
  -- AUTO_ACCEPTED | MERCHANT_CONFIRMED | MERCHANT_CORRECTED | MERCHANT_REJECTED
  -- | REMOVED | MANUAL_REPLACEMENT | UNRESOLVED
  user_action text,
  -- STRONG_POSITIVE | POSITIVE_BEHAVIOR | UNCERTAIN | STRONG_NEGATIVE | NOT_RECOGNITION_NEGATIVE
  feedback_label text,
  checkout_status text,           -- COMPLETED | FAILED | PENDING | null (no sale)
  failure_reason text,            -- PAYMENT_FAILED | INSUFFICIENT_STOCK | ... (context)
  created_by uuid references users(id) on delete set null,
  created_at timestamptz not null default now()
);
create index if not exists idx_recog_events_store on recognition_events (store_id, created_at desc);
create index if not exists idx_recog_events_product on recognition_events (product_id, feedback_label);
create index if not exists idx_recog_events_frame on recognition_events (store_id, frame_id);

-- Idempotency: one feedback action per (frame, detection). The API enforces
-- this too (conflict-aware upsert); the DB enforces it last.
create unique index if not exists uq_recognition_events_frame_detection
  on recognition_events (coalesce(frame_id, ''), coalesce(detection_id, ''));

-- Partial stats index for the most common dashboard query
create index if not exists idx_recog_events_feedback
  on recognition_events (product_id, user_action) where user_action is not null;

-- ---------- MERCHANT CONTRIBUTION CONSENT (per store policy) ----------
-- Stores the merchant's choice to allow anonymous global product-improvement
-- contributions from their verified enrollment images (§44). Default NULL =
-- undecided -> treated as NOT consented. Never silently share merchant images.
alter table store_settings add column if not exists visual_contribution_consent boolean;

-- ---------- TOUCH TRIGGERS ----------
drop trigger if exists trg_global_products_touch on global_products;
create trigger trg_global_products_touch before update on global_products
for each row execute function touch_updated_at();

-- ---------- SECURITY POSTURE (013/015) ----------
do $$
declare t text;
begin
  for t in select unnest(array[
    'global_products', 'global_product_barcodes', 'global_product_images',
    'global_product_embeddings', 'product_global_links',
    'product_visual_contributions', 'recognition_events'
  ])
  loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force row level security', t);
  end loop;
end $$;

do $$
declare t text; r text;
begin
  for t in select unnest(array[
    'global_products', 'global_product_barcodes', 'global_product_images',
    'global_product_embeddings', 'product_global_links',
    'product_visual_contributions', 'recognition_events'
  ])
  loop
    for r in select rolname from pg_roles where rolname in ('anon', 'authenticated')
    loop
      execute format('revoke all privileges on %I from %I', t, r);
    end loop;
  end loop;
end $$;
