import streamlit as st
import yfinance as yf
import plotly.graph_objects as go

# Page Configuration
st.set_page_config(page_title="SethiStock", layout="wide")

# Sidebar: Reverse DCF Calculator Engine (Exit Multiple Method)
st.sidebar.header("Reverse DCF Calculator")
st.sidebar.write("Adjust the parameters to calculate your required entry price.")

# User Input Sliders
discount_rate = st.sidebar.slider("Desired Return (Discount Rate) %", min_value=5.0, max_value=20.0, value=10.0, step=0.5) / 100
growth_rate = st.sidebar.slider("Expected Growth Rate %", min_value=0.0, max_value=30.0, value=15.0, step=0.5) / 100
exit_multiple = st.sidebar.slider("Terminal Exit Multiple (x FCF)", min_value=10, max_value=80, value=35, step=1)
years = st.sidebar.slider("Projection Years", min_value=1, max_value=15, value=5, step=1)

# UI Headers
st.title("SethiStock Analysis Hub")
st.write("Welcome to your personal Stock Analysis platform.")

# Search Bar
ticker = st.text_input("Enter a Stock Ticker (e.g., AAPL, MSFT, TSLA):", "MSFT")

# The Data Engine
if ticker:
    stock = yf.Ticker(ticker)
    stock_data = stock.history(period="3mo")
    
    if not stock_data.empty:
        # Build the Plotly Candlestick Chart
        fig = go.Figure(data=[go.Candlestick(x=stock_data.index,
                        open=stock_data['Open'],
                        high=stock_data['High'],
                        low=stock_data['Low'],
                        close=stock_data['Close'])])
        
        fig.update_layout(title=f"{ticker.upper()} - 3 Month Price Action", 
                          xaxis_rangeslider_visible=False)
        
        # Render the Chart on the Web App
        st.plotly_chart(fig, use_container_width=True)
        
        # --- PHASE 4: DCF VALUATION ENGINE (EXIT MULTIPLE) ---
        st.divider()
        st.subheader(f"{ticker.upper()} - DCF Valuation Engine (Exit Multiple Method)")
        current_price = stock_data['Close'].iloc[-1]
        
        try:
            # 1. Fetch Live Financial Data
            info = stock.info
            shares_outstanding = info.get('sharesOutstanding', None)
            
            # Fetch Free Cash Flow from the cash flow statement
            cf = stock.cashflow
            
            # yfinance indexing can sometimes vary, so we create a robust fallback
            if 'Free Cash Flow' in cf.index:
                fcf_0 = cf.loc['Free Cash Flow'].iloc[0] 
            else:
                operating_cf = cf.loc['Operating Cash Flow'].iloc[0]
                capex = cf.loc['Capital Expenditure'].iloc[0] if 'Capital Expenditure' in cf.index else 0
                fcf_0 = operating_cf + capex 
                
            if shares_outstanding and fcf_0:
                # 2. DCF Mathematics (Exit Multiple Method)
                projected_fcf = []
                discounted_fcf = []
                
                # Project and discount future cash flows
                for t in range(1, years + 1):
                    fcf_t = fcf_0 * ((1 + growth_rate) ** t)
                    disc_fcf_t = fcf_t / ((1 + discount_rate) ** t)
                    projected_fcf.append(fcf_t)
                    discounted_fcf.append(disc_fcf_t)
                    
                # Calculate Terminal Value using Exit Multiple
                final_year_fcf = projected_fcf[-1]
                terminal_value = final_year_fcf * exit_multiple
                discounted_tv = terminal_value / ((1 + discount_rate) ** years)
                
                # Calculate Target Price per Share
                total_intrinsic_value = sum(discounted_fcf) + discounted_tv
                target_price = total_intrinsic_value / shares_outstanding
                
                # 3. Render the UI Metrics
                col1, col2, col3 = st.columns(3)
                col1.metric("Current Trading Price", f"${current_price:.2f}")
                col2.metric("Target Entry Price", f"${target_price:.2f}")
                
                margin_of_safety = ((target_price - current_price) / current_price) * 100
                col3.metric("Implied Margin of Safety", f"{margin_of_safety:.2f}%")
                
                # Display dynamic buy/sell badge based on calculation
                if target_price > current_price:
                    st.success(f"Based on your parameters, {ticker.upper()} is currently UNDERVALUED.")
                else:
                    st.warning(f"Based on your parameters, {ticker.upper()} is currently OVERVALUED.")
                    
            else:
                st.error("Missing critical financial data (Shares Outstanding or Free Cash Flow) to complete the valuation.")
                
        except Exception as e:
            st.error(f"Unable to retrieve sufficient financial data for {ticker.upper()} to run the DCF model.")
            
    else:
        st.error("No data found for that ticker. Please check the spelling and try again.")
    