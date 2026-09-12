from pathlib import Path
p = Path('app.py')
raw = p.read_bytes()
nl = '\r\n' if b'\r\n' in raw else '\n'
text = raw.decode('utf-8').replace('\r\n','\n')
old = '''            if isinstance(cached_analysis, dict):
                if cache_state == "stale":
                    _schedule_cache_refresh(background_tasks, f"stock_analysis:{ticker}", "stock_analysis", get_stock_data, ticker, None, False)
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
                return cached_analysis
'''
new = '''            if isinstance(cached_analysis, dict):
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
                return cached_analysis
'''
if text.count(old) != 1:
    raise SystemExit(f'ordering block matches: {text.count(old)}')
text = text.replace(old,new,1)
p.write_bytes(text.replace('\n',nl).encode('utf-8'))
