import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd
import urllib.parse
import requests

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
        padding: 5px; /* Executive requested tight padding */
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

# --- MAIN PAGE: Top Navigation Bar ---
nav_col1, nav_col2, nav_col3 = st.columns([1, 2, 1], vertical_alignment="center")
with nav_col1:
    st.markdown("### SethiStock")
with nav_col2:
    ticker_symbol = st.text_input("Search", value="GOOG", label_visibility="collapsed", placeholder="Enter Stock Ticker (e.g., AAPL, MSFT)").upper()

st.divider()

# --- SIDEBAR: Inputs & Valuation Results ---
st.sidebar.subheader("Reverse DCF (Exit Multiple)")
proj_years = st.sidebar.slider("Projection Years", 1, 10, 5)
growth_rate = st.sidebar.slider("Expected Annual Growth Rate %", 1.0, 50.0, 15.0) / 100
discount_rate = st.sidebar.slider("Desired Return (Discount Rate) %", 5.0, 20.0, 10.0) / 100
exit_multiple = st.sidebar.slider("Exit Multiple (Price/FCF)", 10.0, 100.0, 30.0)

if ticker_symbol:
    # --- THE BYPASS: Inject Custom Browser Headers ---
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    })
    
    # Pass the disguised session to yfinance
    ticker = yf.Ticker(ticker_symbol, session=session)
    
    # Wrap the info call in a basic try/except to prevent total app crashes
    try:
        info = ticker.info
    except Exception:
        st.error("Yahoo Finance is temporarily rate-limiting this cloud server. Please try again in a few minutes or test locally.")
        info = {}
    
    # --- QUALTRIM-STYLE DYNAMIC HEADER ---
    name = info.get('shortName', info.get('longName', ticker_symbol))
    exchange = info.get('exchange', 'Exchange')
    current_price = info.get('currentPrice', info.get('regularMarketPrice', 0))
    prev_close = info.get('previousClose', 0)
    
    # Calculate Daily Change
    if current_price and prev_close:
        change = current_price - prev_close
        pct_change = (change / prev_close) * 100
    else:
        change = 0
        pct_change = 0
        
    # Formatting the Change Pill
    if change > 0:
        pill_color = "background-color: rgba(34, 197, 94, 0.2); color: #16a34a;"
        sign = "+"
    elif change < 0:
        pill_color = "background-color: rgba(239, 68, 68, 0.2); color: #dc2626;"
        sign = ""
    else:
        pill_color = "background-color: rgba(128, 128, 128, 0.2); color: #6b7280;"
        sign = ""
        
    # Modern Logo API Logic (Hunter.io with Google Favicon Fallback)
    website = info.get('website', '')
    logo_html = ""
    if website:
        domain = urllib.parse.urlparse(website).netloc.replace('www.', '')
        logo_url = f"https://logos.hunter.io/{domain}"
        fallback_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
        logo_html = f'<img src="{logo_url}" onerror="this.src=\'{fallback_url}\';" style="width: 50px; height: 50px; border-radius: 50%; margin-right: 15px; object-fit: contain; background-color: transparent; padding: 0px;">'

    # Render the Header via HTML injection 
    st.markdown(f"""
    <div style="display: flex; align-items: center; margin-bottom: 10px;">
        {logo_html}
        <div>
            <h2 style="margin: 0; padding: 0; font-size: 26px; font-weight: 600;">{name}</h2>
            <p style="margin: 0; padding: 0; color: #888; font-size: 14px; font-weight: 500;">{ticker_symbol} | {exchange}</p>
        </div>
    </div>
    <div style="display: flex; align-items: center; margin-bottom: 30px;">
        <h1 style="margin: 0; padding: 0; font-size: 38px; font-weight: 700; margin-right: 15px;">${current_price:,.2f}</h1>
        <div style="{pill_color} padding: 6px 12px; border-radius: 6px; font-weight: 600; font-size: 16px; display: flex; align-items: center;">
            {sign}${change:,.2f} &nbsp;|&nbsp; {sign}{pct_change:,.2f}%
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # --- 1. CALCULATE DCF FIRST ---
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
    
    # --- 2. Dynamic Price Action Chart ---
    st.subheader("Price Action")
    
    # Using a spacer column (the '1' in the middle) to force the chart toggle to the far right
    ctrl_col1, ctrl_spacer, ctrl_col2 = st.columns([6, 1, 3], vertical_alignment="center")
    with ctrl_col1:
        timeframe = st.radio(
            "Timeframe", 
            ["1 Day", "1 Week", "1 Month", "3 Months", "YTD", "1 Year", "5 Years", "MAX"], 
            horizontal=True, 
            label_visibility="collapsed",
            index=5 # Defaults to 1 Year
        )
    with ctrl_col2:
        chart_type = st.radio(
            "Chart Type", 
            ["Candlestick", "Line Graph"], 
            horizontal=True, 
            label_visibility="collapsed"
        )

    # Map selected timeframe to yfinance period/interval
    tf_map = {
        "1 Day": ("1d", "5m"),
        "1 Week": ("5d", "15m"),
        "1 Month": ("1mo", "1d"),
        "3 Months": ("3mo", "1d"),
        "YTD": ("ytd", "1d"),
        "1 Year": ("1y", "1d"),
        "5 Years": ("5y", "1d"),
        "MAX": ("max", "1wk")
    }
    
    period, interval = tf_map[timeframe]
    history = ticker.history(period=period, interval=interval)
    
    # Slice the dataframe to cutoff anything before the year 2000
    if timeframe == "MAX" and not history.empty:
        history = history.loc['2000':]
    
    if not history.empty:
        fig_price = go.Figure()

        if chart_type == "Candlestick":
            fig_price.add_trace(go.Candlestick(
                x=history.index,
                open=history['Open'],
                high=history['High'],
                low=history['Low'],
                close=history['Close'],
                name="Candlestick"
            ))
        else:
            fig_price.add_trace(go.Scatter(
                x=history.index,
                y=history['Close'],
                mode='lines',
                line=dict(color='#2563eb', width=2),
                fill='tozeroy',
                fillcolor='rgba(37, 99, 235, 0.1)',
                name="Line"
            ))
        
        fig_price.update_layout(
            xaxis_rangeslider_visible=False, 
            margin=dict(l=5, r=5, t=40, b=20), 
            height=400,
            paper_bgcolor="rgba(0,0,0,0)", 
            plot_bgcolor="rgba(0,0,0,0)"
        )
        
        # Hide weekend gaps on the chart (only relevant for daily/intraday data)
        if timeframe not in ["MAX"]: 
            fig_price.update_xaxes(
                rangebreaks=[dict(bounds=["sat", "mon"])]
            )
            
        st.plotly_chart(fig_price, use_container_width=True, config=PLOTLY_CONFIG)
    else:
        st.warning("No price data found for the selected timeframe.")

    # --- 3. Financial Visuals (Master Toggle for Annual vs Quarterly) ---
    st.subheader("Financial Health")
    
    show_quarterly = st.toggle("Switch to Quarterly (TTM) View", value=False)
    
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
        rev = fin_data.loc['Total Revenue'].dropna().head(limit)
        ni = fin_data.loc['Net Income'].dropna().head(limit)
        
        fcf_series = pd.Series(dtype=float)
        if not cf_data.empty and 'Operating Cash Flow' in cf_data.index:
            ocf_hist = cf_data.loc['Operating Cash Flow'].head(limit)
            capex_hist = cf_data.loc['Capital Expenditure'].head(limit) if 'Capital Expenditure' in cf_data.index else pd.Series(0, index=ocf_hist.index)
            fcf_series = (ocf_hist - capex_hist.abs()).dropna()

        df_fin = pd.DataFrame({'Revenue': rev, 'Net Income': ni, 'FCF': fcf_series}).sort_index()
        
        if show_quarterly:
            df_fin.index = pd.to_datetime(df_fin.index).strftime('%Y-%m')
        else:
            df_fin.index = df_fin.index.astype(str).str[:4]
        
        col1, col2, col3 = st.columns(3)
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
