import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd

# Start with the sidebar expanded by default
st.set_page_config(page_title="SethiStock", layout="wide", initial_sidebar_state="expanded")

# --- CUSTOM CSS FOR DYNAMIC UI POLISH ---
st.markdown("""
<style>
    .block-container {
        padding-top: 2rem;
        margin-top: 0rem;
    }
    header {background-color: transparent !important;}
    #MainMenu {visibility: hidden;}
    .stDeployButton {display: none;}
    [data-testid="stHeaderActionElements"] {display: none;}
    
    [data-testid="stMetric"], [data-testid="stPlotlyChart"] {
        background-color: rgba(128, 128, 128, 0.05);
        padding: 15px;
        border-radius: 12px;
        box-shadow: 0px 4px 6px rgba(0,0,0,0.05);
        border: 1px solid rgba(128, 128, 128, 0.1);
        box-sizing: border-box !important;
        overflow: hidden !important;
    }
    
    [data-testid="stPlotlyChart"] > div, [data-testid="stPlotlyChart"] iframe {
        overflow: hidden !important;
    }
</style>
""", unsafe_allow_html=True)

# Central Plotly Config to remove the cluttered Modebar buttons
PLOTLY_CONFIG = {'displayModeBar': False}

st.title("SethiStock Analysis Platform")

# --- MAIN PAGE: Central Search Bar ---
ticker_symbol = st.text_input("Enter Stock Ticker (e.g., AAPL, MSFT)", "AAPL").upper()

# --- SIDEBAR: Inputs & Valuation Results ---
st.sidebar.subheader("Reverse DCF (Exit Multiple)")
proj_years = st.sidebar.slider("Projection Years", 1, 10, 5)
growth_rate = st.sidebar.slider("Expected Annual Growth Rate %", 1.0, 50.0, 15.0) / 100
discount_rate = st.sidebar.slider("Desired Return (Discount Rate) %", 5.0, 20.0, 10.0) / 100
exit_multiple = st.sidebar.slider("Exit Multiple (Price/FCF)", 10.0, 100.0, 30.0)

