from pathlib import Path

p = Path('sethiportfolio.py')
s = p.read_text(encoding='utf-8')

# Restore the generic Yahoo symbol mapping for live valuation/performance. Historical
# transaction FX is handled separately below using an auditable central-bank source.
fx_start = s.index('def _fx_symbol(currency: str, base_currency: str) -> str | None:')
fx_end = s.index('\n\ndef _latest_market_data', fx_start)
fx_replacement = '''def _fx_symbol(currency: str, base_currency: str) -> str | None:\n    currency = (currency or base_currency).upper()\n    base_currency = base_currency.upper()\n    return None if currency == base_currency else f"{currency}{base_currency}=X"\n\n\ndef _historical_fx_reference(currency: str, base_currency: str, requested_date: date) -> tuple[float, date, str]:\n    \"\"\"Return an auditable daily FX reference for transaction accounting.\n\n    Historical transaction FX should not depend on Yahoo daily-bar timezone/close\n    conventions. For GBP-base portfolios, pin the Bank of England provider exposed\n    by Frankfurter. The API returns the provider date as well as the rate, so a\n    weekend/holiday fallback remains explicit in the ledger UI.\n    \"\"\"\n    currency = (currency or base_currency).upper()\n    base_currency = base_currency.upper()\n    if currency == base_currency:\n        return 1.0, requested_date, 'Identity'\n\n    provider = 'BOE' if base_currency == 'GBP' else 'ECB'\n    try:\n        response = requests.get(\n            f'https://api.frankfurter.dev/v2/rate/{currency}/{base_currency}',\n            params={'date': requested_date.isoformat(), 'providers': provider},\n            timeout=8,\n        )\n        if response.status_code >= 400:\n            raise ValueError(f'HTTP {response.status_code}: {response.text[:160]}')\n        data = response.json()\n        rate = float(data['rate'])\n        rate_date = date.fromisoformat(str(data['date']))\n        if rate <= 0:\n            raise ValueError('non-positive FX rate')\n    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:\n        raise HTTPException(\n            status_code=502,\n            detail=f'Historical FX reference unavailable for {currency}/{base_currency} on {requested_date.isoformat()}: {exc}',\n        ) from exc\n\n    return rate, rate_date, f'{provider} via Frankfurter'\n'''
s = s[:fx_start] + fx_replacement + s[fx_end:]

lookup_start = s.index('def admin_instrument_lookup(')
block_start = s.index('    fx_rate_to_base = 1.0\n', lookup_start)
block_end = s.index('\n\n    return {', block_start)
new_block = '''    fx_rate_to_base = 1.0\n    fx_price_date = trade_date\n    fx_used_previous_session = False\n    fx_source_symbol = None\n    fx_source = 'Identity'\n    if normalized_currency != base_currency:\n        fx_rate_to_base, fx_price_date, fx_source = _historical_fx_reference(\n            normalized_currency, base_currency, trade_date\n        )\n        fx_used_previous_session = fx_price_date != trade_date\n        fx_source_symbol = f'{normalized_currency}/{base_currency}'\n'''
s = s[:block_start] + new_block + s[block_end:]

source_line = '        "fx_source_symbol": fx_source_symbol,\n        "source": "Yahoo Finance via yfinance",\n'
replacement_line = '        "fx_source_symbol": fx_source_symbol,\n        "fx_source": fx_source,\n        "source": "Yahoo Finance via yfinance",\n'
if source_line not in s:
    raise SystemExit('instrument lookup response marker missing')
s = s.replace(source_line, replacement_line, 1)

p.write_text(s, encoding='utf-8')
