from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
import numpy as np 
import time
import requests
from bs4 import BeautifulSoup
import urllib.parse
import os
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone, timedelta
import math
import copy
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from scipy.stats import norm
from scipy.optimize import minimize
import market_risk_lab
import stock_research
import sec_fundamentals
import company_drivers
import sethiportfolio

# --- SUPABASE TELEMETRY ENGINE ---
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")

# --- SETHISTOCK PHASE 1D: PERSISTENT SUPABASE CACHE ---
SETHISTOCK_CACHE_TABLE = os.getenv("SETHISTOCK_CACHE_TABLE", "sethistock_cache")
SETHISTOCK_CACHE_VERSION = os.getenv("SETHISTOCK_CACHE_VERSION", "v1")
CACHE_TTL_QUOTE = int(os.getenv("SETHISTOCK_CACHE_TTL_QUOTE", "300"))
CACHE_TTL_CHART = int(os.getenv("SETHISTOCK_CACHE_TTL_CHART", "300"))
CACHE_TTL_PEERS = int(os.getenv("SETHISTOCK_CACHE_TTL_PEERS", "600"))
CACHE_TTL_STOCK = int(os.getenv("SETHISTOCK_CACHE_TTL_STOCK", "21600"))
CACHE_TTL_TICKER = int(os.getenv("SETHISTOCK_CACHE_TTL_TICKER", "2592000"))
CACHE_STALE_QUOTE = int(os.getenv("SETHISTOCK_CACHE_STALE_QUOTE", "900"))
CACHE_STALE_CHART = int(os.getenv("SETHISTOCK_CACHE_STALE_CHART", "1800"))
CACHE_STALE_PEERS = int(os.getenv("SETHISTOCK_CACHE_STALE_PEERS", "3600"))
CACHE_STALE_STOCK = int(os.getenv("SETHISTOCK_CACHE_STALE_STOCK", "86400"))
CACHE_STALE_TICKER = int(os.getenv("SETHISTOCK_CACHE_STALE_TICKER", "15552000"))
CACHE_MAX_HEAVY_REFRESHES = max(1, int(os.getenv("SETHISTOCK_CACHE_MAX_HEAVY_REFRESHES", "1")))
CACHE_HTTP_TIMEOUT = float(os.getenv("SETHISTOCK_CACHE_HTTP_TIMEOUT", "2.5"))
PROCESS_STARTED_AT = time.time()
STOCK_SOURCE_BUDGET_SECONDS = max(5.0, float(os.getenv("SETHISTOCK_SOURCE_BUDGET_SECONDS", "12")))

_CACHE_REFRESH_LOCK = threading.Lock()
_CACHE_REFRESH_INFLIGHT = set()
_CACHE_REFRESH_CONTEXT = threading.local()
STOCK_ANALYSIS_MAX_CONCURRENT = max(1, int(os.getenv("SETHISTOCK_MAX_STOCK_ANALYSES", "1")))
STOCK_ANALYSIS_QUEUE_TIMEOUT = max(1.0, float(os.getenv("SETHISTOCK_ANALYSIS_QUEUE_TIMEOUT", "20")))
_STOCK_ANALYSIS_GATE = threading.BoundedSemaphore(STOCK_ANALYSIS_MAX_CONCURRENT)
_STOCK_ANALYSIS_STATE_LOCK = threading.Lock()
_STOCK_ANALYSIS_ACTIVE = 0
_STOCK_ANALYSIS_PEAK = 0
_STOCK_ANALYSIS_TOTAL = 0
_STOCK_ANALYSIS_ACTIVE_TICKERS = {}


def _process_rss_mb():
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except Exception:
        return None
    return None


def _stock_analysis_started(ticker):
    global _STOCK_ANALYSIS_ACTIVE, _STOCK_ANALYSIS_PEAK, _STOCK_ANALYSIS_TOTAL
    symbol = str(ticker or "").upper()
    with _STOCK_ANALYSIS_STATE_LOCK:
        _STOCK_ANALYSIS_ACTIVE += 1
        _STOCK_ANALYSIS_TOTAL += 1
        _STOCK_ANALYSIS_PEAK = max(_STOCK_ANALYSIS_PEAK, _STOCK_ANALYSIS_ACTIVE)
        _STOCK_ANALYSIS_ACTIVE_TICKERS[symbol] = _STOCK_ANALYSIS_ACTIVE_TICKERS.get(symbol, 0) + 1


def _stock_analysis_finished(ticker):
    global _STOCK_ANALYSIS_ACTIVE
    symbol = str(ticker or "").upper()
    with _STOCK_ANALYSIS_STATE_LOCK:
        _STOCK_ANALYSIS_ACTIVE = max(0, _STOCK_ANALYSIS_ACTIVE - 1)
        count = _STOCK_ANALYSIS_ACTIVE_TICKERS.get(symbol, 0) - 1
        if count > 0:
            _STOCK_ANALYSIS_ACTIVE_TICKERS[symbol] = count
        else:
            _STOCK_ANALYSIS_ACTIVE_TICKERS.pop(symbol, None)


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
        fresh = bool(expires_at and expires_at > now)
        stale_for_seconds = 0.0
        if expires_at and not fresh:
            stale_for_seconds = max(0.0, (now - expires_at).total_seconds())
        return {
            "payload": row.get("payload"),
            "cached_at": row.get("cached_at"),
            "expires_at": expires_raw,
            "fresh": fresh,
            "stale_for_seconds": stale_for_seconds,
        }
    except Exception:
        return None


def _cache_refresh_bypassed(namespace: str):
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
        if bypass_namespace == "stock_analysis":
            heavy_inflight = sum(1 for key in _CACHE_REFRESH_INFLIGHT if key.startswith("stock_analysis:"))
            if heavy_inflight >= CACHE_MAX_HEAVY_REFRESHES:
                return False
        _CACHE_REFRESH_INFLIGHT.add(refresh_key)
    background_tasks.add_task(_run_cache_refresh, refresh_key, bypass_namespace, refresh_callable, *args, **kwargs)
    return True


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


def _refresh_ticker_resolution(normalized: str, identity: str):
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

class TelemetryPayload(BaseModel):
    project: str
    action: str
    ticker: Optional[str] = None
    visitor_id: Optional[str] = None # <-- NEW: Accept the anonymous ID

class LoadTimingPayload(BaseModel):
    ticker: str
    quote_ms: Optional[int] = None
    full_ms: Optional[int] = None
    backend_ms: Optional[int] = None
    cache_hit: Optional[bool] = None
    status: str = "full"
    visitor_id: Optional[str] = None
    error_code: Optional[str] = None
    server_wake_ms: Optional[int] = None
    server_uptime_s: Optional[int] = None
    server_state: Optional[str] = None
