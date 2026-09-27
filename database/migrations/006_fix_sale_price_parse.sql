-- KiranaSaathi AI — 006: Fix create_sale unit_price parsing.
-- The previous version assigned item->>'unit_price' (text) directly to a
-- numeric variable and then compared it to '' — which coerces '' to numeric
-- and fails with `invalid input syntax for type numeric: ""`.
-- Parse through a text variable instead.

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
  v_price_text text;
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
    v_price_text := item->>'unit_price';
    if v_price_text is not null and v_price_text <> '' then
      v_requested_price := v_price_text::numeric;
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
    v_price_text := item->>'unit_price';
    if v_price_text is not null and v_price_text <> '' then
      v_requested_price := v_price_text::numeric;
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

grant execute on function create_sale(uuid, uuid, jsonb, numeric, text, uuid) to postgres;
