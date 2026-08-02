import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd

st.set_page_config(page_title="SethiStock", layout="wide")
st.title("SethiStock Analysis Platform")

# Sidebar for search
st.sidebar.header("Search & Parameters")
ticker_symbol = st.sidebar.text_input("Enter Stock Ticker (e.g., AAPL, MSFT)", "AAPL").upper()

# Sidebar for Exit Multiple Reverse DCF
st.sidebar.subheader("Reverse DCF (Exit Multiple)")
proj_years = st.sidebar.slider("Projection Years", 1, 10, 5)
growth_rate = st.sidebar.slider("Expected Annual Growth Rate %", 1.0, 50.0, 15.0) / 100
discount_rate = st.sidebar.slider("Desired Return (Discount Rate) %", 5.0, 20.0, 10.0) / 100
exit_multiple = st.sidebar.slider("Exit Multiple (Price/FCF)", 10.0, 100.0, 30.0)

if ticker_symbol:
    ticker = yf.Ticker(ticker_symbol)
    
    st.header(f"{ticker_symbol} - Technical & Fundamental Analysis")
    
    # --- 1. Candlestick Chart (3-month) ---
    st.subheader("3-Month Price Action")
    history = ticker.history(period="3mo")
    if not history.empty:
        fig_candle = go.Figure(data=[go.Candlestick(x=history.index,
                    open=history['Open'],
                    high=history['High'],
                    low=history['Low'],
                    close=history['Close'])])
        fig_candle.update_layout(xaxis_rangeslider_visible=False, margin=dict(l=0, r=0, t=0, b=0), height=400)
        st.plotly_chart(fig_candle, use_container_width=True)
    else:
        st.warning("No price data found.")

    # --- 2. Financial Visuals (Upgrade B) ---
    st.subheader("Financial Health: Revenue vs. Net Income")
    financials = ticker.financials
    
    # Check if the required data exists in the API response
    if not financials.empty and 'Total Revenue' in financials.index and 'Net Income' in financials.index:
        # Fetch the last 4 years of data and drop missing values
        rev = financials.loc['Total Revenue'].dropna().head(4)
        ni = financials.loc['Net Income'].dropna().head(4)
        
        # Align dates and sort ascending (oldest to newest) for the chart
        df_fin = pd.DataFrame({'Revenue': rev, 'Net Income': ni}).sort_index()
        
        # Format the dates to just show the Year
        df_fin.index = df_fin.index.astype(str).str[:4]
        
        # Plotly Grouped Bar Chart
        fig_fin = go.Figure()
        fig_fin.add_trace(go.Bar(x=df_fin.index, y=df_fin['Revenue'], name='Revenue', marker_color='#1f77b4'))
        fig_fin.add_trace(go.Bar(x=df_fin.index, y=df_fin['Net Income'], name='Net Income', marker_color='#2ca02c'))
        
        fig_fin.update_layout(barmode='group', margin=dict(l=0, r=0, t=30, b=0), height=400, template="plotly_white")
        st.plotly_chart(fig_fin, use_container_width=True)
    else:
        st.warning("Historical financial data is not currently available for this ticker.")
        
    # --- 3. Reverse DCF Valuation Engine (Exit Multiple) ---
    st.subheader("Intrinsic Valuation (Exit Multiple Method)")
    
    info = ticker.info
    current_price = info.get('currentPrice', info.get('regularMarketPrice', 0))
    shares = info.get('sharesOutstanding', 0)
    
    cash_flow = ticker.cashflow
    if not cash_flow.empty and 'Operating Cash Flow' in cash_flow.index and current_price > 0 and shares > 0:
        ocf = cash_flow.loc['Operating Cash Flow'].dropna().iloc[0]
        
        # Handle Capital Expenditures to get Free Cash Flow
        capex = 0
        if 'Capital Expenditure' in cash_flow.index:
            capex = abs(cash_flow.loc['Capital Expenditure'].dropna().iloc[0])
        
        fcf_0 = ocf - capex
        
        if fcf_0 > 0:
            target_price_sum = 0
            fcf_n = fcf_0
            
            # Project Cash Flows
            for t in range(1, proj_years + 1):
                fcf_n = fcf_n * (1 + growth_rate)
                target_price_sum += fcf_n / ((1 + discount_rate) ** t)
            
            # Calculate Terminal Value using Exit Multiple
            tv = fcf_n * exit_multiple
            target_price_sum += tv / ((1 + discount_rate) ** proj_years)
            
            target_price_per_share = target_price_sum / shares
            margin_of_safety = ((target_price_per_share - current_price) / current_price) * 100
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Current Price", f"${current_price:,.2f}")
            col2.metric("Target Entry Price", f"${target_price_per_share:,.2f}")
            col3.metric("Margin of Safety", f"{margin_of_safety:.2f}%", delta_color="normal" if margin_of_safety > 0 else "inverse")
        else:
            st.warning("Company has negative Free Cash Flow. DCF cannot be calculated.")
    else:
        st.warning("Sufficient cash flow or price data not available for valuation.")
    
