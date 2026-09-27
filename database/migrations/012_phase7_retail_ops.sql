-- ============================================================
-- KiranaSaathi AI — 012 Phase 7: retail operations hardening
-- Product barcodes (multi), packaging UOM conversions,
-- partial receiving, movement ledger extension, adjustments,
-- cycle counts, sale returns, idempotency keys.
-- Supabase PostgreSQL remains the single source of truth.
-- ============================================================

-- ---------- PRODUCT BARCODES (multi-barcode mapping) ----------
create table if not exists product_barcodes (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  product_id uuid not null references products(id) on delete cascade,
  barcode text not null,
  barcode_type text not null default 'EAN'
    check (barcode_type in ('EAN','UPC','GTIN','ITF14','GS1_128','GS1_DATAMATRIX','QR','INTERNAL')),
  -- packaging level this code identifies: each / pack / case (quantity represented)
  packaging_level text not null default 'EACH' check (packaging_level in ('EACH','PACK','CASE')),
  quantity_represented integer not null default 1 check (quantity_represented > 0),
  is_primary boolean not null default false,
  active boolean not null default true,
  source text not null default 'merchant' check (source in ('merchant','external_lookup')),
  created_at timestamptz not null default now(),
  unique (store_id, barcode)
);

create index if not exists idx_product_barcodes_product on product_barcodes(product_id);
create index if not exists idx_product_barcodes_barcode on product_barcodes(barcode);

-- ---------- PACKAGING / UOM ----------
create table if not exists product_packaging (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  product_id uuid not null references products(id) on delete cascade,
  level text not null check (level in ('EACH','PACK','BOX','CASE','CARTON')),
  label text not null,
  conversion_factor integer not null check (conversion_factor > 0), -- units of EACH per this level
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (store_id, product_id, level)
);

-- Deterministic server-side conversion (never client math)
create or replace function convert_to_units(
  p_store_id uuid, p_product_id uuid, p_level text, p_qty integer
) returns integer
language plpgsql
stable
as $$
declare
  v_factor integer;
begin
  if p_level = 'EACH' then
    return p_qty;
  end if;
  select conversion_factor into v_factor
  from product_packaging
  where store_id = p_store_id and product_id = p_product_id
    and level = p_level and active;
  if v_factor is null then
    raise exception 'No packaging conversion configured for level %', p_level;
  end if;
  return p_qty * v_factor;
end;
$$;

-- ---------- PURCHASE ORDERS: partial receiving ----------
alter table purchase_orders
  add column if not exists status text default 'pending';

-- widen the status check to include PARTIALLY_RECEIVED (drop old implicit check by recreating constraint)
alter table purchase_orders drop constraint if exists purchase_orders_status_check;
alter table purchase_orders add constraint purchase_orders_status_check
  check (status in ('pending','partially_received','received','cancelled'));

-- per-line received tracking
alter table purchase_items
  add column if not exists quantity_received integer not null default 0
    check (quantity_received >= 0),
  add column if not exists quantity_damaged integer not null default 0
    check (quantity_damaged >= 0),
  add column if not exists quantity_rejected integer not null default 0
    check (quantity_rejected >= 0);

-- batches: mfd + shelf life + po/supplier lineage
alter table inventory_batches
  add column if not exists manufacturing_date date,
  add column if not exists shelf_life_value integer,
  add column if not exists shelf_life_unit text
    check (shelf_life_unit in ('day','week','month','year')),
  add column if not exists expiry_source text
    check (expiry_source in ('explicit','mfd_shelf_life','default_shelf_life','none')),
  add column if not exists purchase_order_id uuid references purchase_orders(id) on delete set null,
  add column if not exists supplier_id uuid references suppliers(id) on delete set null,
  add column if not exists source text not null default 'purchase'
    check (source in ('purchase','return','adjustment','opening'));

