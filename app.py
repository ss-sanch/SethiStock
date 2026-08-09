from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import requests
import pandas as pd
import os
import google.generativeai as genai
from functools import lru_cache

app = FastAPI(title="SethiStock Data Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=False, 
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

def resolve_ticker(query: str):
    query = query.strip()
    try:
        session = get_session()
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&quotesCount=1&newsCount=0"
        res = session.get(url)
        if res.status_code == 200:
            data = res.json()
            if 'quotes' in data and len(data['quotes']) > 0:
                return data['quotes'][0]['symbol']
    except Exception:
        pass
    return query.upper()

@lru_cache(maxsize=100)
def get_ai_summary(ticker: str, news_context: str):
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return "API Key not found in environment."
    
    genai.configure(api_key=api_key)
    # Note: We are using the correct, modern 1.5-flash model!
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    try:
        prompt = (
            f"You are an expert financial analyst. Review the following recent news headlines for {ticker}: \n"
            f"{news_context}\n"
            f"Write a single, highly insightful paragraph analyzing what these developments mean for the company's current market position. "
            f"Do not list the titles. Synthesize the information to explain the broader narrative and why the stock might be moving. Keep the tone professional and objective."
        )
        response = model.generate_content(prompt)
        return response.text.replace('\n', ' ').strip()
    except Exception as e:
        return f"API ERROR: {str(e)}"

@app.get("/")
def health_check():
    return {"status": "SethiStock API is online."}

@app.get("/api/stock/{raw_ticker}")
def get_stock_data(raw_ticker: str):
    try:
        ticker = resolve_ticker(raw_ticker)
        session = get_session()
        stock = yf.Ticker(ticker, session=session)
        f_info = stock.fast_info
        
        current_price = getattr(f_info, 'last_price', 0)
        prev_close = getattr(f_info, 'previous_close', 0)
        change = current_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close else 0
        mkt_cap = getattr(f_info, 'market_cap', 0)
        shares = getattr(f_info, 'shares', 0)

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
                        "shares": float(row[shares_col]) if shares_col and pd.notna(row[shares_col]) else 0,
                        "value": float(row[val_col]) if val_col and pd.notna(row[val_col]) else 0,
                    })
        except Exception:
            pass

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
            div_yield = f"{round(div_yield_raw, 2)}%" if div_yield_raw is not None else "N/A"
            
            book_value = info.get("bookValue")
            if not book_value and info.get("priceToBook") and current_price:
                book_value = current_price / info.get("priceToBook")

            fiftyTwoWeekHigh = info.get("fiftyTwoWeekHigh", current_price * 1.2)
            fiftyTwoWeekLow = info.get("fiftyTwoWeekLow", current_price * 0.8)

            score = 0
            if fin_data["net"] and fin_data["net"][-1] > 0: score += 10
            if len(fin_data["revenue"]) >= 2 and fin_data["revenue"][-1] > fin_data["revenue"][-2]: score += 10
            if latest_fcf > 0: score += 10
            if info.get("returnOnEquity") and info.get("returnOnEquity") > 0.15: score += 10
            if info.get("profitMargins") and info.get("profitMargins") > 0.10: score += 10
            if info.get("debtToEquity") and info.get("debtToEquity") < 100: score += 10
            if fcf_yield_raw and fcf_yield_raw > 0.05: score += 10
            if info.get("trailingPE") and 0 < info.get("trailingPE") < 25: score += 10
            if info.get("priceToBook") and 0 < info.get("priceToBook") < 5: score += 10
            if div_yield_raw and div_yield_raw > 0: score += 10

            stats = {
                "pe": round(info.get("trailingPE", 0), 2) if info.get("trailingPE") else "N/A",
                "pb": round(info.get("priceToBook", 0), 2) if info.get("priceToBook") else "N/A",
                "eps": round(info.get("trailingEps", 0), 2) if info.get("trailingEps") else "N/A",
                "ev_ebitda": round(info.get("enterpriseToEbitda", 0), 2) if info.get("enterpriseToEbitda") else "N/A",
                "mkt_cap": format_mkt_cap(mkt_cap) if mkt_cap else "N/A",
                "fcf_yield": fcf_yield,
                "div_yield": div_yield,
                "sethi_score": score,
                "book_value": book_value if book_value else 0,
                "fiftyTwoWeekHigh": fiftyTwoWeekHigh,
                "fiftyTwoWeekLow": fiftyTwoWeekLow
            }
        except:
            stats = {"pe": "N/A", "pb": "N/A", "eps": "N/A", "ev_ebitda": "N/A", "mkt_cap": "N/A", "fcf_yield": "N/A", "div_yield": "N/A", "sethi_score": 0, "book_value": 0, "fiftyTwoWeekHigh": current_price*1.2, "fiftyTwoWeekLow": current_price*0.8}

        raw_summary = info.get("longBusinessSummary", "Company profile not currently available.")
        sentences = raw_summary.split('. ')
        short_summary = '. '.join(sentences[:3]) + '.' if len(sentences) > 2 else raw_summary

        # --- NEWS PARSER & AI SUMMARY ---
        raw_news = stock.news
        news_context = "" 
        
        if raw_news:
            for article in raw_news[:3]:
                content = article.get('content', article)
                title = content.get("title", article.get("title", "No Title"))
                news_context += f"- {title}\n"
                
        if news_context.strip():
            # This is where the magic happens! It uses the cache.
            ai_summary = get_ai_summary(ticker.upper(), news_context)
        else:
            ai_summary = "No recent news available to generate an analysis."

        industry = info.get('industry', 'Unknown') if 'info' in locals() else 'Unknown'

        industry = info.get('industry', 'Unknown') if 'info' in locals() else 'Unknown'
        industry_map = {
            'Consumer Electronics': ['MSFT', 'GOOGL', 'META'],
            'Software - Infrastructure': ['AMZN', 'GOOGL', 'MSFT'],
            'Semiconductors': ['AMD', 'INTC', 'TSM', 'NVDA', 'AVGO'],
            'Internet Content & Information': ['GOOGL', 'META', 'SNAP', 'PINS'],
            'Auto Manufacturers': ['TSLA', 'F', 'GM', 'TM', 'RIVN'],
            'Banks - Diversified': ['JPM', 'BAC', 'WFC', 'C'],
            'E-Commerce': ['AMZN', 'BABA', 'WMT', 'EBAY'],
            'Travel Services': ['BKNG', 'EXPE', 'ABNB', 'TRIP'],
            'Software - Application': ['CRM', 'ADBE', 'NOW', 'PLTR'],
            'Software - Travel': ['LYFT', 'ABNB', 'DASH'],
            'Technology': ['AAPL', 'MSFT', 'GOOGL']
        }
        
        candidate_peers = industry_map.get(industry, ['SPY', 'QQQ', 'DIA'])
        peers = [p for p in candidate_peers if p.upper() != ticker.upper()][:3]

        return {
            "ticker": ticker.upper(), "current_price": round(current_price, 2),
            "change": round(change, 2), "pct_change": round(pct_change, 2),
            "shares": shares, "fcf": latest_fcf, "financials": fin_data, "stats": stats,
            "insiders": insider_list, "peers": peers,
            "summary": short_summary, "ai_summary": ai_summary
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/chart/{raw_ticker}")
def get_chart_data(raw_ticker: str, period: str = "1y", interval: str = "1d"):
    try:
        ticker = resolve_ticker(raw_ticker)
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
