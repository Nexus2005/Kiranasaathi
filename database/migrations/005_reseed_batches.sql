-- KiranaSaathi AI — 005: Reseed batches to match inventory exactly.
-- Phase 1 seeded decorative batches (sums != inventory). This rebuilds
-- batches deterministically: sum(batches) == inventory.quantity per product,
-- staggered expiries (a few near-expiry for the expiry engine), and
-- slightly varying batch costs (older batch cheaper) to exercise FEFO.

begin;

delete from inventory_batches;

with ranked as (
  select i.store_id, i.product_id, p.purchase_price, i.quantity,
         row_number() over (order by p.name) as rn
  from inventory i
  join products p on p.id = i.product_id
  where i.quantity > 0
)
-- Every 4th product gets ONE near-expiry batch (12 days); the rest get two
-- batches: 60% (older, cheaper, +40..190d) and remainder (newer, +3%, +75..225d).
insert into inventory_batches
  (store_id, product_id, batch_no, quantity, purchase_cost, expiry_date, received_at, status)
select store_id, product_id,
       'B-INIT-' || rn::text,
       case when rn % 4 = 0 then quantity else ceil(quantity * 0.6)::int end,
       purchase_price,
       case when rn % 4 = 0 then current_date + 12::int
            else current_date + (40 + (rn % 7) * 25)::int end,
       now() - interval '20 days',
       'sellable'
from ranked;

with ranked as (
  select i.store_id, i.product_id, p.purchase_price, i.quantity,
         row_number() over (order by p.name) as rn
  from inventory i
  join products p on p.id = i.product_id
  where i.quantity > 0
)
insert into inventory_batches
  (store_id, product_id, batch_no, quantity, purchase_cost, expiry_date, received_at, status)
select store_id, product_id,
       'B-INIT-' || (rn + 1000)::text,
       quantity - ceil(quantity * 0.6)::int,
       round(purchase_price * 1.03, 2),
       current_date + (75 + (rn % 6) * 30)::int,
       now(),
       'sellable'
from ranked
where rn % 4 <> 0
  and quantity - ceil(quantity * 0.6)::int > 0;

commit;

-- Guard: batch sums must equal inventory (will abort migration on mismatch)
do $$
declare
  bad int;
begin
  select count(*) into bad
  from inventory i
  where i.quantity <> coalesce((
    select sum(b.quantity) from inventory_batches b
    where b.store_id = i.store_id and b.product_id = i.product_id
  ), 0);
  if bad > 0 then
    raise exception 'Batch reseed mismatch: % product(s) where batch sum != inventory', bad;
  end if;
end $$;
