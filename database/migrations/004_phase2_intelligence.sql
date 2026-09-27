-- KiranaSaathi AI — 004 Phase 2: Inventory Intelligence + Pricing + Procurement
-- Adds: store_settings (configurable thresholds), product_price_history,
-- batch lifecycle, FEFO depletion, min_acceptable_price policy,
-- receive_purchase RPC, refresh_inventory_intelligence, alert dedup.

-- ========== SCHEMA EXTENSIONS ==========
alter table products
  add column if not exists brand text,
  add column if not exists target_stock integer;

alter table purchase_orders
  add column if not exists invoice_no text,
  add column if not exists notes text,
  add column if not exists received_at timestamptz,
  add column if not exists expected_delivery_date date,
  add column if not exists created_by uuid references users(id) on delete set null;

alter table purchase_items
  add column if not exists expiry_date date,
  add column if not exists batch_no text;

alter table suppliers
  add column if not exists categories text,
  add column if not exists lead_time_days integer,
  add column if not exists min_order_value numeric(12,2),
  add column if not exists payment_terms text,
  add column if not exists is_active boolean not null default true;

alter table inventory_batches
  add column if not exists status text not null default 'sellable'
    check (status in ('sellable', 'expired', 'quarantined'));

-- Centralized, merchant-configurable thresholds (single source of truth)
create table if not exists store_settings (
  store_id uuid primary key references stores(id) on delete cascade,
  min_margin_pct numeric(5,2) not null default 10.00
    check (min_margin_pct >= 0 and min_margin_pct <= 95),
  expiry_warning_days integer not null default 14 check (expiry_warning_days >= 1),
  expiry_critical_days integer not null default 7 check (expiry_critical_days >= 1),
  low_stock_days integer not null default 3 check (low_stock_days >= 1),
  reorder_lead_time_days integer not null default 3 check (reorder_lead_time_days >= 0),
  reorder_safety_days integer not null default 3 check (reorder_safety_days >= 0),
  slow_moving_days integer not null default 45 check (slow_moving_days >= 1),
  overstock_days integer not null default 90 check (overstock_days >= 1),
  dead_stock_days integer not null default 180 check (dead_stock_days >= 1),
  velocity_window_days integer not null default 30 check (velocity_window_days >= 7),
  updated_at timestamptz not null default now()
);

insert into store_settings (store_id)
select s.id from stores s
where not exists (select 1 from store_settings ss where ss.store_id = s.id);

-- Preserve cost + selling price history (never overwrite historical pricing)
create table if not exists product_price_history (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  product_id uuid not null references products(id) on delete cascade,
  field text not null check (field in ('selling_price', 'purchase_price')),
  old_value numeric(12,2),
  new_value numeric(12,2) not null,
  reason text,
  changed_by uuid references users(id) on delete set null,
  created_at timestamptz not null default now()
);

-- Alert dedup: one open alert per (store, type, entity).
-- Phase 1 could emit duplicate open LOW_STOCK alerts; close all but the newest
-- before creating the enforcing index.
with ranked as (
  select id,
         row_number() over (
           partition by store_id, type, reference_id
           order by created_at desc, id desc
         ) as rn
  from alerts
  where status = 'open'
)
update alerts a set status = 'resolved'
from ranked r
where a.id = r.id and r.rn > 1;

create unique index if not exists uq_alerts_open_dedup
  on alerts (store_id, type, reference_id)
  where status = 'open';

create index if not exists idx_price_history_product
  on product_price_history (product_id, created_at desc);
create index if not exists idx_batches_product
  on inventory_batches (product_id, expiry_date);
create index if not exists idx_purchase_items_product
  on purchase_items (product_id);
create index if not exists idx_po_supplier on purchase_orders (supplier_id, created_at desc);

-- ========== FUNCTIONS ==========

