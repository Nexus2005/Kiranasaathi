-- KiranaSaathi AI — 003 Phase 1 demo seed
-- Demo login after API hash sync: ramesh@kirana.demo / Demo@12345

do $$
declare
  v_user_id uuid;
  v_merchant_id uuid;
  v_store_id uuid;
  v_hash text;
  v_product record;
  v_stock integer;
  v_expiry date;
begin
  select password_hash into v_hash from users where email = 'ramesh@kirana.demo';

  if v_user_id is null and v_hash is null then
    begin
      v_hash := pg_read_file('/tmp/unused');
    exception when others then
      v_hash := null;
    end;
  end if;

  -- Default hash for Demo@12345 (pbkdf2_sha256$310000$kiranaSaathiSalt01$...)
  -- scripts/migrate.py overwrites this with the verified hash after seed.
  if not exists (select 1 from users where email = 'ramesh@kirana.demo') then
    insert into users (email, password_hash, full_name)
    values (
      'ramesh@kirana.demo',
      'pbkdf2_sha256$310000$kiranaSaathiSalt01$a7ac1029cfe06092f8de0094248d859a7da3bcefe396569faf9bb052d77fa2d9',
      'Ramesh Verma'
    )
    returning id into v_user_id;
  else
    select id into v_user_id from users where email = 'ramesh@kirana.demo';
  end if;

  if not exists (select 1 from merchants where user_id = v_user_id) then
    insert into merchants (user_id, name) values (v_user_id, 'Ramesh Verma')
    returning id into v_merchant_id;
  else
    select id into v_merchant_id from merchants where user_id = v_user_id;
  end if;

  if not exists (select 1 from stores where merchant_id = v_merchant_id) then
    insert into stores (merchant_id, name, location)
    values (v_merchant_id, 'Sharma Kirana Store', 'Nashik, Maharashtra')
    returning id into v_store_id;
  else
    select id into v_store_id from stores where merchant_id = v_merchant_id limit 1;
  end if;

  if exists (select 1 from products where store_id = v_store_id) then
    raise notice 'Seed already present for store %', v_store_id;
    return;
  end if;

  create temp table _seed_products on commit drop as
  select * from (values
    ('Parle-G Biscuits (800g)', 'Biscuits & Snacks', 'PARLE-G-800', '8901719101234', 'pack', 45, 42, 32, 30, 12, 90),
    ('Amul Taaza Milk (1L)', 'Dairy & Eggs', 'AMUL-TAAZA-1L', '8901262001235', 'ltr', 58, 56, 48, 20, 3, 6),
    ('Maggi Noodles (70g)', 'Snacks', 'MAGGI-70', '8901058005678', 'pack', 14, 14, 10, 30, 25, 200),
    ('Aashirvaad Atta (5kg)', 'Atta, Rice & Grains', 'AASH-ATTA-5', '8901008009012', 'pack', 275, 265, 210, 10, 0, 180),
    ('Coca-Cola (750ml)', 'Beverages', 'COKE-750', '8901764012345', 'btl', 45, 40, 28, 24, 6, 8),
    ('Surf Excel (1kg)', 'Personal Care', 'SURF-1KG', '8901030890123', 'pack', 115, 110, 95, 15, 18, 300),
    ('Saffola Gold Oil (1L)', 'Cooking Essentials', 'SAFF-1L', '8901088004321', 'btl', 135, 124, 110, 20, 32, 240),
    ('Dabur Toothpaste (200g)', 'Personal Care', 'DABUR-200', '8901207012345', 'pack', 78, 72, 58, 20, 4, 365),
    ('Tata Salt (1kg)', 'Cooking Essentials', 'TATA-SALT-1', '8904043901234', 'pack', 30, 28, 20, 25, 41, 400),
    ('Red Bull (250ml)', 'Beverages', 'RB-250', '8902490101234', 'btl', 125, 110, 85, 12, 0, 200),
    ('Haldiram Bhujia (400g)', 'Snacks', 'HALD-BHJ-400', '8904004401122', 'pack', 95, 90, 72, 15, 16, 150),
    ('Britannia Rusk (300g)', 'Biscuits & Snacks', 'BRIT-RSK-300', '8901063011122', 'pack', 65, 60, 48, 12, 9, 120),
    ('Colgate Toothpaste (150g)', 'Personal Care', 'COL-150', '8901314001199', 'pack', 65, 55, 42, 15, 22, 300),
    ('Tide Detergent (1kg)', 'Personal Care', 'TIDE-1KG', '8901030805678', 'pack', 125, 110, 92, 10, 11, 270),
    ('Fortune Sunflower Oil (1L)', 'Cooking Essentials', 'FORT-SUN-1L', '8906004250012', 'btl', 150, 145, 118, 20, 27, 180)
  ) as t(name, category, sku, barcode, unit, mrp, selling_price, purchase_price, reorder_level, stock, shelf_life_days);

  insert into products (store_id, name, category, sku, barcode, unit, mrp, selling_price, purchase_price, reorder_level)
  select v_store_id, name, category, sku, barcode, unit, mrp, selling_price, purchase_price, reorder_level
  from _seed_products;

  insert into inventory (store_id, product_id, quantity)
  select v_store_id, p.id, sp.stock
  from products p
  join _seed_products sp on sp.sku = p.sku
  where p.store_id = v_store_id;

  insert into inventory_batches (store_id, product_id, batch_no, quantity, purchase_cost, expiry_date, received_at)
  select v_store_id, p.id,
    'B' || to_char(now(), 'MMDD') || '-' || left(p.sku, 4),
    least(inv.quantity, 5),
    p.purchase_price,
    current_date + sp.shelf_life_days,
    now() - interval '10 days'
  from products p
  join _seed_products sp on sp.sku = p.sku
  join inventory inv on inv.product_id = p.id and inv.store_id = v_store_id
  where p.store_id = v_store_id and inv.quantity > 0;

  insert into suppliers (store_id, name, phone, address) values
    (v_store_id, 'Wholesale Bazaar', '+91 98765 43210', 'Main Market, Nashik'),
    (v_store_id, 'Metro Wholesale', '+91 98220 11223', 'Mumbai Naka, Nashik'),
    (v_store_id, 'Shree Distributors', '+91 98900 55667', 'CIDCO, Nashik'),
    (v_store_id, 'Gupta Traders', '+91 97654 33221', 'Panchavati, Nashik');

  insert into customers (store_id, name, phone) values
    (v_store_id, 'Amit Patil', '+919876543210'),
    (v_store_id, 'Sneha Kulkarni', '+918765432109'),
    (v_store_id, 'Rahul Deshmukh', '+919654321098'),
    (v_store_id, 'Priya Sharma', '+919123456789'),
    (v_store_id, 'Suresh Kale', '+919987665540');

  insert into purchase_orders (store_id, supplier_id, total_amount, status)
  select v_store_id, s.id, 8230, 'pending'
  from suppliers s
  where s.store_id = v_store_id and s.name = 'Metro Wholesale';

  insert into purchase_items (purchase_order_id, product_id, quantity, unit_cost)
  select po.id, p.id, 40, p.purchase_price
  from purchase_orders po
  join products p on p.store_id = v_store_id and p.sku = 'MAGGI-70'
  where po.store_id = v_store_id
  limit 1;

  insert into alerts (store_id, type, severity, title, description, reference_id)
  select v_store_id, 'LOW_STOCK', 'warning',
         'Low stock: ' || pr.name,
         'Only ' || inv.quantity || ' units left. Reorder level is ' || pr.reorder_level || '.',
         pr.id
  from inventory inv
  join products pr on pr.id = inv.product_id
  where inv.store_id = v_store_id and inv.quantity <= pr.reorder_level;

  insert into alerts (store_id, type, severity, title, description, reference_id)
  select v_store_id, 'NEAR_EXPIRY', 'critical',
         'Expiry risk: ' || pr.name,
         'Batch expires on ' || to_char(b.expiry_date, 'DD Mon YYYY') || '.',
         pr.id
  from inventory_batches b
  join products pr on pr.id = b.product_id
  where b.store_id = v_store_id
    and b.expiry_date <= current_date + 14
    and b.quantity > 0;

  insert into ai_recommendations (store_id, type, title, description, evidence, severity, confidence, status, proposed_action)
  values
    (v_store_id, 'EXPIRY',
     'Clear near-expiry Amul Taaza milk stock',
     '3 units of Amul Taaza Milk expire within 7 days. Consider a small discount or bundle to avoid loss.',
     jsonb_build_object('product','Amul Taaza Milk (1L)','quantity',3,'expiry_days',6,'stock_value',168,'source','inventory_batches'),
     'critical', 0.92, 'NEW',
     'Apply 7% discount or create a milk+biscuit bundle for walk-in customers.'),
    (v_store_id, 'REORDER',
     'Reorder Maggi Noodles (70g)',
     'Current stock 25 with reorder level 30. Suggested purchase quantity: 48 units.',
     jsonb_build_object('product','Maggi Noodles (70g)','quantity',25,'reorder_level',30,'suggested_qty',48,'source','inventory'),
     'warning', 0.85, 'NEW',
     'Create purchase order for 48 units from Wholesale Bazaar.');

  insert into activity_logs (store_id, event_type, entity_type, message)
  values (v_store_id, 'SEED_COMPLETED', 'system', 'Demo store data seeded for Sharma Kirana Store');

  -- Historical sales for dashboard trends (deterministic sample)
  insert into sales (store_id, customer_id, subtotal, discount, total, payment_method, status, created_by, created_at)
  select
    v_store_id,
    null,
    g::numeric,
    0,
    g::numeric,
    (array['cash','upi','card'])[1 + (g % 3)],
    'completed',
    v_user_id,
    now() - (g || ' days')::interval
  from generate_series(1, 21) g;

  insert into sale_items (sale_id, product_id, quantity, unit_price, unit_cost)
  select s.id, p.id, 1 + (random() * 3)::int, p.selling_price, p.purchase_price
  from sales s
  cross join lateral (
    select * from products where store_id = v_store_id order by random() limit 2
  ) p
  where s.store_id = v_store_id
    and s.created_at >= now() - interval '21 days'
    and not exists (select 1 from sale_items si where si.sale_id = s.id);

  raise notice 'Seed complete. Store %, User %', v_store_id, v_user_id;
end $$;
