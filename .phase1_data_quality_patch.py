from pathlib import Path

path = Path('app.py')
raw = path.read_bytes()
newline = '\r\n' if b'\r\n' in raw else '\n'
text = raw.decode('utf-8').replace('\r\n', '\n')

def replace_once(old, new):
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'Expected one match, found {count}: {old[:140]!r}')
    text = text.replace(old, new, 1)

replace_once(
'''@app.get("/api/stock/{raw_ticker}")
def get_stock_data(raw_ticker: str, background_tasks: BackgroundTasks = None, is_peer: bool = False):
''',
'''def _analysis_cache_payload_valid(payload):
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
    return True


@app.get("/api/stock/{raw_ticker}")
def get_stock_data(raw_ticker: str, background_tasks: BackgroundTasks = None, is_peer: bool = False):
'''
)

replace_once(
'''            cached_analysis, cache_state = _cache_get_swr("stock_analysis", ticker, CACHE_STALE_STOCK)
            if isinstance(cached_analysis, dict):
''',
'''            cached_analysis, cache_state = _cache_get_swr("stock_analysis", ticker, CACHE_STALE_STOCK)
            if _analysis_cache_payload_valid(cached_analysis):
'''
)

replace_once(
'''        try:
            shared_hist = stock.history(period="5y", interval="1d")
        except Exception:
            pass

        recent_hist = shared_hist.tail(5).copy() if shared_hist is not None and not shared_hist.empty else pd.DataFrame()
''',
'''        try:
            shared_hist = stock.history(period="5y", interval="1d")
        except Exception:
            pass

        # Yahoo can append an incomplete current-session row with a null Close.
        # Drop it before price, risk and technical calculations so bad rows cannot poison the 6h analysis cache.
        if shared_hist is not None and not shared_hist.empty and 'Close' in shared_hist.columns:
            numeric_close = pd.to_numeric(shared_hist['Close'], errors='coerce')
            valid_close = numeric_close.notna() & np.isfinite(numeric_close) & (numeric_close > 0)
            shared_hist = shared_hist.loc[valid_close].copy()

        recent_hist = shared_hist.tail(5).copy() if shared_hist is not None and not shared_hist.empty else pd.DataFrame()
'''
)

replace_once(
'''        def safe_float(val, fallback=0.0):
            try:
                if val is None or pd.isna(val) or val == '-': return float(fallback)
                if isinstance(val, str):
                    val = val.replace(',', '').replace('%', '')
                return float(val)
            except Exception:
                return float(fallback)
''',
'''        def safe_float(val, fallback=0.0):
            try:
                if val is None or pd.isna(val) or val == '-': return float(fallback)
                if isinstance(val, str):
                    val = val.replace(',', '').replace('%', '')
                result = float(val)
                return result if np.isfinite(result) else float(fallback)
            except Exception:
                return float(fallback)
'''
)

replace_once(
'''        div_yield_raw = safe_float(info.get("dividendYield") if info else info.get("trailingAnnualDividendYield") if info else None)
        div_yield = f"{round(div_yield_raw * 100, 2)}%" if div_yield_raw > 0 else fallback_div_yield
''',
'''        div_yield_raw = safe_float(info.get("dividendYield") if info else None)
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
'''
)

replace_once(
'''            rsi_14 = round((100 - (100 / (1 + rs))).iloc[-1], 2)

            low_14 = lows.rolling(14).min().iloc[-1]
            high_14 = highs.rolling(14).max().iloc[-1]
            stoch_k = round(100 * ((current_price - low_14) / (high_14 - low_14)), 2) if high_14 != low_14 else 50

            if len(daily_hist) >= 200:
                sma_200 = closes.rolling(200).mean().iloc[-1]
                sma_200_pct = round(((current_price - sma_200) / sma_200) * 100, 2)

        dist_52w_high = round(((current_price - fiftyTwoWeekHigh) / fiftyTwoWeekHigh) * 100, 2) if fiftyTwoWeekHigh and fiftyTwoWeekHigh > 0 else "N/A"
''',
'''            rsi_value = (100 - (100 / (1 + rs))).iloc[-1]
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
'''
)

replace_once(
'''def _peer_metric(value, digits=2):
    value = _peer_safe_float(value)
    return round(value, digits) if value is not None else "N/A"


def _default_peers_for_ticker(symbol: str):
''',
'''def _peer_metric(value, digits=2):
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
'''
)

replace_once(
'''            dividend_yield = _peer_safe_float(info.get("dividendYield"))
            if dividend_yield is None:
                dividend_yield = _peer_safe_float(info.get("trailingAnnualDividendYield"))
''',
'''            dividend_yield_pct = _peer_dividend_yield_pct(info)
'''
)

replace_once(
'''                    "div_yield": f"{round(dividend_yield * 100.0, 2)}%" if dividend_yield is not None and dividend_yield > 0 else "N/A",
''',
'''                    "div_yield": f"{round(dividend_yield_pct, 2)}%" if dividend_yield_pct is not None and dividend_yield_pct > 0 else "N/A",
'''
)

path.write_bytes(text.replace('\n', newline).encode('utf-8'))
