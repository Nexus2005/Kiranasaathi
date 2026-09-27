-- ============================================================
-- KiranaSaathi AI — 015 Smart Counter: visual product catalog
--
-- Store-specific visual embeddings for camera-based product
-- recognition (retrieval, NOT a global classifier):
--   * product_images        — multiple reference images per product
--   * product_visual_embeddings — one embedding per (image, model, dim)
--   * match_product_embedding() — store-scoped cosine similarity search
--
-- pgvector (vector extension) is used when available; the migration is
-- portable: if the extension is missing, tables are created WITHOUT the
-- vector column and the API layer reports VISION_NOT_CONFIGURED instead
-- of failing (honest degradation, no fake capability).
--
-- Security posture (013) preserved:
--   * RLS enabled on new tables (deny-by-default, zero policies)
--   * no grants to anon/authenticated; EXECUTE revoked from public
--   * the FastAPI service connects as the owner role and enforces
--     store scoping in every query (defense-in-depth stays API-first)
-- ============================================================

-- ---------- pgvector (optional) ----------
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'vector') then
    create extension if not exists vector;
  end if;
end $$;

-- pgvector installs ~120 helper functions into `public` and grants EXECUTE
-- to PUBLIC by extension default — reopening the RPC surface that migration
-- 013 closed. We attempt to re-close it here (deny-by-default); NOTE: on
-- Supabase these objects are owned by `supabase_admin`, which the app's
-- `postgres` role cannot ALTER/REVOKE — the statements below are a no-op
-- there (Postgres warns, does not error). scripts/verify_security.py
-- therefore exempts EXTENSION-OWNED functions (visible in pg_depend):
-- they are pure vector math with no table access, so they cannot leak
-- store data; every business function remains fully closed. On
-- self-hosted Postgres where postgres owns the extension, this block
-- does close the surface for real.
do $$
declare r text;
begin
  if exists (select 1 from pg_extension where extname = 'vector') then
    for r in select rolname from pg_roles where rolname in ('anon', 'authenticated')
    loop
      execute format('revoke execute on all functions in schema public from %I', r);
    end loop;
    execute 'revoke execute on all functions in schema public from public';
  end if;
end $$;

-- ---------- PRODUCT IMAGES (reference photos per product) ----------
create table if not exists product_images (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  product_id uuid not null references products(id) on delete cascade,
  -- front | back | side | angled | shelf (extensible)
  view text not null default 'front',
  image_url text,
  -- sha256 of the source bytes; dedup + audit without storing raw images
  content_hash text,
  created_by uuid references users(id) on delete set null,
  created_at timestamptz not null default now(),
  unique (store_id, product_id, content_hash)
);

create index if not exists idx_product_images_store on product_images(store_id, product_id);

-- ---------- VISUAL EMBEDDINGS ----------
create table if not exists product_visual_embeddings (
  id uuid primary key default gen_random_uuid(),
  store_id uuid not null references stores(id) on delete cascade,
  product_id uuid not null references products(id) on delete cascade,
  image_id uuid references product_images(id) on delete cascade,
  -- Dimension is model-determined; pgvector requires a typed dimension, so
  -- the column is created per-model by the API layer via set_embedding_dim()
  -- (see below). The base table carries the vector in the `embedding`
  -- column added by that function when the extension exists.
  embedding_model text not null,
  embedding_version text not null,
  -- Normalized cosine similarity against this row: 0..1 (1 = identical)
  dimensions integer,
  created_by uuid references users(id) on delete set null,
  created_at timestamptz not null default now(),
  unique (store_id, product_id, image_id, embedding_model, embedding_version, dimensions)
);

create index if not exists idx_pve_store_model on product_visual_embeddings(store_id, embedding_model, embedding_version);

-- pgvector column (after table creation; portable if extension missing).
-- NOTE: an ANN index (ivfflat/hnsw) requires a TYPED vector column (fixed
-- dimension). The base `embedding` column is intentionally untyped so any
-- model dimension works from day one; match_product_embeddings() does exact
-- cosine search (sequential scan) — correct, and appropriate at store-catalog
-- scale (10^3–10^5 rows). When a store standardizes on a model dimension,
-- set_embedding_dim(dim) adds a typed column + ivfflat index for ANN.
do $$
begin
  if exists (select 1 from pg_extension where extname = 'vector') then
    execute 'alter table product_visual_embeddings add column if not exists embedding vector';
  end if;
