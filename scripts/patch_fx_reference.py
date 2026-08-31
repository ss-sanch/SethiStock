from pathlib import Path

p=Path('sethiportfolio.py')
s=p.read_text(encoding='utf-8')
old='''    transactions = _transactions(portfolio["id"])\n    book = _derive_book(transactions, str(portfolio.get("base_currency") or "GBP").upper())\n    held_quantity = float((book.get(resolved_symbol) or {}).get("quantity") or 0.0)\n\n    return {\n'''
new='''    transactions = _transactions(portfolio["id"])\n    base_currency = str(portfolio.get("base_currency") or "GBP").upper()\n    book = _derive_book(transactions, base_currency)\n    held_quantity = float((book.get(resolved_symbol) or {}).get("quantity") or 0.0)\n\n    fx_rate_to_base = 1.0\n    fx_price_date = trade_date\n    fx_used_previous_session = False\n    fx_source_symbol = None\n    if normalized_currency != base_currency:\n        fx_source_symbol = _fx_symbol(normalized_currency, base_currency)\n        fx_ticker = yf.Ticker(fx_source_symbol)\n        try:\n            fx_history = fx_ticker.history(start=start.isoformat(), end=end.isoformat(), auto_adjust=False)\n        except Exception as exc:\n            raise HTTPException(status_code=502, detail=f"FX lookup failed for {normalized_currency}/{base_currency}: {exc}") from exc\n        if fx_history is None or fx_history.empty or "Close" not in fx_history:\n            raise HTTPException(status_code=404, detail=f"No FX reference rate is available for {normalized_currency}/{base_currency} around {trade_date.isoformat()}.")\n        fx_closes = fx_history["Close"].dropna()\n        fx_eligible = fx_closes[fx_closes.index.date <= trade_date]\n        if fx_eligible.empty:\n            raise HTTPException(status_code=404, detail=f"No FX reference rate is available on or before {trade_date.isoformat()} for {normalized_currency}/{base_currency}.")\n        fx_ts = fx_eligible.index[-1]\n        fx_rate_to_base = float(fx_eligible.iloc[-1])\n        fx_price_date = fx_ts.date()\n        fx_used_previous_session = fx_price_date != trade_date\n\n    return {\n'''
if old not in s: raise SystemExit('lookup return marker missing')
s=s.replace(old,new,1)
old='''        "held_quantity": held_quantity,\n        "source": "Yahoo Finance via yfinance",\n'''
new='''        "held_quantity": held_quantity,\n        "fx_rate_to_base": round(fx_rate_to_base, 8),\n        "fx_base_currency": base_currency,\n        "fx_price_date": fx_price_date.isoformat(),\n        "fx_used_previous_session": fx_used_previous_session,\n        "fx_source_symbol": fx_source_symbol,\n        "source": "Yahoo Finance via yfinance",\n'''
if old not in s: raise SystemExit('lookup response marker missing')
s=s.replace(old,new,1)
p.write_text(s,encoding='utf-8')
print('historical FX reference added')
