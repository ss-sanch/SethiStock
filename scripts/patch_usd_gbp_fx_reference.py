from pathlib import Path

p = Path('sethiportfolio.py')
s = p.read_text(encoding='utf-8')
old = '''def _fx_symbol(currency: str, base_currency: str) -> str | None:\n    currency = (currency or base_currency).upper()\n    base_currency = base_currency.upper()\n    return None if currency == base_currency else f"{currency}{base_currency}=X"\n'''
new = '''def _fx_symbol(currency: str, base_currency: str) -> str | None:\n    currency = (currency or base_currency).upper()\n    base_currency = base_currency.upper()\n    if currency == base_currency:\n        return None\n    # Yahoo's canonical USD-to-GBP series is GBP=X (GBP received per USD).\n    # USDGBP=X can return a differently aligned daily series, which caused\n    # historical trade-date FX references to pick the prior day's level.\n    if currency == "USD" and base_currency == "GBP":\n        return "GBP=X"\n    return f"{currency}{base_currency}=X"\n'''
if old not in s:
    raise SystemExit('FX symbol marker missing')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')
