from pathlib import Path

path = Path('app.py')
raw = path.read_bytes()
newline = '\r\n' if b'\r\n' in raw else '\n'
text = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')

text = text.replace('from datetime import datetime\n', 'from datetime import datetime, timezone, timedelta\n', 1)
text = text.replace('import math\n', 'import math\nimport copy\n', 1)

anchor = '''SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")

'''
if anchor not in text:
    raise SystemExit('Supabase configuration anchor not found')

cache_block = '''SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")

# --- SETHISTOCK PHASE 1D: PERSISTENT SUPABASE CACHE ---
SETHISTOCK_CACHE_TABLE = os.getenv("SETHISTOCK_CACHE_TABLE", "sethistock_cache")
SETHISTOCK_CACHE_VERSION = os.getenv("SETHISTOCK_CACHE_VERSION", "v1")
CACHE_TTL_QUOTE = int(os.getenv("SETHISTOCK_CACHE_TTL_QUOTE", "300"))
CACHE_TTL_PEERS = int(os.getenv("SETHISTOCK_CACHE_TTL_PEERS", "600"))
CACHE_TTL_STOCK = int(os.getenv("SETHISTOCK_CACHE_TTL_STOCK", "21600"))
CACHE_TTL_TICKER = int(os.getenv("SETHISTOCK_CACHE_TTL_TICKER", "2592000"))
CACHE_HTTP_TIMEOUT = float(os.getenv("SETHISTOCK_CACHE_HTTP_TIMEOUT", "2.5"))


def _cache_base_url():
    if not SUPABASE_URL:
        return None
    return SUPABASE_URL.replace("/rest/v1", "").rstrip("/")


def _cache_headers(prefer=None):
    if not SUPABASE_SERVICE_KEY:
        return None
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _cache_compound_key(namespace: str, identity: str):
    return f"{SETHISTOCK_CACHE_VERSION}:{namespace}:{str(identity).strip().upper()}"


def _cache_json_safe(value):
    if isinstance(value, dict):
        return {str(k): _cache_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_cache_json_safe(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _cache_lookup(namespace: str, identity: str):
    base_url = _cache_base_url()
    headers = _cache_headers()
    if not base_url or not headers:
        return None

    try:
        cache_key = _cache_compound_key(namespace, identity)
        response = requests.get(
            f"{base_url}/rest/v1/{SETHISTOCK_CACHE_TABLE}",
            headers=headers,
            params={
                "cache_key": f"eq.{cache_key}",
                "select": "payload,cached_at,expires_at",
                "limit": "1",
            },
            timeout=CACHE_HTTP_TIMEOUT,
        )
        if response.status_code >= 400:
            return None
        rows = response.json()
        if not rows:
            return None

        row = rows[0]
        expires_raw = row.get("expires_at")
        expires_at = datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00")) if expires_raw else None
        now = datetime.now(timezone.utc)
        return {
            "payload": row.get("payload"),
            "cached_at": row.get("cached_at"),
            "expires_at": expires_raw,
            "fresh": bool(expires_at and expires_at > now),
        }
    except Exception:
        return None


def _cache_get_fresh(namespace: str, identity: str):
    entry = _cache_lookup(namespace, identity)
    if entry and entry.get("fresh"):
        return entry.get("payload")
    return None


def _cache_write(namespace: str, identity: str, payload, ttl_seconds: int, ticker=None):
    base_url = _cache_base_url()
    headers = _cache_headers("resolution=merge-duplicates,return=minimal")
    if not base_url or not headers:
        return False

    try:
        now = datetime.now(timezone.utc)
        record = {
            "cache_key": _cache_compound_key(namespace, identity),
            "namespace": namespace,
            "ticker": str(ticker).upper() if ticker else None,
            "payload": _cache_json_safe(payload),
            "cached_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            "updated_at": now.isoformat(),
        }
        response = requests.post(
            f"{base_url}/rest/v1/{SETHISTOCK_CACHE_TABLE}",
            headers=headers,
            params={"on_conflict": "cache_key"},
            json=record,
            timeout=CACHE_HTTP_TIMEOUT,
        )
        return response.status_code < 400
    except Exception:
        return False


def _resolve_ticker_cached(query: str):
    normalized = str(query or "").strip()
    identity = normalized.upper()
    cached = _cache_get_fresh("ticker_resolution", identity)
    if isinstance(cached, dict) and cached.get("ticker"):
        return str(cached["ticker"]).upper()

    ticker = resolve_ticker(normalized)
    _cache_write(
        "ticker_resolution",
        identity,
        {"ticker": ticker},
        CACHE_TTL_TICKER,
        ticker=ticker,
    )
    return ticker


def _cache_probe():
    base_url = _cache_base_url()
    headers = _cache_headers()
    if not base_url or not headers:
        return False
    try:
        response = requests.get(
            f"{base_url}/rest/v1/{SETHISTOCK_CACHE_TABLE}",
            headers=headers,
            params={"select": "cache_key", "limit": "1"},
            timeout=CACHE_HTTP_TIMEOUT,
        )
        return response.status_code < 400
    except Exception:
        return False

'''
text = text.replace(anchor, cache_block, 1)