-- Average daily sales over a window (0 when no history — callers treat as
-- "insufficient data" rather than fabricating demand)
create or replace function product_velocity(
  p_store_id uuid, p_product_id uuid, p_days integer
) returns numeric
language sql stable as $$
  select coalesce(
    (select sum(si.quantity)::numeric / greatest(p_days, 1)
     from sale_items si
     join sales s on s.id = si.sale_id
     where s.store_id = p_store_id
       and si.product_id = p_product_id
       and s.status = 'completed'
       and s.created_at >= current_date - greatest(p_days, 1)),
    0
  );
$$;

-- Stock status from quantity + velocity + configurable thresholds
create or replace function product_stock_status(
  p_quantity integer,
  p_reorder_level integer,
  p_velocity numeric,
  p_slow_days integer,
  p_overstock_days integer,
  p_dead_days integer
) returns text
language plpgsql immutable as $$
declare
  v_days_cover numeric;
begin
  if p_quantity is null or p_quantity <= 0 then
    return 'OUT_OF_STOCK';
  end if;
  if p_quantity <= greatest(coalesce(p_reorder_level, 0), 0) / 2 then
    return 'CRITICAL_STOCK';
  end if;
  if p_quantity <= greatest(coalesce(p_reorder_level, 0), 0) then
    return 'LOW_STOCK';
  end if;
  if p_velocity is null or p_velocity <= 0 then
    return 'HEALTHY'; -- cannot estimate days cover without demand history
  end if;
  v_days_cover := p_quantity / p_velocity;
  if v_days_cover > p_dead_days then
    return 'DEAD_STOCK';
  end if;
  if v_days_cover > p_overstock_days then
    return 'OVERSTOCKED';
  end if;
  if v_days_cover > p_slow_days then
    return 'SLOW_MOVING';
  end if;
  return 'HEALTHY';
end;
$$;

-- Days of sellable stock remaining at current velocity (null when unknown)
create or replace function days_of_stock(
  p_quantity integer, p_velocity numeric
) returns numeric
language sql immutable as $$
  select case
    when p_velocity is null or p_velocity <= 0 then null
    else round((p_quantity / p_velocity)::numeric, 1)
  end;
$$;

-- Minimum acceptable price per merchant pricing policy.
-- Policy: sellable price must yield at least min_margin_pct margin on price.
-- Clearance exception: when a batch expires inside the warning window and
-- expected sell-through (velocity x days left) is below batch quantity,
-- breakeven (cost) is allowed so the merchant can rescue the stock.
create or replace function min_acceptable_price(
  p_store_id uuid, p_product_id uuid
) returns numeric
language plpgsql stable as $$
declare
  v_cost numeric;
  v_min_margin numeric;
  v_warn_days integer;
  v_velocity numeric;
  v_batch record;
  v_days_left integer;
  v_expected numeric;
begin
  select purchase_price into v_cost
  from products
  where id = p_product_id and store_id = p_store_id;
  if v_cost is null then
    raise exception 'Product not found';
  end if;

  select coalesce(min_margin_pct, 10), coalesce(expiry_warning_days, 14)
    into v_min_margin, v_warn_days
  from store_settings where store_id = p_store_id;

  v_velocity := product_velocity(p_store_id, p_product_id, 30);

  for v_batch in
    select b.quantity, b.expiry_date
    from inventory_batches b
    where b.store_id = p_store_id
      and b.product_id = p_product_id
      and b.status = 'sellable'
      and b.quantity > 0
      and b.expiry_date is not null
      and b.expiry_date <= current_date + v_warn_days
  loop
    v_days_left := greatest(v_batch.expiry_date - current_date, 0);
    v_expected := greatest(coalesce(v_velocity, 0), 0) * v_days_left;
    if v_batch.quantity > v_expected then
      -- near-expiry excess: allow breakeven clearance pricing
      return floor(v_cost);
    end if;
  end loop;

  if v_min_margin >= 100 then
    return v_cost * 100; -- degenerate guard
  end if;
  return ceil(v_cost / (1 - v_min_margin / 100.0));
end;
$$;

