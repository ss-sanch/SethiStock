-- SethiPortfolio Phase 2A.1 benchmark methodology migration
-- Replaces price-only US indices with total-return equivalents so benchmark
-- comparisons include reinvested distributions, consistent with an accumulating portfolio holding.

update public.portfolio_benchmarks
set symbol = '^SP500TR',
    label = 'S&P 500 Total Return',
    currency = 'USD'
where symbol = '^GSPC';

update public.portfolio_benchmarks
set symbol = '^XNDX',
    label = 'Nasdaq-100 Total Return',
    currency = 'USD'
where symbol = '^NDX';

-- VWRL.L remains unchanged. yfinance auto-adjusted prices incorporate its
-- distributions when the performance endpoint downloads the series.