if ticker_symbol:
    ticker = yf.Ticker(ticker_symbol)
    
    # --- 1. CALCULATE DCF FIRST ---
    info = ticker.info
    current_price = info.get('currentPrice', info.get('regularMarketPrice', 0))
    shares = info.get('sharesOutstanding', 0)
    cash_flow = ticker.cashflow
    
    dcf_valid = False
    
    if not cash_flow.empty and 'Operating Cash Flow' in cash_flow.index and current_price > 0 and shares > 0:
        ocf = cash_flow.loc['Operating Cash Flow'].dropna().iloc[0]
        capex = abs(cash_flow.loc['Capital Expenditure'].dropna().iloc[0]) if 'Capital Expenditure' in cash_flow.index else 0
        fcf_0 = ocf - capex
        
        if fcf_0 > 0:
            target_price_sum = 0
            fcf_n = fcf_0
            for t in range(1, proj_years + 1):
                fcf_n = fcf_n * (1 + growth_rate)
                target_price_sum += fcf_n / ((1 + discount_rate) ** t)
            
            tv = fcf_n * exit_multiple
            target_price_sum += tv / ((1 + discount_rate) ** proj_years)
            
            target_price_per_share = target_price_sum / shares
            margin_of_safety = ((target_price_per_share - current_price) / current_price) * 100
            dcf_valid = True

    # --- SIDEBAR: Output Display ---
    st.sidebar.divider()
    st.sidebar.subheader("Valuation Output")
    if dcf_valid:
        st.sidebar.metric("Current Price", f"${current_price:,.2f}")
        st.sidebar.metric("Target Entry Price", f"${target_price_per_share:,.2f}")
        st.sidebar.metric("Margin of Safety", f"{margin_of_safety:.2f}%", delta_color="normal" if margin_of_safety > 0 else "inverse")
    else:
        st.sidebar.warning("DCF cannot be calculated (Missing or Negative FCF).")

    # --- MAIN PAGE: Dashboards & Visuals ---
    st.header(f"{ticker_symbol} - Technical & Fundamental Analysis")
    
    # --- 2. Candlestick Chart (1-Year Period) ---
    st.subheader("1-Year Price Action")
    history = ticker.history(period="1y")
    if not history.empty:
        fig_candle = go.Figure(data=[go.Candlestick(x=history.index,
                    open=history['Open'],
                    high=history['High'],
                    low=history['Low'],
                    close=history['Close'])])
        
        fig_candle.update_layout(
            xaxis_rangeslider_visible=False, 
            margin=dict(l=15, r=15, t=40, b=20), 
            height=400,
            paper_bgcolor="rgba(0,0,0,0)", 
            plot_bgcolor="rgba(0,0,0,0)",
            title="Price Action"
        )
        # Injected PLOTLY_CONFIG to remove modebar buttons
        st.plotly_chart(fig_candle, use_container_width=True, config=PLOTLY_CONFIG)
    else:
        st.warning("No price data found.")

    # --- 3. Financial Visuals (Master Toggle for Annual vs Quarterly) ---
    st.subheader("Financial Health")
    
    # Master Toggle Switch
    show_quarterly = st.toggle("Switch to Quarterly (TTM) View", value=False)
    
    # Logic to fetch the correct dataset based on the toggle
    if show_quarterly:
        fin_data = ticker.quarterly_financials
        cf_data = ticker.quarterly_cashflow
        period_title = "Quarterly"
        limit = 4
    else:
        fin_data = ticker.financials
        cf_data = ticker.cashflow
        period_title = "Annual"
        limit = 10
    
    if not fin_data.empty and 'Total Revenue' in fin_data.index and 'Net Income' in fin_data.index:
        # Pull the data based on the dynamic limit
        rev = fin_data.loc['Total Revenue'].dropna().head(limit)
        ni = fin_data.loc['Net Income'].dropna().head(limit)
        
        fcf_series = pd.Series(dtype=float)
        if not cf_data.empty and 'Operating Cash Flow' in cf_data.index:
            ocf_hist = cf_data.loc['Operating Cash Flow'].head(limit)
            capex_hist = cf_data.loc['Capital Expenditure'].head(limit) if 'Capital Expenditure' in cf_data.index else pd.Series(0, index=ocf_hist.index)
            fcf_series = (ocf_hist - capex_hist.abs()).dropna()

        df_fin = pd.DataFrame({'Revenue': rev, 'Net Income': ni, 'FCF': fcf_series}).sort_index()
        
        # Format the dates elegantly based on the view
        if show_quarterly:
            df_fin.index = pd.to_datetime(df_fin.index).strftime('%Y-%m')
        else:
            df_fin.index = df_fin.index.astype(str).str[:4]
        
        col1, col2, col3 = st.columns(3)
        
        # Increased bottom margin (b=40) and height (260) to prevent text cut-off
        plot_height = 260
        plot_margins = dict(l=15, r=15, t=40, b=40)
        
        with col1:
            fig_rev = go.Figure(data=[go.Bar(x=df_fin.index, y=df_fin['Revenue'], marker_color='#1f77b4')])
            fig_rev.update_layout(title=f"{period_title} Revenue", margin=plot_margins, height=plot_height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_rev, use_container_width=True, config=PLOTLY_CONFIG)
            
        with col2:
            fig_ni = go.Figure(data=[go.Bar(x=df_fin.index, y=df_fin['Net Income'], marker_color='#2ca02c')])
            fig_ni.update_layout(title=f"{period_title} Net Income", margin=plot_margins, height=plot_height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_ni, use_container_width=True, config=PLOTLY_CONFIG)
            
        with col3:
            if not df_fin['FCF'].dropna().empty:
                fig_fcf = go.Figure(data=[go.Bar(x=df_fin.index, y=df_fin['FCF'], marker_color='#9467bd')])
                fig_fcf.update_layout(title=f"{period_title} Free Cash Flow", margin=plot_margins, height=plot_height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_fcf, use_container_width=True, config=PLOTLY_CONFIG)
            else:
                st.warning(f"Historical FCF Unavailable")
    else:
        st.warning("Historical financial data is not currently available.")
