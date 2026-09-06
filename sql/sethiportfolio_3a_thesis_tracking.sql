-- SethiPortfolio Phase 3.1: structured thesis tracking
-- Run once in the existing Supabase SQL editor before enabling the Phase 3.1 frontend.
-- Existing portfolio, transaction and journal tables are not modified.

create table if not exists public.portfolio_theses (
    id uuid primary key default gen_random_uuid(),
    portfolio_id uuid not null references public.portfolios(id) on delete cascade,
    instrument_id uuid not null references public.instruments(id),
    title text not null,
    core_thesis text not null,
    catalysts text not null default '',
    key_risks text not null default '',
    invalidation_condition text not null,
    status text not null default 'ACTIVE'
        check (status in ('ACTIVE', 'WATCH', 'UNDER_REVIEW', 'INVALIDATED', 'CLOSED')),
    conviction integer not null default 3 check (conviction between 1 and 5),
    target_price numeric(24,8) check (target_price is null or target_price >= 0),
    target_currency text,
    opened_date date not null,
    next_review_date date,
    is_published boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists portfolio_theses_portfolio_status_idx
    on public.portfolio_theses(portfolio_id, status, opened_date desc);
create index if not exists portfolio_theses_instrument_idx
    on public.portfolio_theses(portfolio_id, instrument_id, opened_date desc);

create table if not exists public.portfolio_thesis_updates (
    id uuid primary key default gen_random_uuid(),
    thesis_id uuid not null references public.portfolio_theses(id) on delete cascade,
    effective_date date not null,
    status text not null
        check (status in ('ACTIVE', 'WATCH', 'UNDER_REVIEW', 'INVALIDATED', 'CLOSED')),
    conviction integer not null check (conviction between 1 and 5),
    summary text not null,
    evidence text not null default '',
    next_review_date date,
    is_published boolean not null default true,
    created_at timestamptz not null default now()
);

create index if not exists portfolio_thesis_updates_thesis_date_idx
    on public.portfolio_thesis_updates(thesis_id, effective_date desc, created_at desc);

alter table public.portfolio_theses enable row level security;
alter table public.portfolio_thesis_updates enable row level security;

-- Public readers only see explicitly published theses belonging to public portfolios.
drop policy if exists "Read published portfolio theses" on public.portfolio_theses;
create policy "Read published portfolio theses"
    on public.portfolio_theses for select
    using (
        is_published = true
        and exists (
            select 1 from public.portfolios p
            where p.id = portfolio_theses.portfolio_id and p.is_public = true
        )
    );

-- A thesis update is public only if both the update and its parent thesis are published.
drop policy if exists "Read published portfolio thesis updates" on public.portfolio_thesis_updates;
create policy "Read published portfolio thesis updates"
    on public.portfolio_thesis_updates for select
    using (
        is_published = true
        and exists (
            select 1
            from public.portfolio_theses t
            join public.portfolios p on p.id = t.portfolio_id
            where t.id = portfolio_thesis_updates.thesis_id
              and t.is_published = true
              and p.is_public = true
        )
    );

-- No INSERT/UPDATE/DELETE policies are created. Writes continue to use the
-- existing server-side service role plus X-Admin-Secret protection.
