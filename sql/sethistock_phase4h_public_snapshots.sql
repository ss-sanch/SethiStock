create table if not exists public.sethistock_public_snapshots (
    ticker text primary key,
    analysis_payload jsonb,
    quote_payload jsonb,
    chart_payload jsonb,
    analysis_updated_at timestamptz,
    quote_updated_at timestamptz,
    chart_updated_at timestamptz,
    updated_at timestamptz not null default now()
);

create index if not exists sethistock_public_snapshots_updated_at_idx
    on public.sethistock_public_snapshots (updated_at desc);

alter table public.sethistock_public_snapshots enable row level security;

drop policy if exists "Public read SethiStock snapshots" on public.sethistock_public_snapshots;
create policy "Public read SethiStock snapshots"
    on public.sethistock_public_snapshots
    for select
    to anon, authenticated
    using (true);

insert into public.sethistock_public_snapshots (ticker, analysis_payload, analysis_updated_at, updated_at)
select distinct on (upper(ticker))
       upper(ticker), payload, cached_at, cached_at
from public.sethistock_cache
where namespace = 'stock_analysis' and ticker is not null and payload is not null
order by upper(ticker), cached_at desc
on conflict (ticker) do update
set analysis_payload = excluded.analysis_payload,
    analysis_updated_at = excluded.analysis_updated_at,
    updated_at = greatest(public.sethistock_public_snapshots.updated_at, excluded.updated_at);

insert into public.sethistock_public_snapshots (ticker, quote_payload, quote_updated_at, updated_at)
select distinct on (upper(ticker))
       upper(ticker), payload, cached_at, cached_at
from public.sethistock_cache
where namespace = 'quote' and ticker is not null and payload is not null
order by upper(ticker), cached_at desc
on conflict (ticker) do update
set quote_payload = excluded.quote_payload,
    quote_updated_at = excluded.quote_updated_at,
    updated_at = greatest(public.sethistock_public_snapshots.updated_at, excluded.updated_at);

insert into public.sethistock_public_snapshots (ticker, chart_payload, chart_updated_at, updated_at)
select distinct on (upper(ticker))
       upper(ticker), payload, cached_at, cached_at
from public.sethistock_cache
where namespace = 'chart'
  and ticker is not null
  and payload is not null
  and upper(cache_key) like '%:1Y:1D'
order by upper(ticker), cached_at desc
on conflict (ticker) do update
set chart_payload = excluded.chart_payload,
    chart_updated_at = excluded.chart_updated_at,
    updated_at = greatest(public.sethistock_public_snapshots.updated_at, excluded.updated_at);
