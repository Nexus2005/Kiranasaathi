"""Apply database/migrations/*.sql over DATABASE_URL and verify schema."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "database" / "migrations"

EXPECTED_TABLES = [
    "users",
    "merchants",
    "stores",
    "products",
    "inventory",
    "inventory_batches",
    "inventory_movements",
    "suppliers",
    "purchase_orders",
    "purchase_items",
    "customers",
    "sales",
    "sale_items",
    "alerts",
    "ai_recommendations",
    "activity_logs",
    "schema_migrations",
]


def database_url() -> str:
    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("DATABASE_URL missing in .env")
    # asyncpg parses the DSN itself and expects the percent-encoded
    # password intact — do not unquote/rebuild the URL.
    return url


async def verify(conn: asyncpg.Connection, label: str) -> None:
    tables = await conn.fetch(
        "select table_name from information_schema.tables where table_schema='public' and table_type='BASE TABLE'"
    )
    names = {r["table_name"] for r in tables}
    missing = [t for t in EXPECTED_TABLES if t not in names]
    fn = await conn.fetchval(
        "select count(*) from pg_proc where proname='create_sale'"
    )
    users = products = sales = None
    if "users" in names:
        users = await conn.fetchval("select count(*) from users")
        products = await conn.fetchval("select count(*) from products")
        sales = await conn.fetchval("select count(*) from sales")
    print(f"[verify:{label}] tables={len(names)} missing={missing or 'none'} create_sale={fn} users={users} products={products} sales={sales}")
    if missing and label.endswith("complete"):
        raise SystemExit(f"Missing tables after migrate: {missing}")


async def main() -> int:
    url = database_url()
    print(f"Connecting… host={urlparse(url).hostname}")
    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(url, command_timeout=60), timeout=20
        )
    except Exception as exc:
        print(f"CONNECT_FAILED: {type(exc).__name__}: {exc}")
        print(
            "Hint: enable Supabase dedicated IPv4 add-on, or set SUPABASE_ACCESS_TOKEN (sbp_…) for CLI --linked queries."
        )
        return 2

    try:
        ver = await conn.fetchval("select version()")
        print(f"Connected: {ver}")
        await conn.execute(
            "create table if not exists schema_migrations (name text primary key, applied_at timestamptz not null default now())"
        )
        await verify(conn, "baseline")

        files = sorted(MIGRATIONS.glob("*.sql"))
        if not files:
            print("No migration files found")
            return 1

        for path in files:
            name = path.name
            done = await conn.fetchval(
                "select 1 from schema_migrations where name=$1", name
            )
            if done:
                print(f"SKIP {name} (already applied)")
                continue
            sql = path.read_text(encoding="utf-8")
            print(f"APPLY {name} ({len(sql)} chars)…")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "insert into schema_migrations(name) values ($1) on conflict do nothing",
                    name,
                )
            print(f"OK   {name}")
            await verify(conn, name)

        # Ensure demo password hash matches app algorithm
        hash_file = ROOT / "database" / "seed" / "demo_password_hash.txt"
        if hash_file.exists() and hash_file.read_text().strip():
            h = hash_file.read_text().strip()
            await conn.execute(
                "update users set password_hash=$1 where email='ramesh@kirana.demo'",
                h,
            )
            print("Synced demo user password_hash")

        await verify(conn, "complete")
        print("MIGRATIONS_COMPLETE")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