-- FEFO batch depletion. Consumes sellable, non-expired batches
-- (first expiry first out). Returns covered quantity + actual cost so the
-- caller can value the remainder at the product's current purchase price.
create or replace function deplete_batches(
  p_store_id uuid, p_product_id uuid, p_qty integer
) returns jsonb
language plpgsql as $$
declare
  v_remaining integer := p_qty;
  v_covered integer := 0;
  v_cost numeric := 0;
  v_take integer;
  b record;
begin
  if v_remaining is null or v_remaining <= 0 then
    return jsonb_build_object('covered', 0, 'cost', 0);
  end if;

  for b in
    select id, quantity, purchase_cost
    from inventory_batches
    where store_id = p_store_id
      and product_id = p_product_id
      and status = 'sellable'
      and quantity > 0
      and (expiry_date is null or expiry_date >= current_date)
    order by expiry_date nulls last, received_at, id
    for update
  loop
    exit when v_remaining <= 0;
    v_take := least(b.quantity, v_remaining);
    update inventory_batches set quantity = quantity - v_take where id = b.id;
    v_cost := v_cost + v_take * b.purchase_cost;
    v_covered := v_covered + v_take;
    v_remaining := v_remaining - v_take;
  end loop;

  return jsonb_build_object('covered', v_covered, 'cost', v_cost);
end;
$$;

-- ---- Alert dedup helper: insert unless an open alert already exists ----
create or replace function ensure_alert(
  p_store_id uuid,
  p_type text,
  p_severity text,
  p_title text,
  p_description text,
  p_reference uuid
) returns boolean
language plpgsql as $$
declare
  v_id uuid;
  v_ref uuid := coalesce(p_reference, p_store_id);
begin
  insert into alerts (store_id, type, severity, title, description, reference_id)
  values (p_store_id, p_type, p_severity, p_title, p_description, v_ref)
  on conflict (store_id, type, reference_id) where status = 'open'
  do nothing
  returning id into v_id;
  return v_id is not null;
end;
$$;

-- ---- create_sale v2: per-item price floor enforcement + FEFO cost ----
create or replace function create_sale(
  p_store_id uuid,
  p_customer_id uuid,
  p_items jsonb,
  p_discount numeric,
  p_payment_method text,
  p_user_id uuid
)
returns jsonb
language plpgsql
as $$
declare
  v_sale_id uuid;
  v_subtotal numeric := 0;
  v_total numeric;
  item jsonb;
  v_product_id uuid;
  v_qty integer;
  v_price numeric;
  v_requested_price numeric;
  v_floor numeric;
  v_cost numeric;
  v_avail integer;
  v_expired_qty integer;
  v_dep jsonb;
  v_dep_cost numeric;
