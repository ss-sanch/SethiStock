-- SethiStock Phase 2C: persistent normalized SEC fundamentals
-- Backend-only storage. The service role is the only API role granted access.

create table if not exists public.sethistock_fundamental_observations (
    sync_id uuid not null,
    observation_key text not null,
    ticker text not null,
    cik bigint not null,
    schema_version text not null,
    metric text not null,
    kind text not null,
    unit text not null,
    value numeric not null,
    period_start date,
    period_end date not null,
    filed date,
    fy integer,
    fp text,
    form text,
    frame text,
    accn text,
    taxonomy text,
    concept text,
    concept_rank integer,
    duration_days integer,
    derived boolean not null default false,
    formula text,
    components jsonb,
    source_concepts jsonb not null default '[]'::jsonb,
    source text not null,
    created_at timestamptz not null default now(),
    primary key (sync_id, observation_key),
    constraint sethistock_fundamental_observations_kind_check
        check (kind in ('flow', 'instant')),
    constraint sethistock_fundamental_observations_duration_check
        check (duration_days is null or duration_days >= 0),
    constraint sethistock_fundamental_observations_components_check
        check (components is null or jsonb_typeof(components) = 'array'),
    constraint sethistock_fundamental_observations_source_concepts_check
        check (jsonb_typeof(source_concepts) = 'array')
);

create index if not exists sethistock_fundamental_observations_sync_metric_idx
    on public.sethistock_fundamental_observations (sync_id, metric, period_end desc, period_start desc);

create index if not exists sethistock_fundamental_observations_ticker_schema_idx
    on public.sethistock_fundamental_observations (ticker, schema_version, sync_id);

create table if not exists public.sethistock_fundamental_syncs (
    ticker text not null,
    schema_version text not null,
    cik bigint not null,
    title text,
    entity_name text,
    active_sync_id uuid not null,
    source text not null,
    metric_manifest jsonb not null default '{}'::jsonb,
    observation_count integer not null default 0,
    earliest_end date,
    latest_end date,
    source_latest_filed date,
    synced_at timestamptz not null,
    expires_at timestamptz not null,
    updated_at timestamptz not null default now(),
    primary key (ticker, schema_version),
    constraint sethistock_fundamental_syncs_manifest_check
        check (jsonb_typeof(metric_manifest) = 'object'),
    constraint sethistock_fundamental_syncs_count_check
        check (observation_count >= 0)
);

create index if not exists sethistock_fundamental_syncs_expires_at_idx
    on public.sethistock_fundamental_syncs (expires_at);

alter table public.sethistock_fundamental_observations enable row level security;
alter table public.sethistock_fundamental_syncs enable row level security;

revoke all on table public.sethistock_fundamental_observations from anon, authenticated;
revoke all on table public.sethistock_fundamental_syncs from anon, authenticated;

grant select, insert, update, delete on table public.sethistock_fundamental_observations to service_role;
grant select, insert, update, delete on table public.sethistock_fundamental_syncs to service_role;

comment on table public.sethistock_fundamental_observations is
    'Phase 2C backend-only normalized SEC fundamental observations, staged by immutable sync ID.';

comment on table public.sethistock_fundamental_syncs is
    'Phase 2C active snapshot pointer and metadata for each ticker/schema version.';
