-- KiranaSaathi AI — 001 Foundation schema
-- Applied automatically by scripts/migrate.py

create extension if not exists pgcrypto;

-- ========== USERS / MERCHANT / STORE ==========
create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  password_hash text not null,
  full_name text not null,
  created_at timestamptz not null default now()
);

create table if not exists merchants (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null unique references users(id) on delete cascade,
  name text not null,
  created_at timestamptz not null default now()
);

create table if not exists stores (
  id uuid primary key default gen_random_uuid(),
  merchant_id uuid not null references merchants(id) on delete cascade,
  name text not null,
  location text,
  currency text not null default 'INR',
  created_at timestamptz not null default now()
);

-- ========== PRODUCTS / INVENTORY ==========
create table if not exists products (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  name text not null,
  category text not null default 'Other',
  sku text,
  barcode text,
  unit text not null default 'pcs',
  mrp numeric(12,2) not null check (mrp >= 0),
  selling_price numeric(12,2) not null check (selling_price >= 0),
  purchase_price numeric(12,2) not null check (purchase_price >= 0),
  reorder_level integer not null default 10 check (reorder_level >= 0),
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

create unique index if not exists uq_products_store_sku
  on products(store_id, sku) where sku is not null;

create table if not exists inventory (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  product_id uuid not null references products(id) on delete cascade,
  quantity integer not null default 0 check (quantity >= 0),
  updated_at timestamptz not null default now(),
  unique (store_id, product_id)
);

create table if not exists inventory_batches (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  product_id uuid not null references products(id) on delete cascade,
  batch_no text,
  quantity integer not null check (quantity >= 0),
  purchase_cost numeric(12,2) not null check (purchase_cost >= 0),
  expiry_date date,
  received_at timestamptz not null default now()
);

create table if not exists inventory_movements (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  product_id uuid not null references products(id) on delete cascade,
  change integer not null,
  quantity_after integer not null check (quantity_after >= 0),
  reason text not null,
  reference_id uuid,
  created_at timestamptz not null default now()
);

-- ========== SUPPLIERS / PURCHASES ==========
create table if not exists suppliers (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  name text not null,
  phone text,
  address text,
  created_at timestamptz not null default now()
);

create table if not exists purchase_orders (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  supplier_id uuid references suppliers(id) on delete set null,
  total_amount numeric(12,2) not null default 0 check (total_amount >= 0),
  status text not null default 'pending'
    check (status in ('pending','received','cancelled')),
  created_at timestamptz not null default now()
);

create table if not exists purchase_items (
  id uuid primary key default gen_random_uuid(),
  purchase_order_id uuid not null references purchase_orders(id) on delete cascade,
  product_id uuid not null references products(id) on delete restrict,
  quantity integer not null check (quantity > 0),
  unit_cost numeric(12,2) not null check (unit_cost >= 0),
  line_total numeric(12,2) generated always as (quantity * unit_cost) stored
);

-- ========== CUSTOMERS / SALES ==========
create table if not exists customers (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  name text not null,
  phone text,
  created_at timestamptz not null default now()
);

create table if not exists sales (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  customer_id uuid references customers(id) on delete set null,
  subtotal numeric(12,2) not null check (subtotal >= 0),
  discount numeric(12,2) not null default 0 check (discount >= 0),
  total numeric(12,2) not null check (total >= 0),
  payment_method text not null default 'cash'
    check (payment_method in ('cash','upi','card','split','credit')),
  status text not null default 'completed'
    check (status in ('completed','voided','pending')),
  created_by uuid references users(id) on delete set null,
  created_at timestamptz not null default now(),
  check (discount <= subtotal),
  check (total = subtotal - discount)
);

create table if not exists sale_items (
  id uuid primary key default gen_random_uuid(),
  sale_id uuid not null references sales(id) on delete cascade,
  product_id uuid not null references products(id) on delete restrict,
  quantity integer not null check (quantity > 0),
  unit_price numeric(12,2) not null check (unit_price >= 0),
  unit_cost numeric(12,2) not null default 0 check (unit_cost >= 0),
  line_total numeric(12,2) generated always as (quantity * unit_price) stored
);

-- ========== ALERTS / AI / AUDIT ==========
create table if not exists alerts (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  type text not null,
  severity text not null default 'info'
    check (severity in ('info','warning','critical','opportunity')),
  title text not null,
  description text,
  status text not null default 'open'
    check (status in ('open','acknowledged','resolved')),
  reference_id uuid,
  created_at timestamptz not null default now()
);

create table if not exists ai_recommendations (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  type text not null,
  title text not null,
  description text not null,
  evidence jsonb not null default '{}'::jsonb,
  severity text not null default 'info'
    check (severity in ('info','warning','critical','opportunity')),
  confidence numeric(4,3) check (confidence is null or (confidence >= 0 and confidence <= 1)),
  status text not null default 'NEW'
    check (status in ('NEW','REVIEWED','APPROVED','REJECTED','EXECUTED','COMPLETED','FAILED','DISMISSED')),
  proposed_action text,
  outcome text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists activity_logs (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  user_id uuid references users(id) on delete set null,
  event_type text not null,
  entity_type text,
  entity_id uuid,
  previous_state jsonb,
  new_state jsonb,
  message text,
  created_at timestamptz not null default now()
);

-- Indexes
create index if not exists idx_products_store on products(store_id);
create index if not exists idx_inventory_store on inventory(store_id);
create index if not exists idx_sales_store_created on sales(store_id, created_at desc);
create index if not exists idx_sale_items_sale on sale_items(sale_id);
create index if not exists idx_activity_store on activity_logs(store_id, created_at desc);
create index if not exists idx_alerts_store on alerts(store_id, created_at desc);
create index if not exists idx_recommendations_store on ai_recommendations(store_id, created_at desc);
create index if not exists idx_batches_store on inventory_batches(store_id, expiry_date);
create index if not exists idx_customers_store on customers(store_id);
create index if not exists idx_movements_store on inventory_movements(store_id, created_at desc);

-- Migration bookkeeping
create table if not exists schema_migrations (
  name text primary key,
  applied_at timestamptz not null default now()
);
