from pathlib import Path

p = Path('app.py')
with open(p, 'r', encoding='utf-8', newline='') as f:
    s = f.read()
nl = '\r\n' if '\r\n' in s else '\n'

def native(text: str) -> str:
    return text.replace('\n', nl)

marker = native('''        # Annualised Volatility (Strict UK English convention enforced)
        annual_vol = strategy_returns.std() * np.sqrt(252)

        return {
            "status": "success",
            "results": {
                "kpis": {
                    "total_return": round(total_return * 100, 2),
                    "buy_hold_return": round(bh_return * 100, 2),
                    "max_drawdown": round(max_drawdown * 100, 2),
                    "annualised_volatility": round(annual_vol * 100, 2)
                },
                "chart": {
                    "dates": close_prices.index.strftime('%Y-%m-%d').tolist(),
                    "strategy_equity": strategy_equity.round(2).tolist(),
                    "buy_hold_equity": buy_hold_equity.round(2).tolist()
                }
            }
        }''')
replacement = native('''        # Annualised Volatility (Strict UK English convention enforced)
        annual_vol = strategy_returns.std() * np.sqrt(252)

        # Risk-adjusted performance. Cash is assumed to earn 0% in this educational backtest.
        daily_std = float(strategy_returns.std())
        sharpe_ratio = (float(strategy_returns.mean()) / daily_std) * np.sqrt(252) if daily_std > 0 else 0.0
        downside = strategy_returns[strategy_returns < 0]
        downside_std = float(downside.std()) if len(downside) > 1 else 0.0
        sortino_ratio = (float(strategy_returns.mean()) / downside_std) * np.sqrt(252) if downside_std > 0 else 0.0
        excess_return = total_return - bh_return
        time_in_market = float(signals.mean()) if len(signals) else 0.0

        # Build completed trade segments from the already look-ahead-safe position series.
        trade_rows = []
        active_start = None
        signal_values = signals.to_numpy(dtype=float)
        returns_values = daily_returns.to_numpy(dtype=float)
        dates_index = close_prices.index
        prices_values = close_prices.to_numpy(dtype=float)

        for i, position in enumerate(signal_values):
            is_active = position > 0.5
            if is_active and active_start is None:
                active_start = i
            is_last = i == len(signal_values) - 1
            if active_start is not None and ((not is_active) or is_last):
                end_i = i - 1 if not is_active else i
                segment = returns_values[active_start:end_i + 1]
                trade_return = float(np.prod(1.0 + segment) - 1.0) if len(segment) else 0.0
                entry_price_i = max(0, active_start - 1)
                exit_price_i = end_i
                trade_rows.append({
                    "entry_date": dates_index[entry_price_i].strftime('%Y-%m-%d'),
                    "exit_date": dates_index[exit_price_i].strftime('%Y-%m-%d'),
                    "entry_price": round(float(prices_values[entry_price_i]), 2),
                    "exit_price": round(float(prices_values[exit_price_i]), 2),
                    "return_pct": round(trade_return * 100.0, 2),
                    "holding_days": int(max(1, exit_price_i - entry_price_i)),
                })
                active_start = None

        completed_trades = len(trade_rows)
        winning_trades = sum(1 for row in trade_rows if row["return_pct"] > 0)
        win_rate = (winning_trades / completed_trades) if completed_trades else 0.0

        entries = [{"date": row["entry_date"], "price": row["entry_price"]} for row in trade_rows]
        exits = [{"date": row["exit_date"], "price": row["exit_price"]} for row in trade_rows]

        return {
            "status": "success",
            "results": {
                "kpis": {
                    "total_return": round(total_return * 100, 2),
                    "buy_hold_return": round(bh_return * 100, 2),
                    "excess_return_vs_buy_hold": round(excess_return * 100, 2),
                    "max_drawdown": round(max_drawdown * 100, 2),
                    "annualised_volatility": round(annual_vol * 100, 2),
                    "sharpe_ratio": round(sharpe_ratio, 2),
                    "sortino_ratio": round(sortino_ratio, 2),
                    "time_in_market_pct": round(time_in_market * 100, 1),
                    "completed_trades": completed_trades,
                    "win_rate_pct": round(win_rate * 100, 1)
                },
                "chart": {
                    "dates": close_prices.index.strftime('%Y-%m-%d').tolist(),
                    "strategy_equity": strategy_equity.round(2).tolist(),
                    "buy_hold_equity": buy_hold_equity.round(2).tolist(),
                    "drawdown_pct": (drawdown * 100.0).round(2).tolist(),
                    "entries": entries,
                    "exits": exits
                },
                "trades": trade_rows[-25:]
            }
        }''')

if marker not in s:
    raise SystemExit('Backtest KPI return marker not found')
s = s.replace(marker, replacement, 1)
with open(p, 'w', encoding='utf-8', newline='') as f:
    f.write(s)

required = ['sharpe_ratio', 'sortino_ratio', 'excess_return_vs_buy_hold', 'drawdown_pct', 'time_in_market_pct', 'win_rate_pct', '"trades": trade_rows[-25:]']
missing = [x for x in required if x not in s]
if missing:
    raise SystemExit(f'Missing Backtest V2 markers: {missing}')
print('Backtest 2.0 backend applied with original line endings preserved.')
