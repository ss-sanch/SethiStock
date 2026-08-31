from pathlib import Path

path = Path("stock_research.py")
text = path.read_text(encoding="utf-8")
old = '''        five_day_pos = min(reaction_pos + 4, len(prices) - 1)\n        five_day_close = _safe_float(prices["Close"].iloc[five_day_pos])\n'''
new = '''        five_day_pos = reaction_pos + 4\n        five_day_close = _safe_float(prices["Close"].iloc[five_day_pos]) if five_day_pos < len(prices) else None\n'''
if old not in text:
    raise RuntimeError("Could not locate five-day reaction calculation")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
