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
    a.nav-pill {
        text-decoration: none;
        padding: 8px 18px;
        background-color: rgba(128, 128, 128, 0.08);
        border-radius: 20px;
        color: inherit;
        font-size: 0.95rem;
        font-weight: 600;
        border: 1px solid rgba(128, 128, 128, 0.2);
        transition: all 0.2s ease;
    }
    a.nav-pill:hover {
        background-color: rgba(128, 128, 128, 0.18);
        border-color: rgba(128, 128, 128, 0.4);
    }
    .nav-container {
        display: flex;
        justify-content: center;
        gap: 15px;
        margin-bottom: 20px;
    }
    [data-testid="stPlotlyChart"] > div, [data-testid="stPlotlyChart"] iframe {
        overflow: hidden !important;
    }
    a.nav-pill {
        text-decoration: none;
        padding: 8px 18px;
        background-color: rgba(128, 128, 128, 0.08);
        border-radius: 20px;
        color: #E2E8F0;
        font-size: 0.95rem;
        font-weight: 600;
        border: 1px solid rgba(128, 128, 128, 0.2);
        transition: all 0.2s ease;
    }
    a.nav-pill:hover {
        background-color: rgba(128, 128, 128, 0.18);
        border-color: rgba(128, 128, 128, 0.4);
    }
    .nav-container {
        display: flex;
        justify-content: center;
        gap: 15px;
        margin-bottom: 25px;
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

st.markdown("""
<div class="nav-container">
    <a href="#price-action" class="nav-pill">📉 Price Action</a>
    <a href="#insights-stats" class="nav-pill">📊 Insights & Stats</a>
    <a href="#reverse-dcf" class="nav-pill">🧮 Reverse DCF</a>
</div>
""", unsafe_allow_html=True)

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

    st.divider()
    st.markdown("<h2 id='price-action' style='text-align: center; color: #E2E8F0; margin-bottom: 20px;'>Price Action</h2>", unsafe_allow_html=True)

    
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
            margin=dict(l=5, r=50, t=40, b=40),
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
    st.divider()
    
    st.markdown("""
    <div id="insights-stats"></div>
    <h2 style='text-align: center; color: #E2E8F0; margin-bottom: 20px;'>Insights & Stats</h2>
    """, unsafe_allow_html=True)

    # ==========================================
    # --- INSIGHTS & STATS: KEY METRICS BAR ---
    # ==========================================
    @st.cache_data(ttl=3600)
    def fetch_key_metrics(tsymbol):
        from yahooquery import Ticker
        try:
            yq_t = Ticker(tsymbol)
            summary = yq_t.summary_detail.get(tsymbol, {})
            financials = yq_t.financial_data.get(tsymbol, {})
            
            if isinstance(summary, str): summary = {}
            if isinstance(financials, str): financials = {}
            
            # Standard Metrics
            mcap = summary.get('marketCap', 0)
            pe = summary.get('trailingPE', 0)
            dyield = summary.get('dividendYield', 0)
            dyield = dyield * 100 if dyield else 0
            
            # ELITE METRICS (User Suggested)
            fcf = financials.get('freeCashflow', 0)
            fcf_yield = (fcf / mcap) * 100 if mcap and fcf else 0
            
            rev_growth = financials.get('revenueGrowth', 0)
            rev_growth = rev_growth * 100 if rev_growth else 0
            
            profit_growth = financials.get('earningsGrowth', 0)
            profit_growth = profit_growth * 100 if profit_growth else 0
            
            return mcap, pe, dyield, fcf_yield, rev_growth, profit_growth
        except Exception:
            return 0, 0, 0, 0, 0, 0

    mcap, pe, dyield, fcf_yield, rev_growth, profit_growth = fetch_key_metrics(ticker_symbol)

    def format_mcap(val):
        if not val: return "N/A"
        if val >= 1e12: return f"${val/1e12:.2f}T"
        if val >= 1e9: return f"${val/1e9:.2f}B"
        if val >= 1e6: return f"${val/1e6:.2f}M"
        return f"${val:,.0f}"

    # Render the Upgraded 6-Column Grid
    m_col1, m_col2, m_col3, m_col4, m_col5, m_col6 = st.columns(6)
    
    with m_col1:
        st.metric("Market Cap", format_mcap(mcap))
    with m_col2:
        st.metric("P/E Ratio (TTM)", f"{pe:.2f}" if pe else "N/A")
    with m_col3:
        st.metric("FCF Yield", f"{fcf_yield:.2f}%" if fcf_yield else "N/A")
    with m_col4:
        st.metric("Div Yield", f"{dyield:.2f}%" if dyield else "N/A")
    with m_col5:
        st.metric("Rev Growth (YoY)", f"{rev_growth:.2f}%" if rev_growth else "N/A")
    with m_col6:
        st.metric("Profit Growth (YoY)", f"{profit_growth:.2f}%" if profit_growth else "N/A")
        
    st.markdown("<br>", unsafe_allow_html=True) 
    # ==========================================
    show_quarterly = st.toggle("Switch to Quarterly (TTM) View", value=False)
    
    st.markdown("""
    <style>
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background-color: rgba(128, 128, 128, 0.05) !important;
        border-radius: 12px !important;
        box-shadow: 0px 4px 6px rgba(0,0,0,0.05) !important;
        border: 1px solid rgba(128, 128, 128, 0.1) !important;
        padding: 5px !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stPlotlyChart"] {
        background-color: transparent !important;
        border: none !important;
        box-shadow: none !important;
        padding: 0 !important;
    }
    </style>
    """, unsafe_allow_html=True)

    # ==========================================
    # --- NEW: MATH ENGINE FOR CAGR PILLS ---
    # ==========================================
    def calculate_cagr(series, lookback_periods, years):
        if len(series) <= lookback_periods or lookback_periods == 0 or years == 0: 
            return None
            
        ev = series.iloc[-1] 
        bv = series.iloc[-(lookback_periods + 1)] 
        
        if pd.isna(ev) or pd.isna(bv) or bv == 0: 
            return None
            
        if bv > 0 and ev > 0: 
            return (ev / bv) ** (1 / years) - 1
        else:
            return ((ev - bv) / abs(bv)) / years

    def render_cagr_pills(series, is_quarterly=False):
        if not isinstance(series, pd.Series) or series.empty or len(series) < 2: 
            return ""
            
        step = 4 if is_quarterly else 1
        max_lookback = len(series) - 1
        max_years = max_lookback / step
        
        # UPDATED: Explicit "CAGR" labels
        cagrs = {
            "1Y CAGR": calculate_cagr(series, step * 1, 1),
            "2Y CAGR": calculate_cagr(series, step * 2, 2),
            "3Y CAGR": calculate_cagr(series, step * 3, 3),
            "MAX CAGR": calculate_cagr(series, max_lookback, max_years)
        }
        
        # UPDATED: Tighter margins to sit flush with the chart
        html = '<div style="display: flex; justify-content: center; flex-wrap: wrap; gap: 8px; margin-top: -10px; margin-bottom: 10px;">'
        has_data = False
        
        for label, val in cagrs.items():
            if val is not None:
                has_data = True
                pct = val * 100
                if pct > 0:
                    bg, text_c, sign = "rgba(34, 197, 94, 0.2)", "#16a34a", "+"
                elif pct < 0:
                    bg, text_c, sign = "rgba(239, 68, 68, 0.2)", "#dc2626", ""
                else:
                    bg, text_c, sign = "rgba(128, 128, 128, 0.2)", "#A3A8B8", ""
                    
                # UPDATED: Larger font size (13px) and bigger padding for readability
                html += f'<div style="background-color: {bg}; color: {text_c}; padding: 5px 12px; border-radius: 8px; font-size: 13px; font-weight: 700; border: 1px solid {text_c}40;">{label}: {sign}{pct:.1f}%</div>'
        html += '</div>'
        
        return html if has_data else ""


    # --- 1. Top Row Financials (YFinance Data) ---
    if show_quarterly:
        fin_data = ticker.quarterly_financials
        cf_data = ticker.quarterly_cashflow
        period_title = "Quarterly"
        limit = 16 
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
        plot_height = 250
        plot_margins = dict(l=15, r=15, t=15, b=30) 
        
        with col1:
            with st.container(border=True):
                st.markdown(f"<p style='color: #A3A8B8; font-weight: 600; text-align: center; margin-bottom: 15px; margin-top: 5px;'>{period_title} Revenue</p>", unsafe_allow_html=True)
                fig_rev = go.Figure(data=[go.Bar(x=df_fin.index, y=df_fin['Revenue'], marker_color='#1f77b4')])
                fig_rev.update_layout(margin=plot_margins, height=plot_height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_rev, use_container_width=True, config=PLOTLY_CONFIG)
                st.markdown(render_cagr_pills(df_fin['Revenue'], show_quarterly), unsafe_allow_html=True)
            
        with col2:
            with st.container(border=True):
                st.markdown(f"<p style='color: #A3A8B8; font-weight: 600; text-align: center; margin-bottom: 15px; margin-top: 5px;'>{period_title} Net Income</p>", unsafe_allow_html=True)
                fig_ni = go.Figure(data=[go.Bar(x=df_fin.index, y=df_fin['Net Income'], marker_color='#2ca02c')])
                fig_ni.update_layout(margin=plot_margins, height=plot_height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_ni, use_container_width=True, config=PLOTLY_CONFIG)
                st.markdown(render_cagr_pills(df_fin['Net Income'], show_quarterly), unsafe_allow_html=True)
            
        with col3:
            with st.container(border=True):
                st.markdown(f"<p style='color: #A3A8B8; font-weight: 600; text-align: center; margin-bottom: 15px; margin-top: 5px;'>{period_title} Free Cash Flow</p>", unsafe_allow_html=True)
                if not df_fin['FCF'].dropna().empty:
                    fig_fcf = go.Figure(data=[go.Bar(x=df_fin.index, y=df_fin['FCF'], marker_color='#9467bd')])
                    fig_fcf.update_layout(margin=plot_margins, height=plot_height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig_fcf, use_container_width=True, config=PLOTLY_CONFIG)
                    st.markdown(render_cagr_pills(df_fin['FCF'], show_quarterly), unsafe_allow_html=True)
                else:
                    st.warning(f"Historical FCF Unavailable")
    else:
        st.warning("Historical financial data is not currently available.")


    # --- 2. Advanced Charts (YahooQuery Data) ---
    is_quarterly = show_quarterly 
    period = "q" if is_quarterly else "a"
    expected_period_type = "3M" if is_quarterly else "12M"
    
    @st.cache_data(ttl=3600)
    def fetch_yq_data(ticker_symbol, period):
        from yahooquery import Ticker
        try:
            yq_ticker = Ticker(ticker_symbol)
            inc = yq_ticker.income_statement(frequency=period)
            cf = yq_ticker.cash_flow(frequency=period)
            bs = yq_ticker.balance_sheet(frequency=period)
            return inc, cf, bs
        except Exception:
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    inc_raw, cf_raw, bs_raw = fetch_yq_data(ticker_symbol, period)

    def clean_yq_df(df, expected_ptype):
        if isinstance(df, pd.DataFrame) and not df.empty:
            df = df.reset_index()
            if 'periodType' in df.columns:
                df = df[df['periodType'] == expected_ptype]
            if 'asOfDate' in df.columns:
                df['date_obj'] = pd.to_datetime(df['asOfDate'])
                df = df.drop_duplicates(subset=['date_obj'])
                df = df.sort_values('date_obj')
                df['date_str'] = df['date_obj'].dt.strftime('%Y-%m')
                return df
        return pd.DataFrame()

    inc_df = clean_yq_df(inc_raw, expected_period_type)
    cf_df = clean_yq_df(cf_raw, expected_period_type)
    bs_df = clean_yq_df(bs_raw, expected_period_type)

    adv_col1, adv_col2, adv_col3 = st.columns(3)

    with adv_col1:
        with st.container(border=True):
            st.markdown("<p style='color: #A3A8B8; font-weight: 600; text-align: center; margin-bottom: 15px; margin-top: 5px;'>Operating Cash Flow</p>", unsafe_allow_html=True)
            if not cf_df.empty and 'date_str' in cf_df.columns and 'OperatingCashFlow' in cf_df.columns:
                plot_cf = cf_df.dropna(subset=['OperatingCashFlow'])
                if not plot_cf.empty:
                    fig_ocf = go.Figure(go.Bar(x=plot_cf['date_str'], y=plot_cf['OperatingCashFlow'], marker_color='#0ea5e9', name='Operating CF'))
                    fig_ocf.update_layout(xaxis_type='category', height=250, margin=dict(l=20, r=20, t=15, b=30), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color='#A3A8B8'))
                    fig_ocf.update_xaxes(showline=True, linewidth=1, linecolor='#333333') 
                    fig_ocf.update_yaxes(showgrid=True, gridcolor='#1F212E')
                    st.plotly_chart(fig_ocf, use_container_width=True, config={'displayModeBar': False})
                    st.markdown(render_cagr_pills(plot_cf['OperatingCashFlow'], is_quarterly), unsafe_allow_html=True)
                else:
                    st.info("Operating Cash Flow Data Unavailable")
            else:
                st.info("Operating Cash Flow Data Unavailable")

    with adv_col2:
        with st.container(border=True):
            st.markdown("<p style='color: #A3A8B8; font-weight: 600; text-align: center; margin-bottom: 15px; margin-top: 5px;'>Profit Margins (%)</p>", unsafe_allow_html=True)
            if not inc_df.empty and 'date_str' in inc_df.columns and 'TotalRevenue' in inc_df.columns and 'GrossProfit' in inc_df.columns:
                plot_inc = inc_df.dropna(subset=['TotalRevenue', 'GrossProfit'])
                plot_inc = plot_inc[plot_inc['TotalRevenue'] > 0]
                
                if not plot_inc.empty:
                    fig_margin = go.Figure()
                    rev = plot_inc['TotalRevenue']
                    gm = (plot_inc['GrossProfit'] / rev) * 100
                    fig_margin.add_trace(go.Scatter(x=plot_inc['date_str'], y=gm, mode='lines+markers', name='Gross', line=dict(color='#3b82f6')))
                    
                    if 'OperatingIncome' in plot_inc.columns:
                        om = (plot_inc['OperatingIncome'] / rev) * 100
                        fig_margin.add_trace(go.Scatter(x=plot_inc['date_str'], y=om, mode='lines+markers', name='Operating', line=dict(color='#f59e0b')))
                    if 'NetIncome' in plot_inc.columns:
                        nm = (plot_inc['NetIncome'] / rev) * 100
                        fig_margin.add_trace(go.Scatter(x=plot_inc['date_str'], y=nm, mode='lines+markers', name='Net', line=dict(color='#16a34a')))
                    
                    fig_margin.update_layout(
                        xaxis_type='category', height=250, margin=dict(l=30, r=20, t=40, b=30), 
                        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color='#A3A8B8'), 
                        legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="center", x=0.5)
                    )
                    fig_margin.update_xaxes(showline=True, linewidth=1, linecolor='#333333') 
                    fig_margin.update_yaxes(zeroline=True, zerolinecolor='#333333', showgrid=True, gridcolor='#1F212E') 
                    st.plotly_chart(fig_margin, use_container_width=True, config={'displayModeBar': False})
                    st.markdown(render_cagr_pills(gm, is_quarterly), unsafe_allow_html=True)
                else:
                    st.info("Margin Data Unavailable")
            else:
                st.info("Margin Data Unavailable")

    with adv_col3:
        with st.container(border=True):
            st.markdown("<p style='color: #A3A8B8; font-weight: 600; text-align: center; margin-bottom: 15px; margin-top: 5px;'>Total Debt</p>", unsafe_allow_html=True)
            if not bs_df.empty and 'date_str' in bs_df.columns and 'TotalDebt' in bs_df.columns:
                plot_bs = bs_df.dropna(subset=['TotalDebt'])
                if not plot_bs.empty:
                    fig_debt = go.Figure(go.Bar(x=plot_bs['date_str'], y=plot_bs['TotalDebt'], marker_color='#dc2626', name='Total Debt'))
                    fig_debt.update_layout(xaxis_type='category', height=250, margin=dict(l=20, r=20, t=15, b=30), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color='#A3A8B8'))
                    fig_debt.update_xaxes(showline=True, linewidth=1, linecolor='#333333')
                    fig_debt.update_yaxes(showgrid=True, gridcolor='#1F212E')
                    st.plotly_chart(fig_debt, use_container_width=True, config={'displayModeBar': False})
                    st.markdown(render_cagr_pills(plot_bs['TotalDebt'], is_quarterly), unsafe_allow_html=True)
                else:
                    st.info("Debt Data Unavailable")
            else:
                st.info("Debt Data Unavailable")

    adv_col4, adv_col5, adv_col6 = st.columns(3)
    
    with adv_col4:
        with st.container(border=True):
            st.markdown("<p style='color: #A3A8B8; font-weight: 600; text-align: center; margin-bottom: 15px; margin-top: 5px;'>Shares Outstanding</p>", unsafe_allow_html=True)
            share_col = 'DilutedAverageShares' if 'DilutedAverageShares' in inc_df.columns else ('BasicAverageShares' if 'BasicAverageShares' in inc_df.columns else None)
            
            if not inc_df.empty and 'date_str' in inc_df.columns and share_col:
                plot_shares = inc_df.dropna(subset=[share_col])
                if not plot_shares.empty:
                    fig_shares = go.Figure(go.Bar(x=plot_shares['date_str'], y=plot_shares[share_col], marker_color='#8b5cf6', name='Shares Out'))
                    fig_shares.update_layout(xaxis_type='category', height=250, margin=dict(l=20, r=20, t=15, b=30), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font=dict(color='#A3A8B8'))
                    fig_shares.update_xaxes(showline=True, linewidth=1, linecolor='#333333')
                    fig_shares.update_yaxes(showgrid=True, gridcolor='#1F212E')
                    st.plotly_chart(fig_shares, use_container_width=True, config={'displayModeBar': False})
                    st.markdown(render_cagr_pills(plot_shares[share_col], is_quarterly), unsafe_allow_html=True)
                else:
                    st.info("Share Data Unavailable")
            else:
                st.info("Share Data Unavailable")

    # ==========================================
    # --- BOTTOM SECTION: VALUATION ENGINE ---
    # ==========================================
    st.divider()
    st.markdown("<h2 id='reverse-dcf' style='text-align: center; color: #E2E8F0; margin-bottom: 20px;'>Reverse DCF</h2>", unsafe_allow_html=True)

    # 1. State Management for Two-Way Binding (Strict Key-to-Key Sync)
    if 'proj_num' not in st.session_state: st.session_state.proj_num = 5
    if 'proj_sld' not in st.session_state: st.session_state.proj_sld = 5

    if 'grow_num' not in st.session_state: st.session_state.grow_num = 15.0
    if 'grow_sld' not in st.session_state: st.session_state.grow_sld = 15.0

    if 'disc_num' not in st.session_state: st.session_state.disc_num = 10.0
    if 'disc_sld' not in st.session_state: st.session_state.disc_sld = 10.0

    if 'exit_num' not in st.session_state: st.session_state.exit_num = 15.0
    if 'exit_sld' not in st.session_state: st.session_state.exit_sld = 15.0

    # The Engine that locks the two widgets together
    def sync_widgets(source_key, target_key):
        st.session_state[target_key] = st.session_state[source_key]

    # 2. The Hybrid Inputs (Type-in + Slider)
    dcf_col1, dcf_col2, dcf_col3, dcf_col4 = st.columns(4)

    with dcf_col1:
        st.markdown("<p style='color: #A3A8B8; font-weight: 600; text-align: center; margin-bottom: 5px;'>Projection Years</p>", unsafe_allow_html=True)
        st.number_input("Proj Years Input", min_value=1, max_value=20, key='proj_num', on_change=sync_widgets, args=('proj_num', 'proj_sld'), label_visibility="collapsed")
        st.slider("Proj Years Slider", 1, 20, key='proj_sld', on_change=sync_widgets, args=('proj_sld', 'proj_num'), label_visibility="collapsed")
        proj_years = int(st.session_state.proj_num)

    with dcf_col2:
        st.markdown("<p style='color: #A3A8B8; font-weight: 600; text-align: center; margin-bottom: 5px;'>Growth Rate %</p>", unsafe_allow_html=True)
        st.number_input("Growth Rate Input", min_value=1.0, max_value=100.0, step=0.5, key='grow_num', on_change=sync_widgets, args=('grow_num', 'grow_sld'), label_visibility="collapsed")
        st.slider("Growth Rate Slider", 1.0, 100.0, key='grow_sld', on_change=sync_widgets, args=('grow_sld', 'grow_num'), label_visibility="collapsed")
        growth_rate = float(st.session_state.grow_num) / 100.0

    with dcf_col3:
        st.markdown("<p style='color: #A3A8B8; font-weight: 600; text-align: center; margin-bottom: 5px;'>Discount Rate %</p>", unsafe_allow_html=True)
        st.number_input("Discount Rate Input", min_value=1.0, max_value=50.0, step=0.5, key='disc_num', on_change=sync_widgets, args=('disc_num', 'disc_sld'), label_visibility="collapsed")
        st.slider("Discount Rate Slider", 1.0, 50.0, key='disc_sld', on_change=sync_widgets, args=('disc_sld', 'disc_num'), label_visibility="collapsed")
        discount_rate = float(st.session_state.disc_num) / 100.0

    with dcf_col4:
        st.markdown("<p style='color: #A3A8B8; font-weight: 600; text-align: center; margin-bottom: 5px;'>Exit Multiple</p>", unsafe_allow_html=True)
        st.number_input("Exit Mult Input", min_value=1.0, max_value=100.0, step=1.0, key='exit_num', on_change=sync_widgets, args=('exit_num', 'exit_sld'), label_visibility="collapsed")
        st.slider("Exit Mult Slider", 1.0, 100.0, key='exit_sld', on_change=sync_widgets, args=('exit_sld', 'exit_num'), label_visibility="collapsed")
        exit_multiple = float(st.session_state.exit_num)

    # 3. Execute DCF Math
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

    # 4. Render Output Card
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
