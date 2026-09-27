-- ============================================================
-- KiranaSaathi AI — 013: RLS + access hardening (defense-in-depth)
--
-- Architecture:
--   Frontend → FastAPI (store-scoped authorization) → session pooler
--   → postgres (table owner, BYPASSRLS) → Supabase PostgreSQL
--
-- Posture (deliberate):
--   * Row Level Security is ENABLED on every public table with ZERO
--     permissive policies. Any role other than the owner/BYPASSRLS roles
--     (e.g. `anon` via the publishable key, future Supabase Auth JWTs)
--     is DENY-BY-DEFAULT on every row of every table.
--   * Permissive policies are added per-feature, deliberately, only when
--     a feature starts consuming Supabase directly — never blanket.
--   * Functions are NOT executable by anon/authenticated (Postgres grants
--     EXECUTE to PUBLIC by default at creation, which would otherwise
--     expose engine functions like create_sale() through PostgREST
--     /rest/v1/rpc/* to anyone holding the publishable key).
--   * The FastAPI service is unaffected: it connects as the owner role.
--
-- Idempotent: safe to re-run, portable to non-Supabase Postgres
-- (guarded against missing anon/authenticated roles).
-- ============================================================

-- 1) RLS on every current public table (idempotent; 013 catches any
--    table created before this posture existed)
do $$
declare
  t text;
begin
  for t in
    select c.relname::text
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind = 'r'
      and not c.relrowsecurity
  loop
    execute format('alter table public.%I enable row level security', t);
  end loop;
end $$;

-- 2) Close the RPC surface: no function executable by anon/authenticated
do $$
declare
  r text;
begin
  for r in select rolname from pg_roles where rolname in ('anon', 'authenticated')
  loop
    execute format('revoke execute on all functions in schema public from %I', r);
  end loop;
end $$;

revoke execute on all functions in schema public from public;

-- 3) Tables: revoke all table privileges from anon/authenticated.
--    Supabase's default privileges grant ALL on tables created by the
--    postgres role to anon/authenticated; combined with zero policies
--    RLS still denies rows today, but the grants are standing attack
--    surface (any future permissive policy would expose the table
--    instantly). Deny-by-default means no grants, not just no policies.
do $$
declare
  r text;
begin
  for r in select rolname from pg_roles where rolname in ('anon', 'authenticated')
  loop
    execute format('revoke all privileges on all tables in schema public from %I', r);
  end loop;
end $$;

-- 4) Sequences: no direct usage for anon/authenticated (hygiene)
do $$
declare
  r text;
begin
  for r in select rolname from pg_roles where rolname in ('anon', 'authenticated')
  loop
    execute format('revoke all on all sequences in schema public from %I', r);
  end loop;
end $$;

-- 5) Future objects created by migrations keep the same posture
--    (tables included — this counteracts Supabase's grant-all defaults)
do $$
declare
  r text;
begin
  for r in select rolname from pg_roles where rolname in ('anon', 'authenticated')
  loop
    execute format(
      'alter default privileges in schema public revoke all on tables from %I', r);
    execute format(
      'alter default privileges in schema public revoke execute on functions from %I', r);
    execute format(
      'alter default privileges in schema public revoke all on sequences from %I', r);
  end loop;
end $$;

alter default privileges in schema public revoke execute on functions from public;

-- NOTE: no permissive policies are created here, and none inherit by
-- default. When a future feature (e.g. Phase 8 customer ordering over
-- Supabase Auth) needs direct PostgREST access, its migration must add
-- narrowly-scoped policies + targeted grants and extend
-- scripts/verify_security.py accordingly.
