-- ============================================================
-- KiranaSaathi AI — 019 Product Identity Enrichment + Image + Edit Rights
--
-- Real product data from Open Food Facts (ODbL) replaces dummy seeds:
--   * products.brand / description / image_url / source / external_id
--   * image_url is the display image (external OFF URL OR merchant upload)
--   * source: 'openfoodfacts' | 'merchant' | 'seed' | 'manual'
--   * product edit rights: sellers may edit now; a single flip of
--     app_settings.product_edit_policy ('seller' → 'admin') moves ALL
--     create/update/delete rights to platform admins without new tables.
-- ============================================================

-- 1. Product identity/image columns ----------------------------------------
alter table products add column if not exists brand text;
alter table products add column if not exists description text;
alter table products add column if not exists image_url text;
alter table products add column if not exists source text not null default 'manual';
alter table products add column if not exists external_id text;   -- OFF barcode/`code`
alter table products add column if not exists attribution text;   -- ODbL duty
alter table products add column if not exists updated_by uuid references users(id) on delete set null;
alter table products add column if not exists updated_at timestamptz not null default now();

create index if not exists idx_products_source on products(source);
create index if not exists idx_products_barcode on products(barcode) where barcode is not null;

-- 2. Platform-admin flag (edit-rights policy enforcer) ----------------------
alter table users add column if not exists is_platform_admin boolean not null default false;

-- 3. Central edit-rights policy ---------------------------------------------
create table if not exists app_settings (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now(),
  updated_by uuid references users(id) on delete set null
);
insert into app_settings(key, value)
values ('product_edit_policy', '{"who": "seller", "note": "sellers may create/edit products; flip to admin to centralize rights"}'::jsonb)
on conflict (key) do nothing;

-- 4. RLS posture (013): no policies for client roles on new infra table
alter table app_settings enable row level security;
alter table app_settings force row level security;
