from pathlib import Path

path = Path('app.py')
raw = path.read_bytes()
newline = '\r\n' if b'\r\n' in raw else '\n'
text = raw.decode('utf-8').replace('\r\n', '\n')

def replace_once(old, new):
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'Expected one match, found {count}: {old[:100]!r}')
    text = text.replace(old, new, 1)

replace_once(
    'CACHE_STALE_TICKER = int(os.getenv("SETHISTOCK_CACHE_STALE_TICKER", "15552000"))\nCACHE_HTTP_TIMEOUT = float(os.getenv("SETHISTOCK_CACHE_HTTP_TIMEOUT", "2.5"))\n',
    'CACHE_STALE_TICKER = int(os.getenv("SETHISTOCK_CACHE_STALE_TICKER", "15552000"))\n'
    'CACHE_MAX_HEAVY_REFRESHES = max(1, int(os.getenv("SETHISTOCK_CACHE_MAX_HEAVY_REFRESHES", "1")))\n'
    'CACHE_HTTP_TIMEOUT = float(os.getenv("SETHISTOCK_CACHE_HTTP_TIMEOUT", "2.5"))\n'
)

replace_once(
    '    with _CACHE_REFRESH_LOCK:\n        if refresh_key in _CACHE_REFRESH_INFLIGHT:\n            return False\n        _CACHE_REFRESH_INFLIGHT.add(refresh_key)\n',
    '    with _CACHE_REFRESH_LOCK:\n'
    '        if refresh_key in _CACHE_REFRESH_INFLIGHT:\n'
    '            return False\n'
    '        if bypass_namespace == "stock_analysis":\n'
    '            heavy_inflight = sum(1 for key in _CACHE_REFRESH_INFLIGHT if key.startswith("stock_analysis:"))\n'
    '            if heavy_inflight >= CACHE_MAX_HEAVY_REFRESHES:\n'
    '                return False\n'
    '        _CACHE_REFRESH_INFLIGHT.add(refresh_key)\n'
)

replace_once(
    '        "refreshes_inflight": len(_CACHE_REFRESH_INFLIGHT),\n    }\n',
    '        "refreshes_inflight": len(_CACHE_REFRESH_INFLIGHT),\n'
    '        "max_heavy_refreshes": CACHE_MAX_HEAVY_REFRESHES,\n'
    '    }\n'
)

old_chart = '''@app.get("/api/chart/{raw_ticker}")
def get_chart_data(raw_ticker: str, period: str = "1y", interval: str = "1d", background_tasks: BackgroundTasks = None):
    try:
        ticker = _resolve_ticker_cached(raw_ticker, background_tasks)
        cache_identity = f"{ticker.upper()}:{period}:{interval}"
        cached_chart, cache_state = _cache_get_swr("chart", cache_identity, CACHE_STALE_CHART)
        if isinstance(cached_chart, dict):
            if cache_state == "stale":
                _schedule_cache_refresh(background_tasks, f"chart:{cache_identity}", "chart", get_chart_data, ticker, period, interval, None)
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
'''

new_chart = '''def _sanitize_chart_payload(payload):
    empty = {"dates": [], "opens": [], "highs": [], "lows": [], "closes": []}
    if not isinstance(payload, dict):
        return empty

    dates = payload.get("dates") or []
    opens = payload.get("opens") or []
    highs = payload.get("highs") or []
    lows = payload.get("lows") or []
    closes = payload.get("closes") or []
    size = min(len(dates), len(opens), len(highs), len(lows), len(closes))
    if size <= 0:
        return empty

    clean = {"dates": [], "opens": [], "highs": [], "lows": [], "closes": []}
    for i in range(size):
        close = _peer_safe_float(closes[i])
        if close is None or close <= 0:
            continue
        open_price = _peer_safe_float(opens[i], close)
        high = _peer_safe_float(highs[i], close)
        low = _peer_safe_float(lows[i], close)
        clean["dates"].append(dates[i])
        clean["opens"].append(open_price if open_price is not None else close)
        clean["highs"].append(high if high is not None else close)
        clean["lows"].append(low if low is not None else close)
        clean["closes"].append(close)
    return clean


@app.get("/api/chart/{raw_ticker}")
def get_chart_data(raw_ticker: str, period: str = "1y", interval: str = "1d", background_tasks: BackgroundTasks = None):
    try:
        ticker = _resolve_ticker_cached(raw_ticker, background_tasks)
        cache_identity = f"{ticker.upper()}:{period}:{interval}"
        cached_chart, cache_state = _cache_get_swr("chart", cache_identity, CACHE_STALE_CHART)
        if isinstance(cached_chart, dict):
            clean_cached_chart = _sanitize_chart_payload(cached_chart)
            if cache_state == "stale":
                _schedule_cache_refresh(background_tasks, f"chart:{cache_identity}", "chart", get_chart_data, ticker, period, interval, None)
            return clean_cached_chart

        hist = pd.DataFrame()
        try:
            stock = yf.Ticker(ticker.upper())
            hist = stock.history(period=period, interval=interval)
        except Exception:
            pass

        if hist is None or hist.empty:
            return {"dates": [], "opens": [], "highs": [], "lows": [], "closes": []}

        if period == "max":
            hist = hist.loc['2000':]

        result = _sanitize_chart_payload({
            "dates": hist.index.strftime('%Y-%m-%d %H:%M:%S').tolist(),
            "opens": hist['Open'].tolist(),
            "highs": hist['High'].tolist(),
            "lows": hist['Low'].tolist(),
            "closes": hist['Close'].tolist(),
        })
        if result["closes"]:
            _cache_write("chart", cache_identity, result, CACHE_TTL_CHART, ticker=ticker)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
'''
replace_once(old_chart, new_chart)

path.write_bytes(text.replace('\n', newline).encode('utf-8'))
