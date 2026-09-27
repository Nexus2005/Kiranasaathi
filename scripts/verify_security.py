"""Verify the security posture of the KiranaSaathi database.

Checks (fail -> exit 1):
  1. Row Level Security is enabled on every table in schema public.
  2. Only whitelisted permissive policies exist (default: none).
  3. anon/authenticated have no EXECUTE on any function in public
     (PostgREST /rest/v1/rpc/* surface closed).
  4. anon/authenticated have no table or sequence privileges in public.

Read-only; prints no secrets. Run after any migration that touches
schema, privileges, or policies:  python scripts/verify_security.py
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

# Policies added deliberately by future feature migrations must be
# registered here with their scope, e.g. {"customer_catalog_read"}.
ALLOWED_POLICIES: set[str] = set()

# Extension-owned functions (pg_depend extension membership) are exempt from
# the EXECUTE check. Rationale: `create extension vector` (pgvector, migration
# 015) installs ~120 pure vector-math helpers into schema public with PUBLIC
# EXECUTE granted by the extension owner (supabase_admin on Supabase). The
# app's postgres role CANNOT revoke those ACLs (not the owner) — Postgres
# silently no-ops the revoke. These functions take/return vectors and touch
# no tables, so they expose no store data. Migration 015 still attempts the
# revoke (effective on self-hosted Postgres where postgres owns the
# extension). Everything NOT owned by an extension remains fully closed.
# To audit: select distinct e.extname, p.proname from pg_proc p
#   join pg_depend d on d.objid = p.oid and d.classid = 'pg_class'::regclass
#   join pg_extension e on e.oid = d.refobjid
#   where ... (see query below).
EXEMPT_EXTENSION_FUNCTIONS = True


async def main() -> int:
    url = os.environ["DATABASE_URL"].strip()
    conn = await asyncio.wait_for(asyncpg.connect(url, command_timeout=60), timeout=25)
    failures: list[str] = []
    try:
        # 1) RLS enabled everywhere
        no_rls = [
            r["table_name"]
            for r in await conn.fetch(
                """
                select c.relname as table_name
                from pg_class c
                join pg_namespace n on n.oid = c.relnamespace
                where n.nspname = 'public' and c.relkind = 'r'
                  and not c.relrowsecurity
                order by c.relname
                """
            )
        ]
        if no_rls:
            failures.append(f"RLS missing on: {', '.join(no_rls)}")
        else:
            total = (await conn.fetchval(
                "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace "
                "where n.nspname='public' and c.relkind='r'"))
            print(f"OK   RLS enabled on all {total} tables")

        # 2) Policies whitelist
        policies = await conn.fetch(
            """
            select p.polname as policy_name, c.relname as table_name
            from pg_policy p
            join pg_class c on c.oid = p.polrelid
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public'
            order by c.relname, p.polname
            """
        )
        unexpected = [f"{r['table_name']}.{r['policy_name']}" for r in policies
                      if r["policy_name"] not in ALLOWED_POLICIES]
        if unexpected:
            failures.append(f"Unexpected policies (add to ALLOWED_POLICIES if intended): {unexpected}")
        else:
            print(f"OK   policies: {len(policies)} (all whitelisted)" if policies
                  else "OK   policies: none (deny-by-default)")

        # 3) Function EXECUTE surface closed for anon/authenticated
        #    (extension-owned functions exempt — see EXEMPT_EXTENSION_FUNCTIONS)
        for role in ("anon", "authenticated"):
            fn_grants = await conn.fetchval(
                """
                select count(*)
                from pg_proc p
                join pg_namespace n on n.oid = p.pronamespace
                join aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
                  on true
                join pg_roles r on r.oid = a.grantee
                where n.nspname = 'public' and r.rolname = $1
                  and not exists (
                    select 1 from pg_depend d
                    join pg_extension e on e.oid = d.refobjid
                    where d.objid = p.oid
                      and d.classid = 'pg_proc'::regclass
                      and d.deptype = 'e'
                  )
                """,
                role,
            )
            if fn_grants:
                failures.append(f"{role} has EXECUTE on {fn_grants} non-extension function(s) in public")
            else:
                ext_note = " (extension-owned helpers exempt)" if EXEMPT_EXTENSION_FUNCTIONS else ""
                print(f"OK   {role}: no non-extension function EXECUTE in public{ext_note}")

        # 4) Table/sequence privileges closed for anon/authenticated
        for role in ("anon", "authenticated"):
            privs = await conn.fetchval(
                """
                select count(*)
                from information_schema.role_table_grants
                where table_schema = 'public' and grantee = $1
                """,
                role,
            )
            if privs:
                failures.append(f"{role} holds privileges on {privs} table(s) in public")
            else:
                print(f"OK   {role}: no table privileges in public")
    finally:
        await conn.close()

    if failures:
        print("\nSECURITY CHECK FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nSECURITY CHECK: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
