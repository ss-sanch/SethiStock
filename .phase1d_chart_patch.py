from pathlib import Path

path = Path('app.py')
raw = path.read_bytes()
newline = '\r\n' if b'\r\n' in raw else '\n'
text = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')

text = text.replace(
    'CACHE_TTL_QUOTE = int(os.getenv("SETHISTOCK_CACHE_TTL_QUOTE", "300"))\nCACHE_TTL_PEERS = int(os.getenv("SETHISTOCK_CACHE_TTL_PEERS", "600"))\n',
    'CACHE_TTL_QUOTE = int(os.getenv("SETHISTOCK_CACHE_TTL_QUOTE", "300"))\nCACHE_TTL_CHART = int(os.getenv("SETHISTOCK_CACHE_TTL_CHART", "300"))\nCACHE_TTL_PEERS = int(os.getenv("SETHISTOCK_CACHE_TTL_PEERS", "600"))\n',
    1,
)

text = text.replace(
    '            "quote": CACHE_TTL_QUOTE,\n            "peer_snapshots": CACHE_TTL_PEERS,\n',
    '            "quote": CACHE_TTL_QUOTE,\n            "chart": CACHE_TTL_CHART,\n            "peer_snapshots": CACHE_TTL_PEERS,\n',
    1,
)

old_chart = '''@app.get("/api/chart/{raw_ticker}")
def get_chart_data(raw_ticker: str, period: str = "1y", interval: str = "1d"):
    try:
        ticker = resolve_ticker(raw_ticker)
        
        hist = pd.DataFrame()
        try:
            stock = yf.Ticker(ticker.upper())
            hist = stock.history(period=period, interval=interval)
        except Exception:
            pass

        if hist is None or hist.empty: 
            return {"dates": [], "opens": [], "highs": [], "lows": [], "closes": []}
            
        if period == "max": hist = hist.loc['2000':] 
        return {
            "dates": hist.index.strftime('%Y-%m-%d %H:%M:%S').tolist(),
            "opens": hist['Open'].tolist(), "highs": hist['High'].tolist(),
            "lows": hist['Low'].tolist(), "closes": hist['Close'].tolist()
        }
'''
new_chart = '''@app.get("/api/chart/{raw_ticker}")
def get_chart_data(raw_ticker: str, period: str = "1y", interval: str = "1d"):
    try:
        ticker = _resolve_ticker_cached(raw_ticker)
        cache_identity = f"{ticker.upper()}:{period}:{interval}"
        cached_chart = _cache_get_fresh("chart", cache_identity)
        if isinstance(cached_chart, dict):
            return cached_chart
        
        hist = pd.DataFrame()
        try:
            stock = yf.Ticker(ticker.upper())
            hist = stock.history(period=period, interval=interval)
        except Exception:
            pass

        if hist is None or hist.empty: 
            return {"dates": [], "opens": [], "highs": [], "lows": [], "closes": []}
            
        if period == "max": hist = hist.loc['2000':]
        result = {
            "dates": hist.index.strftime('%Y-%m-%d %H:%M:%S').tolist(),
            "opens": hist['Open'].tolist(), "highs": hist['High'].tolist(),
            "lows": hist['Low'].tolist(), "closes": hist['Close'].tolist()
        }
        _cache_write("chart", cache_identity, result, CACHE_TTL_CHART, ticker=ticker)
        return result
'''
if old_chart not in text:
    raise SystemExit('Chart endpoint anchor not found')
text = text.replace(old_chart, new_chart, 1)

if newline == '\r\n':
    text = text.replace('\n', '\r\n')
path.write_bytes(text.encode('utf-8'))
