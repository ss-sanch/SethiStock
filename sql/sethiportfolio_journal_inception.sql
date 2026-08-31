-- SethiPortfolio Phase 2C: first published investment-journal entry.
-- Idempotent: safe to re-run. The entry is explicitly retrospective and
-- links to the opening VUAG transaction where available.

insert into public.portfolio_journal_entries (
    portfolio_id,
    slug,
    title,
    summary,
    body,
    category,
    effective_date,
    published_at,
    related_transaction_id,
    is_published
)
select
    p.id,
    'portfolio-inception-starting-from-the-benchmark',
    'Portfolio Inception: Starting From the Benchmark',
    'The portfolio begins fully indexed. Active positions must earn their allocation by offering a stronger risk-reward case than leaving that capital in the benchmark.',
    'This is a retrospective methodology note documenting how the notional Fundamental Portfolio has been constructed from inception. It was written after the 1 January 2026 effective date and should not be read as a contemporaneous publication from that date.\n\nStarting point\nThe portfolio begins with £100,000 of notional capital and is deliberately anchored to the S&P 500 rather than cash. The first investable session after New Year was 2 January 2026, when the portfolio was represented by 1,023 units of Vanguard S&P 500 UCITS ETF USD Accumulation (VUAG.L) at the portfolio''s recorded reference price of £97.66, leaving a small residual cash balance.\n\nWhy start indexed?\nBeginning in the benchmark creates a clear opportunity cost for every future stock decision. Buying an individual company is not treated as finding something better than cash; it means deliberately selling part of a diversified equity benchmark and accepting greater company-specific risk. That raises the hurdle for active allocation.\n\nDecision rule\nCapital should move away from the benchmark only when the investment case offers a sufficiently attractive combination of business quality, valuation, downside protection and expected return. If that case weakens, the default alternative remains the benchmark rather than forcing capital into another active idea.\n\nHow performance should be interpreted\nThe portfolio is transaction-backed and notional. Performance is reconstructed from the stored transaction history and market prices, with foreign-currency series translated into the portfolio''s GBP base currency. Benchmark comparisons are designed to show whether active decisions have added value relative to remaining passively invested, not to imply a realised personal-money track record.\n\nThis framework will also make later journal entries more useful: each material purchase, reduction or sale can be judged against the capital that would otherwise have remained in the benchmark.',
    'Methodology',
    '2026-01-01'::date,
    now(),
    (
        select t.id
        from public.portfolio_transactions t
        join public.instruments i on i.id = t.instrument_id
        where t.portfolio_id = p.id
          and i.symbol = 'VUAG.L'
          and t.side = 'BUY'
          and t.trade_date = '2026-01-02'::date
        order by t.created_at asc
        limit 1
    ),
    true
from public.portfolios p
where p.slug = 'fundamental'
on conflict (portfolio_id, slug) do update
set
    title = excluded.title,
    summary = excluded.summary,
    body = excluded.body,
    category = excluded.category,
    effective_date = excluded.effective_date,
    published_at = excluded.published_at,
    related_transaction_id = excluded.related_transaction_id,
    is_published = excluded.is_published,
    updated_at = now();
