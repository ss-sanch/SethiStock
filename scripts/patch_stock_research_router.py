from pathlib import Path

path = Path("app.py")
data = path.read_bytes()
newline = b"\r\n" if b"\r\n" in data else b"\n"

if b"import stock_research" not in data:
    needle = b"import market_risk_lab" + newline
    replacement = needle + b"import stock_research" + newline
    if needle not in data:
        raise RuntimeError("Could not locate market_risk_lab import in app.py")
    data = data.replace(needle, replacement, 1)

if b"app.include_router(stock_research.router)" not in data:
    needle = b"app.include_router(market_risk_lab.router)" + newline
    replacement = needle + b"app.include_router(stock_research.router)" + newline
    if needle not in data:
        raise RuntimeError("Could not locate Market Risk Lab router mount in app.py")
    data = data.replace(needle, replacement, 1)

path.write_bytes(data)