health_anchor = '''def health_check():
    return {"status": "SethiStock API is online."}

@app.get("/api/stock/{raw_ticker}")
'''
if health_anchor not in text:
    raise SystemExit('Health endpoint anchor not found')

health_replacement = '''def health_check():
    return {"status": "SethiStock API is online."}


@app.get("/api/cache/status")
def cache_status():
    service_role_configured = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)
    return {
        "configured": service_role_configured,
        "ready": service_role_configured and _cache_probe(),
        "table": SETHISTOCK_CACHE_TABLE,
        "version": SETHISTOCK_CACHE_VERSION,
        "ttl_seconds": {
            "quote": CACHE_TTL_QUOTE,
            "peer_snapshots": CACHE_TTL_PEERS,
            "stock_analysis": CACHE_TTL_STOCK,
            "ticker_resolution": CACHE_TTL_TICKER,
        },
    }


@app.get("/api/stock/{raw_ticker}")
'''
text = text.replace(health_anchor, health_replacement, 1)

stock_lookup_anchor = '''        ticker = resolve_ticker(raw_ticker)
        
        f_info, fin, cf, bs, info = None, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}
'''
if stock_lookup_anchor not in text:
    raise SystemExit('Stock ticker lookup anchor not found')

stock_lookup_replacement = '''        ticker = _resolve_ticker_cached(raw_ticker)

        if not is_peer:
            cached_analysis = _cache_get_fresh("stock_analysis", ticker)
            if isinstance(cached_analysis, dict):
                cached_analysis = copy.deepcopy(cached_analysis)
                try:
                    live_quote = get_stock_quote(ticker)
                    cached_analysis["ticker"] = str(live_quote.get("ticker", ticker)).upper()
                    cached_analysis["current_price"] = live_quote.get("current_price", cached_analysis.get("current_price", 0))
                    cached_analysis["change"] = live_quote.get("change", cached_analysis.get("change", 0))
                    cached_analysis["pct_change"] = live_quote.get("pct_change", cached_analysis.get("pct_change", 0))
                    if isinstance(cached_analysis.get("stats"), dict):
                        cached_analysis["stats"]["mkt_cap"] = live_quote.get("market_cap", cached_analysis["stats"].get("mkt_cap", "N/A"))
                except Exception:
                    pass
                return cached_analysis
        
        f_info, fin, cf, bs, info = None, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}
'''
text = text.replace(stock_lookup_anchor, stock_lookup_replacement, 1)

