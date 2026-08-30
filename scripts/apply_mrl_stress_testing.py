from pathlib import Path

p = Path('market_risk_lab.py')
s = p.read_text(encoding='utf-8')

# 1. Add optional custom stress payload.
marker = '''class PortfolioRiskInput(BaseModel):\n    positions: List[PortfolioPosition]\n    options: List[OptionPosition] = Field(default_factory=list)'''
replacement = '''class StressScenarioInput(BaseModel):\n    equity_shock_pct: float = Field(ge=-80.0, le=80.0)\n    vol_shock_points: float = Field(ge=-100.0, le=200.0)\n\n\nclass PortfolioRiskInput(BaseModel):\n    positions: List[PortfolioPosition]\n    options: List[OptionPosition] = Field(default_factory=list)\n    custom_stress: StressScenarioInput | None = None'''
if marker not in s:
    raise SystemExit('PortfolioRiskInput marker not found')
s = s.replace(marker, replacement, 1)

# 2. Add full-revaluation stress testing before parametric attribution.
marker = '''def _parametric_risk_attribution(asset_simple_returns, tickers, weights, portfolio_value, confidence, horizon_days):'''
helper = '''def _stress_test_suite(equity_tickers, weights, portfolio_value, option_inventory, rate, custom_stress=None):\n    \"\"\"Instantaneous scenario shocks with full Black-Scholes option revaluation.\n\n    Equity positions are shocked linearly by market value. Options are repriced at\n    shocked spot and implied volatility, avoiding local-Greeks extrapolation for\n    large stress moves.\n    \"\"\"\n    scenarios = [\n        {\"name\": \"Severe Sell-off\", \"equity_shock_pct\": -20.0, \"vol_shock_points\": 20.0},\n        {\"name\": \"Risk-off\", \"equity_shock_pct\": -10.0, \"vol_shock_points\": 10.0},\n        {\"name\": \"Volatility Spike\", \"equity_shock_pct\": 0.0, \"vol_shock_points\": 15.0},\n        {\"name\": \"Relief Rally\", \"equity_shock_pct\": 10.0, \"vol_shock_points\": -5.0},\n    ]\n    if custom_stress is not None:\n        scenarios.append({\n            \"name\": \"Custom\",\n            \"equity_shock_pct\": float(custom_stress.equity_shock_pct),\n            \"vol_shock_points\": float(custom_stress.vol_shock_points),\n        })\n\n    rows = []\n    for scenario in scenarios:\n        shock = scenario[\"equity_shock_pct\"] / 100.0\n        vol_points = scenario[\"vol_shock_points\"]\n        equity_pnl = float(portfolio_value * shock)\n        options_pnl = 0.0\n\n        for option in option_inventory:\n            shocked_spot = max(0.01, option[\"spot\"] * (1.0 + shock))\n            shocked_vol = float(np.clip(option[\"implied_vol\"] + vol_points / 100.0, 0.01, 3.0))\n            shocked = _bs_price_greeks(\n                shocked_spot,\n                option[\"strike\"],\n                option[\"days_to_expiry\"] / 365.0,\n                rate,\n                shocked_vol,\n                option[\"option_type\"],\n            )\n            options_pnl += (shocked[\"price\"] - option[\"price\"]) * option[\"units\"]\n\n        total_pnl = equity_pnl + options_pnl\n        rows.append({\n            \"name\": scenario[\"name\"],\n            \"equity_shock_pct\": round(scenario[\"equity_shock_pct\"], 2),\n            \"vol_shock_points\": round(vol_points, 2),\n            \"equity_pnl\": round(equity_pnl, 2),\n            \"options_pnl\": round(options_pnl, 2),\n            \"total_pnl\": round(total_pnl, 2),\n            \"total_pnl_pct\": round((total_pnl / portfolio_value) * 100.0, 2),\n        })\n\n    worst = min(rows, key=lambda row: row[\"total_pnl\"]) if rows else None\n    return {\n        \"method\": \"full_revaluation\",\n        \"scope\": \"equity_plus_options\",\n        \"scenarios\": rows,\n        \"worst_scenario\": worst[\"name\"] if worst else None,\n        \"worst_pnl\": worst[\"total_pnl\"] if worst else 0.0,\n    }\n\n\n''' + marker
if marker not in s:
    raise SystemExit('Attribution marker not found')
s = s.replace(marker, helper, 1)

# 3. Calculate the stress suite after derivatives diagnostics.
marker = '''    derivatives = _derivatives_summary(option_inventory)\n    correlation = simple_returns[risk_tickers].corr().round(4)'''
replacement = '''    derivatives = _derivatives_summary(option_inventory)\n    stress_testing = _stress_test_suite(\n        equity_tickers,\n        weights,\n        data.portfolio_value,\n        option_inventory,\n        data.risk_free_rate,\n        data.custom_stress,\n    )\n    correlation = simple_returns[risk_tickers].corr().round(4)'''
if marker not in s:
    raise SystemExit('Derivatives summary marker not found')
s = s.replace(marker, replacement, 1)

# 4. Return stress-testing diagnostics.
marker = '''        \"derivatives\": derivatives,\n        \"diagnostics\":'''
replacement = '''        \"derivatives\": derivatives,\n        \"stress_testing\": stress_testing,\n        \"diagnostics\":'''
if marker not in s:
    raise SystemExit('Return marker not found')
s = s.replace(marker, replacement, 1)

p.write_text(s, encoding='utf-8')

required = [
    'class StressScenarioInput',
    'def _stress_test_suite',
    '"method": "full_revaluation"',
    '"stress_testing": stress_testing',
]
missing = [x for x in required if x not in s]
if missing:
    raise SystemExit(f'Missing stress markers: {missing}')

print('Phase 3 backend stress testing applied.')
