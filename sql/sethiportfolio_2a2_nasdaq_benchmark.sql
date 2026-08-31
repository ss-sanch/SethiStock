-- SethiPortfolio Phase 2A.2 benchmark compatibility fix
-- Yahoo/yfinance does not expose reliable historical data for ^XNDX in this setup.
-- Use QQQ adjusted prices as an investable Nasdaq-100 total-return proxy.

update public.portfolio_benchmarks
set symbol = 'QQQ',
    label = 'Nasdaq-100 (QQQ Total Return Proxy)',
    currency = 'USD'
where symbol = '^XNDX';
