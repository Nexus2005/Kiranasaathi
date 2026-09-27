-- ============================================================
-- KiranaSaathi AI — 014 Phase 8: omnichannel commerce
-- Extends the Phase 6 order model (no new order engine):
--   * channel + external source on orders (single lifecycle for all channels)
--   * delivery tracking (no live logistics claims)
--   * guest customer fields + public token (customer ordering links)
--   * campaign attribution column (orders carry campaign_id)
--   * channel price overrides (bounded by the same pricing rules)
--   * quick-commerce provider registry, listing mapping, event dedup
--   * store catalog settings (merchant-controlled storefront visibility)
--   * customer OTP codes (hashed; lightweight customer auth)
-- Supabase PostgreSQL remains the single source of truth.
-- ============================================================

-- ---------- STORE: public ordering identity ----------
alter table stores add column if not exists slug text;
create unique index if not exists uq_stores_slug on stores(slug) where slug is not null;

create table if not exists store_catalog_settings (
  store_id uuid primary key references stores(id) on delete cascade,
  is_published boolean not null default false,
  show_stock boolean not null default false,      -- expose availability counts (not internal qty intelligence)
  allow_guest_checkout boolean not null default true,
  delivery_fee numeric(12,2) not null default 0 check (delivery_fee >= 0),
  min_order_amount numeric(12,2) not null default 0 check (min_order_amount >= 0),
  updated_at timestamptz not null default now()
);

-- ---------- ORDERS: channel + source + delivery + guest + attribution ----------
alter table orders
  add column if not exists channel text not null default 'POS'
    check (channel in ('POS','CUSTOMER_WEB','CUSTOMER_LINK','WHATSAPP','QUICK_COMMERCE','MARKETPLACE','PHONE','MANUAL','OTHER')),
  add column if not exists source_name text,
  add column if not exists external_order_id text,
  add column if not exists external_reference text,
  add column if not exists guest_name text,
  add column if not exists guest_phone text,
  add column if not exists delivery_method text not null default 'PICKUP'
    check (delivery_method in ('PICKUP','MERCHANT_DELIVERY','THIRD_PARTY_DELIVERY','OTHER')),
  add column if not exists delivery_address text,
  add column if not exists delivery_status text
    check (delivery_status is null or delivery_status in
      ('NOT_REQUIRED','PENDING','ASSIGNED','OUT_FOR_DELIVERY','DELIVERED','FAILED')),
  add column if not exists delivery_reference text,
  add column if not exists public_token uuid not null default gen_random_uuid(),
  add column if not exists campaign_id uuid references campaigns(id) on delete set null,
  add column if not exists accepted_at timestamptz,
  add column if not exists dispatched_at timestamptz,
  add column if not exists delivered_at timestamptz,
  add column if not exists cancelled_reason text;

create index if not exists idx_orders_store_channel on orders(store_id, channel, created_at desc);
-- One logical order per external provider order (webhook dedup at DB level)
create unique index if not exists uq_orders_store_external
  on orders(store_id, external_order_id) where external_order_id is not null;
create index if not exists idx_orders_store_token on orders(store_id, public_token);

-- Merchant acceptance + fulfillment states (customer-order lifecycle):
-- PAID -> CONFIRMED -> PROCESSING -> READY -> OUT_FOR_DELIVERY -> DELIVERED -> COMPLETED
-- (PICKUP orders go READY -> COMPLETED; POS orders skip fulfillment states)
alter table orders drop constraint if exists orders_state_check;
alter table orders add constraint orders_state_check
  check (state in (
    'DRAFT','PENDING_PAYMENT','PARTIALLY_PAID','PAID','CONFIRMED','PROCESSING','READY',
    'OUT_FOR_DELIVERY','DELIVERED','COMPLETED','CANCELLED','PAYMENT_FAILED',
    'REFUND_PENDING','REFUNDED'
  ));

-- Storefront product imagery (optional; professional placeholder when null)
alter table products add column if not exists image_url text;

-- ---------- CHANNEL PRICE OVERRIDES ----------
create table if not exists channel_prices (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  product_id uuid not null references products(id) on delete cascade,
  channel text not null check (channel in
    ('CUSTOMER_WEB','CUSTOMER_LINK','WHATSAPP','QUICK_COMMERCE','MARKETPLACE','PHONE','MANUAL','OTHER')),
  price numeric(12,2) not null check (price >= 0),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (store_id, product_id, channel)
);

create index if not exists idx_channel_prices_store on channel_prices(store_id, channel);

-- ---------- QUICK COMMERCE: provider registry (truthful status) ----------
create table if not exists qc_providers (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  provider text not null check (provider in ('BLINKIT','ZEPTO','SWIGGY','ONDC','OTHER')),
  -- Truthful status machine. DEMO/SIMULATED must never be displayed as live.
  status text not null default 'NOT_CONFIGURED'
    check (status in ('CONNECTED','NOT_CONFIGURED','AUTH_REQUIRED','ERROR','SYNCING','DEMO')),
  mode text not null default 'unconfigured' check (mode in ('live','demo','unconfigured')),
  config jsonb not null default '{}'::jsonb,   -- never secret material beyond what the merchant enters
  connected_account text,
  last_sync_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (store_id, provider)
);

-- ---------- QUICK COMMERCE: catalog mapping ----------
create table if not exists qc_listings (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  provider_id uuid not null references qc_providers(id) on delete cascade,
  product_id uuid not null references products(id) on delete cascade,
  internal_sku text,
  external_product_id text,
  external_sku text,
  external_listing_id text,
  sync_status text not null default 'PENDING'
    check (sync_status in ('PENDING','SYNCED','ERROR','REMOVED')),
  last_synced_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (provider_id, product_id)
);

create index if not exists idx_qc_listings_store on qc_listings(store_id, sync_status);

-- ---------- QUICK COMMERCE: inbound event dedup ----------
create table if not exists qc_events (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  provider text not null,
  event_type text not null,
  external_event_id text not null,
  payload jsonb,
  status text not null default 'PROCESSED' check (status in ('PROCESSED','REJECTED','ERROR')),
  note text,
  created_at timestamptz not null default now(),
  unique (store_id, provider, external_event_id)
);

-- ---------- CUSTOMER LIGHTWEIGHT AUTH (OTP) ----------
create table if not exists customer_otp_codes (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  phone text not null,
  code_hash text not null,            -- sha256; plaintext code is never stored
  expires_at timestamptz not null,
  consumed_at timestamptz,
  attempts integer not null default 0 check (attempts >= 0),
  created_at timestamptz not null default now()
);

create index if not exists idx_customer_otp_lookup
  on customer_otp_codes(store_id, phone, created_at desc);

-- ---------- updated_at triggers for new tables ----------
drop trigger if exists trg_channel_prices_touch on channel_prices;
create trigger trg_channel_prices_touch before update on channel_prices
for each row execute function touch_updated_at();

drop trigger if exists trg_qc_providers_touch on qc_providers;
create trigger trg_qc_providers_touch before update on qc_providers
for each row execute function touch_updated_at();

drop trigger if exists trg_qc_listings_touch on qc_listings;
create trigger trg_qc_listings_touch before update on qc_listings
for each row execute function touch_updated_at();

drop trigger if exists trg_store_catalog_settings_touch on store_catalog_settings;
create trigger trg_store_catalog_settings_touch before update on store_catalog_settings
for each row execute function touch_updated_at();
