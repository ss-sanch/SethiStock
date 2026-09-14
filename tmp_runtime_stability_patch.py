from pathlib import Path
import re

path = Path('app.py')
raw = path.read_bytes().decode('utf-8')
newline = '\r\n' if '\r\n' in raw else '\n'
s = raw.replace('\r\n', '\n')
original = s

s = s.replace('from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError\n', '')
s = s.replace('_RESEARCH_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sethistock-research")\n', '''STOCK_ANALYSIS_MAX_CONCURRENT = max(1, int(os.getenv("SETHISTOCK_MAX_STOCK_ANALYSES", "1")))\nSTOCK_ANALYSIS_QUEUE_TIMEOUT = max(1.0, float(os.getenv("SETHISTOCK_ANALYSIS_QUEUE_TIMEOUT", "20")))\n_STOCK_ANALYSIS_GATE = threading.BoundedSemaphore(STOCK_ANALYSIS_MAX_CONCURRENT)\n_STOCK_ANALYSIS_STATE_LOCK = threading.Lock()\n_STOCK_ANALYSIS_ACTIVE = 0\n_STOCK_ANALYSIS_PEAK = 0\n_STOCK_ANALYSIS_TOTAL = 0\n_STOCK_ANALYSIS_ACTIVE_TICKERS = {}\n\n\ndef _process_rss_mb():\n    try:\n        with open("/proc/self/status", "r", encoding="utf-8") as handle:\n            for line in handle:\n                if line.startswith("VmRSS:"):\n                    return round(int(line.split()[1]) / 1024.0, 1)\n    except Exception:\n        return None\n    return None\n\n\ndef _stock_analysis_started(ticker):\n    global _STOCK_ANALYSIS_ACTIVE, _STOCK_ANALYSIS_PEAK, _STOCK_ANALYSIS_TOTAL\n    symbol = str(ticker or "").upper()\n    with _STOCK_ANALYSIS_STATE_LOCK:\n        _STOCK_ANALYSIS_ACTIVE += 1\n        _STOCK_ANALYSIS_TOTAL += 1\n        _STOCK_ANALYSIS_PEAK = max(_STOCK_ANALYSIS_PEAK, _STOCK_ANALYSIS_ACTIVE)\n        _STOCK_ANALYSIS_ACTIVE_TICKERS[symbol] = _STOCK_ANALYSIS_ACTIVE_TICKERS.get(symbol, 0) + 1\n\n\ndef _stock_analysis_finished(ticker):\n    global _STOCK_ANALYSIS_ACTIVE\n    symbol = str(ticker or "").upper()\n    with _STOCK_ANALYSIS_STATE_LOCK:\n        _STOCK_ANALYSIS_ACTIVE = max(0, _STOCK_ANALYSIS_ACTIVE - 1)\n        count = _STOCK_ANALYSIS_ACTIVE_TICKERS.get(symbol, 0) - 1\n        if count > 0:\n            _STOCK_ANALYSIS_ACTIVE_TICKERS[symbol] = count\n        else:\n            _STOCK_ANALYSIS_ACTIVE_TICKERS.pop(symbol, None)\n''')

needle = '\ndef _analysis_cache_payload_valid(payload):\n'
assert needle in s, 'analysis cache marker missing'
runtime_endpoint = '''\n@app.get("/api/runtime/status")\ndef runtime_status():\n    with _STOCK_ANALYSIS_STATE_LOCK:\n        active = _STOCK_ANALYSIS_ACTIVE\n        peak = _STOCK_ANALYSIS_PEAK\n        total = _STOCK_ANALYSIS_TOTAL\n        tickers = dict(_STOCK_ANALYSIS_ACTIVE_TICKERS)\n    return {\n        "rss_mb": _process_rss_mb(),\n        "stock_analyses_active": active,\n        "stock_analyses_peak": peak,\n        "stock_analyses_total": total,\n        "active_tickers": tickers,\n        "max_concurrent_stock_analyses": STOCK_ANALYSIS_MAX_CONCURRENT,\n        "analysis_queue_timeout_seconds": STOCK_ANALYSIS_QUEUE_TIMEOUT,\n        "cache_refreshes_inflight": len(_CACHE_REFRESH_INFLIGHT),\n    }\n\n'''
s = s.replace(needle, runtime_endpoint + needle, 1)