-- ---------- MOVEMENT LEDGER: widen reasons + batch link ----------
alter table inventory_movements
  add column if not exists batch_id uuid references inventory_batches(id) on delete set null,
  add column if not exists movement_type text
    check (movement_type in (
      'RECEIPT','SALE','SALE_REVERSAL','RETURN','RETURN_TO_SUPPLIER','DAMAGE',
      'WASTAGE','EXPIRY','ADJUSTMENT_IN','ADJUSTMENT_OUT','TRANSFER_IN',
      'TRANSFER_OUT','RESERVATION','RELEASE'
    )),
  add column if not exists reference_type text;

create index if not exists idx_movements_product_time on inventory_movements(store_id, product_id, created_at desc);
create index if not exists idx_movements_type on inventory_movements(store_id, movement_type);

-- legacy rows: backfill movement_type from reason where obvious
update inventory_movements set movement_type = 'SALE' where reason = 'SALE' and movement_type is null;
update inventory_movements set movement_type = 'RECEIPT' where reason = 'PURCHASE_RECEIPT' and movement_type is null;

-- ---------- STOCK ADJUSTMENTS + CYCLE COUNTS ----------
create table if not exists stock_adjustments (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  product_id uuid not null references products(id) on delete restrict,
  batch_id uuid references inventory_batches(id) on delete set null,
  change integer not null check (change <> 0),
  reason text not null check (reason in ('DAMAGE','WASTAGE','SHRINKAGE','COUNTING_ERROR','EXPIRED','RETURN_TO_SUPPLIER','OTHER')),
  note text,
  counted_quantity integer,
  system_quantity integer,
  cycle_count_id uuid,
  user_id uuid references users(id) on delete set null,
  created_at timestamptz not null default now()
);

