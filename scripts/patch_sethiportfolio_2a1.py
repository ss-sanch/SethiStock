from pathlib import Path

p = Path('sethiportfolio.py')
s = p.read_text(encoding='utf-8')

old_download = '''    if isinstance(closes, pd.Series):\n        closes = closes.to_frame(name=symbols[0])\n    return closes.sort_index().ffill()\n'''
new_download = '''    if isinstance(closes, pd.Series):\n        closes = closes.to_frame(name=symbols[0])\n    # Keep the raw market calendar here. Individual valuation series are\n    # forward-filled only after the portfolio valuation calendar is chosen.\n    return closes.sort_index()\n'''
if old_download not in s:
    raise SystemExit('download block not found')
s = s.replace(old_download, new_download, 1)

old_calendar = '''    closes = _download_adjusted_close(download_symbols, inception)\n    trading_dates = closes.index\n'''
new_calendar = '''    closes = _download_adjusted_close(download_symbols, inception)\n\n    # Drive NAV dates from actual portfolio-security sessions rather than the\n    # union of US benchmarks, London securities and FX calendars. This avoids\n    # synthetic flat points caused solely by another market being open.\n    available_portfolio_symbols = [symbol for symbol in portfolio_symbols if symbol in closes.columns]\n    if available_portfolio_symbols:\n        trading_dates = closes[available_portfolio_symbols].dropna(how="all").index\n    else:\n        trading_dates = closes.dropna(how="all").index\n'''
if old_calendar not in s:
    raise SystemExit('calendar block not found')
s = s.replace(old_calendar, new_calendar, 1)

old_nav_loop = '''        nav = cash\n        for symbol, qty in quantities.items():\n            if not qty or symbol not in closes.columns or pd.isna(closes.loc[ts, symbol]):\n                continue\n            fx_series = _series_fx(closes, symbol_currency.get(symbol, base_currency), base_currency, trading_dates)\n            nav += qty * float(closes.loc[ts, symbol]) * float(fx_series.loc[ts])\n'''
new_nav_loop = '''        nav = cash\n        for symbol, qty in quantities.items():\n            if not qty or symbol not in closes.columns:\n                continue\n            price_series = closes[symbol].reindex(trading_dates).ffill()\n            if pd.isna(price_series.loc[ts]):\n                continue\n            fx_series = _series_fx(closes, symbol_currency.get(symbol, base_currency), base_currency, trading_dates)\n            nav += qty * float(price_series.loc[ts]) * float(fx_series.loc[ts])\n'''
if old_nav_loop not in s:
    raise SystemExit('nav loop block not found')
s = s.replace(old_nav_loop, new_nav_loop, 1)

old_rebase = '''    portfolio_index = [] if not nav_values or nav_values[0] == 0 else [round(100.0 * value / nav_values[0], 4) for value in nav_values]\n'''
new_rebase = '''    initial_capital = float(portfolio.get("initial_capital") or 0.0)\n    portfolio_index = (\n        []\n        if not nav_values or initial_capital == 0\n        else [round(100.0 * value / initial_capital, 4) for value in nav_values]\n    )\n'''
if old_rebase not in s:
    raise SystemExit('rebase block not found')
s = s.replace(old_rebase, new_rebase, 1)

old_method = '''        "method": "transaction_reconstructed_nav_with_fx",\n'''
new_method = '''        "method": "transaction_reconstructed_nav_with_fx_initial_capital_base",\n'''
if old_method not in s:
    raise SystemExit('method block not found')
s = s.replace(old_method, new_method, 1)

p.write_text(s, encoding='utf-8')
