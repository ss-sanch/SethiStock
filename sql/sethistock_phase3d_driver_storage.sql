-- SethiStock Phase 3D: persistent Company Driver histories
-- Backend-only storage. The service role is the only API role granted access.

create table if not exists public.sethistock_driver_observations (
    sync_id uuid not null,
    observation_key text not null,
    ticker text not null,
    metric text not null,
    history_version text not null,
    period text not null,
    filing_limit integer not null,
    period_type text,
    period_start date,
    period_end date,
    instant date,
    value numeric not null,
    unit_ref text,
    qualified_concept text,
    dimensions jsonb not null default '[]'::jsonb,
    form text,
    filing_date date,
    report_date date,
    accession text,
    source_url text,
    extraction_method text not null,
    score numeric,
    derived boolean not null default false,
    derivation jsonb,
    created_at timestamptz not null default now(),
    primary key (sync_id, observation_key),
    constraint sethistock_driver_observations_period_check
        check (period in ('quarterly', 'annual', 'reported')),
    constraint sethistock_driver_observations_filing_limit_check
        check (filing_limit between 1 and 32),
    constraint sethistock_driver_observations_date_check
        check (period_end is not null or instant is not null),
    constraint sethistock_driver_observations_dimensions_check
        check (jsonb_typeof(dimensions) = 'array'),
    constraint sethistock_driver_observations_derivation_check
        check (derivation is null or jsonb_typeof(derivation) = 'object')
);

create index if not exists sethistock_driver_observations_sync_period_idx
    on public.sethistock_driver_observations (sync_id, period_end asc, instant asc, period_start asc);

create index if not exists sethistock_driver_observations_lookup_idx
    on public.sethistock_driver_observations (ticker, metric, history_version, period, filing_limit, sync_id);

create table if not exists public.sethistock_driver_syncs (
    ticker text not null,
    metric text not null,
    history_version text not null,
    period text not null,
    filing_limit integer not null,
    active_sync_id uuid not null,
    source_mode text,
    data_state text not null,
    history_meta jsonb not null default '{}'::jsonb,
    observation_count integer not null default 0,
    coverage_start date,
    coverage_end date,
    source_latest_filing date,
    synced_at timestamptz not null,
    expires_at timestamptz not null,
    updated_at timestamptz not null default now(),
    primary key (ticker, metric, history_version, period, filing_limit),
    constraint sethistock_driver_syncs_period_check
        check (period in ('quarterly', 'annual', 'reported')),
    constraint sethistock_driver_syncs_filing_limit_check
        check (filing_limit between 1 and 32),
    constraint sethistock_driver_syncs_meta_check
        check (jsonb_typeof(history_meta) = 'object'),
    constraint sethistock_driver_syncs_count_check
        check (observation_count >= 0)
);

create index if not exists sethistock_driver_syncs_expires_at_idx
    on public.sethistock_driver_syncs (expires_at);

alter table public.sethistock_driver_observations enable row level security;
alter table public.sethistock_driver_syncs enable row level security;

revoke all on table public.sethistock_driver_observations from anon, authenticated;
revoke all on table public.sethistock_driver_syncs from anon, authenticated;

grant select, insert, update, delete on table public.sethistock_driver_observations to service_role;
grant select, insert, update, delete on table public.sethistock_driver_syncs to service_role;

comment on table public.sethistock_driver_observations is
    'Phase 3D backend-only verified Company Driver observations, staged by immutable sync ID.';

comment on table public.sethistock_driver_syncs is
    'Phase 3D active snapshot pointer and cache metadata for each ticker/metric/history/period/filing-depth key.';
