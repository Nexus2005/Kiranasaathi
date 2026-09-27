-- ============================================================
-- KiranaSaathi AI — 011 Phase 6: Orders, Payments, Splitter, Refunds
-- Design rules:
--   * sales table REMAINS the source of truth for completed transactions.
--     An order is the pre-payment container; the sale is created ONLY on
--     confirmed payment (create_sale RPC re-used — no duplicate engine).
--   * order state and payment state are separate machines.
--   * webhook_events has a unique provider_event_id => idempotent processing.
--   * payments have unique (store_id, idempotency_key) => no duplicate charges.
--   * split sums are validated in SQL functions; order cannot complete while
--     any required split is unpaid.
-- ============================================================

-- ---------- ORDERS ----------
create table if not exists orders (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  customer_id uuid references customers(id) on delete set null,
  state text not null default 'DRAFT'
    check (state in (
      'DRAFT','PENDING_PAYMENT','PARTIALLY_PAID','PAID','PROCESSING','READY',
      'COMPLETED','CANCELLED','PAYMENT_FAILED','REFUND_PENDING','REFUNDED'
    )),
  -- Cart snapshot (product_id, quantity, optional unit_price negotiated)
  cart jsonb not null default '[]'::jsonb,
  cart_discount numeric(12,2) not null default 0 check (cart_discount >= 0),
  payment_method text not null default 'cash'
    check (payment_method in ('cash','upi','card','split','credit')),
  currency text not null default 'INR',
  -- Filled when the order completes via create_sale
  sale_id uuid references sales(id) on delete set null,
  total numeric(12,2) not null default 0 check (total >= 0),
  created_by uuid references users(id) on delete set null,
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_orders_store_state on orders(store_id, state, created_at desc);
create index if not exists idx_orders_store_recent on orders(store_id, created_at desc);

create table if not exists order_items (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references orders(id) on delete cascade,
  product_id uuid not null references products(id) on delete restrict,
  quantity integer not null check (quantity > 0),
  unit_price numeric(12,2) not null check (unit_price >= 0),
  line_total numeric(12,2) generated always as (quantity * unit_price) stored
);

create index if not exists idx_order_items_order on order_items(order_id);

-- ---------- PAYMENTS ----------
create table if not exists payments (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  order_id uuid not null references orders(id) on delete cascade,
  amount numeric(12,2) not null check (amount >= 0),
  currency text not null default 'INR',
  method text not null default 'cash'
    check (method in ('cash','upi','card','split','credit')),
  state text not null default 'CREATED'
    check (state in (
      'CREATED','PENDING','PARTIALLY_PAID','PAID','FAILED','CANCELLED',
      'EXPIRED','REFUND_PENDING','REFUNDED'
    )),
  provider text not null default 'manual',
  provider_payment_id text unique,
  -- client-supplied key to prevent duplicate payment creation on retry
  idempotency_key text,
  verified boolean not null default false,
  verified_at timestamptz,
  verified_amount numeric(12,2),
  error_note text,
  created_by uuid references users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- One active payment per intent: retry with the same key returns the same payment
create unique index if not exists uq_payments_store_idem
  on payments(store_id, idempotency_key)
  where idempotency_key is not null;

create index if not exists idx_payments_order on payments(order_id);
create index if not exists idx_payments_store_state on payments(store_id, state, created_at desc);

-- ---------- PAYMENT EVENTS (state machine audit + idempotent webhooks) ----------
create table if not exists payment_events (
  id uuid primary key default gen_random_uuid(),
  payment_id uuid references payments(id) on delete cascade,
  store_id uuid not null references stores(id) on delete cascade,
  event_type text not null,
  -- provider webhook dedup lives here; null for internal events
  provider_event_id text,
  payload jsonb,
  created_at timestamptz not null default now()
);

-- Webhook idempotency: the same provider event can only be applied once per store
create unique index if not exists uq_payment_events_provider
  on payment_events(store_id, provider_event_id)
  where provider_event_id is not null;

create index if not exists idx_payment_events_payment on payment_events(payment_id);

-- ---------- SPLIT PAYMENTS ----------
create table if not exists payment_split_groups (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  order_id uuid not null references orders(id) on delete cascade,
  total_amount numeric(12,2) not null check (total_amount >= 0),
  paid_amount numeric(12,2) not null default 0 check (paid_amount >= 0),
  mode text not null default 'EQUAL' check (mode in ('EQUAL','CUSTOM')),
  status text not null default 'OPEN'
    check (status in ('OPEN','COMPLETED','CANCELLED','EXPIRED')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (order_id)
);

create table if not exists payment_splits (
  id uuid primary key default gen_random_uuid(),
  group_id uuid not null references payment_split_groups(id) on delete cascade,
  store_id uuid not null references stores(id) on delete cascade,
  payer_label text not null,
  amount numeric(12,2) not null check (amount > 0),
  state text not null default 'PENDING'
    check (state in ('PENDING','PROCESSING','PAID','FAILED','CANCELLED')),
  payment_id uuid references payments(id) on delete set null,
  position integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_payment_splits_group on payment_splits(group_id);

-- ---------- REFUNDS ----------
create table if not exists refunds (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  payment_id uuid not null references payments(id) on delete restrict,
  order_id uuid references orders(id) on delete set null,
  amount numeric(12,2) not null check (amount > 0),
  reason text,
  state text not null default 'REFUND_PENDING'
    check (state in ('REFUND_PENDING','REFUND_PROCESSING','REFUNDED','REFUND_FAILED')),
  provider text,
  provider_refund_id text unique,
  created_by uuid references users(id) on delete set null,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

create index if not exists idx_refunds_store on refunds(store_id, created_at desc);

-- ---------- ORDER EVENTS (transaction audit trail) ----------
create table if not exists order_events (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  order_id uuid not null references orders(id) on delete cascade,
  event_type text not null,
  actor text not null default 'merchant',
  payload jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_order_events_order on order_events(order_id, created_at);

-- ---------- HELPERS ----------
-- Sellable stock for a product in a store (matches create_sale v2 semantics:
-- inventory minus expired batches). Shared by checkout validation.
create or replace function sellable_stock(p_store_id uuid, p_product_id uuid)
returns integer
language sql
stable
as $$
  select coalesce(max(i.quantity), 0)::int
         - coalesce((select sum(b.quantity) from inventory_batches b
                     where b.store_id = p_store_id
                       and b.product_id = p_product_id
                       and b.status = 'expired' and b.quantity > 0), 0)::int
  from inventory i
  where i.store_id = p_store_id and i.product_id = p_product_id
    and i.quantity = (select max(i2.quantity) from inventory i2
                      where i2.store_id = p_store_id and i2.product_id = p_product_id)
  having count(*) > 0 or false
$$;

-- Simpler + correct: keep it deterministic
create or replace function sellable_stock(p_store_id uuid, p_product_id uuid)
returns integer
language plpgsql
stable
as $$
declare
  v_qty integer;
  v_expired integer;
begin
  select coalesce(i.quantity, 0) into v_qty
  from inventory i
  where i.store_id = p_store_id and i.product_id = p_product_id;
  if v_qty is null then
    return 0;
  end if;
  select coalesce(sum(b.quantity), 0) into v_expired
  from inventory_batches b
  where b.store_id = p_store_id and b.product_id = p_product_id
    and b.status = 'expired' and b.quantity > 0;
  return v_qty - v_expired;
end;
$$;

-- ---------- TRIGGER: order.updated_at ----------
create or replace function touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists trg_orders_touch on orders;
create trigger trg_orders_touch before update on orders
for each row execute function touch_updated_at();

drop trigger if exists trg_payments_touch on payments;
create trigger trg_payments_touch before update on payments
for each row execute function touch_updated_at();

drop trigger if exists trg_split_groups_touch on payment_split_groups;
create trigger trg_split_groups_touch before update on payment_split_groups
for each row execute function touch_updated_at();

drop trigger if exists trg_splits_touch on payment_splits;
create trigger trg_splits_touch before update on payment_splits
for each row execute function touch_updated_at();

grant execute on function sellable_stock(uuid, uuid) to postgres;
