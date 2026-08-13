from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
import numpy as np 
import time

app = FastAPI(title="SethiStock Data Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=False, 
    allow_methods=["*"],
    allow_headers=["*"],
)

def resolve_ticker(query: str):
    query = query.strip()
    try:
        ticker_obj = yf.Ticker(query)
        info_dict = getattr(ticker_obj, 'info', {})
        if info_dict and 'symbol' in info_dict:
            return info_dict['symbol']
    except Exception:
        pass
    return query.upper()

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
def get_stock_data(raw_ticker: str):
    try:
        ticker = resolve_ticker(raw_ticker)
        
        f_info, fin, cf, bs, info = None, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}
        q_fin = pd.DataFrame() 
        
        # PURE NATIVE FETCH - Letting yfinance manage its own cookies and crumbs
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

        # =================================================================
        # --- SECURE MATH ENGINE ---
        # =================================================================
        def safe_float(val, fallback=0.0):
            try:
                if val is None or pd.isna(val): return float(fallback)
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

        insider_list = []
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

        # =======================================================
        # NEW: PROPRIETARY DATA MATHEMATICAL FALLBACKS
        # =======================================================
        
        # 1. Manually Calculate EV/EBITDA
        try:
            cash_on_hand = fin_data["cash"][-1] if fin_data["cash"] and len(fin_data["cash"]) > 0 else 0
            ev = final_mkt_cap + fallback_debt - cash_on_hand
            ebitda = fin_data["operating"][-1] if fin_data["operating"] and len(fin_data["operating"]) > 0 else 0
            fallback_ev_ebitda = round(ev / ebitda, 2) if ebitda > 0 else "N/A"
        except Exception:
            fallback_ev_ebitda = "N/A"

        # 2. Manually Calculate 1-Year Dividend Yield
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

        # 3. Manually Calculate Beta (1-Year Volatility vs S&P 500)
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

        # =======================================================

        def format_mkt_cap(val):
            if val >= 1e12: return f"${val/1e12:.2f}T"
            if val >= 1e9: return f"${val/1e9:.2f}B"
            if val >= 1e6: return f"${val/1e6:.2f}M"
            return f"${val:,.0f}"
            
        fcf_yield_raw = (latest_fcf / final_mkt_cap) if final_mkt_cap and latest_fcf else 0
        fcf_yield = f"{round(fcf_yield_raw * 100, 2)}%" if fcf_yield_raw != 0 else "N/A"
        
        # Inject our calculated Div Yield if the API fails
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
        short_float = info.get("shortPercentOfFloat") if info else None
        short_interest = f"{round(short_float * 100, 2)}%" if short_float else "N/A"

        next_earnings = "N/A"
        next_dividend = "N/A"
        try:
            cal = stock.calendar
            if isinstance(cal, dict) and 'Earnings Date' in cal:
                raw_earnings = cal['Earnings Date']
                if isinstance(raw_earnings, list) and len(raw_earnings) > 0:
                    next_earnings = raw_earnings[0].strftime('%b %d, %Y')
        except Exception:
            pass 

        stats = {
            "pe": round(actual_pe, 2) if actual_pe else "N/A",
            "pb": round(actual_pb, 2) if actual_pb else "N/A",
            "eps": round(actual_eps, 2) if actual_eps else "N/A",
            "forward_eps": round(info.get("forwardEps", 0), 2) if info and info.get("forwardEps") else "N/A",
            # Inject our calculated EV/EBITDA and Beta here
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

        raw_summary = info.get("longBusinessSummary", "Company profile not currently available.") if info else "Company profile not currently available."
        sentences = raw_summary.split('. ')
        short_summary = '. '.join(sentences[:3]) + '.' if len(sentences) > 2 else raw_summary

        # =======================================================
        # NEW: HARDCODED PEER MATRIX (Bypasses the missing 'info' dict)
        # =======================================================
        ticker_peers = {
            'AAPL': ['MSFT', 'GOOGL', 'META'],
            'MSFT': ['AAPL', 'GOOGL', 'AMZN'],
            'TSLA': ['F', 'GM', 'RIVN'],
            'NVDA': ['AMD', 'INTC', 'TSM'],
            'AMZN': ['WMT', 'BABA', 'EBAY'],
            'META': ['GOOGL', 'SNAP', 'PINS'],
            'GOOGL': ['META', 'MSFT', 'AMZN'],
            'NFLX': ['DIS', 'WBD', 'AMZN'],
            'JPM': ['BAC', 'WFC', 'C'],
            'V': ['MA', 'AXP', 'PYPL'],
            'AMD': ['NVDA', 'INTC', 'QCOM']
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
