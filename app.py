from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import requests
import pandas as pd

app = FastAPI(title="SethiStock Data Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
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
    """Fetches core pricing, DCF inputs, and all historical financial statements."""
    try:
        session = get_session()
        stock = yf.Ticker(ticker.upper(), session=session)
        f_info = stock.fast_info
        
        # 1. Pricing
        current_price = f_info.last_price
        prev_close = f_info.previous_close
        change = current_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close else 0

        # 2. DCF Inputs (Latest FCF & Shares)
        shares = getattr(f_info, 'shares', 0)
        fcf = 0
        cf = stock.cashflow
        if not cf.empty and 'Operating Cash Flow' in cf.index:
            try:
                ocf = cf.loc['Operating Cash Flow'].dropna().iloc[0]
                capex = abs(cf.loc['Capital Expenditure'].dropna().iloc[0]) if 'Capital Expenditure' in cf.index else 0
                fcf = float(ocf - capex)
            except: pass

        # 3. Full Financial Statement History (For the 6 Charts)
        fin = stock.financials
        bs = stock.balance_sheet
        
        fin_data = {
            "years": [], "revenue": [], "net": [], "op_margin": [], "net_margin": [],
            "fcf": [], "capex": [], "cash": [], "debt": [], "shares": []
        }

        if not fin.empty:
            cols = fin.columns[:4][::-1] # Last 4 years
            fin_data["years"] = [str(c.year) for c in cols]

            # Helper function to safely extract rows even if a company is missing them
            def get_hist(df, row_name):
                try:
                    if not df.empty and row_name in df.index:
                        return [float(df.loc[row_name, c]) if not pd.isna(df.loc[row_name, c]) else 0 for c in cols if c in df.columns]
                except: pass
                return [0] * len(cols)

            rev = get_hist(fin, 'Total Revenue')
            net = get_hist(fin, 'Net Income')
            op_inc = get_hist(fin, 'Operating Income')

            # Margins
            fin_data["op_margin"] = [(o/r*100) if r else 0 for o, r in zip(op_inc, rev)]
            fin_data["net_margin"] = [(n/r*100) if r else 0 for n, r in zip(net, rev)]

            # Cashflow
            ocf_hist = get_hist(cf, 'Operating Cash Flow')
            capex_hist = get_hist(cf, 'Capital Expenditure')
            capex_abs = [abs(x) for x in capex_hist]
            
            fin_data["revenue"] = rev
            fin_data["net"] = net
            fin_data["fcf"] = [o - c for o, c in zip(ocf_hist, capex_abs)]
            fin_data["capex"] = capex_abs

            # Balance Sheet
            fin_data["cash"] = get_hist(bs, 'Cash And Cash Equivalents')
            fin_data["debt"] = get_hist(bs, 'Total Debt')
            fin_data["shares"] = get_hist(bs, 'Ordinary Shares Number')

        # 4. Advanced Stats Matrix
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
            "ticker": ticker.upper(), "current_price": round(current_price, 2),
            "change": round(change, 2), "pct_change": round(pct_change, 2),
            "shares": shares, "fcf": fcf, "financials": fin_data, "stats": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/chart/{ticker}")
def get_chart_data(ticker: str, period: str = "1y", interval: str = "1d"):
    """Fetches dynamic OHLC chart data."""
    try:
        session = get_session()
        stock = yf.Ticker(ticker.upper(), session=session)
        hist = stock.history(period=period, interval=interval)
        if hist.empty: return {"dates": [], "opens": [], "highs": [], "lows": [], "closes": []}
        if period == "max": hist = hist.loc['2000':] # The Y2K Slicer
        return {
            "dates": hist.index.strftime('%Y-%m-%d %H:%M:%S').tolist(),
            "opens": hist['Open'].tolist(), "highs": hist['High'].tolist(),
            "lows": hist['Low'].tolist(), "closes": hist['Close'].tolist()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
