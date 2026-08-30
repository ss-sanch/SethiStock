from pathlib import Path

p = Path('market_risk_lab.py')
s = p.read_text(encoding='utf-8')

# 1. Add chi-square distribution for Kupiec coverage test.
s = s.replace('from scipy.stats import norm', 'from scipy.stats import chi2, norm', 1)

# 2. Insert rolling VaR backtesting helpers before parametric attribution.
marker = 'def _parametric_risk_attribution(asset_simple_returns, tickers, weights, portfolio_value, confidence, horizon_days):'
helper = '''def _kupiec_unconditional_coverage_test(breaches: int, observations: int, expected_exception_prob: float):\n    \"\"\"Kupiec (1995) proportion-of-failures test for VaR exception frequency.\"\"\"\n    if observations <= 0:\n        return {\"lr_stat\": 0.0, \"p_value\": 1.0, \"status\": \"insufficient_data\"}\n\n    x = int(breaches)\n    n = int(observations)\n    p = float(np.clip(expected_exception_prob, 1e-9, 1.0 - 1e-9))\n    phat = float(np.clip(x / n, 1e-9, 1.0 - 1e-9))\n\n    log_l0 = (n - x) * np.log(1.0 - p) + x * np.log(p)\n    log_l1 = (n - x) * np.log(1.0 - phat) + x * np.log(phat)\n    lr_stat = max(0.0, -2.0 * (log_l0 - log_l1))\n    p_value = float(chi2.sf(lr_stat, df=1))\n    return {\n        \"lr_stat\": round(float(lr_stat), 4),\n        \"p_value\": round(p_value, 4),\n        \"status\": \"pass\" if p_value >= 0.05 else \"review\",\n    }\n\n\ndef _rolling_var_backtest(asset_simple_returns, tickers, weights, portfolio_value, confidence):\n    \"\"\"Backtest rolling one-day equity VaR forecasts against next-day realised P&L.\n\n    Validation deliberately uses non-overlapping one-day outcomes. The estimation\n    window is up to 250 observations, with a 125-observation minimum when the\n    selected lookback is shorter. Options are excluded because historical IV and\n    time-varying contract state are not reconstructed by this educational engine.\n    \"\"\"\n    portfolio_returns = pd.Series(\n        asset_simple_returns[tickers].to_numpy(dtype=float) @ weights,\n        index=asset_simple_returns.index,\n    ).dropna()\n\n    n_returns = len(portfolio_returns)\n    if n_returns < 150:\n        return {\n            \"available\": False,\n            \"reason\": \"At least 150 daily equity-return observations are required for rolling validation.\",\n            \"scope\": \"equity_book_only\",\n        }\n\n    estimation_window = min(250, max(125, n_returns // 2))\n    max_test_observations = 250\n    start = max(estimation_window, n_returns - max_test_observations)\n    z_score = float(norm.ppf(confidence))\n    expected_prob = 1.0 - float(confidence)\n\n    dates = []\n    realised_pnl = []\n    hist_var = []\n    param_var = []\n    hist_breach = []\n    param_breach = []\n\n    values = portfolio_returns.to_numpy(dtype=float)\n    idx = portfolio_returns.index\n    for i in range(start, n_returns):\n        window = values[i - estimation_window:i]\n        realised = float(values[i] * portfolio_value)\n\n        hist_q = float(np.quantile(window, expected_prob))\n        historical_var_loss = max(0.0, -hist_q * portfolio_value)\n\n        mu = float(np.mean(window))\n        sigma = float(np.std(window, ddof=1))\n        parametric_var_loss = max(0.0, (z_score * sigma - mu) * portfolio_value)\n\n        dates.append(pd.Timestamp(idx[i]).strftime('%Y-%m-%d'))\n        realised_pnl.append(round(realised, 2))\n        hist_var.append(round(historical_var_loss, 2))\n        param_var.append(round(parametric_var_loss, 2))\n        hist_breach.append(bool(realised < -historical_var_loss))\n        param_breach.append(bool(realised < -parametric_var_loss))\n\n    observations = len(dates)\n    expected_breaches = observations * expected_prob\n\n    def _model_summary(name, var_series, breach_flags):\n        breaches = int(sum(breach_flags))\n        breach_rate = (breaches / observations * 100.0) if observations else 0.0\n        kupiec = _kupiec_unconditional_coverage_test(breaches, observations, expected_prob)\n        return {\n            \"name\": name,\n            \"breaches\": breaches,\n            \"expected_breaches\": round(expected_breaches, 2),\n            \"breach_rate_pct\": round(breach_rate, 2),\n            \"expected_breach_rate_pct\": round(expected_prob * 100.0, 2),\n            \"average_var_loss\": round(float(np.mean(var_series)), 2) if var_series else 0.0,\n            \"kupiec\": kupiec,\n        }\n\n    return {\n        \"available\": True,\n        \"method\": \"rolling_one_day_var_backtest\",\n        \"scope\": \"equity_book_only\",\n        \"confidence\": float(confidence),\n        \"horizon_days\": 1,\n        \"estimation_window\": int(estimation_window),\n        \"observations\": int(observations),\n        \"historical\": _model_summary(\"Historical VaR\", hist_var, hist_breach),\n        \"parametric\": _model_summary(\"Parametric VaR\", param_var, param_breach),\n        \"series\": {\n            \"dates\": dates,\n            \"realised_pnl\": realised_pnl,\n            \"historical_var\": hist_var,\n            \"parametric_var\": param_var,\n            \"historical_breach\": hist_breach,\n            \"parametric_breach\": param_breach,\n        },\n    }\n\n\n''' + marker
if marker not in s:
    raise SystemExit('parametric attribution marker not found')
s = s.replace(marker, helper, 1)

# 3. Calculate validation after stress testing.
marker = '''    stress_testing = _stress_test_suite(\n        equity_tickers,\n        weights,\n        data.portfolio_value,\n        option_inventory,\n        data.risk_free_rate,\n        data.custom_stress,\n    )\n    correlation = simple_returns[risk_tickers].corr().round(4)'''
replacement = '''    stress_testing = _stress_test_suite(\n        equity_tickers,\n        weights,\n        data.portfolio_value,\n        option_inventory,\n        data.risk_free_rate,\n        data.custom_stress,\n    )\n    validation = _rolling_var_backtest(\n        simple_returns,\n        equity_tickers,\n        weights,\n        data.portfolio_value,\n        data.confidence,\n    )\n    correlation = simple_returns[risk_tickers].corr().round(4)'''
if marker not in s:
    raise SystemExit('stress testing calculation marker not found')
s = s.replace(marker, replacement, 1)

# 4. Return validation payload.
marker = '''        \"stress_testing\": stress_testing,\n        \"diagnostics\":'''
replacement = '''        \"stress_testing\": stress_testing,\n        \"validation\": validation,\n        \"diagnostics\":'''
if marker not in s:
    raise SystemExit('return marker not found')
s = s.replace(marker, replacement, 1)

p.write_text(s, encoding='utf-8')

required = [
    'from scipy.stats import chi2, norm',
    'def _kupiec_unconditional_coverage_test',
    'def _rolling_var_backtest',
    '"method": "rolling_one_day_var_backtest"',
    '"validation": validation',
]
missing = [x for x in required if x not in s]
if missing:
    raise SystemExit(f'Missing Phase 4 markers: {missing}')
print('Phase 4 VaR validation backend applied.')
# trigger workflow