begin
  if p_items is null or jsonb_array_length(p_items) = 0 then
    raise exception 'Cart is empty';
  end if;
  if p_discount is null or p_discount < 0 then
    raise exception 'Invalid discount';
  end if;
  if p_payment_method not in ('cash','upi','card','split','credit') then
    raise exception 'Invalid payment method';
  end if;

  for item in select * from jsonb_array_elements(p_items)
  loop
    v_product_id := (item->>'product_id')::uuid;
    v_qty := (item->>'quantity')::int;
    v_requested_price := (item->>'unit_price');
    if v_requested_price is not null and v_requested_price <> '' then
      v_requested_price := v_requested_price::numeric;
    else
      v_requested_price := null;
    end if;
    if v_qty is null or v_qty <= 0 then
      raise exception 'Invalid quantity';
    end if;

    select coalesce(i.quantity, 0), p.selling_price, p.purchase_price
    into v_avail, v_price, v_cost
    from products p
    left join inventory i on i.product_id = p.id and i.store_id = p.store_id
    where p.id = v_product_id and p.store_id = p_store_id and p.is_active;

    if v_price is null then
      raise exception 'Product not found or inactive';
    end if;

    -- Expired batches are separated from sellable stock: they cannot be sold.
    select coalesce(sum(b.quantity), 0) into v_expired_qty
    from inventory_batches b
    where b.store_id = p_store_id and b.product_id = v_product_id
      and b.status = 'expired' and b.quantity > 0;
    v_avail := v_avail - v_expired_qty;

    if v_avail < v_qty then
      raise exception 'Insufficient stock (available %, requested %)', v_avail, v_qty;
    end if;

    -- Per-item bargain price: enforce pricing policy server-side
    if v_requested_price is not null then
      if v_requested_price > v_price then
        raise exception 'Offered price above selling price';
      end if;
      v_floor := min_acceptable_price(p_store_id, v_product_id);
      if v_requested_price < v_floor then
        raise exception 'Price below minimum acceptable (minimum ₹%)', v_floor;
      end if;
      v_price := v_requested_price;
    end if;

    v_subtotal := v_subtotal + (v_price * v_qty);
  end loop;

  if p_discount > v_subtotal then
    raise exception 'Discount cannot exceed subtotal';
  end if;

  v_total := v_subtotal - p_discount;

  insert into sales (store_id, customer_id, subtotal, discount, total, payment_method, status, created_by)
  values (p_store_id, p_customer_id, v_subtotal, p_discount, v_total, p_payment_method, 'completed', p_user_id)
  returning id into v_sale_id;

  for item in select * from jsonb_array_elements(p_items)
  loop
    v_product_id := (item->>'product_id')::uuid;
    v_qty := (item->>'quantity')::int;
    v_requested_price := (item->>'unit_price');
    if v_requested_price is not null and v_requested_price <> '' then
      v_requested_price := v_requested_price::numeric;
    else
      v_requested_price := null;
    end if;

    select p.selling_price, p.purchase_price into v_price, v_cost
    from products p where p.id = v_product_id;
    if v_requested_price is not null then
      v_price := v_requested_price;
    end if;

    insert into sale_items (sale_id, product_id, quantity, unit_price, unit_cost)
    values (v_sale_id, v_product_id, v_qty, v_price, 0);

    update inventory
    set quantity = quantity - v_qty,
        updated_at = now()
    where store_id = p_store_id and product_id = v_product_id
    returning quantity into v_avail;

    -- FEFO batch depletion -> actual unit cost for real profit accounting
    v_dep := deplete_batches(p_store_id, v_product_id, v_qty);
    v_dep_cost := (v_dep->>'cost')::numeric
      + (v_qty - (v_dep->>'covered')::int) * v_cost;
    update sale_items
    set unit_cost = round((v_dep_cost / v_qty)::numeric, 2)
    where sale_id = v_sale_id and product_id = v_product_id;

    insert into inventory_movements (store_id, product_id, change, quantity_after, reason, reference_id)
    values (p_store_id, v_product_id, -v_qty, v_avail, 'SALE', v_sale_id);
  end loop;

  insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
  values (
    p_store_id, p_user_id, 'SALE_CREATED', 'sale', v_sale_id,
    jsonb_build_object(
      'total', v_total,
      'discount', p_discount,
      'payment_method', p_payment_method,
      'customer_id', p_customer_id,
      'item_count', jsonb_array_length(p_items)
    ),
    'Sale completed: ₹' || v_total::text
  );

  perform refresh_inventory_intelligence(p_store_id);

  return jsonb_build_object(
    'sale_id', v_sale_id,
    'subtotal', v_subtotal,
    'discount', p_discount,
    'total', v_total
  );
end;
$$;

-- ---- receive_purchase: PO -> batches + inventory + cost history, atomic ----
create or replace function receive_purchase(
  p_purchase_order_id uuid,
  p_user_id uuid
)
returns jsonb
language plpgsql
as $$
declare
  v_po record;
  v_line record;
  v_product_id uuid;
  v_qty integer;
  v_cost numeric;
  v_new_inv integer;
  v_old_cost numeric;
  v_batch_no text;
  v_total numeric := 0;
  v_lines integer := 0;
  v_cost_changes integer := 0;