def log_telemetry_event(project: str, action: str, ticker: Optional[str] = None, visitor_id: Optional[str] = None):
    """Silently logs user interactions to Supabase without blocking requests."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
        
    try:
        clean_url = SUPABASE_URL.replace("/rest/v1", "").rstrip("/")
        url = f"{clean_url}/rest/v1/traffic_logs"
        
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }
        payload = {
            "project": project,
            "action": action,
            "ticker": ticker.upper() if ticker else None,
            "visitor_id": visitor_id # <-- NEW: Pass to the database
        }
        
        requests.post(url, json=payload, headers=headers, timeout=5)
            
    except Exception as e:
        pass # Fail silently in production

app = FastAPI(title="SethiStock Data Engine") # <--- THIS IS THE MISSING LINE!

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=False, 
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market_risk_lab.router)
app.include_router(stock_research.router)
app.include_router(sec_fundamentals.router)
app.include_router(company_drivers.router)
sethiportfolio.configure_supabase(SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_KEY, ADMIN_SECRET)
app.include_router(sethiportfolio.router)

def resolve_ticker(query: str):
    query = query.strip()
    try:
        # Ping Yahoo's lightweight search directory to translate names to tickers
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(query)}&quotesCount=1&newsCount=0"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        
        res = requests.get(url, headers=headers, timeout=3)
        data = res.json()
        
        # If it finds a match, return the official symbol (e.g., APPLE -> AAPL)
        if 'quotes' in data and len(data['quotes']) > 0:
            return data['quotes'][0]['symbol']
    except Exception as e:
        print(f"Ticker resolution failed: {e}")
        pass
        
    # If the search fails for any reason, fallback to exactly what the user typed
    return query.upper()

@app.get("/autocomplete")
def autocomplete_ticker(q: str):
    try:
        import requests
        # Instantly taps Yahoo's global search engine
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=5&newsCount=0"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = requests.get(url, headers=headers)
        quotes = res.json().get('quotes', [])
        
        # Filter for actual equities and clean up the data
        results = [{"symbol": item["symbol"], "name": item.get("shortname", "Unknown")} for item in quotes if item.get("quoteType") in ["EQUITY", "ETF"]]
        return {"results": results}
    except Exception:
        return {"results": []}
# ==========================================
# --- NEW: FINVIZ HTML SCRAPER ENGINE ---
# ==========================================

def scrape_finviz_data(ticker: str):
    """Surgically extracts Proprietary Stats, Profile, and Insider Trading directly from Finviz"""
    finviz_stats = {}
    finviz_insiders = []
    company_summary = "Company profile not currently available."
    
    try:
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        # Heavily disguised browser headers to bypass Finviz bot-blocks
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Upgrade-Insecure-Requests": "1"
        }
        
        response = requests.get(url, headers=headers, timeout=3)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 1. BULLETPROOF STATS EXTRACTION 
            # Hunts exactly for the text labels, completely ignoring Finviz's CSS class changes
            targets = ["Forward P/E", "Short Float", "Earnings", "Dividend Ex-Date"]
            for td in soup.find_all('td'):
                txt = td.text.strip()
                if txt in targets:
                    nxt = td.find_next_sibling('td')
                    if nxt:
                        finviz_stats[txt] = nxt.text.strip()
                        
            # 2. Scrape the Company Overview / Summary
            try:
                profile_box = soup.find('td', class_='fullview-profile')
                if profile_box:
                    company_summary = profile_box.text.strip()
            except Exception:
                pass
                
            # 3. Scrape the Insider Trading Table
            try:
                insider_table = soup.find('table', class_='body-table')
                if insider_table:
                    rows = insider_table.find_all('tr')[1:] # Skip header
                    for row in rows[:15]: # Grab top 15 trades
                        cols = row.find_all('td')
                        if len(cols) >= 9:
                            name = cols[0].text.strip()
                            position = cols[1].text.strip()
                            transaction_text = cols[3].text.strip()
                            shares_str = cols[5].text.strip().replace(',', '')
                            value_str = cols[7].text.strip().replace(',', '')
                            
                            action = "Buy" if "Buy" in transaction_text else "Sell" if "Sale" in transaction_text else "Execute/Other"
                            shares = float(shares_str) if shares_str.replace('.','').isdigit() else 0
                            value = float(value_str) if value_str.replace('.','').isdigit() else 0
                            
                            if shares > 0: # Only append valid trades
                                finviz_insiders.append({
                                    "name": name,
                                    "position": position,
                                    "transaction": action,
                                    "shares": shares,
                                    "value": value
                                })
            except Exception:
                pass
                
    except Exception:
        pass
        
    return finviz_stats, finviz_insiders, company_summary

# ==========================================
# --- QUANTITATIVE ENGINES ---
# ==========================================

def calculate_dupont_analysis(income_stmt, balance_sheet):
    try:
        net_income = income_stmt.loc['Net Income'].iloc[0] if 'Net Income' in income_stmt.index else income_stmt.loc['Net Income Common Stockholders'].iloc[0]
        revenue = income_stmt.loc['Total Revenue'].iloc[0] if 'Total Revenue' in income_stmt.index else income_stmt.loc['Operating Revenue'].iloc[0]
        total_assets = balance_sheet.loc['Total Assets'].iloc[0]
        
        if 'Stockholders Equity' in balance_sheet.index:
            total_equity = balance_sheet.loc['Stockholders Equity'].iloc[0]
        elif 'Total Equity Gross Minority Interest' in balance_sheet.index:
            total_equity = balance_sheet.loc['Total Equity Gross Minority Interest'].iloc[0]
        else:
            total_equity = balance_sheet.loc['Common Stock Equity'].iloc[0]

        net_profit_margin = net_income / revenue
        asset_turnover = revenue / total_assets
        equity_multiplier = total_assets / total_equity
        
        roe = net_profit_margin * asset_turnover * equity_multiplier
        
        return {
            "net_profit_margin": round(net_profit_margin * 100, 2),
            "asset_turnover": round(asset_turnover, 2),
            "equity_multiplier": round(equity_multiplier, 2),
            "calculated_roe": round(roe * 100, 2)
        }
    except Exception:
        return {"error": "DuPont Data Unavailable"}

def calculate_risk_profile(hist):
    try:
        if hist is None or hist.empty or 'Close' not in hist.columns:
            return {"error": "Risk Profile Unavailable"}

        daily_returns = hist['Close'].pct_change().dropna()
        if daily_returns.empty:
            return {"error": "Risk Profile Unavailable"}

        var_95 = np.percentile(daily_returns, 5)
        volatility = daily_returns.std() * np.sqrt(252)

        return {
            "daily_var_95": round(var_95 * 100, 2),
            "annualized_volatility": round(volatility * 100, 2)
        }
    except Exception:
        return {"error": "Risk Profile Unavailable"}


def generate_sensitivity_matrix(base_wacc, base_exit_multiple, fcf_projections, shares_outstanding):
    try:
        wacc_steps = [base_wacc - 0.02, base_wacc - 0.01, base_wacc, base_wacc + 0.01, base_wacc + 0.02]
        mult_steps = [base_exit_multiple - 2, base_exit_multiple - 1, base_exit_multiple, base_exit_multiple + 1, base_exit_multiple + 2]
        
        matrix = []
        for wacc in wacc_steps:
            row = []
            for mult in mult_steps:
                pv_fcfs = sum([fcf / ((1 + wacc) ** i) for i, fcf in enumerate(fcf_projections, 1)])
                terminal_value = fcf_projections[-1] * mult
                pv_tv = terminal_value / ((1 + wacc) ** len(fcf_projections))
                implied_price = (pv_fcfs + pv_tv) / (shares_outstanding if shares_outstanding else 1)
                row.append(round(implied_price, 2))
            matrix.append({f"WACC_{round(wacc*100, 1)}%": row})
        return matrix
    except Exception:
        return []

# ==========================================
# --- MAIN API ENDPOINTS -------------------
# ==========================================

@app.get("/")
@app.head("/")
def health_check():
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
            "chart": CACHE_TTL_CHART,
            "peer_snapshots": CACHE_TTL_PEERS,
            "stock_analysis": CACHE_TTL_STOCK,
            "ticker_resolution": CACHE_TTL_TICKER,
        },
        "stale_while_revalidate_seconds": {
            "quote": CACHE_STALE_QUOTE,
            "chart": CACHE_STALE_CHART,
            "peer_snapshots": CACHE_STALE_PEERS,
            "stock_analysis": CACHE_STALE_STOCK,
            "ticker_resolution": CACHE_STALE_TICKER,
        },
        "refreshes_inflight": len(_CACHE_REFRESH_INFLIGHT),
        "max_heavy_refreshes": CACHE_MAX_HEAVY_REFRESHES,
    }


@app.get("/api/runtime/status")
def runtime_status():
    with _STOCK_ANALYSIS_STATE_LOCK:
        active = _STOCK_ANALYSIS_ACTIVE
        peak = _STOCK_ANALYSIS_PEAK
        total = _STOCK_ANALYSIS_TOTAL
        tickers = dict(_STOCK_ANALYSIS_ACTIVE_TICKERS)
    return {
        "rss_mb": _process_rss_mb(),
        "stock_analyses_active": active,
        "stock_analyses_peak": peak,
        "stock_analyses_total": total,
        "active_tickers": tickers,
        "max_concurrent_stock_analyses": STOCK_ANALYSIS_MAX_CONCURRENT,
        "analysis_queue_timeout_seconds": STOCK_ANALYSIS_QUEUE_TIMEOUT,
        "source_budget_seconds": STOCK_SOURCE_BUDGET_SECONDS,
        "cache_refreshes_inflight": len(_CACHE_REFRESH_INFLIGHT),
        "process_uptime_seconds": round(max(0.0, time.time() - PROCESS_STARTED_AT), 1),
    }

def _analysis_cache_payload_valid(payload):
    if not isinstance(payload, dict):
        return False
    try:
        cached_price = float(payload.get("current_price", 0))
        if not np.isfinite(cached_price) or cached_price <= 0:
            return False
    except Exception:
        return False
    stats = payload.get("stats")
    if isinstance(stats, dict):
        for value in stats.values():
            if isinstance(value, str) and ("nan" in value.lower() or "inf" in value.lower()):
                return False

    # Phase 4A initial-dashboard payload contract. Old six-hour analysis cache rows
    # predate these fields; treating them as invalid refreshes only stock_analysis
    # while preserving the independent quote/chart/ticker caches.
    financials = payload.get("financials")
    research = payload.get("research")
    chart_preview = payload.get("chart_preview")
    if not isinstance(financials, dict) or not isinstance(financials.get("ebitda"), list):
        return False
    if not isinstance(research, dict) or "valuation_bands" not in research or "earnings_reaction" not in research:
        return False
    if not isinstance(chart_preview, dict) or not isinstance(chart_preview.get("closes"), list):
        return False
    return True


@app.get("/api/stock/{raw_ticker}")
def get_stock_data(raw_ticker: str, background_tasks: BackgroundTasks = None, is_peer: bool = False):
    try:
        analysis_started_at = time.perf_counter()

        if not is_peer and not getattr(_CACHE_REFRESH_CONTEXT, "suppress_telemetry", False):
            log_telemetry_event(project="SethiStock", action="ticker_search", ticker=raw_ticker)
        ticker = _resolve_ticker_cached(raw_ticker, background_tasks)
        if not is_peer:
            cached_analysis, cache_state = _cache_get_swr("stock_analysis", ticker, CACHE_STALE_STOCK)
            if _analysis_cache_payload_valid(cached_analysis):
                should_refresh_analysis = cache_state == "stale"
                cached_analysis = copy.deepcopy(cached_analysis)
                try:
                    live_quote = get_stock_quote(ticker, background_tasks)
                    cached_analysis["ticker"] = str(live_quote.get("ticker", ticker)).upper()
                    cached_analysis["current_price"] = live_quote.get("current_price", cached_analysis.get("current_price", 0))
                    cached_analysis["change"] = live_quote.get("change", cached_analysis.get("change", 0))
                    cached_analysis["pct_change"] = live_quote.get("pct_change", cached_analysis.get("pct_change", 0))
                    if isinstance(cached_analysis.get("stats"), dict):
                        cached_analysis["stats"]["mkt_cap"] = live_quote.get("market_cap", cached_analysis["stats"].get("mkt_cap", "N/A"))
                except Exception:
                    pass
                if should_refresh_analysis:
                    _schedule_cache_refresh(background_tasks, f"stock_analysis:{ticker}", "stock_analysis", get_stock_data, ticker, None, False)
                meta = cached_analysis.get("_meta") if isinstance(cached_analysis.get("_meta"), dict) else {}
                meta["served_from_cache"] = True
                meta["request_ms"] = round((time.perf_counter() - analysis_started_at) * 1000)
                cached_analysis["_meta"] = meta
                return cached_analysis
        
        analysis_slot_acquired = False
        if not is_peer:
            if not _STOCK_ANALYSIS_GATE.acquire(timeout=STOCK_ANALYSIS_QUEUE_TIMEOUT):
                raise HTTPException(status_code=503, detail="SethiStock analysis is busy. Please retry shortly.")
            analysis_slot_acquired = True
            _stock_analysis_started(ticker)

            # A previous queued request may have populated this exact ticker while we waited.
            queued_cached, _queued_state = _cache_get_swr("stock_analysis", ticker, CACHE_STALE_STOCK)
            if _analysis_cache_payload_valid(queued_cached):
                queued_cached = copy.deepcopy(queued_cached)
                meta = queued_cached.get("_meta") if isinstance(queued_cached.get("_meta"), dict) else {}
                meta["served_from_cache"] = True
                meta["request_ms"] = round((time.perf_counter() - analysis_started_at) * 1000)
                queued_cached["_meta"] = meta
                _stock_analysis_finished(ticker)
                _STOCK_ANALYSIS_GATE.release()
                analysis_slot_acquired = False
                return queued_cached

        f_info, fin, cf, bs, info = None, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}
        q_fin = pd.DataFrame() 
        
        # 1. NATIVE MARKET-DATA FETCH
        # Cold-cache requests used to perform Yahoo statements, quote-summary, history
        # and Finviz calls serially. These are network-bound, so run the independent
        # groups concurrently and keep the initial analysis latency closer to the slowest
        # upstream call rather than the sum of every call.
        source_timeouts = []
        source_errors = {}

        def _fetch_statement_bundle():
            local_stock = yf.Ticker(ticker)
            local_fin, local_cf, local_bs, local_q_fin = pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
            try: local_fin = local_stock.financials
            except Exception: pass
            try: local_cf = local_stock.cashflow
            except Exception: pass
            try: local_bs = local_stock.balance_sheet
            except Exception: pass
            try: local_q_fin = local_stock.quarterly_financials
            except Exception: pass
            return local_fin, local_cf, local_bs, local_q_fin

        def _fetch_info_and_finviz():
            local_stock = yf.Ticker(ticker)
            local_fast, local_info = None, {}
            try: local_fast = local_stock.fast_info
            except Exception: pass
            try:
                fetched = local_stock.info
                if fetched: local_info = fetched
            except Exception:
                pass
            try:
                local_fv = scrape_finviz_data(ticker)
            except Exception:
                local_fv = ({}, [], "Company profile not currently available.")
            return local_fast, local_info, local_fv

        def _fetch_history_bundle():
            local_stock = yf.Ticker(ticker)
            try:
                return local_stock.history(period="5y", interval="1d")
            except Exception:
                return pd.DataFrame()

        # A single slow Yahoo/Finviz source must never own the only analysis slot forever.
        # Collect what finishes inside one shared budget and continue with safe fallbacks.
        source_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="sethistock-core")
        source_futures = {
            "statements": source_executor.submit(_fetch_statement_bundle),
            "profile": source_executor.submit(_fetch_info_and_finviz),
            "history": source_executor.submit(_fetch_history_bundle),
        }
        done, _not_done = wait(source_futures.values(), timeout=STOCK_SOURCE_BUDGET_SECONDS)
        source_results = {}
        for source_name, future in source_futures.items():
            if future in done:
                try:
                    source_results[source_name] = future.result()
                except Exception as exc:
                    source_errors[source_name] = type(exc).__name__
            else:
                source_timeouts.append(source_name)
                future.cancel()

        # Do not wait for a late network worker during request teardown.
        source_executor.shutdown(wait=False, cancel_futures=True)

        fin, cf, bs, q_fin = source_results.get(
            "statements",
            (pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()),
        )
        f_info, info, finviz_bundle = source_results.get(
            "profile",
            (None, {}, ({}, [], "Company profile not currently available.")),
        )
        try:
            fv_stats, fv_insiders, fv_summary = finviz_bundle
        except Exception:
            fv_stats, fv_insiders, fv_summary = {}, [], "Company profile not currently available."
        shared_hist = source_results.get("history", pd.DataFrame())
        # Yahoo can append an incomplete current-session row with a null Close.
        # Drop it before price, risk and technical calculations so bad rows cannot poison the 6h analysis cache.
        if shared_hist is not None and not shared_hist.empty and 'Close' in shared_hist.columns:
            numeric_close = pd.to_numeric(shared_hist['Close'], errors='coerce')
            valid_close = numeric_close.notna() & np.isfinite(numeric_close) & (numeric_close > 0)
            shared_hist = shared_hist.loc[valid_close].copy()

        recent_hist = shared_hist.tail(5).copy() if shared_hist is not None and not shared_hist.empty else pd.DataFrame()

        # Reuse the already-fetched five-year price history for valuation/earnings research.
        # Keep this inside the bounded stock-analysis request rather than spawning another
        # worker that can overlap memory-heavy pandas/yfinance work on a small Render instance.
        current_pe_hint = None
        try:
            raw_pe = (info or {}).get("trailingPE")
            if raw_pe is not None:
                current_pe_hint = float(raw_pe)
                if not np.isfinite(current_pe_hint) or current_pe_hint <= 0:
                    current_pe_hint = None
        except Exception:
            current_pe_hint = None

        # Finviz was fetched concurrently with the Yahoo core bundle above.

        # =================================================================
        # --- SECURE MATH ENGINE ---
        # =================================================================
        def safe_float(val, fallback=0.0):
            try:
                if val is None or pd.isna(val) or val == '-': return float(fallback)
                if isinstance(val, str):
                    val = val.replace(',', '').replace('%', '')
                result = float(val)
                return result if np.isfinite(result) else float(fallback)
            except Exception:
                return float(fallback)

        def get_fast_info(f_obj, prop_name):
            if f_obj is None: return None
            try: return getattr(f_obj, prop_name)
            except Exception: return None

        current_price, prev_close, mkt_cap, shares = 0.0, 0.0, 0.0, 0.0

        if recent_hist is not None and not recent_hist.empty and len(recent_hist) >= 2:
            current_price = safe_float(recent_hist['Close'].iloc[-1])
            prev_close = safe_float(recent_hist['Close'].iloc[-2])
        else:
            cp_val = get_fast_info(f_info, 'last_price')
            current_price = safe_float(cp_val, safe_float(info.get('currentPrice') if info else 0))
                
            pc_val = get_fast_info(f_info, 'previous_close')
            prev_close = safe_float(pc_val, safe_float(info.get('previousClose') if info else 0))

        mc_val = get_fast_info(f_info, 'market_cap')
        mkt_cap = safe_float(mc_val, safe_float(info.get('marketCap') if info else 0))
            
        sh_val = get_fast_info(f_info, 'shares')
        shares = safe_float(sh_val, safe_float(info.get('sharesOutstanding') if info else 0))

        # The browser requests /api/quote in parallel. If a core Yahoo source timed out,
        # reuse that already-persisted lightweight quote rather than returning $0 basics.
        if source_timeouts and not is_peer and (current_price <= 0 or prev_close <= 0):
            try:
                cached_quote_fallback, _quote_state = _cache_get_swr("quote", ticker, CACHE_STALE_QUOTE)
                if isinstance(cached_quote_fallback, dict):
                    quote_price = safe_float(cached_quote_fallback.get("current_price"))
                    quote_change = safe_float(cached_quote_fallback.get("change"))
                    if current_price <= 0 and quote_price > 0:
                        current_price = quote_price
                    if prev_close <= 0 and current_price > 0:
                        prev_close = current_price - quote_change
            except Exception:
                pass

        change = current_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close else 0

        fin_data = {
            "years": [], "revenue": [], "operating": [], "net": [], "ebitda": [],
            "gross_margin": [], "op_margin": [], "net_margin": [],
            "fcf": [], "ocf": [], "capex": [], "cash": [], "debt": [], "shares": []
        }

        def get_hist(df, possible_names):
            if df is None or df.empty: return []
            if isinstance(possible_names, str): possible_names = [possible_names]
            idx_map = {str(k).strip().lower(): k for k in df.index}
            for name in possible_names:
                clean_name = name.strip().lower()
                if clean_name in idx_map:
                    orig_idx = idx_map[clean_name]
                    try:
                        extracted = [float(df.loc[orig_idx, c]) if not pd.isna(df.loc[orig_idx, c]) else 0 for c in df.columns[::-1]]
                        if any(extracted): return extracted
                    except: pass
            return [0] * len(df.columns) if df is not None and not df.empty else []

        if fin is not None and not fin.empty:
            cols = fin.columns[::-1] 
            fin_data["years"] = [str(c.year) for c in cols]
            rev = get_hist(fin, ['Total Revenue', 'Operating Revenue'])
            net = get_hist(fin, ['Net Income', 'Net Income Common Stockholders', 'Net Profit'])
            op_inc = get_hist(fin, ['Operating Income', 'Operating Profit'])
            gross = get_hist(fin, ['Gross Profit'])
            ebitda_hist = get_hist(fin, ['EBITDA', 'Normalized EBITDA'])
            if not any(ebitda_hist or []):
                da_hist = get_hist(cf, ['Depreciation And Amortization', 'Depreciation', 'Reconciled Depreciation'])
                if da_hist and len(da_hist) == len(op_inc):
                    ebitda_hist = [o + abs(d) if (o or d) else 0 for o, d in zip(op_inc, da_hist)]

            fin_data["revenue"] = rev if rev else [0]*len(cols)
            fin_data["operating"] = op_inc if op_inc else [0]*len(cols)
            fin_data["net"] = net if net else [0]*len(cols)
            fin_data["ebitda"] = ebitda_hist if ebitda_hist else [0]*len(cols)

            fin_data["op_margin"] = [(o/r*100) if r else 0 for o, r in zip(fin_data["operating"], fin_data["revenue"])]
            fin_data["net_margin"] = [(n/r*100) if r else 0 for n, r in zip(fin_data["net"], fin_data["revenue"])]
            fin_data["gross_margin"] = [(g/r*100) if r else 0 for g, r in zip(gross, fin_data["revenue"])] if gross else [0]*len(cols)

            ocf_hist = get_hist(cf, ['Operating Cash Flow', 'Total Cash From Operating Activities'])
            capex_hist = get_hist(cf, ['Capital Expenditure', 'CapEx'])
            capex_abs = [abs(x) for x in capex_hist] if capex_hist else [0]*len(cols)
            
            fin_data["ocf"] = ocf_hist if ocf_hist else [0]*len(cols)
            fin_data["capex"] = capex_abs
            fin_data["fcf"] = [o - c for o, c in zip(fin_data["ocf"], capex_abs)]

            fin_data["cash"] = get_hist(bs, ['Cash And Cash Equivalents', 'Total Cash'])
            fin_data["debt"] = get_hist(bs, ['Total Debt', 'Long Term Debt'])
            fin_data["shares"] = get_hist(bs, ['Ordinary Shares Number', 'Common Stock', 'Basic Average Shares'])

            for k, v in fin_data.items():
                if not v: fin_data[k] = [0] * len(cols)
        
        latest_fcf = fin_data["fcf"][-1] if fin_data["fcf"] and len(fin_data["fcf"]) > 0 and fin_data["fcf"][-1] != 0 else 0
        dupont_metrics = calculate_dupont_analysis(fin, bs) if isinstance(fin, pd.DataFrame) and isinstance(bs, pd.DataFrame) else {}
        risk_metrics = calculate_risk_profile(shared_hist)
        
        base_fcf_projections = [latest_fcf * ((1.15) ** i) for i in range(1, 6)] if latest_fcf > 0 else [0,0,0,0,0]
        sensitivity_matrix = generate_sensitivity_matrix(0.10, 15.0, base_fcf_projections, shares)

        # Keep first-load analysis bounded: Finviz insiders arrive with the main scrape.
        # Yahoo insider_transactions is a separate slow network call, so do not block the
        # initial dashboard if Finviz has no rows. A deferred endpoint can hydrate it later.
        insider_list = fv_insiders or []

        # --- THE TTM FALLBACK ENGINE ---
        calc_shares = shares if shares > 0 else (fin_data["shares"][-1] if fin_data["shares"] and len(fin_data["shares"]) > 0 and fin_data["shares"][-1] > 0 else 1)
        
        ttm_net_income = 0
        ttm_revenue = 0
        if q_fin is not None and not q_fin.empty:
            q_net = get_hist(q_fin, ['Net Income', 'Net Income Common Stockholders', 'Net Profit'])
            q_rev = get_hist(q_fin, ['Total Revenue', 'Operating Revenue'])
            ttm_net_income = sum(q_net[-4:]) if len(q_net) >= 4 else sum(q_net)
            ttm_revenue = sum(q_rev[-4:]) if len(q_rev) >= 4 else sum(q_rev)
        else:
            ttm_net_income = fin_data["net"][-1] if fin_data["net"] and len(fin_data["net"]) > 0 else 0
            ttm_revenue = fin_data["revenue"][-1] if fin_data["revenue"] and len(fin_data["revenue"]) > 0 else 0
        
        total_equity = 0
        if bs is not None and not bs.empty:
            if 'Stockholders Equity' in bs.index:
                total_equity = bs.loc['Stockholders Equity'].iloc[0]
            elif 'Total Equity Gross Minority Interest' in bs.index:
                total_equity = bs.loc['Total Equity Gross Minority Interest'].iloc[0]
            elif 'Common Stock Equity' in bs.index:
                total_equity = bs.loc['Common Stock Equity'].iloc[0]

        fallback_eps = ttm_net_income / calc_shares if calc_shares > 1 else 0
        fallback_pe = current_price / fallback_eps if fallback_eps > 0 else 0
        fallback_bv = total_equity / calc_shares if calc_shares > 1 else 0
        fallback_pb = current_price / fallback_bv if fallback_bv > 0 else 0
        fallback_mkt_cap = current_price * calc_shares if calc_shares > 1 else 0
        
        fallback_roe = (fallback_eps / fallback_bv) if fallback_bv > 0 else 0
        fallback_margin = (ttm_net_income / ttm_revenue) if ttm_revenue > 0 else 0
        
        fallback_debt = fin_data["debt"][-1] if fin_data["debt"] and len(fin_data["debt"]) > 0 else 0
        fallback_de = (fallback_debt / total_equity * 100) if total_equity > 0 else 0
        final_mkt_cap = mkt_cap if mkt_cap > 0 else fallback_mkt_cap

        # --- ADVANCED MANUAL FALLBACKS ---
        try:
            cash_on_hand = fin_data["cash"][-1] if fin_data["cash"] and len(fin_data["cash"]) > 0 else 0
            ev = final_mkt_cap + fallback_debt - cash_on_hand
            ebitda = fin_data["ebitda"][-1] if fin_data.get("ebitda") and fin_data["ebitda"][-1] > 0 else (fin_data["operating"][-1] if fin_data["operating"] else 0)
            fallback_ev_ebitda = round(ev / ebitda, 2) if ebitda > 0 else "N/A"
        except Exception:
            fallback_ev_ebitda = "N/A"

        # Avoid extra Yahoo requests on an uncached first load. If quote-summary does not
        # provide dividend yield or beta, the UI can show N/A rather than delaying every
        # other section for secondary fallbacks.
        fallback_div_yield = "N/A"
        fallback_beta = "N/A"

        def format_mkt_cap(val):
            if val >= 1e12: return f"${val/1e12:.2f}T"
            if val >= 1e9: return f"${val/1e9:.2f}B"
            if val >= 1e6: return f"${val/1e6:.2f}M"
            return f"${val:,.0f}"
            
        fcf_yield_raw = (latest_fcf / final_mkt_cap) if final_mkt_cap and latest_fcf else 0
        fcf_yield = f"{round(fcf_yield_raw * 100, 2)}%" if fcf_yield_raw != 0 else "N/A"
        
        div_yield_raw = safe_float(info.get("dividendYield") if info else None)
        trailing_div_yield_raw = safe_float(info.get("trailingAnnualDividendYield") if info else None)
        info_dividend_pct = 0.0
        if trailing_div_yield_raw > 0:
            trailing_pct = trailing_div_yield_raw * 100.0
            if div_yield_raw > 0:
                as_reported_pct = div_yield_raw
                as_ratio_pct = div_yield_raw * 100.0
                info_dividend_pct = as_reported_pct if abs(as_reported_pct - trailing_pct) <= abs(as_ratio_pct - trailing_pct) else as_ratio_pct
            else:
                info_dividend_pct = trailing_pct
        elif div_yield_raw > 0:
            # Current yfinance commonly reports dividendYield in percentage points.
            info_dividend_pct = div_yield_raw if div_yield_raw >= 0.2 else div_yield_raw * 100.0

        if fallback_div_yield != "N/A":
            div_yield = fallback_div_yield
        elif 0 < info_dividend_pct < 100:
            div_yield = f"{round(info_dividend_pct, 2)}%"
        else:
            div_yield = "N/A"
        
        book_value = safe_float(info.get("bookValue") if info else None, fallback_bv)
        fiftyTwoWeekHigh = safe_float(info.get("fiftyTwoWeekHigh") if info else None, current_price * 1.2)
        fiftyTwoWeekLow = safe_float(info.get("fiftyTwoWeekLow") if info else None, current_price * 0.8)

        actual_roe = safe_float(info.get("returnOnEquity") if info else None, fallback_roe)
        actual_margin = safe_float(info.get("profitMargins") if info else None, fallback_margin)
        actual_pe = safe_float(info.get("trailingPE") if info else None, fallback_pe)
        actual_eps = safe_float(info.get("trailingEps") if info else None, fallback_eps)
        actual_pb = safe_float(info.get("priceToBook") if info else None, fallback_pb)
        actual_de = safe_float(info.get("debtToEquity") if info else None, fallback_de)

        # --- SETHISCORE TRACKER ---
        score_breakdown = {}
        score = 0
        
        def grade(metric_name, condition):
            nonlocal score
            is_pass = bool(condition)
            score_breakdown[metric_name] = is_pass
            if is_pass:
                score += 10

        grade("Positive Net Income", ttm_net_income > 0)
        grade("Consistent Revenue Growth", len(fin_data["revenue"]) >= 2 and fin_data["revenue"][-1] > fin_data["revenue"][-2])
        grade("Positive Free Cash Flow", latest_fcf > 0)
        grade("Return on Equity (ROE) > 15%", actual_roe > 0.15)
        grade("Net Profit Margin > 10%", actual_margin > 0.10)
        grade("Debt-to-Equity Ratio < 1.0", actual_de < 100) 
        grade("Free Cash Flow Yield > 5%", fcf_yield_raw > 0.05)
        grade("P/E Ratio < 25", 0 < actual_pe < 25)
        grade("P/B Ratio < 5", 0 < actual_pb < 5)
        grade("Pays a Dividend", (div_yield_raw > 0) or (fallback_div_yield != "N/A"))

        daily_hist = pd.DataFrame()
        try:
            daily_hist = shared_hist.tail(260).copy() if shared_hist is not None and not shared_hist.empty else pd.DataFrame()
        except Exception:
            pass

        rsi_14 = "N/A"
        stoch_k = "N/A"
        sma_200_pct = "N/A"
        
        if daily_hist is not None and not daily_hist.empty and len(daily_hist) >= 14:
            closes = daily_hist['Close']
            lows = daily_hist['Low']
            highs = daily_hist['High']

            delta = closes.diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
            rs = avg_gain / avg_loss
            rsi_value = (100 - (100 / (1 + rs))).iloc[-1]
            rsi_14 = round(float(rsi_value), 2) if np.isfinite(rsi_value) else "N/A"

            low_14 = lows.rolling(14).min().iloc[-1]
            high_14 = highs.rolling(14).max().iloc[-1]
            if current_price > 0 and np.isfinite(low_14) and np.isfinite(high_14):
                stoch_k = round(100 * ((current_price - low_14) / (high_14 - low_14)), 2) if high_14 != low_14 else 50

            if len(daily_hist) >= 200:
                sma_200 = closes.rolling(200).mean().iloc[-1]
                if np.isfinite(sma_200) and sma_200 > 0 and current_price > 0:
                    sma_200_pct = round(((current_price - sma_200) / sma_200) * 100, 2)

        dist_52w_high = round(((current_price - fiftyTwoWeekHigh) / fiftyTwoWeekHigh) * 100, 2) if current_price > 0 and fiftyTwoWeekHigh and fiftyTwoWeekHigh > 0 and np.isfinite(fiftyTwoWeekHigh) else "N/A"
        
        # --- FINVIZ DATA MAPPING ---
        short_interest = fv_stats.get("Short Float", "N/A")
        next_earnings = fv_stats.get("Earnings", "N/A")
        next_dividend = fv_stats.get("Dividend Ex-Date", "N/A")

        # Quant Math: Calculate Forward EPS using Current Price and Finviz Forward P/E
        forward_eps = "N/A"
        try:
            fwd_pe_str = fv_stats.get("Forward P/E", "N/A")
            if fwd_pe_str != "N/A" and current_price > 0:
                fwd_pe = float(fwd_pe_str.replace(',', ''))
                if fwd_pe > 0:
                    forward_eps = round(current_price / fwd_pe, 2)
        except Exception:
            pass

        stats = {
            "pe": round(actual_pe, 2) if actual_pe else "N/A",
            "pb": round(actual_pb, 2) if actual_pb else "N/A",
            "eps": round(actual_eps, 2) if actual_eps else "N/A",
            "forward_eps": forward_eps,
            "ev_ebitda": round(info.get("enterpriseToEbitda", 0), 2) if info and info.get("enterpriseToEbitda") else fallback_ev_ebitda,
            "mkt_cap": format_mkt_cap(final_mkt_cap) if final_mkt_cap else "N/A",
            "fcf_yield": fcf_yield,
            "div_yield": div_yield,
            "roe": f"{round(actual_roe * 100, 2)}%" if actual_roe else "N/A",
            "sethi_score": score,
            "sethi_score_breakdown": score_breakdown, 
            "book_value": book_value if book_value else 0,
            "fiftyTwoWeekHigh": fiftyTwoWeekHigh,
            "fiftyTwoWeekLow": fiftyTwoWeekLow,
            "beta": round(info.get("beta", 0), 2) if info and info.get("beta") else fallback_beta,
            "short_interest": short_interest,
            "dist_52w_high": f"{dist_52w_high}%" if dist_52w_high != "N/A" else "N/A",
            "rsi_14": rsi_14,
            "stoch_k": stoch_k,
            "sma_200_pct": f"{sma_200_pct}%" if sma_200_pct != "N/A" else "N/A",
            "next_earnings": next_earnings,  
            "next_dividend": next_dividend   
        }

        # Format the Finviz Company Overview down to 3 sentences
        raw_summary = fv_summary if fv_summary and fv_summary != "Company profile not currently available." else (info.get("longBusinessSummary", "Company profile not currently available.") if info else "Company profile not currently available.")
        sentences = raw_summary.split('. ')
        short_summary = '. '.join(sentences[:3]) + '.' if len(sentences) > 2 else raw_summary

        ticker_peers = {
            'AAPL': ['MSFT', 'GOOGL', 'META'], 'MSFT': ['AAPL', 'GOOGL', 'AMZN'], 'TSLA': ['F', 'GM', 'RIVN'],
            'NVDA': ['AMD', 'INTC', 'TSM'], 'AMZN': ['WMT', 'BABA', 'EBAY'], 'META': ['GOOGL', 'SNAP', 'PINS'],
            'GOOGL': ['META', 'MSFT', 'AMZN'], 'NFLX': ['DIS', 'WBD', 'AMZN'], 'JPM': ['BAC', 'WFC', 'C'],
            'V': ['MA', 'AXP', 'PYPL'], 'AMD': ['NVDA', 'INTC', 'QCOM']
        }
        peers = ticker_peers.get(ticker.upper(), ['SPY', 'QQQ', 'DIA'])

        # Historical P/E and earnings-event research is intentionally deferred to
        # /api/research/{ticker}. The financial-history frontend already hydrates those
        # cards independently, so computing them here only makes the first uncached stock
        # request wait for another expensive Yahoo workflow.
        research_payload = {
            "ticker": ticker.upper(),
            "earnings_reaction": {"available": False},
            "valuation_bands": {"available": False},
            "deferred": True,
        }

        chart_preview = {"dates": [], "opens": [], "highs": [], "lows": [], "closes": []}
        try:
            preview_hist = shared_hist.tail(260).copy() if shared_hist is not None and not shared_hist.empty else pd.DataFrame()
            if not preview_hist.empty:
                chart_preview = {
                    "dates": preview_hist.index.strftime('%Y-%m-%d %H:%M:%S').tolist(),
                    "opens": preview_hist['Open'].astype(float).tolist(),
                    "highs": preview_hist['High'].astype(float).tolist(),
                    "lows": preview_hist['Low'].astype(float).tolist(),
                    "closes": preview_hist['Close'].astype(float).tolist(),
                }
        except Exception:
            pass

        result = {
            "ticker": ticker.upper(), "current_price": round(current_price, 2),
            "change": round(change, 2), "pct_change": round(pct_change, 2),
            "shares": shares, "fcf": latest_fcf, "financials": fin_data, "stats": stats,
            "insiders": insider_list, "peers": peers,
            "summary": short_summary,
            "research": research_payload,
            "chart_preview": chart_preview,
            "dupont_analysis": dupont_metrics,            
            "risk_profile": risk_metrics,                
            "sensitivity_matrix": sensitivity_matrix,
            "_meta": {
                "analysis_ms": round((time.perf_counter() - analysis_started_at) * 1000),
                "request_ms": round((time.perf_counter() - analysis_started_at) * 1000),
                "served_from_cache": False,
                "research_deferred": True,
                "analysis_degraded": bool(source_timeouts or source_errors),
                "source_timeouts": source_timeouts,
                "source_errors": source_errors,
                "source_budget_seconds": STOCK_SOURCE_BUDGET_SECONDS,
            }
        }
        if not is_peer and not source_timeouts and not source_errors:
            _cache_write("stock_analysis", ticker, result, CACHE_TTL_STOCK, ticker=ticker)
        if analysis_slot_acquired:
            _stock_analysis_finished(ticker)
            _STOCK_ANALYSIS_GATE.release()
            analysis_slot_acquired = False
        return result
    except HTTPException:
        if locals().get("analysis_slot_acquired", False):
            _stock_analysis_finished(locals().get("ticker", raw_ticker))
            _STOCK_ANALYSIS_GATE.release()
            analysis_slot_acquired = False
        raise
    except Exception as e:
        if locals().get("analysis_slot_acquired", False):
            _stock_analysis_finished(locals().get("ticker", raw_ticker))
            _STOCK_ANALYSIS_GATE.release()
            analysis_slot_acquired = False
        raise HTTPException(status_code=500, detail=str(e))

# --- SETHISTOCK: LIGHTWEIGHT PEER SNAPSHOT API ---
def _peer_safe_float(value, fallback=None):
    try:
        if value is None or pd.isna(value):
            return fallback
        value = float(value)
        return value if np.isfinite(value) else fallback
    except Exception:
        return fallback


def _peer_format_market_cap(value):
    value = _peer_safe_float(value, 0.0) or 0.0
    if value >= 1e12:
        return f"${value / 1e12:.2f}T"
    if value >= 1e9:
        return f"${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:.2f}M"
    return f"${value:,.0f}" if value > 0 else "N/A"


def _peer_metric(value, digits=2):
    value = _peer_safe_float(value)
    return round(value, digits) if value is not None else "N/A"


def _peer_dividend_yield_pct(info):
    if not isinstance(info, dict):
        return None
    primary = _peer_safe_float(info.get("dividendYield"))
    trailing = _peer_safe_float(info.get("trailingAnnualDividendYield"))
    if trailing is not None and trailing > 0:
        trailing_pct = trailing * 100.0
        if primary is None or primary <= 0:
            return trailing_pct
        as_reported_pct = primary
        as_ratio_pct = primary * 100.0
        return as_reported_pct if abs(as_reported_pct - trailing_pct) <= abs(as_ratio_pct - trailing_pct) else as_ratio_pct
    if primary is not None and primary > 0:
        return primary if primary >= 0.2 else primary * 100.0
    return None


def _default_peers_for_ticker(symbol: str):
    ticker_peers = {
        'AAPL': ['MSFT', 'GOOGL', 'META'], 'MSFT': ['AAPL', 'GOOGL', 'AMZN'], 'TSLA': ['F', 'GM', 'RIVN'],
        'NVDA': ['AMD', 'INTC', 'TSM'], 'AMZN': ['WMT', 'BABA', 'EBAY'], 'META': ['GOOGL', 'SNAP', 'PINS'],
        'GOOGL': ['META', 'MSFT', 'AMZN'], 'NFLX': ['DIS', 'WBD', 'AMZN'], 'JPM': ['BAC', 'WFC', 'C'],
        'V': ['MA', 'AXP', 'PYPL'], 'AMD': ['NVDA', 'INTC', 'QCOM']
    }
    return ticker_peers.get(symbol.upper(), ['SPY', 'QQQ', 'DIA'])


@app.get("/api/quote/{raw_ticker}")
def get_stock_quote(raw_ticker: str, background_tasks: BackgroundTasks = None):
    """Fast above-the-fold quote used while the full SethiStock analysis loads."""
    try:
        ticker = _resolve_ticker_cached(raw_ticker, background_tasks).upper()
        cached_quote, cache_state = _cache_get_swr("quote", ticker, CACHE_STALE_QUOTE)
        if isinstance(cached_quote, dict):
            if cache_state == "stale":
                _schedule_cache_refresh(background_tasks, f"quote:{ticker}", "quote", get_stock_quote, ticker, None)
            return cached_quote

        stock = yf.Ticker(ticker)

        fast = None
        try:
            fast = stock.fast_info
        except Exception:
            pass

        current_price = _peer_safe_float(getattr(fast, "last_price", None) if fast is not None else None)
        previous_close = _peer_safe_float(getattr(fast, "previous_close", None) if fast is not None else None)
        market_cap = _peer_safe_float(getattr(fast, "market_cap", None) if fast is not None else None)

        if current_price is None or previous_close is None:
            try:
                hist = stock.history(period="5d", interval="1d")
                if hist is not None and not hist.empty:
                    current_price = current_price if current_price is not None else _peer_safe_float(hist['Close'].iloc[-1])
                    if previous_close is None and len(hist) >= 2:
                        previous_close = _peer_safe_float(hist['Close'].iloc[-2])
            except Exception:
                pass

        current_price = current_price or 0.0
        previous_close = previous_close or current_price
        change = current_price - previous_close
        pct_change = (change / previous_close * 100.0) if previous_close else 0.0

        result = {
            "ticker": ticker,
            "current_price": round(current_price, 2),
            "change": round(change, 2),
            "pct_change": round(pct_change, 2),
            "market_cap": _peer_format_market_cap(market_cap),
            "peers": _default_peers_for_ticker(ticker),
        }
        _cache_write("quote", ticker, result, CACHE_TTL_QUOTE, ticker=ticker)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/peers")
def get_peer_snapshots(tickers: str, background_tasks: BackgroundTasks = None):
    # Return only metrics required by SethiStock peer benchmarking.
    # This avoids the full /api/stock pipeline for competitor rows.
    symbols = []
    seen = set()
    for raw in tickers.split(","):
        symbol = raw.strip().upper()
        if symbol and symbol not in seen:
            if len(symbol) > 20:
                raise HTTPException(status_code=400, detail=f"Invalid ticker: {symbol}")
            symbols.append(symbol)
            seen.add(symbol)

    if not symbols:
        raise HTTPException(status_code=400, detail="Provide at least one ticker.")
    if len(symbols) > 8:
        raise HTTPException(status_code=400, detail="Peer snapshot supports up to 8 tickers per request.")

    cache_identity = ",".join(symbols)
    cached_peers, cache_state = _cache_get_swr("peer_snapshots", cache_identity, CACHE_STALE_PEERS)
    if isinstance(cached_peers, dict):
        if cache_state == "stale":
            _schedule_cache_refresh(background_tasks, f"peer_snapshots:{cache_identity}", "peer_snapshots", get_peer_snapshots, cache_identity, None)
        return cached_peers

    results = []
    for symbol in symbols:
        try:
            stock = yf.Ticker(symbol)
            try:
                info = stock.info or {}
            except Exception:
                info = {}

            current_price = _peer_safe_float(info.get("currentPrice"))
            if current_price is None:
                current_price = _peer_safe_float(info.get("regularMarketPrice"))

            market_cap = _peer_safe_float(info.get("marketCap"))

            if current_price is None or market_cap is None:
                try:
                    fast = stock.fast_info
                    if current_price is None:
                        current_price = _peer_safe_float(getattr(fast, "last_price", None))
                    if market_cap is None:
                        market_cap = _peer_safe_float(getattr(fast, "market_cap", None))
                except Exception:
                    pass

            trailing_pe = _peer_safe_float(info.get("trailingPE"))
            price_to_book = _peer_safe_float(info.get("priceToBook"))
            trailing_eps = _peer_safe_float(info.get("trailingEps"))
            free_cash_flow = _peer_safe_float(info.get("freeCashflow"))
            dividend_yield_pct = _peer_dividend_yield_pct(info)

            fcf_yield = None
            if free_cash_flow is not None and market_cap and market_cap > 0:
                fcf_yield = (free_cash_flow / market_cap) * 100.0

            results.append({
                "ticker": symbol,
                "current_price": round(current_price, 2) if current_price is not None else 0.0,
                "stats": {
                    "mkt_cap": _peer_format_market_cap(market_cap),
                    "pe": _peer_metric(trailing_pe),
                    "pb": _peer_metric(price_to_book),
                    "eps": _peer_metric(trailing_eps),
                    "div_yield": f"{round(dividend_yield_pct, 2)}%" if dividend_yield_pct is not None and dividend_yield_pct > 0 else "N/A",
                    "fcf_yield": f"{round(fcf_yield, 2)}%" if fcf_yield is not None else "N/A",
                },
            })
        except Exception as exc:
            results.append({
                "ticker": symbol,
                "current_price": 0.0,
                "stats": {
                    "mkt_cap": "N/A",
                    "pe": "N/A",
                    "pb": "N/A",
                    "eps": "N/A",
                    "div_yield": "N/A",
                    "fcf_yield": "N/A",
                },
                "error": str(exc),
            })

    result = {"results": results, "count": len(results)}
    _cache_write("peer_snapshots", cache_identity, result, CACHE_TTL_PEERS)
    return result


def _sanitize_chart_payload(payload):
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

# --- UNIVERSAL TELEMETRY PING ---
@app.post("/api/telemetry/log")
def log_event(data: TelemetryPayload):
    """Receives page views and events from Sethiway Hub, SethiMacro, etc."""
    log_telemetry_event(project=data.project, action=data.action, ticker=data.ticker, visitor_id=data.visitor_id)
    return {"status": "recorded"}

@app.post("/api/telemetry/sethistock-load")
def log_sethistock_load(data: LoadTimingPayload):
    """Stores user-perceived SethiStock load timings for performance monitoring."""
    if not SUPABASE_URL:
        return {"status": "disabled"}

    clean_status = str(data.status or "full").strip().lower()
    if clean_status not in {"full", "partial", "error"}:
        clean_status = "error"
    clean_server_state = str(data.server_state or "unknown").strip().lower()
    if clean_server_state not in {"warm", "cold", "unknown"}:
        clean_server_state = "unknown"

    def _bounded_ms(value):
        if value is None:
            return None
        try:
            numeric = int(value)
            return max(0, min(numeric, 300000))
        except Exception:
            return None

    ticker = str(data.ticker or "").strip().upper()[:20]
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")

    key = SUPABASE_SERVICE_KEY or SUPABASE_KEY
    if not key:
        return {"status": "disabled"}

    try:
        clean_url = SUPABASE_URL.replace("/rest/v1", "").rstrip("/")
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        payload = {
            "ticker": ticker,
            "quote_ms": _bounded_ms(data.quote_ms),
            "full_ms": _bounded_ms(data.full_ms),
            "backend_ms": _bounded_ms(data.backend_ms),
            "cache_hit": data.cache_hit,
            "status": clean_status,
            "visitor_id": str(data.visitor_id)[:128] if data.visitor_id else None,
            "error_code": str(data.error_code)[:80] if data.error_code else None,
            "server_wake_ms": _bounded_ms(data.server_wake_ms),
            "server_uptime_s": _bounded_ms(data.server_uptime_s),
            "server_state": clean_server_state,        }
        response = requests.post(
            f"{clean_url}/rest/v1/sethistock_load_telemetry",
            headers=headers,
            json=payload,
            timeout=3,
        )
        return {"status": "recorded" if response.status_code < 400 else "ignored"}
    except Exception:
        return {"status": "ignored"}

# --- SECURE ADMIN METRICS ENDPOINT ---
@app.get("/api/admin/telemetry")
def get_admin_metrics(secret: str):
    """Secured analytics data for the admin command center."""
    if secret != ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid admin credentials")
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=500, detail="Supabase environment variables not configured")

    try:
        clean_url = SUPABASE_URL.replace("/rest/v1", "").rstrip("/")
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        }
        
        # 1. Fetch Traffic Logs
        url_traffic = f"{clean_url}/rest/v1/traffic_logs?select=*&order=created_at.desc&limit=1000"
        res_traffic = requests.get(url_traffic, headers=headers, timeout=5)
        logs = res_traffic.json() if res_traffic.status_code == 200 else []

        # 2. Fetch Markowitz Telemetry Logs
        url_mkw = f"{clean_url}/rest/v1/markowitz_telemetry?select=*&order=timestamp.desc&limit=50"
        res_mkw = requests.get(url_mkw, headers=headers, timeout=5)
        mkw_logs = res_mkw.json() if res_mkw.status_code == 200 else []

        # 3. Fetch SethiStock load-performance telemetry
        admin_key = SUPABASE_SERVICE_KEY or SUPABASE_KEY
        perf_headers = {
            "apikey": admin_key,
            "Authorization": f"Bearer {admin_key}",
        }
        url_loads = f"{clean_url}/rest/v1/sethistock_load_telemetry?select=*&order=created_at.desc&limit=500"
        res_loads = requests.get(url_loads, headers=perf_headers, timeout=5)
        load_logs = res_loads.json() if res_loads.status_code == 200 else []

        # 4. Compute Traffic Metrics
        total_events = len(logs)
        project_counts = {}
        ticker_counts = {}
        action_counts = {}

        for log in logs:
            proj = log.get("project", "Unknown")
            project_counts[proj] = project_counts.get(proj, 0) + 1

            act = log.get("action", "unknown")
            action_counts[act] = action_counts.get(act, 0) + 1

            tick = log.get("ticker")
            if tick:
                ticker_counts[tick] = ticker_counts.get(tick, 0) + 1

        sorted_tickers = sorted(ticker_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        top_tickers = [{"ticker": k, "count": v} for k, v in sorted_tickers]

        def _numeric_ms(rows, key):
            values = []
            for row in rows:
                try:
                    value = row.get(key)
                    if value is not None:
                        value = int(value)
                        if value >= 0:
                            values.append(value)
                except Exception:
                    pass
            return values

        def _avg(values):
            return round(sum(values) / len(values)) if values else None

        def _percentile(values, percentile):
            if not values:
                return None
            ordered = sorted(values)
            index = max(0, min(len(ordered) - 1, math.ceil((percentile / 100) * len(ordered)) - 1))
            return ordered[index]

        quote_values = _numeric_ms(load_logs, "quote_ms")
        full_rows = [row for row in load_logs if row.get("status") == "full" and row.get("full_ms") is not None]
        full_values = _numeric_ms(full_rows, "full_ms")
        cached_rows = [row for row in full_rows if row.get("cache_hit") is True]
        cold_rows = [row for row in full_rows if row.get("cache_hit") is False]
        timeout_rows = [row for row in load_logs if row.get("status") == "partial"]

        ticker_perf = {}
        for row in load_logs:
            symbol = str(row.get("ticker") or "").upper()
            if not symbol:
                continue
            bucket = ticker_perf.setdefault(symbol, {"ticker": symbol, "loads": 0, "full_ms": [], "quote_ms": [], "timeouts": 0})
            bucket["loads"] += 1
            if row.get("status") == "partial":
                bucket["timeouts"] += 1
            try:
                if row.get("full_ms") is not None:
                    bucket["full_ms"].append(int(row["full_ms"]))
                if row.get("quote_ms") is not None:
                    bucket["quote_ms"].append(int(row["quote_ms"]))
            except Exception:
                pass

        ticker_loads = []
        for bucket in ticker_perf.values():
            ticker_loads.append({
                "ticker": bucket["ticker"],
                "loads": bucket["loads"],
                "avg_full_ms": _avg(bucket["full_ms"]),
                "avg_quote_ms": _avg(bucket["quote_ms"]),
                "timeouts": bucket["timeouts"],
            })
        ticker_loads.sort(key=lambda row: (-row["loads"], row["ticker"]))

        server_wake_values = _numeric_ms(load_logs, "server_wake_ms")
        server_cold_rows = [row for row in load_logs if row.get("server_state") == "cold"]
        server_known_rows = [row for row in load_logs if row.get("server_state") in {"warm", "cold"}]
        load_performance = {
            "samples": len(load_logs),
            "avg_quote_ms": _avg(quote_values),
            "avg_full_ms": _avg(full_values),
            "p95_full_ms": _percentile(full_values, 95),
            "timeout_rate_pct": round((len(timeout_rows) / len(load_logs)) * 100, 1) if load_logs else 0.0,
            "cache_hit_rate_pct": round((len([r for r in load_logs if r.get("cache_hit") is True]) / len(load_logs)) * 100, 1) if load_logs else 0.0,
            "avg_cached_full_ms": _avg(_numeric_ms(cached_rows, "full_ms")),
            "avg_cold_full_ms": _avg(_numeric_ms(cold_rows, "full_ms")),
            "avg_server_wake_ms": _avg(server_wake_values),
            "server_cold_rate_pct": round((len(server_cold_rows) / len(server_known_rows)) * 100, 1) if server_known_rows else 0.0,        }

        return {
            "total_events": total_events,
            "project_breakdown": project_counts,
            "action_breakdown": action_counts,
            "top_tickers": top_tickers,
            "recent_logs": logs[:25],
            "markowitz_logs": mkw_logs,
            "load_performance": load_performance,
            "recent_stock_loads": load_logs[:50],
            "ticker_loads": ticker_loads[:50]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
# ==========================================
# SETHIQUANT: BLACK-SCHOLES OPTIONS ENGINE
# ==========================================
class BlackScholesInput(BaseModel):
    S: float      # Underlying Asset Price
    K: float      # Strike Price
    T: float      # Time to Expiry (in years)
    r: float      # Risk-Free Interest Rate (decimal, e.g., 0.05 for 5%)
    sigma: float  # Implied Volatility (decimal, e.g., 0.20 for 20%)
    option_type: str = "call" 

@app.get("/api/quant/risk-free-rate")
def get_risk_free_rate():
    """Fetches the live US 10-Year Treasury Yield to peg the Risk-Free Rate"""
    try:
        # ^TNX is the ticker for the CBOE 10-Year Treasury Yield
        tnx = yf.Ticker("^TNX").history(period="5d")
        if not tnx.empty:
            last_yield = float(tnx['Close'].iloc[-1])
            return {"status": "success", "rate": round(last_yield, 2)}
    except Exception:
        pass
    return {"status": "fallback", "rate": 4.60}

@app.post("/api/quant/black-scholes")
def calculate_black_scholes(data: BlackScholesInput):
    """
    Deterministic Options Pricing Engine.
    Calculates theoretical price and Greeks using the Black-Scholes-Merton model.
    """
    try:
        S, K, T, r, sigma = data.S, data.K, data.T, data.r, data.sigma
        
        # Edge Case Firewall
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            raise HTTPException(status_code=400, detail="Invalid inputs for Black-Scholes calculus.")

        # Core Probability Variables
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        N_d1 = norm.cdf(d1)
        N_d2 = norm.cdf(d2)
        N_prime_d1 = norm.pdf(d1)

        # 1. Calculate Option Price & Theta
        if data.option_type.lower() == "call":
            price = S * N_d1 - K * math.exp(-r * T) * N_d2
            delta = N_d1
            theta = (- (S * N_prime_d1 * sigma) / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * N_d2) / 365
        else: # Put Option
            price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
            delta = N_d1 - 1
            theta = (- (S * N_prime_d1 * sigma) / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * norm.cdf(-d2)) / 365

        # 2. Calculate Gamma and Vega
        gamma = N_prime_d1 / (S * sigma * math.sqrt(T))
        vega = (S * math.sqrt(T) * N_prime_d1) / 100 

        return {
            "status": "success",
            "results": {
                "theoretical_price": round(price, 4),
                "greeks": {
                    "delta": round(delta, 4),
                    "gamma": round(gamma, 4),
                    "theta": round(theta, 4),
                    "vega": round(vega, 4)
                }
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# SETHIQUANT: MARKOWITZ PORTFOLIO OPTIMIZER
# ==========================================
class MarkowitzInput(BaseModel):
    tickers: list[str]
    risk_free_rate: float = 0.05  # Default 5%

@app.post("/api/quant/markowitz")
def optimize_portfolio(data: MarkowitzInput):
    """
    Modern Portfolio Theory (MPT) Optimizer.
    Uses SLSQP optimization to find the exact asset weights that maximize the Sharpe Ratio.
    """
    try:
        # 1. Clean and cap inputs to prevent server overload
        tickers = [t.strip().upper() for t in data.tickers if t.strip()][:5]
        if len(tickers) < 2:
            raise HTTPException(status_code=400, detail="Please provide at least 2 tickers to optimize.")

        # 2. Fetch 2 Years of historical pricing data
        prices = yf.download(tickers, period="2y", interval="1d")["Close"]
        if prices.empty:
            raise HTTPException(status_code=400, detail="Failed to retrieve market data. Check ticker symbols.")
            
        # Drop columns with entirely missing data, then drop NaN rows
        prices = prices.dropna(axis=1, how='all').dropna()
        valid_tickers = list(prices.columns)
        
        if len(valid_tickers) < 2:
            raise HTTPException(status_code=400, detail="Not enough valid historical data for optimization.")

        # 3. Calculate Daily Returns, Mean Annual Returns, and the Covariance Matrix
        returns = prices.pct_change().dropna()
        mean_returns = returns.mean() * 252
        cov_matrix = returns.cov() * 252
        num_assets = len(valid_tickers)

        # 4. Objective Function: We want to Maximize Sharpe, which means Minimizing Negative Sharpe
        def negative_sharpe(weights):
            p_ret = np.sum(mean_returns * weights)
            p_vol = np.sqrt(np.dot(weights.T, np.dot(cov_matrix, weights)))
            return -(p_ret - data.risk_free_rate) / p_vol

        # 5. Optimization Constraints & Bounds
        # Constraint: All weights must sum exactly to 1.0 (100%)
        constraints = ({'type': 'eq', 'fun': lambda w: np.sum(w) - 1})
        # Bounds: No short selling, asset weights must be between 0 and 1
        bounds = tuple((0, 1) for _ in range(num_assets))
        # Initial Guess: Equal weighting for all assets
        init_guess = num_assets * [1. / num_assets]

        # 6. Execute SLSQP Minimizer
        opt_results = minimize(negative_sharpe, init_guess, method='SLSQP', bounds=bounds, constraints=constraints)

        if not opt_results.success:
            raise HTTPException(status_code=500, detail="Optimization algorithm failed to converge.")

        # 7. Extract and Format Results
        opt_weights = opt_results.x
        opt_ret = np.sum(mean_returns * opt_weights)
        opt_vol = np.sqrt(np.dot(opt_weights.T, np.dot(cov_matrix, opt_weights)))
        opt_sharpe = (opt_ret - data.risk_free_rate) / opt_vol

        # Map the optimal weights back to their respective tickers
        weight_allocation = {valid_tickers[i]: round(opt_weights[i] * 100, 2) for i in range(num_assets)}

        return {
            "status": "success",
            "results": {
                "expected_annual_return": round(opt_ret * 100, 2),
                "expected_annual_volatility": round(opt_vol * 100, 2),
                "max_sharpe_ratio": round(opt_sharpe, 2),
                "optimal_weights": weight_allocation
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# SETHIQUANT: AI MARKOWITZ SYNTHESIS
# ==========================================
class MarkowitzAIInput(BaseModel):
    allocation: dict
    expected_return: float
    volatility: float

@app.post("/api/quant/markowitz-ai")
def generate_markowitz_rationale(data: MarkowitzAIInput):
    """Generates a 3-bullet quantitative synthesis with an automatic 503 fallback."""
    try:
        from google import genai
        from google.genai import types
        import json
        import os
        
        GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
        if not GOOGLE_API_KEY:
            raise ValueError("Missing GOOGLE_API_KEY in Render environment")
            
        client = genai.Client(api_key=GOOGLE_API_KEY)
        
        prompt = f"""
        You are a quantitative portfolio manager. The Markowitz algorithm just calculated the Tangency Portfolio: {data.allocation}.
        Expected Annual Return: {data.expected_return}%. Annualised Volatility: {data.volatility}%.
        
        Write a highly professional, 3-bullet-point synthesis explaining this allocation. Use UK English.
        
        - Bullet 1 (The Catalyst): Explain exactly WHY the specific heavily weighted companies were chosen over the excluded ones. Reference real-world market dominance, recent financial momentum, or structural sector advantages.
        - Bullet 2 (The Math): Explain the correlation dynamics. Why do these specific assets balance each other out to minimize overall portfolio drawdown risk?
        - Bullet 3 (The Verdict): Summarize the risk-adjusted return (Sharpe Ratio) efficiency of this exact weighting.
        
        Return STRICTLY as a raw JSON array of 3 strings. Do not use markdown blocks. Example: ["Bullet 1", "Bullet 2", "Bullet 3"]
        """
        
        config = types.GenerateContentConfig(temperature=0.3)
        
        try:
            # Primary attempt using the flagship 3.7 model
            response = client.models.generate_content(
                model='gemini-3.7-flash',
                contents=prompt,
                config=config
            )
        except Exception as model_error:
            # Instantly catch the 503 Overload and reroute to the stable backup
            print(f"3.7-flash overloaded ({model_error}), falling back to 3.6-flash...")
            response = client.models.generate_content(
                model='gemini-3.6-flash',
                contents=prompt,
                config=config
            )
        
        # BULLETPROOF JSON PARSER
        text_response = response.text.replace("```json", "").replace("```", "").strip()
        bullets = json.loads(text_response)
        
        return {"status": "success", "bullets": bullets}
        
    except Exception as e:
        print(f"MARKOWITZ AI CRASH: {e}")
        return {
            "status": "success", 
            "bullets": [
                "The algorithm maximised the Sharpe Ratio by overweighting assets with superior risk-adjusted historical returns.",
                "Capital was dynamically allocated to minimise the overarching covariance matrix, reducing total portfolio drawdown risk.",
                f"SYSTEM ALERT: AI generation failed. Log: {str(e)[:80]}"
            ]
        }

# ==========================================
# SETHIQUANT: ALGORITHMIC BACKTESTER
# ==========================================
class BacktestInput(BaseModel):
    ticker: str
    strategy: str = "sma_crossover"
    short_window: int = 50
    long_window: int = 200
    bb_window: int = 20
    bb_std: float = 2.0
    period: str = "5y"

@app.post("/api/quant/backtest")
def run_backtest(data: BacktestInput):
    """
    Algorithmic Backtesting Engine.
    Dynamically routes historical data into Trend Following or Mean Reversion pipelines.
    """
    try:
        import numpy as np
        import pandas as pd
        
        ticker = data.ticker.strip().upper()
        
        # 1. Armored Data Fetch (Hybrid Fetcher to prevent Yahoo Finance crashes)
        hist = pd.DataFrame()
        try:
            safe_sess = get_safe_session()
            stock = yf.Ticker(ticker, session=safe_sess)
            hist = stock.history(period=data.period)
        except Exception:
            pass
            
        if hist is None or hist.empty:
            try:
                native_stock = yf.Ticker(ticker)
                hist = native_stock.history(period=data.period)
            except Exception:
                pass
                
        if hist is None or hist.empty:
            raise HTTPException(status_code=400, detail="Failed to retrieve market data. Check ticker symbol.")
            
        close_prices = hist['Close'].dropna()
        signals = pd.Series(index=close_prices.index, data=0.0)
        
        # ========================================
        # STRATEGY 1: SMA Crossover (Trend Following)
        # ========================================
        if data.strategy == "sma_crossover":
            if len(close_prices) < data.long_window:
                raise HTTPException(status_code=400, detail="Not enough historical data for SMA calculation.")
                
            sma_short = close_prices.rolling(window=data.short_window).mean()
            sma_long = close_prices.rolling(window=data.long_window).mean()
            
            raw_signals = np.where(sma_short > sma_long, 1.0, 0.0)
            # Shift by 1 day to strictly eliminate look-ahead bias
            signals = pd.Series(raw_signals, index=close_prices.index).shift(1).fillna(0)
            
        # ========================================
        # STRATEGY 2: Bollinger Bands (Mean Reversion)
        # ========================================
        elif data.strategy == "bollinger_bands":
            if len(close_prices) < data.bb_window:
                raise HTTPException(status_code=400, detail="Not enough historical data for Bollinger Bands.")
                
            sma = close_prices.rolling(window=data.bb_window).mean()
            std = close_prices.rolling(window=data.bb_window).std()
            lower_band = sma - (data.bb_std * std)
            upper_band = sma + (data.bb_std * std)
            
            # Mathematical Logic: Buy when statistically oversold, Sell when overbought
            raw_signals = pd.Series(index=close_prices.index, data=np.nan)
            raw_signals[close_prices < lower_band] = 1.0  # Buy Signal
            raw_signals[close_prices > upper_band] = 0.0  # Sell Signal (Go to cash)
            
            # Forward-fill the active position to hold between signals, fill beginning with 0
            signals = raw_signals.ffill().fillna(0)
            # Shift by 1 day to strictly eliminate look-ahead bias
            signals = signals.shift(1).fillna(0)
            
        else:
            raise HTTPException(status_code=400, detail="Invalid algorithmic strategy selected.")

        # 4. Calculate Returns
        daily_returns = close_prices.pct_change().fillna(0)
        strategy_returns = daily_returns * signals

        # 5. Build Equity Curves (Base 100)
        buy_hold_equity = (1 + daily_returns).cumprod() * 100
        strategy_equity = (1 + strategy_returns).cumprod() * 100

        # 6. Calculate Key Performance Indicators (KPIs)
        total_return = (strategy_equity.iloc[-1] / strategy_equity.iloc[0]) - 1
        bh_return = (buy_hold_equity.iloc[-1] / buy_hold_equity.iloc[0]) - 1

        # Max Drawdown
        rolling_max = strategy_equity.cummax()
        drawdown = (strategy_equity - rolling_max) / rolling_max
        max_drawdown = drawdown.min()

        # Annualised Volatility (Strict UK English convention enforced)
        annual_vol = strategy_returns.std() * np.sqrt(252)

        # Risk-adjusted performance. Cash is assumed to earn 0% in this educational backtest.
        daily_std = float(strategy_returns.std())
        sharpe_ratio = (float(strategy_returns.mean()) / daily_std) * np.sqrt(252) if daily_std > 0 else 0.0
        downside = strategy_returns[strategy_returns < 0]
        downside_std = float(downside.std()) if len(downside) > 1 else 0.0
        sortino_ratio = (float(strategy_returns.mean()) / downside_std) * np.sqrt(252) if downside_std > 0 else 0.0
        excess_return = total_return - bh_return
        time_in_market = float(signals.mean()) if len(signals) else 0.0

        # Build completed trade segments from the already look-ahead-safe position series.
        trade_rows = []
        active_start = None
        signal_values = signals.to_numpy(dtype=float)
        returns_values = daily_returns.to_numpy(dtype=float)
        dates_index = close_prices.index
        prices_values = close_prices.to_numpy(dtype=float)

        for i, position in enumerate(signal_values):
            is_active = position > 0.5
            if is_active and active_start is None:
                active_start = i
            is_last = i == len(signal_values) - 1
            if active_start is not None and ((not is_active) or is_last):
                end_i = i - 1 if not is_active else i
                segment = returns_values[active_start:end_i + 1]
                trade_return = float(np.prod(1.0 + segment) - 1.0) if len(segment) else 0.0
                entry_price_i = max(0, active_start - 1)
                exit_price_i = end_i
                trade_rows.append({
                    "entry_date": dates_index[entry_price_i].strftime('%Y-%m-%d'),
                    "exit_date": dates_index[exit_price_i].strftime('%Y-%m-%d'),
                    "entry_price": round(float(prices_values[entry_price_i]), 2),
                    "exit_price": round(float(prices_values[exit_price_i]), 2),
                    "return_pct": round(trade_return * 100.0, 2),
                    "holding_days": int(max(1, exit_price_i - entry_price_i)),
                })
                active_start = None

        completed_trades = len(trade_rows)
        winning_trades = sum(1 for row in trade_rows if row["return_pct"] > 0)
        win_rate = (winning_trades / completed_trades) if completed_trades else 0.0

        entries = [{"date": row["entry_date"], "price": row["entry_price"]} for row in trade_rows]
        exits = [{"date": row["exit_date"], "price": row["exit_price"]} for row in trade_rows]

        return {
            "status": "success",
            "results": {
                "kpis": {
                    "total_return": round(total_return * 100, 2),
                    "buy_hold_return": round(bh_return * 100, 2),
                    "excess_return_vs_buy_hold": round(excess_return * 100, 2),
                    "max_drawdown": round(max_drawdown * 100, 2),
                    "annualised_volatility": round(annual_vol * 100, 2),
                    "sharpe_ratio": round(sharpe_ratio, 2),
                    "sortino_ratio": round(sortino_ratio, 2),
                    "time_in_market_pct": round(time_in_market * 100, 1),
                    "completed_trades": completed_trades,
                    "win_rate_pct": round(win_rate * 100, 1)
                },
                "chart": {
                    "dates": close_prices.index.strftime('%Y-%m-%d').tolist(),
                    "strategy_equity": strategy_equity.round(2).tolist(),
                    "buy_hold_equity": buy_hold_equity.round(2).tolist(),
                    "drawdown_pct": (drawdown * 100.0).round(2).tolist(),
                    "entries": entries,
                    "exits": exits
                },
                "trades": trade_rows[-25:]
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# SETHIQUANT: MONTE CARLO VaR (GBM)
# ==========================================
class VaRInput(BaseModel):
    ticker: str
    days_forward: int = 30
    simulations: int = 10000

@app.post("/api/quant/monte-carlo-var")
def calculate_monte_carlo_var(data: VaRInput):
    """
    Monte Carlo Value at Risk Engine.
    Uses Geometric Brownian Motion (GBM) to simulate 10,000 future price paths.
    """
    try:
        ticker = data.ticker.strip().upper()
        
        # 1. Fetch 5 years of historical data for robust volatility modeling
        hist = yf.Ticker(ticker).history(period="5y")
        if hist.empty:
            raise HTTPException(status_code=400, detail="Failed to retrieve market data.")
            
        closes = hist['Close'].dropna()
        current_price = closes.iloc[-1]
        
        # 2. Calculate daily historical returns, drift, and volatility
        daily_returns = closes.pct_change().dropna()
        mu = daily_returns.mean()
        sigma = daily_returns.std()
        
        # 3. Geometric Brownian Motion (GBM) Setup
        # We run 10,000 simulations over the requested timeframe
        simulations = data.simulations
        days = data.days_forward
        
        import numpy as np
        # Generate random normal shocks for the entire matrix
        Z = np.random.normal(0, 1, (days, simulations))
        
        # Pre-allocate price matrix: Rows = Days, Columns = Simulations
        price_paths = np.zeros((days, simulations))
        price_paths[0] = current_price
        
        # 4. Run the Monte Carlo Simulation
        for t in range(1, days):
            # GBM Formula: S_t = S_{t-1} * exp((mu - (sigma^2 / 2)) + sigma * Z)
            drift = mu - (0.5 * sigma**2)
            shock = sigma * Z[t]
            price_paths[t] = price_paths[t-1] * np.exp(drift + shock)
            
        # 5. Extract Final Prices and Calculate VaR
        final_prices = price_paths[-1]
        simulated_returns = (final_prices - current_price) / current_price
        
        # Calculate 95% and 99% Value at Risk (VaR)
        var_95 = np.percentile(simulated_returns, 5)
        var_99 = np.percentile(simulated_returns, 1)
        
        # Calculate Expected Shortfall (CVaR) - the average of the worst-case losses
        cvar_95 = simulated_returns[simulated_returns <= var_95].mean()
        cvar_99 = simulated_returns[simulated_returns <= var_99].mean()

        # 6. Prepare Chart Data (Send a maximum of 50 paths to the frontend to prevent crashing the browser)
        visual_paths = price_paths[:, :50].round(2).tolist()
        
        return {
            "status": "success",
            "results": {
                "current_price": round(current_price, 2),
                "kpis": {
                    "var_95": round(var_95 * 100, 2),
                    "var_99": round(var_99 * 100, 2),
                    "cvar_95": round(cvar_95 * 100, 2),
                    "cvar_99": round(cvar_99 * 100, 2)
                },
                "chart": {
                    "paths": visual_paths,
                    "days": list(range(1, days + 1))
                }
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# SETHIQUANT: BINOMIAL OPTIONS PRICING (AMERICAN)
# ==========================================
class BinomialInput(BaseModel):
    S: float      # Underlying Price
    K: float      # Strike Price
    T: float      # Time to Expiry (in years)
    r: float      # Risk-Free Rate
    sigma: float  # Implied Volatility
    N: int = 100  # Number of Binomial Steps
    option_type: str = "call"

@app.post("/api/quant/binomial-american")
def calculate_binomial_american(data: BinomialInput):
    """
    Cox-Ross-Rubinstein (CRR) Binomial Tree.
    Prices American options by building a lattice and checking for early exercise at every node.
    """
    try:
        import numpy as np
        import math
        
        S, K, T, r, sigma = data.S, data.K, data.T, data.r, data.sigma
        N = data.N
        
        # Edge Case Firewall
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0 or N <= 0:
            raise HTTPException(status_code=400, detail="Invalid inputs for Binomial calculus.")
            
        # Hard cap steps at 1000 to prevent server CPU timeouts on Render
        N = min(N, 1000)
        
        # 1. Calculate CRR Lattice Parameters
        dt = T / N
        u = math.exp(sigma * math.sqrt(dt))
        d = 1 / u
        p = (math.exp(r * dt) - d) / (u - d)
        discount_factor = math.exp(-r * dt)
        
        # 2. Initialize Asset Prices at Maturity (Time Step N)
        # S_T = S * u^j * d^(N-j) where j is the number of up-steps
        prices = np.zeros(N + 1)
        for j in range(N + 1):
            prices[j] = S * (u ** j) * (d ** (N - j))
            
        # 3. Initialize Option Values at Maturity
        values = np.zeros(N + 1)
        for j in range(N + 1):
            if data.option_type.lower() == "call":
                values[j] = max(0, prices[j] - K)
            else: # Put Option
                values[j] = max(0, K - prices[j])
                
        # 4. Step Backwards Through the Tree (Dynamic Programming)
        for i in range(N - 1, -1, -1):
            for j in range(i + 1):
                # Calculate the Continuation Value (holding the option)
                continuation = discount_factor * (p * values[j + 1] + (1 - p) * values[j])
                
                # Calculate the Intrinsic Value at this specific node (early exercise)
                current_price = S * (u ** j) * (d ** (i - j))
                if data.option_type.lower() == "call":
                    exercise = current_price - K
                else:
                    exercise = K - current_price
                    
                # The American Premium: We take the absolute maximum of holding vs exercising early
                values[j] = max(exercise, continuation)
                
        return {
            "status": "success",
            "parameters": {"steps": N, "dt": round(dt, 4)},
            "results": {
                "american_price": round(values[0], 4)
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
