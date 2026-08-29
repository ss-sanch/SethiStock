from fastapi import FastAPI, HTTPException
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
from datetime import datetime
import math
from scipy.stats import norm
from scipy.optimize import minimize
import market_risk_lab

# --- SUPABASE TELEMETRY ENGINE ---
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "admin123")

class TelemetryPayload(BaseModel):
    project: str
    action: str
    ticker: Optional[str] = None
    visitor_id: Optional[str] = None # <-- NEW: Accept the anonymous ID

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

def calculate_risk_profile(ticker_symbol):
    try:
        stock = yf.Ticker(ticker_symbol)
        hist = stock.history(period="5y")
            
        daily_returns = hist['Close'].pct_change().dropna()
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

@app.get("/api/stock/{raw_ticker}")
def get_stock_data(raw_ticker: str, is_peer: bool = False):
    try:
        if not is_peer:
            log_telemetry_event(project="SethiStock", action="ticker_search", ticker=raw_ticker)
            
        ticker = resolve_ticker(raw_ticker)
        
        f_info, fin, cf, bs, info = None, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}
        q_fin = pd.DataFrame() 
        
        # 1. NATIVE YFINANCE FETCH
        stock = yf.Ticker(ticker)
        
        try: f_info = stock.fast_info
        except Exception: f_info = None
        
        try: fin = stock.financials
        except Exception: fin = pd.DataFrame()
        
        try: cf = stock.cashflow
        except Exception: cf = pd.DataFrame()
        
        try: bs = stock.balance_sheet
        except Exception: bs = pd.DataFrame()
        
        try: q_fin = stock.quarterly_financials
        except Exception: q_fin = pd.DataFrame()
        
        try: 
            fetched_info = stock.info 
            if fetched_info: info = fetched_info  
        except Exception: pass
        
        recent_hist = pd.DataFrame()
        try:
            recent_hist = stock.history(period="5d")
        except Exception:
            pass

        # 2. RUN THE FINVIZ SCRAPER TO FILL IN THE BLANKS
        fv_stats, fv_insiders, fv_summary = scrape_finviz_data(ticker)

        # =================================================================
        # --- SECURE MATH ENGINE ---
        # =================================================================
        def safe_float(val, fallback=0.0):
            try:
                if val is None or pd.isna(val) or val == '-': return float(fallback)
                if isinstance(val, str):
                    val = val.replace(',', '').replace('%', '')
                return float(val)
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
            
        change = current_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close else 0

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
        
        latest_fcf = fin_data["fcf"][-1] if fin_data["fcf"] and len(fin_data["fcf"]) > 0 and fin_data["fcf"][-1] != 0 else 0
        dupont_metrics = calculate_dupont_analysis(fin, bs) if isinstance(fin, pd.DataFrame) and isinstance(bs, pd.DataFrame) else {}
        risk_metrics = calculate_risk_profile(ticker)
        
        base_fcf_projections = [latest_fcf * ((1.15) ** i) for i in range(1, 6)] if latest_fcf > 0 else [0,0,0,0,0]
        sensitivity_matrix = generate_sensitivity_matrix(0.10, 15.0, base_fcf_projections, shares)

        # Use Finviz Insiders if available, otherwise try Yahoo
        insider_list = fv_insiders
        if not insider_list:
            try:
                ins_df = stock.insider_transactions
                if ins_df is not None and not ins_df.empty:
                    ins_df = ins_df.reset_index()
                    ins_df.columns = [str(c).strip() for c in ins_df.columns]
                    
                    name_col = next((c for c in ins_df.columns if 'insider' in str(c).lower() or 'name' in str(c).lower()), None)
                    pos_col = next((c for c in ins_df.columns if 'position' in str(c).lower() or 'title' in str(c).lower()), None)
                    shares_col = next((c for c in ins_df.columns if 'share' in str(c).lower()), None)
                    val_col = next((c for c in ins_df.columns if 'value' in str(c).lower()), None)

                    for _, row in ins_df.head(15).iterrows():
                        raw_action = str(row.get('Text', row.get('Transaction Text', row.get('Acquisition or Disposition', row.get('Transaction', ''))))).lower()
                        if not raw_action or raw_action == 'nan':
                            trans_col = next((c for c in ins_df.columns if 'text' in str(c).lower() or 'action' in str(c).lower() or 'transaction' in str(c).lower()), None)
                            if trans_col: raw_action = str(row[trans_col]).lower()

                        if 'buy' in raw_action or 'purchase' in raw_action or raw_action.strip() == 'a': action = 'Buy'
                        elif 'sell' in raw_action or 'sale' in raw_action or raw_action.strip() == 'd': action = 'Sell'
                        elif 'grant' in raw_action or 'award' in raw_action or 'option' in raw_action: action = 'Grant'
                        else: action = 'Execute/Other'

                        insider_list.append({
                            "name": str(row[name_col]) if name_col else "Executive",
                            "position": str(row[pos_col]) if pos_col else "N/A",
                            "transaction": action,
                            "shares": safe_float(row[shares_col]),
                            "value": safe_float(row[val_col])
                        })
            except Exception:
                pass

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
            ebitda = fin_data["operating"][-1] if fin_data["operating"] and len(fin_data["operating"]) > 0 else 0
            fallback_ev_ebitda = round(ev / ebitda, 2) if ebitda > 0 else "N/A"
        except Exception:
            fallback_ev_ebitda = "N/A"

        fallback_div_yield = "N/A"
        try:
            divs = stock.dividends
            if divs is not None and not divs.empty:
                recent_divs = divs[divs.index > (pd.Timestamp.now(tz=divs.index.tz) - pd.DateOffset(years=1))]
                if not recent_divs.empty and current_price > 0:
                    yield_pct = (recent_divs.sum() / current_price) * 100
                    fallback_div_yield = f"{round(yield_pct, 2)}%"
        except Exception:
            pass

        fallback_beta = "N/A"
        try:
            spy_hist = yf.Ticker("SPY").history(period="1y")
            daily_hist = stock.history(period="1y")
            if not daily_hist.empty and not spy_hist.empty:
                stock_rets = daily_hist['Close'].pct_change().dropna()
                spy_rets = spy_hist['Close'].pct_change().dropna()
                aligned = pd.concat([stock_rets, spy_rets], axis=1).dropna()
                covar = np.cov(aligned.iloc[:,0], aligned.iloc[:,1])[0][1]
                spy_var = np.var(aligned.iloc[:,1])
                fallback_beta = round(covar / spy_var, 2) if spy_var > 0 else "N/A"
        except Exception:
            pass

        def format_mkt_cap(val):
            if val >= 1e12: return f"${val/1e12:.2f}T"
            if val >= 1e9: return f"${val/1e9:.2f}B"
            if val >= 1e6: return f"${val/1e6:.2f}M"
            return f"${val:,.0f}"
            
        fcf_yield_raw = (latest_fcf / final_mkt_cap) if final_mkt_cap and latest_fcf else 0
        fcf_yield = f"{round(fcf_yield_raw * 100, 2)}%" if fcf_yield_raw != 0 else "N/A"
        
        div_yield_raw = safe_float(info.get("dividendYield") if info else info.get("trailingAnnualDividendYield") if info else None)
        div_yield = f"{round(div_yield_raw * 100, 2)}%" if div_yield_raw > 0 else fallback_div_yield
        
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
            daily_hist = stock.history(period="1y")
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
            rsi_14 = round((100 - (100 / (1 + rs))).iloc[-1], 2)

            low_14 = lows.rolling(14).min().iloc[-1]
            high_14 = highs.rolling(14).max().iloc[-1]
            stoch_k = round(100 * ((current_price - low_14) / (high_14 - low_14)), 2) if high_14 != low_14 else 50

            if len(daily_hist) >= 200:
                sma_200 = closes.rolling(200).mean().iloc[-1]
                sma_200_pct = round(((current_price - sma_200) / sma_200) * 100, 2)

        dist_52w_high = round(((current_price - fiftyTwoWeekHigh) / fiftyTwoWeekHigh) * 100, 2) if fiftyTwoWeekHigh and fiftyTwoWeekHigh > 0 else "N/A"
        
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

        return {
            "ticker": ticker.upper(), "current_price": round(current_price, 2),
            "change": round(change, 2), "pct_change": round(pct_change, 2),
            "shares": shares, "fcf": latest_fcf, "financials": fin_data, "stats": stats,
            "insiders": insider_list, "peers": peers,
            "summary": short_summary,
            "dupont_analysis": dupont_metrics,            
            "risk_profile": risk_metrics,                
            "sensitivity_matrix": sensitivity_matrix     
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/chart/{raw_ticker}")
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- UNIVERSAL TELEMETRY PING ---
@app.post("/api/telemetry/log")
def log_event(data: TelemetryPayload):
    """Receives page views and events from Sethiway Hub, SethiMacro, etc."""
    log_telemetry_event(project=data.project, action=data.action, ticker=data.ticker, visitor_id=data.visitor_id)
    return {"status": "recorded"}

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

        # 3. Compute Traffic Metrics
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

        return {
            "total_events": total_events,
            "project_breakdown": project_counts,
            "action_breakdown": action_counts,
            "top_tickers": top_tickers,
            "recent_logs": logs[:25],
            "markowitz_logs": mkw_logs
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

        return {
            "status": "success",
            "results": {
                "kpis": {
                    "total_return": round(total_return * 100, 2),
                    "buy_hold_return": round(bh_return * 100, 2),
                    "max_drawdown": round(max_drawdown * 100, 2),
                    "annualised_volatility": round(annual_vol * 100, 2)
                },
                "chart": {
                    "dates": close_prices.index.strftime('%Y-%m-%d').tolist(),
                    "strategy_equity": strategy_equity.round(2).tolist(),
                    "buy_hold_equity": buy_hold_equity.round(2).tolist()
                }
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
