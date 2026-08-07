from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import requests

# Initialize the Server
app = FastAPI(title="SethiStock Data Engine")

# ==========================================
# --- THE CORS SECURITY SHIELD ---
# ==========================================
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

# ==========================================
# --- API ENDPOINTS ---
# ==========================================

@app.get("/")
def health_check():
    return {"status": "SethiStock API is online and secure."}

@app.get("/api/stock/{ticker}")
def get_stock_data(ticker: str):
    """Fetches real-time fast_info data using a stealth browser session."""
    try:
        # 1. The Stealth Disguise (Bypasses Yahoo Cloud Firewall)
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
        })
        
        # 2. Fetch Data
        stock = yf.Ticker(ticker.upper(), session=session)
        f_info = stock.fast_info
        
        # 3. Calculate daily change
        current_price = f_info.last_price
        prev_close = f_info.previous_close
        change = current_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close else 0

        # 4. Return the pure data as JSON
        return {
            "ticker": ticker.upper(),
            "current_price": round(current_price, 2),
            "change": round(change, 2),
            "pct_change": round(pct_change, 2),
            "market_cap": f_info.market_cap
        }
    except Exception as e:
        # If it fails, print the exact system error instead of hiding it
        raise HTTPException(status_code=500, detail=f"Yahoo Finance Error: {str(e)}")