begin
  select * into v_po from purchase_orders
  where id = p_purchase_order_id
  for update;

  if not found then
    raise exception 'Purchase order not found';
  end if;
  if v_po.status = 'received' then
    raise exception 'Purchase order already received';
  end if;
  if v_po.status = 'cancelled' then
    raise exception 'Purchase order is cancelled';
  end if;

  for v_line in
    select pi.product_id, pi.quantity, pi.unit_cost, pi.expiry_date, pi.batch_no, p.name
    from purchase_items pi
    join products p on p.id = pi.product_id
    where pi.purchase_order_id = p_purchase_order_id
    order by p.name
  loop
    v_product_id := v_line.product_id;
    v_qty := v_line.quantity;
    v_cost := v_line.unit_cost;
    v_batch_no := coalesce(nullif(v_line.batch_no, ''),
                           'B-' || left(v_po.id::text, 8) || '-' || v_lines::text);
    v_lines := v_lines + 1;

    insert into inventory_batches
      (store_id, product_id, batch_no, quantity, purchase_cost, expiry_date, received_at)
    values
      (v_po.store_id, v_product_id, v_batch_no, v_qty, v_cost, v_line.expiry_date, now());

    insert into inventory (store_id, product_id, quantity)
    values (v_po.store_id, v_product_id, v_qty)
    on conflict (store_id, product_id)
    do update set quantity = inventory.quantity + excluded.quantity, updated_at = now()
    returning quantity into v_new_inv;

    insert into inventory_movements (store_id, product_id, change, quantity_after, reason, reference_id)
    values (v_po.store_id, v_product_id, v_qty, v_new_inv, 'PURCHASE_RECEIVED', v_po.id);

    -- Cost history: never overwrite historical purchase costs silently
    select purchase_price into v_old_cost from products where id = v_product_id;
    if v_old_cost is distinct from v_cost then
      insert into product_price_history
        (store_id, product_id, field, old_value, new_value, reason, changed_by)
      values
        (v_po.store_id, v_product_id, 'purchase_price', v_old_cost, v_cost,
         'Purchase received' || coalesce(' (' || nullif(v_po.invoice_no, '') || ')', ''), p_user_id);
      update products set purchase_price = v_cost where id = v_product_id;
      insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
      values (v_po.store_id, p_user_id, 'SUPPLIER_PRICE_CHANGED', 'product', v_product_id,
              jsonb_build_object('old_cost', v_old_cost, 'new_cost', v_cost),
              'Purchase cost updated for ' || v_line.name || ': ₹' || v_old_cost::text || ' → ₹' || v_cost::text);
      v_cost_changes := v_cost_changes + 1;
    end if;

    v_total := v_total + v_qty * v_cost;
  end loop;

  update purchase_orders
  set status = 'received', received_at = now(), total_amount = v_total
  where id = v_po.id;

  insert into activity_logs (store_id, user_id, event_type, entity_type, entity_id, new_state, message)
  values (v_po.store_id, p_user_id, 'PURCHASE_RECEIVED', 'purchase_order', v_po.id,
          jsonb_build_object('lines', v_lines, 'total_amount', v_total, 'cost_changes', v_cost_changes),
          'Purchase received: ' || v_lines || ' product(s), ₹' || v_total::text);

  insert into activity_logs (store_id, user_id, event_type, entity_type, message)
  values (v_po.store_id, p_user_id, 'INVENTORY_INCREASED', 'inventory',
          'Stock increased from purchase: ' || v_lines || ' product line(s)');

  perform refresh_inventory_intelligence(v_po.store_id);

  return jsonb_build_object(
    'purchase_order_id', v_po.id,
    'status', 'received',
    'lines', v_lines,
    'cost_changes', v_cost_changes,
    'total_amount', v_total
  );
end;
$$;