return_anchor = '''        return {
            "ticker": ticker.upper(), "current_price": round(current_price, 2),
            "change": round(change, 2), "pct_change": round(pct_change, 2),
            "shares": shares, "fcf": latest_fcf, "financials": fin_data, "stats": stats,
            "insiders": insider_list, "peers": peers,
            "summary": short_summary,
            "dupont_analysis": dupont_metrics,            
            "risk_profile": risk_metrics,                
            "sensitivity_matrix": sensitivity_matrix     
        }
'''
if return_anchor not in text:
    raise SystemExit('Stock return payload anchor not found')

return_replacement = '''        result = {
            "ticker": ticker.upper(), "current_price": round(current_price, 2),
            "change": round(change, 2), "pct_change": round(pct_change, 2),
            "shares": shares, "fcf": latest_fcf, "financials": fin_data, "stats": stats,
            "insiders": insider_list, "peers": peers,
            "summary": short_summary,
            "dupont_analysis": dupont_metrics,            
            "risk_profile": risk_metrics,                
            "sensitivity_matrix": sensitivity_matrix     
        }
        if not is_peer:
            _cache_write("stock_analysis", ticker, result, CACHE_TTL_STOCK, ticker=ticker)
        return result
'''
text = text.replace(return_anchor, return_replacement, 1)

quote_ticker_anchor = '''        ticker = resolve_ticker(raw_ticker).upper()
        stock = yf.Ticker(ticker)
'''
if quote_ticker_anchor not in text:
    raise SystemExit('Quote ticker anchor not found')

quote_ticker_replacement = '''        ticker = _resolve_ticker_cached(raw_ticker).upper()
        cached_quote = _cache_get_fresh("quote", ticker)
        if isinstance(cached_quote, dict):
            return cached_quote

        stock = yf.Ticker(ticker)
'''
text = text.replace(quote_ticker_anchor, quote_ticker_replacement, 1)

quote_return_anchor = '''        return {
            "ticker": ticker,
            "current_price": round(current_price, 2),
            "change": round(change, 2),
            "pct_change": round(pct_change, 2),
            "market_cap": _peer_format_market_cap(market_cap),
            "peers": _default_peers_for_ticker(ticker),
        }
'''
if quote_return_anchor not in text:
    raise SystemExit('Quote return payload anchor not found')

quote_return_replacement = '''        result = {
            "ticker": ticker,
            "current_price": round(current_price, 2),
            "change": round(change, 2),
            "pct_change": round(pct_change, 2),
            "market_cap": _peer_format_market_cap(market_cap),
            "peers": _default_peers_for_ticker(ticker),
        }
        _cache_write("quote", ticker, result, CACHE_TTL_QUOTE, ticker=ticker)
        return result
'''
text = text.replace(quote_return_anchor, quote_return_replacement, 1)

peers_anchor = '''    if len(symbols) > 8:
        raise HTTPException(status_code=400, detail="Peer snapshot supports up to 8 tickers per request.")

    results = []
'''
if peers_anchor not in text:
    raise SystemExit('Peer cache lookup anchor not found')

peers_replacement = '''    if len(symbols) > 8:
        raise HTTPException(status_code=400, detail="Peer snapshot supports up to 8 tickers per request.")

    cache_identity = ",".join(symbols)
    cached_peers = _cache_get_fresh("peer_snapshots", cache_identity)
    if isinstance(cached_peers, dict):
        return cached_peers

    results = []
'''
text = text.replace(peers_anchor, peers_replacement, 1)

peers_return_anchor = '''    return {"results": results, "count": len(results)}
'''
if peers_return_anchor not in text:
    raise SystemExit('Peer return payload anchor not found')

peers_return_replacement = '''    result = {"results": results, "count": len(results)}
    _cache_write("peer_snapshots", cache_identity, result, CACHE_TTL_PEERS)
    return result
'''
text = text.replace(peers_return_anchor, peers_return_replacement, 1)

if newline == '\r\n':
    text = text.replace('\n', '\r\n')
path.write_bytes(text.encode('utf-8'))
