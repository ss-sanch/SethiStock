-- SethiStock Phase 1D: persistent Supabase cache
-- Run once in the Supabase SQL editor for the project used by SethiStock.

create table if not exists public.sethistock_cache (
    cache_key text primary key,
    namespace text not null,
    ticker text,
    payload jsonb not null,
    cached_at timestamptz not null default now(),
    expires_at timestamptz not null,
    updated_at timestamptz not null default now()
);

create index if not exists sethistock_cache_namespace_ticker_idx
    on public.sethistock_cache (namespace, ticker);

create index if not exists sethistock_cache_expires_at_idx
    on public.sethistock_cache (expires_at);

alter table public.sethistock_cache enable row level security;

revoke all on table public.sethistock_cache from anon, authenticated;
grant all on table public.sethistock_cache to service_role;

comment on table public.sethistock_cache is
    'Persistent backend-only cache for SethiStock API responses and ticker resolution.';
