from pathlib import Path

p = Path('market_risk_lab.py')
s = p.read_text(encoding='utf-8')

marker = '''def _bs_price_greeks(spot, strike, time_years, rate, sigma, option_type):\n'''
helpers = '''def _historical_horizon_returns(asset_simple_returns: pd.DataFrame, weights: np.ndarray, horizon_days: int) -> np.ndarray:\n    portfolio_daily = asset_simple_returns.to_numpy() @ weights\n    portfolio_daily = pd.Series(portfolio_daily, index=asset_simple_returns.index)\n    if horizon_days == 1:\n        return portfolio_daily.dropna().to_numpy()\n    horizon_returns = (1.0 + portfolio_daily).rolling(horizon_days).apply(np.prod, raw=True) - 1.0\n    return horizon_returns.dropna().to_numpy()\n\n\ndef _equity_monte_carlo_returns(asset_log_returns: pd.DataFrame, weights: np.ndarray, horizon_days: int, simulations: int, seed: int) -> np.ndarray:\n    mu = asset_log_returns.mean().to_numpy(dtype=float)\n    cov = asset_log_returns.cov().to_numpy(dtype=float) + np.eye(asset_log_returns.shape[1]) * 1e-12\n    rng = np.random.default_rng(seed)\n    try:\n        shocks = rng.multivariate_normal(mean=mu, cov=cov, size=(simulations, horizon_days), check_valid=\"warn\")\n    except Exception as exc:\n        raise HTTPException(status_code=500, detail=f\"Monte Carlo simulation failed: {exc}\")\n    cumulative_log_returns = shocks.sum(axis=1)\n    cumulative_simple_returns = np.exp(cumulative_log_returns) - 1.0\n    return cumulative_simple_returns @ weights\n\n\n'''
if marker not in s:
    raise SystemExit('Black-Scholes marker not found')
s = s.replace(marker, helpers + marker, 1)

old = '''    hist_asset_returns, hist_dvol = _historical_factor_scenarios(simple_returns, data.horizon_days)\n    historical_pnl = _portfolio_pnl_from_scenarios(hist_asset_returns[risk_tickers].to_numpy(dtype=float), hist_dvol[risk_tickers].to_numpy(dtype=float), risk_tickers, equity_tickers, weights, data.portfolio_value, option_inventory, data.horizon_days)\n    historical_returns = historical_pnl / data.portfolio_value\n\n    mc_asset_returns, mc_dvol = _joint_monte_carlo_scenarios(simple_returns[risk_tickers], log_returns[risk_tickers], data.horizon_days, data.simulations, data.seed)\n    mc_pnl = _portfolio_pnl_from_scenarios(mc_asset_returns, mc_dvol, risk_tickers, equity_tickers, weights, data.portfolio_value, option_inventory, data.horizon_days)\n    mc_returns = mc_pnl / data.portfolio_value\n'''
new = '''    if option_inventory:\n        hist_asset_returns, hist_dvol = _historical_factor_scenarios(simple_returns, data.horizon_days)\n        historical_pnl = _portfolio_pnl_from_scenarios(hist_asset_returns[risk_tickers].to_numpy(dtype=float), hist_dvol[risk_tickers].to_numpy(dtype=float), risk_tickers, equity_tickers, weights, data.portfolio_value, option_inventory, data.horizon_days)\n        historical_returns = historical_pnl / data.portfolio_value\n\n        mc_asset_returns, mc_dvol = _joint_monte_carlo_scenarios(simple_returns[risk_tickers], log_returns[risk_tickers], data.horizon_days, data.simulations, data.seed)\n        mc_pnl = _portfolio_pnl_from_scenarios(mc_asset_returns, mc_dvol, risk_tickers, equity_tickers, weights, data.portfolio_value, option_inventory, data.horizon_days)\n        mc_returns = mc_pnl / data.portfolio_value\n    else:\n        historical_returns = _historical_horizon_returns(simple_returns[equity_tickers], weights, data.horizon_days)\n        mc_returns = _equity_monte_carlo_returns(log_returns[equity_tickers], weights, data.horizon_days, data.simulations, data.seed)\n        mc_pnl = mc_returns * data.portfolio_value\n'''
if old not in s:
    raise SystemExit('Portfolio scenario block not found')
s = s.replace(old, new, 1)

s = s.replace('    options: List[OptionPosition] = []\n', '    options: List[OptionPosition] = Field(default_factory=list)\n', 1)
p.write_text(s, encoding='utf-8')
print('Preserved Phase 2A equity-only VaR path and fixed options default factory.')