create table if not exists cycle_counts (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  status text not null default 'OPEN' check (status in ('OPEN','COMPLETED','CANCELLED')),
  scope_note text,
  created_by uuid references users(id) on delete set null,
  completed_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists cycle_count_lines (
  id uuid primary key default gen_random_uuid(),
  cycle_count_id uuid not null references cycle_counts(id) on delete cascade,
  product_id uuid not null references products(id) on delete restrict,
  expected_quantity integer not null default 0,
  counted_quantity integer not null default 0,
  variance integer not null default 0,
  reason text check (reason in ('DAMAGE','WASTAGE','SHRINKAGE','COUNTING_ERROR','EXPIRED','OTHER')),
  applied boolean not null default false,
  unique (cycle_count_id, product_id)
);

-- ---------- SALE RETURNS ----------
create table if not exists sale_returns (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  sale_id uuid not null references sales(id) on delete restrict,
  product_id uuid not null references products(id) on delete restrict,
  quantity integer not null check (quantity > 0),
  classification text not null check (classification in ('RESALEABLE','DAMAGED','EXPIRED','OTHER')),
  restock boolean not null default false, -- only RESALEABLE goes back to a batch
  batch_id uuid references inventory_batches(id) on delete set null,
  refund_amount numeric(12,2) not null default 0 check (refund_amount >= 0),
  refund_state text not null default 'NOT_REFUNDED'
    check (refund_state in ('NOT_REFUNDED','REFUND_PENDING','REFUNDED','REFUND_FAILED')),
  reason text,
  user_id uuid references users(id) on delete set null,
  created_at timestamptz not null default now()
);

create index if not exists idx_sale_returns_sale on sale_returns(sale_id);

-- ---------- IDEMPOTENCY KEYS (critical mutations) ----------
create table if not exists idempotency_keys (
  store_id uuid not null references stores(id) on delete cascade,
  key text not null,
  endpoint text not null,
  result jsonb not null,
  status_code int not null default 200,
  created_at timestamptz not null default now(),
  primary key (store_id, key, endpoint)
);

-- ---------- EXPIRY CALCULATION (deterministic, never AI math) ----------
create or replace function calculate_expiry(
  p_expiry_date date,
  p_manufacturing_date date,
  p_shelf_life_value integer,
  p_shelf_life_unit text
) returns date
language plpgsql
immutable
as $$
declare
  v_expiry date;
begin
  if p_expiry_date is not null then
    return p_expiry_date;
  end if;
  if p_manufacturing_date is null or p_shelf_life_value is null or p_shelf_life_unit is null then
    return null;
  end if;
  v_expiry := case p_shelf_life_unit
    when 'day'   then p_manufacturing_date + p_shelf_life_value
    when 'week'  then p_manufacturing_date + (p_shelf_life_value * 7)
    when 'month' then p_manufacturing_date + (p_shelf_life_value || ' months')::interval
    when 'year'  then p_manufacturing_date + (p_shelf_life_value || ' years')::interval
    else null end;
  return v_expiry;
end;
$$;

-- ---------- RECEIVE PURCHASE v2: partial + case + multi-batch ----------
-- p_lines jsonb: [{product_id, units (EACH, post-conversion), unit_cost,
--                  batch_no, manufacturing_date, expiry_date,
--                  shelf_life_value, shelf_life_unit, damaged, rejected}]
create or replace function receive_purchase_v2(
  p_store_id uuid,
  p_purchase_order_id uuid,
  p_lines jsonb,
  p_user_id uuid
)
returns jsonb
language plpgsql
as $$
declare
  v_po record;
  v_line jsonb;
  v_product_id uuid;
  v_units integer;
  v_unit_cost numeric;
  v_damaged integer;
  v_rejected integer;
  v_order_qty integer;
  v_already integer;
  v_batch_id uuid;
  v_expiry date;
  v_inv_after integer;
  v_received_total integer := 0;
  v_ordered_total integer := 0;
  v_batches int := 0;
  v_result jsonb;
begin
  select * into v_po from purchase_orders
  where id = p_purchase_order_id and store_id = p_store_id
  for update;

  if not found then
    raise exception 'Purchase order not found';
  end if;
  if v_po.status in ('received', 'cancelled') then
    raise exception 'Purchase order is %; cannot receive', v_po.status;
  end if;
  if p_lines is null or jsonb_array_length(p_lines) = 0 then
    raise exception 'No receipt lines supplied';
  end if;

  for v_line in select * from jsonb_array_elements(p_lines)
  loop
    v_product_id := (v_line->>'product_id')::uuid;
    v_units := coalesce((v_line->>'units')::int, 0);          -- EACH units, converted server-side before call
    v_unit_cost := coalesce((v_line->>'unit_cost')::numeric, 0);
    v_damaged := coalesce((v_line->>'damaged')::int, 0);
    v_rejected := coalesce((v_line->>'rejected')::int, 0);

    if v_product_id is null then
      raise exception 'Receipt line missing product_id';
    end if;
    -- line must belong to this PO
    select quantity, quantity_received into v_order_qty, v_already
    from purchase_items
    where purchase_order_id = p_purchase_order_id and product_id = v_product_id;
    if not found then
      raise exception 'Product % is not on this purchase order', v_product_id;
    end if;
    if v_units + v_damaged + v_rejected > v_order_qty - v_already then
      raise exception 'Received % exceeds remaining % for product %',
        v_units + v_damaged + v_rejected, v_order_qty - v_already, v_product_id;
    end if;
    if v_units < 0 or v_damaged < 0 or v_rejected < 0 then
      raise exception 'Negative receipt quantity';
    end if;

    -- batch (each receipt line = one batch; multiple lines allow multi-batch)
    if v_units + v_damaged > 0 then
      v_expiry := calculate_expiry(
        nullif(v_line->>'expiry_date','')::date,
        nullif(v_line->>'manufacturing_date','')::date,
        nullif(v_line->>'shelf_life_value','')::int,
        nullif(v_line->>'shelf_life_unit','')
      );
      insert into inventory_batches
        (store_id, product_id, batch_no, quantity, purchase_cost, expiry_date,
         manufacturing_date, shelf_life_value, shelf_life_unit, expiry_source,
         purchase_order_id, supplier_id, source)
      values
        (p_store_id, v_product_id, nullif(v_line->>'batch_no',''),
         v_units + v_damaged, v_unit_cost, v_expiry,
         nullif(v_line->>'manufacturing_date','')::date,
         nullif(v_line->>'shelf_life_value','')::int,
         nullif(v_line->>'shelf_life_unit',''),
         case when nullif(v_line->>'expiry_date','') is not null then 'explicit'
              when nullif(v_line->>'manufacturing_date','') is not null then 'mfd_shelf_life'
              else 'none' end,
         p_purchase_order_id, v_po.supplier_id, 'purchase')
      returning id into v_batch_id;
      v_batches := v_batches + 1;

      -- damaged units live in the batch but are immediately quarantined, never sellable
      if v_damaged > 0 then
        update inventory_batches set status = 'quarantined' where id = v_batch_id;
      end if;
    end if;

    -- inventory increases by GOOD units only
    if v_units > 0 then
      update inventory
      set quantity = quantity + v_units, updated_at = now()
      where store_id = p_store_id and product_id = v_product_id
      returning quantity into v_inv_after;

      if not found then
        insert into inventory (store_id, product_id, quantity)
        values (p_store_id, v_product_id, v_units)
        returning quantity into v_inv_after;
      end if;

      insert into inventory_movements
        (store_id, product_id, batch_id, change, quantity_after, reason, movement_type, reference_type, reference_id)
      values
        (p_store_id, v_product_id, v_batch_id, v_units, v_inv_after, 'PURCHASE_RECEIPT', 'RECEIPT', 'purchase_order', p_purchase_order_id);
    end if;

    update purchase_items
    set quantity_received = quantity_received + v_units,
        quantity_damaged = quantity_damaged + v_damaged,
        quantity_rejected = quantity_rejected + v_rejected
    where purchase_order_id = p_purchase_order_id and product_id = v_product_id;

    v_received_total := v_received_total + v_units + v_damaged + v_rejected;
  end loop;

  select coalesce(sum(quantity), 0) into v_ordered_total
  from purchase_items where purchase_order_id = p_purchase_order_id;

  -- PO status from actual received quantities (never auto-complete)
  if (select coalesce(sum(quantity_received + quantity_damaged + quantity_rejected), 0)
      from purchase_items where purchase_order_id = p_purchase_order_id) >= v_ordered_total then
    update purchase_orders set status = 'received', received_at = now()
    where id = p_purchase_order_id;
  else
    update purchase_orders set status = 'partially_received'
    where id = p_purchase_order_id;
  end if;

  insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
  values (p_store_id, p_user_id, 'INVENTORY_RECEIVED', 'purchase_order', p_purchase_order_id,
    jsonb_build_object('lines', jsonb_array_length(p_lines), 'units', v_received_total, 'batches', v_batches),
    'Shipment received: ' || v_received_total || ' units in ' || v_batches || ' batch(es)');

  v_result := jsonb_build_object(
    'purchase_order_id', p_purchase_order_id,
    'units_received', v_received_total,
    'batches_created', v_batches,
    'status', (select status from purchase_orders where id = p_purchase_order_id)
  );
  return v_result;
end;
$$;

grant execute on function receive_purchase_v2(uuid, uuid, jsonb, uuid) to postgres;
grant execute on function convert_to_units(uuid, uuid, text, integer) to postgres;
grant execute on function calculate_expiry(date, date, integer, text) to postgres;

-- ---------- BARCODE INDEX (fast scan lookups) ----------
create index if not exists idx_products_store_barcode on products(store_id, barcode) where barcode is not null;
create index if not exists idx_products_store_sku on products(store_id, sku) where sku is not null;
