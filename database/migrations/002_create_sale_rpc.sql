-- KiranaSaathi AI — 002 create_sale RPC (atomic business transaction)

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
  v_cost numeric;
  v_avail integer;
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
    if v_avail < v_qty then
      raise exception 'Insufficient stock (available %, requested %)', v_avail, v_qty;
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

    select p.selling_price, p.purchase_price into v_price, v_cost
    from products p where p.id = v_product_id;

    insert into sale_items (sale_id, product_id, quantity, unit_price, unit_cost)
    values (v_sale_id, v_product_id, v_qty, v_price, v_cost);

    update inventory
    set quantity = quantity - v_qty,
        updated_at = now()
    where store_id = p_store_id and product_id = v_product_id
    returning quantity into v_avail;

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

  insert into alerts (store_id, type, severity, title, description, reference_id)
  select p_store_id, 'LOW_STOCK', 'warning',
         'Low stock: ' || pr.name,
         'Only ' || inv.quantity || ' left (reorder level ' || pr.reorder_level || ').',
         pr.id
  from inventory inv
  join products pr on pr.id = inv.product_id
  where inv.store_id = p_store_id
    and pr.is_active
    and inv.quantity <= pr.reorder_level
    and not exists (
      select 1 from alerts a
      where a.store_id = p_store_id
        and a.type = 'LOW_STOCK'
        and a.reference_id = pr.id
        and a.status = 'open'
    );

  return jsonb_build_object(
    'sale_id', v_sale_id,
    'subtotal', v_subtotal,
    'discount', p_discount,
    'total', v_total
  );
end;
$$;

grant execute on function create_sale(uuid, uuid, jsonb, numeric, text, uuid) to postgres;
