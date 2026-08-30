-- SethiPortfolio v1 schema
-- Run in the existing Supabase project's SQL editor.
-- Source-of-truth records are portfolios, instruments, transactions and journal entries.

create extension if not exists pgcrypto;

create table if not exists public.portfolios (
    id uuid primary key default gen_random_uuid(),
    slug text not null unique,
    name text not null,
    description text,
    inception_date date not null,
    base_currency text not null default 'GBP',
    initial_capital numeric(18,4) not null check (initial_capital >= 0),
    is_public boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.instruments (
    id uuid primary key default gen_random_uuid(),
    symbol text not null unique,
    name text not null,
    asset_type text not null default 'equity',
    currency text not null default 'USD',
    created_at timestamptz not null default now()
);

create table if not exists public.portfolio_transactions (
    id uuid primary key default gen_random_uuid(),
    portfolio_id uuid not null references public.portfolios(id) on delete cascade,
    instrument_id uuid not null references public.instruments(id),
    trade_date date not null,
    side text not null check (side in ('BUY', 'SELL')),
    quantity numeric(24,8) not null check (quantity > 0),
    price numeric(24,8) not null check (price >= 0),
    fees numeric(18,4) not null default 0 check (fees >= 0),
    currency text not null default 'USD',
    note text,
    created_at timestamptz not null default now()
);

create index if not exists portfolio_transactions_portfolio_date_idx
    on public.portfolio_transactions(portfolio_id, trade_date, created_at);

create table if not exists public.portfolio_benchmarks (
    id uuid primary key default gen_random_uuid(),
    portfolio_id uuid not null references public.portfolios(id) on delete cascade,
    symbol text not null,
    label text not null,
    is_primary boolean not null default false,
    display_order integer not null default 0,
    created_at timestamptz not null default now(),
    unique(portfolio_id, symbol)
);

create table if not exists public.portfolio_journal_entries (
    id uuid primary key default gen_random_uuid(),
    portfolio_id uuid not null references public.portfolios(id) on delete cascade,
    slug text not null,
    title text not null,
    summary text,
    body text not null,
    category text not null default 'Research Note',
    effective_date date not null,
    published_at timestamptz,
    related_transaction_id uuid references public.portfolio_transactions(id) on delete set null,
    is_published boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique(portfolio_id, slug)
);

-- Optional cache for later. The API currently reconstructs NAV directly from
-- immutable transactions; this table can store precomputed daily values once
-- the portfolio grows or scheduled refreshes are added.
create table if not exists public.portfolio_daily_values (
    portfolio_id uuid not null references public.portfolios(id) on delete cascade,
    value_date date not null,
    nav numeric(20,4) not null,
    cash numeric(20,4),
    calculated_at timestamptz not null default now(),
    primary key (portfolio_id, value_date)
);

-- Public website access is read-only. Writes should later go through the
-- authenticated FastAPI admin endpoints, never directly from public HTML.
alter table public.portfolios enable row level security;
alter table public.instruments enable row level security;
alter table public.portfolio_transactions enable row level security;
alter table public.portfolio_benchmarks enable row level security;
alter table public.portfolio_journal_entries enable row level security;
alter table public.portfolio_daily_values enable row level security;

-- These policies are intentionally conservative. If the server uses a
-- service-role key they are bypassed server-side. Do not expose that key in
-- frontend code.

-- Example portfolio setup (run only after choosing the actual S&P 500 ETF):
-- insert into public.portfolios
--   (slug, name, description, inception_date, base_currency, initial_capital, is_public)
-- values
--   ('fundamental', 'Fundamental Portfolio',
--    'Bottom-up equities focused on durable quality, valuation discipline and long-term capital allocation.',
--    '2026-01-01', 'GBP', 100000, true);
--
-- The inception date may be 1 Jan 2026 even though the initial ETF purchase
-- should use the first tradable market session and its actual execution price.
