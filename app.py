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
    # Your perfectly flush transparent logo
    st.image("sethistock_logo.png", width=360) 

with nav_col2:
    # Native placeholder implementation (clean, centered, zero CSS hacks)
    ticker_symbol = st.text_input(
        "Search", 
        value="", # Left blank so the placeholder is visible immediately!
        label_visibility="collapsed", 
        placeholder="Search Ticker..."
    ).upper()

if ticker_symbol:
    ticker = yf.Ticker(ticker_symbol)
    info = {} # THE PATCH: Empty dummy dict to bypass the blocked .info vault
    
    # --- QUALTRIM-STYLE DYNAMIC HEADER ---
    # Fetching live tape data via fast_info to bypass rate limits
    try:
        f_info = ticker.fast_info
        current_price = f_info.last_price
        prev_close = f_info.previous_close
    except Exception:
        current_price = 0
        prev_close = 0
            
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

    # Render the Minimalist Header via HTML injection 
    st.markdown(f"""
<div style="background-color: #1E1E1E; padding: 20px; border-radius: 10px; border: 1px solid #333; display: flex; flex-direction: column; gap: 5px;">
    <h2 style="margin: 0; padding: 0; font-size: 26px; font-weight: 600;">{ticker_symbol}</h2>
    <div style="display: flex; align-items: center; gap: 15px; margin-top: 10px;">
        <h1 style="margin: 0; padding: 0; font-size: 36px; font-weight: 700;">${current_price:,.2f}</h1>
        <div style="padding: 5px 10px; border-radius: 8px; font-weight: 600; {pill_color}">
            {sign}${change:,.2f} &nbsp;|&nbsp; {sign}{pct_change:,.2f}%
        </div>
    </div>
</div>
    """, unsafe_allow_html=True)
    
    # --- MAIN PAGE: Dashboards & Visuals ---
    # --- 2. Dynamic Price Action Chart ---
    st.subheader("Price Action")
    
    # Using a spacer column (the '1' in the middle) to force the chart toggle to the far right
    ctrl_col1, ctrl_spacer, ctrl_col2 = st.columns([6, 6, 1.7], vertical_alignment="center")
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
            margin=dict(l=5, r=5, t=40, b=40),
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

 # ==========================================
        # --- ADVANCED FINANCIAL HEALTH MATRIX (FMP API) ---
        # ==========================================
        st.markdown("<br><h3 style='color: #E2E8F0;'>🔬 Advanced Financial Metrics</h3>", unsafe_allow_html=True)
        
        # 1. Fetch Reliable Data via FMP API
        fmp_key = st.secrets["FMP_API_KEY"]
        period = "quarter" if is_quarterly else "annual"
        
        @st.cache_data(ttl=3600) # Caches data so you don't burn your free API limits
        def fetch_fmp_data(ticker, statement_type, period, key):
            url = f"https://financialmodelingprep.com/api/v3/{statement_type}/{ticker}?period={period}&limit=5&apikey={key}"
            try:
                res = requests.get(url)
                return pd.DataFrame(res.json())
            except Exception:
                return pd.DataFrame()

        # Pull Income Statement and Cash Flow (Reliable & Instant)
        inc_df = fetch_fmp_data(ticker_symbol, "income-statement", period, fmp_key)
        cf_df = fetch_fmp_data(ticker_symbol, "cash-flow-statement", period, fmp_key)
        bs_df = fetch_fmp_data(ticker_symbol, "balance-sheet-statement", period, fmp_key)

        # 2. Build the 2x2 UI Grid
        adv_col1, adv_col2 = st.columns(2)
        adv_col3, adv_col4 = st.columns(2)

        # --- GRAPH 1: Free Cash Flow ---
        with adv_col1:
            st.markdown("<p style='color: #A3A8B8; font-weight: 600;'>Free Cash Flow</p>", unsafe_allow_html=True)
            if not cf_df.empty and 'freeCashFlow' in cf_df.columns:
                # Reverse the dataframe to plot chronological order (oldest to newest)
                plot_df = cf_df.iloc[::-1]
                fig_fcf = go.Figure(go.Bar(x=plot_df['date'], y=plot_df['freeCashFlow'], marker_color='#16a34a'))
                fig_fcf.update_layout(height=250, margin=dict(l=0, r=0, t=10, b=30), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color='#A3A8B8'))
                st.plotly_chart(fig_fcf, use_container_width=True, config={'displayModeBar': False})
            else:
                st.info("FCF Data Unavailable")

        # --- GRAPH 2: Profit Margins ---
        with adv_col2:
            st.markdown("<p style='color: #A3A8B8; font-weight: 600;'>Profit Margins (%)</p>", unsafe_allow_html=True)
            if not inc_df.empty:
                plot_df = inc_df.iloc[::-1]
                fig_margin = go.Figure()
                
                # FMP provides these margins natively!
                fig_margin.add_trace(go.Scatter(x=plot_df['date'], y=plot_df['grossProfitRatio']*100, mode='lines+markers', name='Gross', line=dict(color='#3b82f6')))
                fig_margin.add_trace(go.Scatter(x=plot_df['date'], y=plot_df['operatingIncomeRatio']*100, mode='lines+markers', name='Operating', line=dict(color='#f59e0b')))
                fig_margin.add_trace(go.Scatter(x=plot_df['date'], y=plot_df['netIncomeRatio']*100, mode='lines+markers', name='Net', line=dict(color='#16a34a')))
                
                fig_margin.update_layout(height=250, margin=dict(l=0, r=0, t=10, b=30), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color='#A3A8B8'), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                st.plotly_chart(fig_margin, use_container_width=True, config={'displayModeBar': False})
            else:
                st.info("Margin Data Unavailable")

        # --- GRAPH 3: Total Debt ---
        with adv_col3:
            st.markdown("<p style='color: #A3A8B8; font-weight: 600;'>Total Debt</p>", unsafe_allow_html=True)
            if not bs_df.empty and 'totalDebt' in bs_df.columns:
                plot_df = bs_df.iloc[::-1]
                fig_debt = go.Figure(go.Bar(x=plot_df['date'], y=plot_df['totalDebt'], marker_color='#dc2626'))
                fig_debt.update_layout(height=250, margin=dict(l=0, r=0, t=10, b=30), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color='#A3A8B8'))
                st.plotly_chart(fig_debt, use_container_width=True, config={'displayModeBar': False})
            else:
                st.info("Debt Data Unavailable")

        # --- GRAPH 4: Shares Outstanding ---
        with adv_col4:
            st.markdown("<p style='color: #A3A8B8; font-weight: 600;'>Shares Outstanding</p>", unsafe_allow_html=True)
            if not inc_df.empty and 'weightedAverageShsOutDil' in inc_df.columns:
                plot_df = inc_df.iloc[::-1]
                fig_shares = go.Figure(go.Bar(x=plot_df['date'], y=plot_df['weightedAverageShsOutDil'], marker_color='#8b5cf6'))
                fig_shares.update_layout(height=250, margin=dict(l=0, r=0, t=10, b=30), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color='#A3A8B8'))
                st.plotly_chart(fig_shares, use_container_width=True, config={'displayModeBar': False})
            else:
                st.info("Share Data Unavailable")
    
    # ==========================================
    # --- BOTTOM SECTION: VALUATION ENGINE ---
    # ==========================================
    st.markdown("<br><hr><br>", unsafe_allow_html=True)
    st.markdown("### 📊 Valuation Engine: Reverse DCF")
    st.markdown("<p style='color: #A3A8B8; margin-bottom: 20px;'>Adjust the parameters below to reverse-engineer Wall Street expectations and calculate intrinsic value.</p>", unsafe_allow_html=True)

    # 1. The Sliders in a clean row
    dcf_col1, dcf_col2, dcf_col3, dcf_col4 = st.columns(4)
    with dcf_col1:
        proj_years = st.slider("Projection Years", 1, 10, 5)
    with dcf_col2:
        growth_rate = st.slider("Growth Rate %", 1.0, 50.0, 15.0) / 100
    with dcf_col3:
        discount_rate = st.slider("Discount Rate %", 5.0, 20.0, 10.0) / 100
    with dcf_col4:
        exit_multiple = st.slider("Exit Multiple", 10.0, 100.0, 15.0)

    # 2. Execute DCF Math
    dcf_valid = False
    fcf = 0
    try:
        shares = f_info.shares
    except Exception:
        shares = 0
            
    cash_flow = ticker.cashflow
    if not cash_flow.empty and 'Operating Cash Flow' in cash_flow.index:
        try:
            ocf = cash_flow.loc['Operating Cash Flow'].dropna().iloc[0]
            capex = abs(cash_flow.loc['Capital Expenditure'].dropna().iloc[0]) if 'Capital Expenditure' in cash_flow.index else 0
            fcf = ocf - capex
        except Exception:
            fcf = 0

    if fcf > 0 and shares > 0 and current_price > 0:
        target_price_sum = 0
        fcf_n = fcf
            
        for t in range(1, proj_years + 1):
            fcf_n = fcf_n * (1 + growth_rate)
            target_price_sum += fcf_n / ((1 + discount_rate) ** t)
                
        tv = fcf_n * exit_multiple
        target_price_sum += tv / ((1 + discount_rate) ** proj_years)
            
        target_price_per_share = target_price_sum / shares
        margin_of_safety = ((target_price_per_share - current_price) / current_price) * 100
        dcf_valid = True

    # 3. Render Output Card
    if dcf_valid:
        if margin_of_safety > 0:
            mos_color = "#16a34a"  # Green
        else:
            mos_color = "#dc2626"  # Red
                
        st.markdown(f"""
        <div style="background-color: #1E1E1E; padding: 25px; border-radius: 10px; border: 1px solid #333; margin-top: 20px; display: flex; justify-content: space-around; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.3);">
            <div>
                <p style="color: #A3A8B8; margin: 0; font-size: 14px; font-weight: 600; letter-spacing: 1px;">TARGET ENTRY PRICE</p>
                <h1 style="margin: 0; color: #E2E8F0; font-size: 36px;">${target_price_per_share:,.2f}</h1>
            </div>
            <div>
                <p style="color: #A3A8B8; margin: 0; font-size: 14px; font-weight: 600; letter-spacing: 1px;">MARGIN OF SAFETY</p>
                <h1 style="margin: 0; color: {mos_color}; font-size: 36px;">{margin_of_safety:,.2f}%</h1>
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.warning("Insufficient cash flow data to run DCF Valuation for this ticker.")
