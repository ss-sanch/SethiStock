from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import requests

app = FastAPI(title="SethiStock Data Engine")

allowed_origins = [
    "https://sethiway.com",
    "https://sethistock.sethiway.com",
    "http://localhost:5500", 
    "http://127.0.0.1:5500"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def health_check():
    return {"status": "SethiStock API is online and secure."}

@app.get("/api/stock/{ticker}")
def get_stock_data(ticker: str):
    """Fetches real-time price, 1Y chart history, and DCF cash flow data."""
    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
        })
        
        stock = yf.Ticker(ticker.upper(), session=session)
        f_info = stock.fast_info
        
        # 1. Core Pricing
        current_price = f_info.last_price
        prev_close = f_info.previous_close
        change = current_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close else 0
        
        # 2. Chart History (1 Year)
        hist = stock.history(period="1y")
        dates = hist.index.strftime('%Y-%m-%d').tolist() if not hist.empty else []
        closes = hist['Close'].tolist() if not hist.empty else []

        # 3. DCF Base Data (Shares & Free Cash Flow)
        try:
            shares = f_info.shares
        except Exception:
            shares = 0
            
        fcf = 0
        cf = stock.cashflow
        if not cf.empty and 'Operating Cash Flow' in cf.index:
            try:
                ocf = cf.loc['Operating Cash Flow'].dropna().iloc[0]
                capex = abs(cf.loc['Capital Expenditure'].dropna().iloc[0]) if 'Capital Expenditure' in cf.index else 0
                fcf = float(ocf - capex)
            except Exception:
                pass

        return {
            "ticker": ticker.upper(),
            "current_price": round(current_price, 2),
            "change": round(change, 2),
            "pct_change": round(pct_change, 2),
            "market_cap": getattr(f_info, 'market_cap', 0),
            "shares": shares,
            "fcf": fcf,
            "chart_dates": dates,
            "chart_closes": closes
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Yahoo Finance Error: {str(e)}")