pattern = re.compile(
    r'        # Reuse the already-fetched five-year price history for valuation/earnings research\.\n'
    r'.*?'
    r'        # 2\. RUN THE FINVIZ SCRAPER TO FILL IN THE BLANKS\n',
    re.S,
)
replacement = '''        # Reuse the already-fetched five-year price history for valuation/earnings research.\n        # Keep this inside the bounded stock-analysis request rather than spawning another\n        # worker that can overlap memory-heavy pandas/yfinance work on a small Render instance.\n        current_pe_hint = None\n        try:\n            raw_pe = (info or {}).get("trailingPE")\n            if raw_pe is not None:\n                current_pe_hint = float(raw_pe)\n                if not np.isfinite(current_pe_hint) or current_pe_hint <= 0:\n                    current_pe_hint = None\n        except Exception:\n            current_pe_hint = None\n\n        # 2. RUN THE FINVIZ SCRAPER TO FILL IN THE BLANKS\n'''
s, count = pattern.subn(replacement, s, count=1)
assert count == 1, 'research executor launch block not replaced'

needle = '        f_info, fin, cf, bs, info = None, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}\n'
assert needle in s, 'heavy analysis start marker missing'
gate = '''        analysis_slot_acquired = False\n        if not is_peer:\n            if not _STOCK_ANALYSIS_GATE.acquire(timeout=STOCK_ANALYSIS_QUEUE_TIMEOUT):\n                raise HTTPException(status_code=503, detail="SethiStock analysis is busy. Please retry shortly.")\n            analysis_slot_acquired = True\n            _stock_analysis_started(ticker)\n\n            # A previous queued request may have populated this exact ticker while we waited.\n            queued_cached, _queued_state = _cache_get_swr("stock_analysis", ticker, CACHE_STALE_STOCK)\n            if _analysis_cache_payload_valid(queued_cached):\n                queued_cached = copy.deepcopy(queued_cached)\n                _stock_analysis_finished(ticker)\n                _STOCK_ANALYSIS_GATE.release()\n                analysis_slot_acquired = False\n                return queued_cached\n\n'''
s = s.replace(needle, gate + needle, 1)

old_research = '''        research_payload = {\n            "ticker": ticker.upper(),\n            "earnings_reaction": {"available": False},\n            "valuation_bands": {"available": False},\n        }\n        if research_future is not None:\n            try:\n                research_payload = research_future.result(timeout=2.5)\n            except FuturesTimeoutError:\n                pass\n            except Exception:\n                pass\n'''
assert old_research in s, 'research result block missing'
new_research = '''        research_payload = {\n            "ticker": ticker.upper(),\n            "earnings_reaction": {"available": False},\n            "valuation_bands": {"available": False},\n        }\n        try:\n            research_payload = stock_research.get_stock_research_payload(\n                ticker, stock, shared_hist, current_pe_hint\n            )\n        except Exception:\n            pass\n'''
s = s.replace(old_research, new_research, 1)

old_tail = '''        if not is_peer:\n            _cache_write("stock_analysis", ticker, result, CACHE_TTL_STOCK, ticker=ticker)\n        return result\n    except Exception as e:\n        raise HTTPException(status_code=500, detail=str(e))\n'''
assert old_tail in s, 'stock analysis tail missing'
new_tail = '''        if not is_peer:\n            _cache_write("stock_analysis", ticker, result, CACHE_TTL_STOCK, ticker=ticker)\n        if analysis_slot_acquired:\n            _stock_analysis_finished(ticker)\n            _STOCK_ANALYSIS_GATE.release()\n            analysis_slot_acquired = False\n        return result\n    except HTTPException:\n        if locals().get("analysis_slot_acquired", False):\n            _stock_analysis_finished(locals().get("ticker", raw_ticker))\n            _STOCK_ANALYSIS_GATE.release()\n            analysis_slot_acquired = False\n        raise\n    except Exception as e:\n        if locals().get("analysis_slot_acquired", False):\n            _stock_analysis_finished(locals().get("ticker", raw_ticker))\n            _STOCK_ANALYSIS_GATE.release()\n            analysis_slot_acquired = False\n        raise HTTPException(status_code=500, detail=str(e))\n'''
s = s.replace(old_tail, new_tail, 1)

assert s != original
assert '_RESEARCH_EXECUTOR' not in s
assert 'research_future' not in s
out = s if newline == '\n' else s.replace('\n', '\r\n')
path.write_bytes(out.encode('utf-8'))
print({'status':'BACKEND_RUNTIME_PATCH_OK','newline':repr(newline)})
