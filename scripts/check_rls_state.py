"""Check current RLS posture: which tables lack RLS, role privileges.

Read-only diagnostic. Uses DATABASE_URL from .env; prints no secrets.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


async def main() -> int:
    url = os.environ["DATABASE_URL"].strip()
    conn = await asyncio.wait_for(asyncpg.connect(url, command_timeout=60), timeout=25)
    try:
        roles = await conn.fetch(
            "select rolname, rolsuper, rolbypassrls from pg_roles "
            "where rolname in ('postgres','anon','authenticated','service_role','authenticator') "
            "order by rolname"
        )
        print("ROLES (rolname | superuser | bypassrls):")
        for r in roles:
            print(f"  {r['rolname']:<16} | {r['rolsuper']} | {r['rolbypassrls']}")

        tables = await conn.fetch(
            """
            select c.relname as table_name,
                   c.relrowsecurity as rls_enabled,
                   c.relforcerowsecurity as rls_forced
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relkind = 'r'
            order by c.relname
            """
        )
        no_rls = [t["table_name"] for t in tables if not t["rls_enabled"]]
        forced = [t["table_name"] for t in tables if t["rls_forced"]]
        with_policies = await conn.fetch(
            """
            select distinct c.relname as table_name
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            join pg_policy p on p.polrelid = c.oid
            where n.nspname = 'public'
            order by c.relname
            """
        )
        print(f"\nTABLES: {len(tables)} total, RLS enabled on {len(tables) - len(no_rls)}")
        print(f"WITHOUT RLS ({len(no_rls)}): {', '.join(no_rls) if no_rls else 'none'}")
        print(f"FORCED RLS: {', '.join(forced) if forced else 'none'}")
        print(f"TABLES WITH POLICIES: {[t['table_name'] for t in with_policies] or 'none'}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
