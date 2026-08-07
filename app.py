from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import requests
import pandas as pd

app = FastAPI(title="SethiStock Data Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allowing all for seamless testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
    })
    return session

@app.get("/")
def health_check():
    return {"status": "SethiStock API is online."}

@app.get("/api/stock/{ticker}")
def get_stock_data(ticker: str):
    """Fetches core pricing, DCF inputs, and deep Insights/Stats."""
    try:
        session = get_session()
        stock = yf.Ticker(ticker.upper(), session=session)
        f_info = stock.fast_info
        
        # 1. Pricing
        current_price = f_info.last_price
        prev_close = f_info.previous_close
        change = current_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close else 0

        # 2. DCF Inputs
        shares = getattr(f_info, 'shares', 0)
        fcf = 0
        cf = stock.cashflow
        if not cf.empty and 'Operating Cash Flow' in cf.index:
            try:
                ocf = cf.loc['Operating Cash Flow'].dropna().iloc[0]
                capex = abs(cf.loc['Capital Expenditure'].dropna().iloc[0]) if 'Capital Expenditure' in cf.index else 0
                fcf = float(ocf - capex)
            except: pass

        # 3. Financial Statement History (For Bar Charts)
        fin = stock.financials
        fin_data = {"years": [], "revenue": [], "operating": [], "net": []}
        if not fin.empty:
            cols = fin.columns[:4][::-1] # Last 4 years, chronological
            fin_data["years"] = [str(c.year) for c in cols]
            def safe_get(row):
                return [float(fin.loc[row, c]) if row in fin.index and not pd.isna(fin.loc[row, c]) else 0 for c in cols]
            fin_data["revenue"] = safe_get('Total Revenue')
            fin_data["operating"] = safe_get('Operating Income')
            fin_data["net"] = safe_get('Net Income')

        # 4. Advanced Stats
        try:
            info = stock.info
            stats = {
                "pe": round(info.get("trailingPE", 0), 2) if info.get("trailingPE") else "N/A",
                "pb": round(info.get("priceToBook", 0), 2) if info.get("priceToBook") else "N/A",
                "eps": round(info.get("trailingEps", 0), 2) if info.get("trailingEps") else "N/A",
                "roe": round(info.get("returnOnEquity", 0) * 100, 2) if info.get("returnOnEquity") else "N/A",
                "profit_margin": round(info.get("profitMargins", 0) * 100, 2) if info.get("profitMargins") else "N/A",
                "debt_to_eq": round(info.get("debtToEquity", 0), 2) if info.get("debtToEquity") else "N/A"
            }
        except:
            stats = {"pe": "N/A", "pb": "N/A", "eps": "N/A", "roe": "N/A", "profit_margin": "N/A", "debt_to_eq": "N/A"}

        return {
            "ticker": ticker.upper(),
            "current_price": round(current_price, 2),
            "change": round(change, 2),
            "pct_change": round(pct_change, 2),
            "market_cap": getattr(f_info, 'market_cap', 0),
            "shares": shares,
            "fcf": fcf,
            "financials": fin_data,
            "stats": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/chart/{ticker}")
def get_chart_data(ticker: str, period: str = "1y", interval: str = "1d"):
    """Fetches dynamic OHLC chart data based on user timeframe selection."""
    try:
        session = get_session()
        stock = yf.Ticker(ticker.upper(), session=session)
        hist = stock.history(period=period, interval=interval)
        
        if hist.empty:
            return {"dates": [], "opens": [], "highs": [], "lows": [], "closes": []}
            
        if period == "max":
            hist = hist.loc['2000':] # The Y2K Guillotine

        dates = hist.index.strftime('%Y-%m-%d %H:%M:%S').tolist()
        return {
            "dates": dates,
            "opens": hist['Open'].tolist(),
            "highs": hist['High'].tolist(),
            "lows": hist['Low'].tolist(),
            "closes": hist['Close'].tolist()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
