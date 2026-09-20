create table if not exists public.sethistock_load_telemetry (
    id bigint generated always as identity primary key,
    created_at timestamptz not null default now(),
    ticker text not null check (char_length(ticker) between 1 and 20),
    quote_ms integer check (quote_ms is null or quote_ms >= 0),
    full_ms integer check (full_ms is null or full_ms >= 0),
    backend_ms integer check (backend_ms is null or backend_ms >= 0),
    cache_hit boolean,
    status text not null default 'full' check (status in ('full', 'partial', 'error')),
    visitor_id text,
    error_code text
);

create index if not exists sethistock_load_telemetry_created_at_idx
    on public.sethistock_load_telemetry (created_at desc);

create index if not exists sethistock_load_telemetry_ticker_created_at_idx
    on public.sethistock_load_telemetry (ticker, created_at desc);

alter table public.sethistock_load_telemetry enable row level security;

comment on table public.sethistock_load_telemetry is
    'User-perceived SethiStock ticker load timings. Written and read only through the backend service role.';
