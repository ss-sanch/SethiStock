from pathlib import Path

path = Path('app.py')
raw = path.read_bytes()
newline = '\r\n' if b'\r\n' in raw else '\n'
text = raw.decode('utf-8').replace('\r\n', '\n')


def replace_once(old, new):
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'Expected exactly one match, found {count}: {old[:120]!r}')
    text = text.replace(old, new, 1)

replace_once('from fastapi import FastAPI, HTTPException\n','from fastapi import FastAPI, HTTPException, BackgroundTasks\n')
replace_once('import copy\n','import copy\nimport threading\n')
replace_once(
    'CACHE_TTL_TICKER = int(os.getenv("SETHISTOCK_CACHE_TTL_TICKER", "2592000"))\nCACHE_HTTP_TIMEOUT = float(os.getenv("SETHISTOCK_CACHE_HTTP_TIMEOUT", "2.5"))\n',
    'CACHE_TTL_TICKER = int(os.getenv("SETHISTOCK_CACHE_TTL_TICKER", "2592000"))\n'
    'CACHE_STALE_QUOTE = int(os.getenv("SETHISTOCK_CACHE_STALE_QUOTE", "900"))\n'
    'CACHE_STALE_CHART = int(os.getenv("SETHISTOCK_CACHE_STALE_CHART", "1800"))\n'
    'CACHE_STALE_PEERS = int(os.getenv("SETHISTOCK_CACHE_STALE_PEERS", "3600"))\n'
    'CACHE_STALE_STOCK = int(os.getenv("SETHISTOCK_CACHE_STALE_STOCK", "86400"))\n'
    'CACHE_STALE_TICKER = int(os.getenv("SETHISTOCK_CACHE_STALE_TICKER", "15552000"))\n'
    'CACHE_HTTP_TIMEOUT = float(os.getenv("SETHISTOCK_CACHE_HTTP_TIMEOUT", "2.5"))\n\n'
    '_CACHE_REFRESH_LOCK = threading.Lock()\n'
    '_CACHE_REFRESH_INFLIGHT = set()\n'
    '_CACHE_REFRESH_CONTEXT = threading.local()\n'
)
replace_once(
    '        return {\n            "payload": row.get("payload"),\n            "cached_at": row.get("cached_at"),\n            "expires_at": expires_raw,\n            "fresh": bool(expires_at and expires_at > now),\n        }\n',
    '        fresh = bool(expires_at and expires_at > now)\n'
    '        stale_for_seconds = 0.0\n'
    '        if expires_at and not fresh:\n'
    '            stale_for_seconds = max(0.0, (now - expires_at).total_seconds())\n'
    '        return {\n'
    '            "payload": row.get("payload"),\n'
    '            "cached_at": row.get("cached_at"),\n'
    '            "expires_at": expires_raw,\n'
    '            "fresh": fresh,\n'
    '            "stale_for_seconds": stale_for_seconds,\n'
    '        }\n'
)
replace_once(
    'def _cache_get_fresh(namespace: str, identity: str):\n    entry = _cache_lookup(namespace, identity)\n    if entry and entry.get("fresh"):\n        return entry.get("payload")\n    return None\n\n\n',
    '''def _cache_refresh_bypassed(namespace: str):
    bypass = getattr(_CACHE_REFRESH_CONTEXT, "bypass_namespaces", set())
    return namespace in bypass


def _cache_get_fresh(namespace: str, identity: str):
    if _cache_refresh_bypassed(namespace):
        return None
    entry = _cache_lookup(namespace, identity)
    if entry and entry.get("fresh"):
        return entry.get("payload")
    return None


def _cache_get_swr(namespace: str, identity: str, max_stale_seconds: int):
    if _cache_refresh_bypassed(namespace):
        return None, "bypass"
    entry = _cache_lookup(namespace, identity)
    if not entry or entry.get("payload") is None:
        return None, "miss"
    if entry.get("fresh"):
        return entry.get("payload"), "fresh"
    stale_for = entry.get("stale_for_seconds")
    if stale_for is not None and stale_for <= max_stale_seconds:
        return entry.get("payload"), "stale"
    return None, "expired"


def _run_cache_refresh(refresh_key: str, bypass_namespace: str, refresh_callable, *args, **kwargs):
    previous_bypass = getattr(_CACHE_REFRESH_CONTEXT, "bypass_namespaces", set())
    previous_suppress = getattr(_CACHE_REFRESH_CONTEXT, "suppress_telemetry", False)
    _CACHE_REFRESH_CONTEXT.bypass_namespaces = set(previous_bypass) | {bypass_namespace}
    _CACHE_REFRESH_CONTEXT.suppress_telemetry = True
    try:
        refresh_callable(*args, **kwargs)
    except Exception:
        pass
    finally:
        _CACHE_REFRESH_CONTEXT.bypass_namespaces = previous_bypass
        _CACHE_REFRESH_CONTEXT.suppress_telemetry = previous_suppress
        with _CACHE_REFRESH_LOCK:
            _CACHE_REFRESH_INFLIGHT.discard(refresh_key)


def _schedule_cache_refresh(background_tasks, refresh_key: str, bypass_namespace: str, refresh_callable, *args, **kwargs):
    if background_tasks is None:
        return False
    with _CACHE_REFRESH_LOCK:
        if refresh_key in _CACHE_REFRESH_INFLIGHT:
            return False
        _CACHE_REFRESH_INFLIGHT.add(refresh_key)
    background_tasks.add_task(_run_cache_refresh, refresh_key, bypass_namespace, refresh_callable, *args, **kwargs)
    return True


'''
)
replace_once(
    'def _resolve_ticker_cached(query: str):\n    normalized = str(query or "").strip()\n    identity = normalized.upper()\n    cached = _cache_get_fresh("ticker_resolution", identity)\n    if isinstance(cached, dict) and cached.get("ticker"):\n        return str(cached["ticker"]).upper()\n\n    ticker = resolve_ticker(normalized)\n    _cache_write(\n        "ticker_resolution",\n        identity,\n        {"ticker": ticker},\n        CACHE_TTL_TICKER,\n        ticker=ticker,\n    )\n    return ticker\n',
    '''def _refresh_ticker_resolution(normalized: str, identity: str):
    ticker = resolve_ticker(normalized)
    _cache_write("ticker_resolution", identity, {"ticker": ticker}, CACHE_TTL_TICKER, ticker=ticker)
    return ticker


def _resolve_ticker_cached(query: str, background_tasks=None):
    normalized = str(query or "").strip()
    identity = normalized.upper()
    cached, cache_state = _cache_get_swr("ticker_resolution", identity, CACHE_STALE_TICKER)
    if isinstance(cached, dict) and cached.get("ticker"):
        if cache_state == "stale":
            _schedule_cache_refresh(background_tasks, f"ticker_resolution:{identity}", "ticker_resolution", _refresh_ticker_resolution, normalized, identity)
        return str(cached["ticker"]).upper()
    return _refresh_ticker_resolution(normalized, identity)
'''
)
replace_once(
    '            "ticker_resolution": CACHE_TTL_TICKER,\n        },\n    }\n',
    '            "ticker_resolution": CACHE_TTL_TICKER,\n        },\n'
    '        "stale_while_revalidate_seconds": {\n'
    '            "quote": CACHE_STALE_QUOTE,\n'
    '            "chart": CACHE_STALE_CHART,\n'
    '            "peer_snapshots": CACHE_STALE_PEERS,\n'
    '            "stock_analysis": CACHE_STALE_STOCK,\n'
    '            "ticker_resolution": CACHE_STALE_TICKER,\n'
    '        },\n'
    '        "refreshes_inflight": len(_CACHE_REFRESH_INFLIGHT),\n'
    '    }\n'
)
replace_once(
    'def get_stock_data(raw_ticker: str, is_peer: bool = False):\n    try:\n        if not is_peer:\n            log_telemetry_event(project="SethiStock", action="ticker_search", ticker=raw_ticker)\n            \n        ticker = _resolve_ticker_cached(raw_ticker)\n\n        if not is_peer:\n            cached_analysis = _cache_get_fresh("stock_analysis", ticker)\n            if isinstance(cached_analysis, dict):\n                cached_analysis = copy.deepcopy(cached_analysis)\n                try:\n                    live_quote = get_stock_quote(ticker)\n',
    '''def get_stock_data(raw_ticker: str, background_tasks: BackgroundTasks = None, is_peer: bool = False):
    try:
        if not is_peer and not getattr(_CACHE_REFRESH_CONTEXT, "suppress_telemetry", False):
            log_telemetry_event(project="SethiStock", action="ticker_search", ticker=raw_ticker)
        ticker = _resolve_ticker_cached(raw_ticker, background_tasks)
        if not is_peer:
            cached_analysis, cache_state = _cache_get_swr("stock_analysis", ticker, CACHE_STALE_STOCK)
            if isinstance(cached_analysis, dict):
                if cache_state == "stale":
                    _schedule_cache_refresh(background_tasks, f"stock_analysis:{ticker}", "stock_analysis", get_stock_data, ticker, None, False)
                cached_analysis = copy.deepcopy(cached_analysis)
                try:
                    live_quote = get_stock_quote(ticker, background_tasks)
'''
)
replace_once(
    'def get_stock_quote(raw_ticker: str):\n    """Fast above-the-fold quote used while the full SethiStock analysis loads."""\n    try:\n        ticker = _resolve_ticker_cached(raw_ticker).upper()\n        cached_quote = _cache_get_fresh("quote", ticker)\n        if isinstance(cached_quote, dict):\n            return cached_quote\n',
    '''def get_stock_quote(raw_ticker: str, background_tasks: BackgroundTasks = None):
    """Fast above-the-fold quote used while the full SethiStock analysis loads."""
    try:
        ticker = _resolve_ticker_cached(raw_ticker, background_tasks).upper()
        cached_quote, cache_state = _cache_get_swr("quote", ticker, CACHE_STALE_QUOTE)
        if isinstance(cached_quote, dict):
            if cache_state == "stale":
                _schedule_cache_refresh(background_tasks, f"quote:{ticker}", "quote", get_stock_quote, ticker, None)
            return cached_quote
'''
)
replace_once('def get_peer_snapshots(tickers: str):\n','def get_peer_snapshots(tickers: str, background_tasks: BackgroundTasks = None):\n')
replace_once(
    '    cached_peers = _cache_get_fresh("peer_snapshots", cache_identity)\n    if isinstance(cached_peers, dict):\n        return cached_peers\n',
    '''    cached_peers, cache_state = _cache_get_swr("peer_snapshots", cache_identity, CACHE_STALE_PEERS)
    if isinstance(cached_peers, dict):
        if cache_state == "stale":
            _schedule_cache_refresh(background_tasks, f"peer_snapshots:{cache_identity}", "peer_snapshots", get_peer_snapshots, cache_identity, None)
        return cached_peers
'''
)
replace_once(
    'def get_chart_data(raw_ticker: str, period: str = "1y", interval: str = "1d"):\n    try:\n        ticker = _resolve_ticker_cached(raw_ticker)\n        cache_identity = f"{ticker.upper()}:{period}:{interval}"\n        cached_chart = _cache_get_fresh("chart", cache_identity)\n        if isinstance(cached_chart, dict):\n            return cached_chart\n',
    '''def get_chart_data(raw_ticker: str, period: str = "1y", interval: str = "1d", background_tasks: BackgroundTasks = None):
    try:
        ticker = _resolve_ticker_cached(raw_ticker, background_tasks)
        cache_identity = f"{ticker.upper()}:{period}:{interval}"
        cached_chart, cache_state = _cache_get_swr("chart", cache_identity, CACHE_STALE_CHART)
        if isinstance(cached_chart, dict):
            if cache_state == "stale":
                _schedule_cache_refresh(background_tasks, f"chart:{cache_identity}", "chart", get_chart_data, ticker, period, interval, None)
            return cached_chart
'''
)
path.write_bytes(text.replace('\n', newline).encode('utf-8'))
