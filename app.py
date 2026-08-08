from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import requests
import pandas as pd
import re

app = FastAPI(title="SethiStock Data Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=False, # Securely False
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    })
    return session

@app.get("/")
def health_check():
    return {"status": "SethiStock API is online."}

@app.get("/api/stock/{ticker}")
def get_stock_data(ticker: str):
    try:
        session = get_session()
        stock = yf.Ticker(ticker.upper(), session=session)
        f_info = stock.fast_info
        
        # 1. Core Pricing
        current_price = getattr(f_info, 'last_price', 0)
        prev_close = getattr(f_info, 'previous_close', 0)
        change = current_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close else 0
        mkt_cap = getattr(f_info, 'market_cap', 0)
        shares = getattr(f_info, 'shares', 0)

        # 2. Strict Financial Statement Extraction
        fin = stock.financials
        cf = stock.cashflow
        bs = stock.balance_sheet
        
        fin_data = {
            "years": [], "revenue": [], "operating": [], "net": [], 
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
                        extracted = [float(df.loc[orig_idx, c]) if not pd.isna(df.loc[orig_idx, c]) else 0 for c in cols]
                        if any(extracted): return extracted
                    except: pass
            return [0] * len(cols)

        if not fin.empty:
            cols = fin.columns[::-1] 
            fin_data["years"] = [str(c.year) for c in cols]

            rev = get_hist(fin, ['Total Revenue', 'Operating Revenue'])
            net = get_hist(fin, ['Net Income', 'Net Income Common Stockholders', 'Net Profit'])
            op_inc = get_hist(fin, ['Operating Income', 'Operating Profit'])
            gross = get_hist(fin, ['Gross Profit'])

            fin_data["revenue"] = rev if rev else [0]*len(cols)
            fin_data["operating"] = op_inc if op_inc else [0]*len(cols)
            fin_data["net"] = net if net else [0]*len(cols)

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
        
        latest_fcf = fin_data["fcf"][-1] if fin_data["fcf"] and fin_data["fcf"][-1] != 0 else 0

        # 3. Advanced Stats & Sethi Score Algorithm
        try:
            info = stock.info
            def format_mkt_cap(val):
                if val >= 1e12: return f"${val/1e12:.2f}T"
                if val >= 1e9: return f"${val/1e9:.2f}B"
                if val >= 1e6: return f"${val/1e6:.2f}M"
                return f"${val:,.0f}"

            fcf_yield_raw = (latest_fcf / mkt_cap) if mkt_cap and latest_fcf else None
            fcf_yield = f"{round(fcf_yield_raw * 100, 2)}%" if fcf_yield_raw else "N/A"

            div_yield_raw = info.get("dividendYield")
            div_yield = f"{round(div_yield_raw * 100, 2)}%" if div_yield_raw is not None else "N/A"

            # --- ALGORITHMIC SETHI SCORE (Out of 100) ---
            score = 0
            # 1. Positive Net Income (10 pts)
            if fin_data["net"] and fin_data["net"][-1] > 0: score += 10
            # 2. Revenue Growth (10 pts)
            if len(fin_data["revenue"]) >= 2 and fin_data["revenue"][-1] > fin_data["revenue"][-2]: score += 10
            # 3. Positive Free Cash Flow (10 pts)
            if latest_fcf > 0: score += 10
            # 4. ROE > 15% (10 pts)
            if info.get("returnOnEquity") and info.get("returnOnEquity") > 0.15: score += 10
            # 5. Net Margin > 10% (10 pts)
            if info.get("profitMargins") and info.get("profitMargins") > 0.10: score += 10
            # 6. Debt/Equity < 1.0 (10 pts)
            if info.get("debtToEquity") and info.get("debtToEquity") < 100: score += 10 # Yahoo returns 100 for 1.0
            # 7. FCF Yield > 5% (10 pts)
            if fcf_yield_raw and fcf_yield_raw > 0.05: score += 10
            # 8. P/E < 25 (10 pts)
            if info.get("trailingPE") and 0 < info.get("trailingPE") < 25: score += 10
            # 9. P/B < 5 (10 pts)
            if info.get("priceToBook") and 0 < info.get("priceToBook") < 5: score += 10
            # 10. Pays a Dividend (10 pts)
            if div_yield_raw and div_yield_raw > 0: score += 10

            stats = {
                "pe": round(info.get("trailingPE", 0), 2) if info.get("trailingPE") else "N/A",
                "pb": round(info.get("priceToBook", 0), 2) if info.get("priceToBook") else "N/A",
                "eps": round(info.get("trailingEps", 0), 2) if info.get("trailingEps") else "N/A",
                "ev_ebitda": round(info.get("enterpriseToEbitda", 0), 2) if info.get("enterpriseToEbitda") else "N/A",
                "mkt_cap": format_mkt_cap(mkt_cap) if mkt_cap else "N/A",
                "fcf_yield": fcf_yield,
                "div_yield": div_yield,
                "sethi_score": score
            }
        except:
            stats = {"pe": "N/A", "pb": "N/A", "eps": "N/A", "ev_ebitda": "N/A", "mkt_cap": "N/A", "fcf_yield": "N/A", "div_yield": "N/A", "sethi_score": 0}

        return {
            "ticker": ticker.upper(), "current_price": round(current_price, 2),
            "change": round(change, 2), "pct_change": round(pct_change, 2),
            "shares": shares, "fcf": latest_fcf, "financials": fin_data, "stats": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/chart/{ticker}")
def get_chart_data(ticker: str, period: str = "1y", interval: str = "1d"):
    try:
        session = get_session()
        stock = yf.Ticker(ticker.upper(), session=session)
        hist = stock.history(period=period, interval=interval)
        if hist.empty: return {"dates": [], "opens": [], "highs": [], "lows": [], "closes": []}
        if period == "max": hist = hist.loc['2000':] 
        return {
            "dates": hist.index.strftime('%Y-%m-%d %H:%M:%S').tolist(),
            "opens": hist['Open'].tolist(), "highs": hist['High'].tolist(),
            "lows": hist['Low'].tolist(), "closes": hist['Close'].tolist()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