end $$;

-- Model dimension registry: one active dimension per (model, version).
create table if not exists visual_embedding_models (
  model text primary key,
  version text not null,
  dimensions integer not null check (dimensions > 0),
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

-- ---------- DIMENSION MANAGEMENT ----------
-- pgvector columns are typed with a fixed dimension. Changing models means a
-- new column per dimension; this helper (idempotent, owner-role only) adds
-- `<dim>_dim` columns to product_visual_embeddings and mirrors them into the
-- `embedding` column used by the match function.
create or replace function set_embedding_dim(p_dim integer)
returns void
language plpgsql
as $$
begin
  if p_dim is null or p_dim <= 0 or p_dim > 4096 then
    raise exception 'invalid embedding dimension';
  end if;
  if not exists (select 1 from pg_extension where extname = 'vector') then
    raise exception 'pgvector extension not available';
  end if;
  execute format('alter table product_visual_embeddings add column if not exists emb_%1$s vector(%1$s)', p_dim);
  -- ANN index for the typed column (exact search remains available on the
  -- untyped `embedding` column regardless).
  execute format('drop index if exists idx_pve_emb_%1$s', p_dim);
  execute format('create index idx_pve_emb_%1$s on product_visual_embeddings using ivfflat (emb_%1$s vector_cosine_ops) with (lists = 100)', p_dim);
end;
$$;

-- ---------- STORE-SCOPED MATCH (used by the recognition pipeline) ----------
-- Returns top-K products by best cosine similarity across all embeddings of
-- each product. Only rows for THIS store and the given model/version are
-- searched — no cross-store leakage is possible by construction.
-- Guarded: only created when pgvector exists (references the vector type).
do $$
begin
  if exists (select 1 from pg_extension where extname = 'vector') then
    execute $fn$
      create or replace function match_product_embeddings(
        p_store_id uuid,
        p_query vector,
        p_model text,
        p_version text,
        p_top_k integer default 5,
        p_min_similarity numeric default 0.0
      )
      returns table (
        product_id uuid,
        image_id uuid,
        similarity numeric,
        embedding_id uuid
      )
      language sql
      stable
      as $body$
        select e.product_id,
               e.image_id,
               round((1 - (e.embedding <=> p_query))::numeric, 6) as similarity,
               e.id as embedding_id
        from product_visual_embeddings e
        where e.store_id = p_store_id
          and e.embedding_model = p_model
          and e.embedding_version = p_version
          and e.embedding is not null
          and (1 - (e.embedding <=> p_query)) >= p_min_similarity
        order by e.embedding <=> p_query
        limit greatest(p_top_k, 1)
      $body$;
    $fn$;
  end if;
end $$;

-- ---------- TOUCH TRIGGER ----------
drop trigger if exists trg_product_visual_embeddings_touch on product_visual_embeddings;
create trigger trg_product_visual_embeddings_touch before update on product_visual_embeddings
for each row execute function touch_updated_at();

-- ---------- SECURITY POSTURE (matches migration 013) ----------
do $$
begin
  -- RLS deny-by-default on the new tables
  execute 'alter table product_images enable row level security';
  execute 'alter table product_visual_embeddings enable row level security';
  execute 'alter table visual_embedding_models enable row level security';
exception when undefined_table then null;
end $$;

do $$
declare r text;
begin
  for r in select rolname from pg_roles where rolname in ('anon', 'authenticated')
  loop
    execute format('revoke all privileges on product_images, product_visual_embeddings, visual_embedding_models from %I', r);
  end loop;
exception when undefined_table then null;
end $$;

revoke execute on function set_embedding_dim(integer) from public;

do $$
declare r text;
begin
  for r in select rolname from pg_roles where rolname in ('anon', 'authenticated')
  loop
    execute format('revoke execute on function set_embedding_dim(integer) from %I', r);
    if exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
               where n.nspname = 'public' and p.proname = 'match_product_embeddings') then
      execute format('revoke execute on function match_product_embeddings(uuid, vector, text, text, integer, numeric) from %I', r);
    end if;
  end loop;
exception when undefined_object then null;
end $$;

do $$
begin
  if exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
             where n.nspname = 'public' and p.proname = 'match_product_embeddings') then
    execute 'revoke execute on function match_product_embeddings(uuid, vector, text, text, integer, numeric) from public';
  end if;
end $$;
