"""Quick DB smoke test for Phase 2 SQL functions (read-only except a rolled-back transaction)."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
import asyncpg


async def deplete_rollback(conn, store, pid):
    """Inside a transaction: deplete 2 units, verify, then force rollback."""
    async with conn.transaction():
        inv_before = await conn.fetchval(
            "select quantity from inventory where store_id=$1 and product_id=$2", store, pid
        )
        raw = await conn.fetchval("select deplete_batches($1, $2, $3)", store, pid, 2)
        dep = json.loads(raw) if isinstance(raw, str) else raw
        print(f"PASS  deplete covered=2 {dep}" if dep["covered"] == 2 else f"FAIL  deplete covered {dep}")
        bsum = await conn.fetchval(
            "select sum(quantity) from inventory_batches where store_id=$1 and product_id=$2",
            store, pid,
        )
        print(
            f"PASS  batch sum -2 ({bsum} vs {inv_before})"
            if int(bsum) == int(inv_before) - 2
            else f"FAIL  batch sum {bsum} vs {inv_before}"
        )
        raise RuntimeError("rollback")  # expected — always rolls back


async def main() -> int:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    store = await conn.fetchval("select id from stores limit 1")
    fails = []

    def check(name, cond, detail=""):
        print(f"{'PASS' if cond else 'FAIL'}  {name} {detail}")
        if not cond:
            fails.append(name)

    cfg = await conn.fetchrow("select * from store_settings where store_id=$1", store)
    check("store_settings", cfg is not None)

    s = await conn.fetchval("select product_stock_status(0, 10, 1.0, 45, 90, 180)")
    check("status OUT_OF_STOCK", s == "OUT_OF_STOCK", s)
    s = await conn.fetchval("select product_stock_status(4, 10, 1.0, 45, 90, 180)")
    check("status CRITICAL_STOCK", s == "CRITICAL_STOCK", s)
    s = await conn.fetchval("select product_stock_status(9, 10, 1.0, 45, 90, 180)")
    check("status LOW_STOCK", s == "LOW_STOCK", s)
    s = await conn.fetchval("select product_stock_status(500, 10, 0.5, 45, 90, 180)")
    check("status DEAD_STOCK (250d cover > 180)", s == "DEAD_STOCK", s)
    s = await conn.fetchval("select product_stock_status(200, 10, 2.0, 45, 90, 180)")
    check("status OVERSTOCKED (100d)", s == "OVERSTOCKED", s)
    s = await conn.fetchval("select product_stock_status(100, 10, 2.0, 45, 90, 180)")
    check("status SLOW_MOVING (50d)", s == "SLOW_MOVING", s)
    s = await conn.fetchval("select product_stock_status(20, 10, 2.0, 45, 90, 180)")
    check("status HEALTHY (10d)", s == "HEALTHY", s)

    pid = await conn.fetchval(
        """
        select si.product_id from sale_items si
        join sales s on s.id = si.sale_id
        where s.store_id = $1 and s.status='completed'
        group by si.product_id order by sum(si.quantity) desc limit 1
        """,
        store,
    )
    v = await conn.fetchval("select product_velocity($1, $2, 30)", store, pid)
    check("velocity > 0", float(v) > 0, f"v={v}")

    cost = await conn.fetchval("select purchase_price from products where id=$1", pid)
    floor_p = await conn.fetchval("select min_acceptable_price($1, $2)", store, pid)
    check("floor >= cost", float(floor_p) >= float(cost), f"floor={floor_p} cost={cost}")

    # FEFO depletion inside a transaction that always rolls back
    inv_before = await conn.fetchval(
        "select quantity from inventory where store_id=$1 and product_id=$2", store, pid
    )
    try:
        await deplete_rollback(conn, store, pid)
    except RuntimeError:
        pass
    bsum_after = await conn.fetchval(
        "select sum(quantity) from inventory_batches where store_id=$1 and product_id=$2",
        store, pid,
    )
    check("rollback restored batches", int(bsum_after) == int(inv_before), f"{bsum_after}")

    r1 = json.loads(await conn.fetchval("select refresh_inventory_intelligence($1)", store))
    r2 = json.loads(await conn.fetchval("select refresh_inventory_intelligence($1)", store))
    check("refresh idempotent (2nd run creates 0)", r2["alerts_created"] == 0, str(r2))

    dups = await conn.fetchval(
        """
        select count(*) from (
          select store_id, type, reference_id, count(*) c
          from alerts where status='open'
          group by store_id, type, reference_id having count(*) > 1
        ) x
        """
    )
    check("no duplicate open alerts", int(dups) == 0, f"dups={dups}")

    await conn.close()
    print(f"\n{'ALL SMOKE CHECKS PASSED' if not fails else 'FAILURES: ' + ', '.join(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