-- ---- refresh_inventory_intelligence: state-based, deduped alerts ----
-- Recomputes stock statuses + expiry risk for every active product and
-- creates/resolves alerts idempotently. Safe to call after any mutation.
create or replace function refresh_inventory_intelligence(p_store_id uuid)
returns jsonb
language plpgsql
as $$
declare
  v_cfg store_settings;
  v_row record;
  v_velocity numeric;
  v_status text;
  v_created integer := 0;
  v_resolved integer := 0;
  v_expired integer := 0;
  v_warn_batch record;
  v_days_left integer;
  v_expected numeric;
  v_excess numeric;
  v_reorder_point numeric;
  v_has_pending_po boolean;
  v_ok boolean;
begin
  select * into v_cfg from store_settings where store_id = p_store_id;
  if not found then
    insert into store_settings (store_id) values (p_store_id)
    returning * into v_cfg;
  end if;

  -- 1) Expire batches whose date has passed (one-time state transition)
  with expired as (
    update inventory_batches
    set status = 'expired'
    where store_id = p_store_id
      and status = 'sellable'
      and expiry_date is not null
      and expiry_date < current_date
    returning quantity
  )
  select count(*), coalesce(sum(quantity), 0)
  into v_expired, v_expired
  from expired;

  if v_expired > 0 then
    insert into activity_logs (store_id, event_type, entity_type, message)
    values (p_store_id, 'PRODUCT_EXPIRED', 'inventory_batch',
            v_expired || ' batch(es) marked expired and separated from sellable stock');
    v_ok := ensure_alert(p_store_id, 'EXPIRED_STOCK', 'critical',
      'Expired stock in store',
      v_expired || ' batch(es) have expired and are excluded from sellable stock. Remove and write them off.',
      p_store_id);
    if v_ok then v_created := v_created + 1; end if;
  end if;

  -- 2) Per-product intelligence
  for v_row in
    select p.id as product_id, p.name, p.reorder_level, p.target_stock,
           coalesce(i.quantity, 0) as qty
    from products p
    left join inventory i on i.product_id = p.id and i.store_id = p.store_id
    where p.store_id = p_store_id and p.is_active
  loop
    v_velocity := product_velocity(p_store_id, v_row.product_id, v_cfg.velocity_window_days);
    v_status := product_stock_status(
      v_row.qty, v_row.reorder_level, v_velocity,
      v_cfg.slow_moving_days, v_cfg.overstock_days, v_cfg.dead_stock_days);

    -- Stock-state alerts (deduped)
    if v_status = 'OUT_OF_STOCK' then
      v_ok := ensure_alert(p_store_id, 'OUT_OF_STOCK', 'critical',
        'Out of stock: ' || v_row.name,
        'No sellable units left. Reorder level is ' || v_row.reorder_level || '.',
        v_row.product_id);
      if v_ok then v_created := v_created + 1; end if;
    elsif v_status = 'CRITICAL_STOCK' then
      v_ok := ensure_alert(p_store_id, 'CRITICAL_STOCK', 'critical',
        'Critical stock: ' || v_row.name,
        'Only ' || v_row.qty || ' left — below half the reorder level (' || v_row.reorder_level || ').',
        v_row.product_id);
      if v_ok then v_created := v_created + 1; end if;
    elsif v_status = 'LOW_STOCK' then
      v_ok := ensure_alert(p_store_id, 'LOW_STOCK', 'warning',
        'Low stock: ' || v_row.name,
        v_row.qty || ' units left (reorder level ' || v_row.reorder_level || ').',
        v_row.product_id);
      if v_ok then v_created := v_created + 1; end if;
    elsif v_status = 'OVERSTOCKED' then
      v_ok := ensure_alert(p_store_id, 'OVERSTOCK', 'info',
        'Overstocked: ' || v_row.name,
        'About ' || round(days_of_stock(v_row.qty, v_velocity)) || ' days of stock at current sales pace.',
        v_row.product_id);
      if v_ok then v_created := v_created + 1; end if;
    elsif v_status = 'DEAD_STOCK' then
      v_ok := ensure_alert(p_store_id, 'DEAD_STOCK', 'warning',
        'Dead stock: ' || v_row.name,
        'No significant sales recently while holding ' || v_row.qty || ' units. Money is trapped in this stock.',
        v_row.product_id);
      if v_ok then v_created := v_created + 1; end if;
    end if;

    -- Resolve stock alerts when recovered
    if v_status not in ('OUT_OF_STOCK', 'CRITICAL_STOCK', 'LOW_STOCK') then
      update alerts set status = 'resolved'
      where store_id = p_store_id and reference_id = v_row.product_id
        and type in ('LOW_STOCK', 'CRITICAL_STOCK', 'OUT_OF_STOCK')
        and status = 'open';
    end if;

    -- Expiry risk (worst sellable batch inside warning window)
    select * into v_warn_batch
    from inventory_batches
    where store_id = p_store_id and product_id = v_row.product_id
      and status = 'sellable' and quantity > 0
      and expiry_date is not null
      and expiry_date <= current_date + v_cfg.expiry_warning_days
    order by expiry_date
    limit 1;

    if found then
      v_days_left := greatest(v_warn_batch.expiry_date - current_date, 0);
      v_expected := greatest(coalesce(v_velocity, 0), 0) * v_days_left;
      v_excess := v_warn_batch.quantity - v_expected;
      if v_excess > 0 then
        v_ok := ensure_alert(p_store_id, 'EXPIRY_RISK', 'warning',
          'Expiry risk: ' || v_row.name,
          'Batch of ' || v_warn_batch.quantity || ' expires in ' || v_days_left ||
          ' day(s). Estimated excess after expected sales: ~' || ceil(v_excess) ||
          ' unit(s) (estimate based on recent sales pace).',
          v_row.product_id);
        if v_ok then v_created := v_created + 1; end if;
      else
        v_ok := ensure_alert(p_store_id, 'NEAR_EXPIRY', 'info',
          'Near expiry: ' || v_row.name,
          'Batch of ' || v_warn_batch.quantity || ' expires in ' || v_days_left ||
          ' day(s). Expected to sell through at current pace.',
          v_row.product_id);
        if v_ok then v_created := v_created + 1; end if;
      end if;
    else
      update alerts set status = 'resolved'
      where store_id = p_store_id and reference_id = v_row.product_id
        and type in ('NEAR_EXPIRY', 'EXPIRY_RISK')
        and status = 'open';
    end if;

    -- Reorder requirement (velocity-aware reorder point, pending POs respected)
    v_reorder_point := greatest(
      v_row.reorder_level,
      coalesce(v_velocity, 0) * (v_cfg.reorder_lead_time_days + v_cfg.reorder_safety_days)
    );
    if coalesce(v_velocity, 0) > 0 and v_row.qty <= v_reorder_point then
      select exists (
        select 1 from purchase_orders po
        join purchase_items pi on pi.purchase_order_id = po.id
        where po.store_id = p_store_id and po.status = 'pending'
          and pi.product_id = v_row.product_id
      ) into v_has_pending_po;
      if not v_has_pending_po then
        v_ok := ensure_alert(p_store_id, 'REORDER_REQUIRED', 'warning',
          'Reorder required: ' || v_row.name,
          'Stock ' || v_row.qty || ' is at/below the reorder point (' || ceil(v_reorder_point) ||
          ') at ~' || round(v_velocity, 1) || ' units/day.',
          v_row.product_id);
        if v_ok then v_created := v_created + 1; end if;
      end if;
    else
      update alerts set status = 'resolved'
      where store_id = p_store_id and reference_id = v_row.product_id
        and type = 'REORDER_REQUIRED' and status = 'open';
    end if;
  end loop;

  return jsonb_build_object('store_id', p_store_id, 'alerts_created', v_created,
                            'batches_expired', v_expired);
end;
$$;

grant execute on function create_sale(uuid, uuid, jsonb, numeric, text, uuid) to postgres;
grant execute on function receive_purchase(uuid, uuid) to postgres;
grant execute on function refresh_inventory_intelligence(uuid) to postgres;
