from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf

# Initialize the Server
app = FastAPI(title="SethiStock Data Engine")

# ==========================================
# --- THE CORS SECURITY SHIELD ---
# ==========================================
# This ensures ONLY your custom domains can talk to this server.
allowed_origins = [
    "https://sethiway.com",
    "https://sethistock.sethiway.com",
    "http://localhost:5500", # For local testing
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
    """Fetches real-time fast_info data for the requested ticker."""
    try:
        stock = yf.Ticker(ticker.upper())
        f_info = stock.fast_info
        
        # Calculate daily change
        current_price = f_info.last_price
        prev_close = f_info.previous_close
        change = current_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close else 0

        # Return the pure data as JSON
        return {
            "ticker": ticker.upper(),
            "current_price": round(current_price, 2),
            "change": round(change, 2),
            "pct_change": round(pct_change, 2),
            "market_cap": f_info.market_cap
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail="Ticker data not found or rate limited.")
