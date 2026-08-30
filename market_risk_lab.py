"""SethiQuant Market Risk Lab V2.

Portfolio-level Historical VaR, correlated Monte Carlo VaR, parametric Euler
risk attribution, and a Delta-Gamma-Vega derivatives overlay for European
options. Designed to be mounted from app.py.
"""

from typing import List

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from scipy.stats import norm

router = APIRouter(prefix="/api/quant/risk", tags=["Market Risk Lab"])


class PortfolioPosition(BaseModel):
    ticker: str
    weight: float = Field(gt=0, le=1)


class OptionPosition(BaseModel):
    underlying: str
    option_type: str = "call"
    strike: float = Field(gt=0)
    days_to_expiry: int = Field(ge=1, le=1095)
    implied_vol: float = Field(gt=0.01, le=3.0)
    contracts: int = Field(ge=-1000, le=1000)
    multiplier: int = Field(default=100, ge=1, le=1000)


class StressScenarioInput(BaseModel):
    equity_shock_pct: float = Field(ge=-80.0, le=80.0)
    vol_shock_points: float = Field(ge=-100.0, le=200.0)


class PortfolioRiskInput(BaseModel):
    positions: List[PortfolioPosition]
    options: List[OptionPosition] = Field(default_factory=list)
    custom_stress: StressScenarioInput | None = None
    portfolio_value: float = Field(default=100000.0, gt=0)
    confidence: float = Field(default=0.99, ge=0.90, lt=1.0)
    horizon_days: int = Field(default=10, ge=1, le=60)
    lookback: str = "2y"
    simulations: int = Field(default=10000, ge=1000, le=50000)
    seed: int = 42
    risk_free_rate: float = Field(default=0.05, ge=-0.05, le=0.25)


def _clean_input(data: PortfolioRiskInput):
    if not 2 <= len(data.positions) <= 5:
        raise HTTPException(status_code=400, detail="Use between 2 and 5 equity assets.")
    if len(data.options) > 3:
        raise HTTPException(status_code=400, detail="Use no more than 3 option positions.")

    tickers = [p.ticker.strip().upper() for p in data.positions]
    if any(not ticker for ticker in tickers):
        raise HTTPException(status_code=400, detail="Ticker symbols cannot be blank.")
    if len(set(tickers)) != len(tickers):
        raise HTTPException(status_code=400, detail="Each equity ticker must be unique.")

    weights = np.array([p.weight for p in data.positions], dtype=float)
    weight_sum = float(weights.sum())
    if not np.isclose(weight_sum, 1.0, atol=0.005):
        raise HTTPException(status_code=400, detail=f"Portfolio weights must sum to 1.00. Current sum: {weight_sum:.4f}.")
    weights = weights / weight_sum

    clean_options = []
    for option in data.options:
        underlying = option.underlying.strip().upper()
        option_type = option.option_type.strip().lower()
        if not underlying:
            raise HTTPException(status_code=400, detail="Option underlying cannot be blank.")
        if option_type not in {"call", "put"}:
            raise HTTPException(status_code=400, detail="Option type must be call or put.")
        if option.contracts == 0:
            continue
        clean_options.append({
            "underlying": underlying,
            "option_type": option_type,
            "strike": float(option.strike),
            "days_to_expiry": int(option.days_to_expiry),
            "implied_vol": float(option.implied_vol),
            "contracts": int(option.contracts),
            "multiplier": int(option.multiplier),
        })

    risk_tickers = list(dict.fromkeys(tickers + [o["underlying"] for o in clean_options]))
    return tickers, weights, clean_options, risk_tickers


def _download_prices(tickers: List[str], lookback: str) -> pd.DataFrame:
    try:
        raw = yf.download(tickers, period=lookback, interval="1d", auto_adjust=True, progress=False, group_by="column")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Market data request failed: {exc}")

    if raw is None or raw.empty:
        raise HTTPException(status_code=400, detail="No market data returned for the portfolio.")

    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" not in raw.columns.get_level_values(0):
            raise HTTPException(status_code=400, detail="Close prices unavailable.")
        prices = raw["Close"].copy()
    else:
        prices = raw[["Close"]].copy()
        prices.columns = tickers[:1]

    if isinstance(prices, pd.Series):
        prices = prices.to_frame()

    prices = prices.dropna(axis=1, how="all").dropna(how="any")
    missing = [ticker for ticker in tickers if ticker not in prices.columns]
    if missing:
        raise HTTPException(status_code=400, detail=f"Insufficient price history for: {', '.join(missing)}.")
    if len(prices) < 100:
        raise HTTPException(status_code=400, detail="Insufficient historical observations.")
    return prices[tickers]


def _loss_metrics(returns: np.ndarray, confidence: float, portfolio_value: float):
    returns = np.asarray(returns, dtype=float)
    returns = returns[np.isfinite(returns)]
    if returns.size == 0:
        raise HTTPException(status_code=500, detail="Risk distribution was empty.")
    threshold_return = float(np.quantile(returns, 1.0 - confidence))
    tail = returns[returns <= threshold_return]
    es_return = float(tail.mean()) if tail.size else threshold_return
    return {
        "var_return_pct": round(max(0.0, -threshold_return) * 100, 3),
        "expected_shortfall_pct": round(max(0.0, -es_return) * 100, 3),
        "var_loss": round(max(0.0, -threshold_return * portfolio_value), 2),
        "expected_shortfall_loss": round(max(0.0, -es_return * portfolio_value), 2),
    }


def _historical_horizon_returns(asset_simple_returns: pd.DataFrame, weights: np.ndarray, horizon_days: int) -> np.ndarray:
    portfolio_daily = asset_simple_returns.to_numpy() @ weights
    portfolio_daily = pd.Series(portfolio_daily, index=asset_simple_returns.index)
    if horizon_days == 1:
        return portfolio_daily.dropna().to_numpy()
    horizon_returns = (1.0 + portfolio_daily).rolling(horizon_days).apply(np.prod, raw=True) - 1.0
    return horizon_returns.dropna().to_numpy()


def _equity_monte_carlo_returns(asset_log_returns: pd.DataFrame, weights: np.ndarray, horizon_days: int, simulations: int, seed: int) -> np.ndarray:
    mu = asset_log_returns.mean().to_numpy(dtype=float)
    cov = asset_log_returns.cov().to_numpy(dtype=float) + np.eye(asset_log_returns.shape[1]) * 1e-12
    rng = np.random.default_rng(seed)
    try:
        shocks = rng.multivariate_normal(mean=mu, cov=cov, size=(simulations, horizon_days), check_valid="warn")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Monte Carlo simulation failed: {exc}")
    cumulative_log_returns = shocks.sum(axis=1)
    cumulative_simple_returns = np.exp(cumulative_log_returns) - 1.0
    return cumulative_simple_returns @ weights


def _bs_price_greeks(spot, strike, time_years, rate, sigma, option_type):
    if spot <= 0 or strike <= 0 or time_years <= 0 or sigma <= 0:
        raise HTTPException(status_code=400, detail="Invalid option parameters.")
    sqrt_t = np.sqrt(time_years)
    d1 = (np.log(spot / strike) + (rate + 0.5 * sigma ** 2) * time_years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    pdf_d1 = norm.pdf(d1)
    if option_type == "call":
        price = spot * norm.cdf(d1) - strike * np.exp(-rate * time_years) * norm.cdf(d2)
        delta = norm.cdf(d1)
        theta = (-(spot * pdf_d1 * sigma) / (2 * sqrt_t) - rate * strike * np.exp(-rate * time_years) * norm.cdf(d2)) / 365.0
    else:
        price = strike * np.exp(-rate * time_years) * norm.cdf(-d2) - spot * norm.cdf(-d1)
        delta = norm.cdf(d1) - 1.0
        theta = (-(spot * pdf_d1 * sigma) / (2 * sqrt_t) + rate * strike * np.exp(-rate * time_years) * norm.cdf(-d2)) / 365.0
    gamma = pdf_d1 / (spot * sigma * sqrt_t)
    vega = (spot * sqrt_t * pdf_d1) / 100.0
    return {"price": float(price), "delta": float(delta), "gamma": float(gamma), "vega": float(vega), "theta": float(theta)}


def _option_inventory(options, latest_prices, rate):
    inventory = []
    for idx, option in enumerate(options):
        spot = float(latest_prices[option["underlying"]])
        greeks = _bs_price_greeks(spot, option["strike"], option["days_to_expiry"] / 365.0, rate, option["implied_vol"], option["option_type"])
        inventory.append({**option, "id": idx + 1, "spot": spot, "units": option["contracts"] * option["multiplier"], **greeks})
    return inventory


def _historical_factor_scenarios(simple_returns: pd.DataFrame, horizon_days: int):
    realised_vol = simple_returns.rolling(20).std() * np.sqrt(252.0)
    if horizon_days == 1:
        horizon_returns = simple_returns.copy()
        horizon_dvol = realised_vol.diff()
    else:
        horizon_returns = (1.0 + simple_returns).rolling(horizon_days).apply(np.prod, raw=True) - 1.0
        horizon_dvol = realised_vol.diff(horizon_days)
    combined = pd.concat({"return": horizon_returns, "dvol": horizon_dvol}, axis=1).dropna()
    return combined["return"], combined["dvol"]


def _joint_monte_carlo_scenarios(simple_returns: pd.DataFrame, log_returns: pd.DataFrame, horizon_days: int, simulations: int, seed: int):
    realised_vol = simple_returns.rolling(20).std() * np.sqrt(252.0)
    vol_changes = realised_vol.diff()
    factor_df = pd.concat([log_returns.add_prefix("ret::"), vol_changes.add_prefix("dvol::")], axis=1).dropna()
    if len(factor_df) < 60:
        raise HTTPException(status_code=400, detail="Insufficient history for derivatives factor simulation.")
    mu = factor_df.mean().to_numpy(dtype=float)
    cov = factor_df.cov().to_numpy(dtype=float) + np.eye(factor_df.shape[1]) * 1e-12
    rng = np.random.default_rng(seed)
    try:
        shocks = rng.multivariate_normal(mean=mu, cov=cov, size=(simulations, horizon_days), check_valid="warn")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Monte Carlo simulation failed: {exc}")
    cumulative = shocks.sum(axis=1)
    n_assets = simple_returns.shape[1]
    return np.exp(cumulative[:, :n_assets]) - 1.0, cumulative[:, n_assets:]


def _portfolio_pnl_from_scenarios(asset_returns, vol_changes, risk_tickers, equity_tickers, weights, portfolio_value, options_inventory, horizon_days):
    ticker_index = {ticker: i for i, ticker in enumerate(risk_tickers)}
    equity_idx = [ticker_index[t] for t in equity_tickers]
    pnl = asset_returns[:, equity_idx] @ (weights * portfolio_value)
    for option in options_inventory:
        idx = ticker_index[option["underlying"]]
        d_s = option["spot"] * asset_returns[:, idx]
        new_vol = np.clip(option["implied_vol"] + vol_changes[:, idx], 0.01, 3.0)
        dvol_points = (new_vol - option["implied_vol"]) * 100.0
        per_share_pnl = option["delta"] * d_s + 0.5 * option["gamma"] * np.square(d_s) + option["vega"] * dvol_points + option["theta"] * horizon_days
        pnl = pnl + per_share_pnl * option["units"]
    return np.asarray(pnl, dtype=float)


def _derivatives_summary(options_inventory):
    rows = []
    totals = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "value": 0.0}
    for option in options_inventory:
        units = option["units"]
        dollar_delta = option["delta"] * option["spot"] * units
        gamma_1pct = 0.5 * option["gamma"] * (option["spot"] * 0.01) ** 2 * units
        vega_1vol = option["vega"] * units
        theta_1day = option["theta"] * units
        position_value = option["price"] * units
        totals["delta"] += dollar_delta; totals["gamma"] += gamma_1pct; totals["vega"] += vega_1vol; totals["theta"] += theta_1day; totals["value"] += position_value
        rows.append({
            "id": option["id"], "underlying": option["underlying"], "option_type": option["option_type"],
            "strike": round(option["strike"], 2), "days_to_expiry": option["days_to_expiry"],
            "implied_vol_pct": round(option["implied_vol"] * 100.0, 2), "contracts": option["contracts"], "multiplier": option["multiplier"],
            "spot": round(option["spot"], 2), "theoretical_price": round(option["price"], 4), "position_value": round(position_value, 2),
            "delta": round(option["delta"], 4), "gamma": round(option["gamma"], 6), "vega": round(option["vega"], 4), "theta": round(option["theta"], 4),
            "dollar_delta": round(dollar_delta, 2), "delta_pnl_1pct": round(dollar_delta * 0.01, 2),
            "gamma_pnl_1pct": round(gamma_1pct, 2), "vega_pnl_1vol": round(vega_1vol, 2), "theta_pnl_1day": round(theta_1day, 2),
        })
    return {
        "active": bool(rows), "method": "delta_gamma_vega_theta", "market_value": round(totals["value"], 2),
        "aggregate_dollar_delta": round(totals["delta"], 2), "aggregate_gamma_pnl_1pct": round(totals["gamma"], 2),
        "aggregate_vega_pnl_1vol": round(totals["vega"], 2), "aggregate_theta_pnl_1day": round(totals["theta"], 2), "options": rows,
    }


def _stress_test_suite(equity_tickers, weights, portfolio_value, option_inventory, rate, custom_stress=None):
    """Instantaneous scenario shocks with full Black-Scholes option revaluation.

    Equity positions are shocked linearly by market value. Options are repriced at
    shocked spot and implied volatility, avoiding local-Greeks extrapolation for
    large stress moves.
    """
    scenarios = [
        {"name": "Severe Sell-off", "equity_shock_pct": -20.0, "vol_shock_points": 20.0},
        {"name": "Risk-off", "equity_shock_pct": -10.0, "vol_shock_points": 10.0},
        {"name": "Volatility Spike", "equity_shock_pct": 0.0, "vol_shock_points": 15.0},
        {"name": "Relief Rally", "equity_shock_pct": 10.0, "vol_shock_points": -5.0},
    ]
    if custom_stress is not None:
        scenarios.append({
            "name": "Custom",
            "equity_shock_pct": float(custom_stress.equity_shock_pct),
            "vol_shock_points": float(custom_stress.vol_shock_points),
        })

    rows = []
    for scenario in scenarios:
        shock = scenario["equity_shock_pct"] / 100.0
        vol_points = scenario["vol_shock_points"]
        equity_pnl = float(portfolio_value * shock)
        options_pnl = 0.0

        for option in option_inventory:
            shocked_spot = max(0.01, option["spot"] * (1.0 + shock))
            shocked_vol = float(np.clip(option["implied_vol"] + vol_points / 100.0, 0.01, 3.0))
            shocked = _bs_price_greeks(
                shocked_spot,
                option["strike"],
                option["days_to_expiry"] / 365.0,
                rate,
                shocked_vol,
                option["option_type"],
            )
            options_pnl += (shocked["price"] - option["price"]) * option["units"]

        total_pnl = equity_pnl + options_pnl
        rows.append({
            "name": scenario["name"],
            "equity_shock_pct": round(scenario["equity_shock_pct"], 2),
            "vol_shock_points": round(vol_points, 2),
            "equity_pnl": round(equity_pnl, 2),
            "options_pnl": round(options_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round((total_pnl / portfolio_value) * 100.0, 2),
        })

    worst = min(rows, key=lambda row: row["total_pnl"]) if rows else None
    return {
        "method": "full_revaluation",
        "scope": "equity_plus_options",
        "scenarios": rows,
        "worst_scenario": worst["name"] if worst else None,
        "worst_pnl": worst["total_pnl"] if worst else 0.0,
    }


def _parametric_risk_attribution(asset_simple_returns, tickers, weights, portfolio_value, confidence, horizon_days):
    daily_cov = asset_simple_returns[tickers].cov().to_numpy(dtype=float)
    horizon_cov = daily_cov * float(horizon_days)
    exposures = weights * float(portfolio_value)
    portfolio_variance = float(exposures @ horizon_cov @ exposures)
    portfolio_sigma = float(np.sqrt(max(portfolio_variance, 0.0)))
    z_score = float(norm.ppf(confidence))
    if not np.isfinite(portfolio_sigma) or portfolio_sigma <= 0:
        raise HTTPException(status_code=500, detail="Unable to decompose portfolio risk.")
    portfolio_var = z_score * portfolio_sigma
    covariance_with_portfolio = horizon_cov @ exposures
    marginal_var_per_pound = z_score * covariance_with_portfolio / portfolio_sigma
    component_var = exposures * marginal_var_per_pound
    standalone_sigma = np.sqrt(np.clip(np.diag(horizon_cov), 0.0, None))
    standalone_var = z_score * np.abs(exposures) * standalone_sigma
    standalone_total = float(standalone_var.sum())
    diversification_benefit = max(0.0, standalone_total - portfolio_var)
    diversification_pct = (diversification_benefit / standalone_total) * 100.0 if standalone_total > 0 else 0.0
    components = []
    for i, ticker in enumerate(tickers):
        contribution_pct = float(component_var[i] / portfolio_var * 100.0) if portfolio_var > 0 else 0.0
        components.append({"ticker": ticker, "weight_pct": round(float(weights[i]) * 100.0, 2), "exposure": round(float(exposures[i]), 2), "standalone_var": round(float(standalone_var[i]), 2), "marginal_var_per_1000": round(float(marginal_var_per_pound[i]) * 1000.0, 2), "component_var": round(float(component_var[i]), 2), "contribution_pct": round(contribution_pct, 2)})
    return {"method": "variance_covariance_euler", "label": "Parametric Euler VaR", "scope": "equity_book_only", "confidence": confidence, "horizon_days": horizon_days, "portfolio_var": round(float(portfolio_var), 2), "sum_component_var": round(float(component_var.sum()), 2), "standalone_var_sum": round(standalone_total, 2), "diversification_benefit": round(diversification_benefit, 2), "diversification_pct": round(diversification_pct, 2), "components": components}


@router.post("/portfolio")
def calculate_portfolio_risk(data: PortfolioRiskInput):
    """Portfolio VaR with optional European-option Delta-Gamma-Vega overlay."""
    equity_tickers, weights, options, risk_tickers = _clean_input(data)
    prices = _download_prices(risk_tickers, data.lookback)
    simple_returns = prices.pct_change().dropna()
    log_returns = np.log(prices / prices.shift(1)).dropna()
    latest_prices = prices.iloc[-1]
    option_inventory = _option_inventory(options, latest_prices, data.risk_free_rate)

    if option_inventory:
        hist_asset_returns, hist_dvol = _historical_factor_scenarios(simple_returns, data.horizon_days)
        historical_pnl = _portfolio_pnl_from_scenarios(hist_asset_returns[risk_tickers].to_numpy(dtype=float), hist_dvol[risk_tickers].to_numpy(dtype=float), risk_tickers, equity_tickers, weights, data.portfolio_value, option_inventory, data.horizon_days)
        historical_returns = historical_pnl / data.portfolio_value

        mc_asset_returns, mc_dvol = _joint_monte_carlo_scenarios(simple_returns[risk_tickers], log_returns[risk_tickers], data.horizon_days, data.simulations, data.seed)
        mc_pnl = _portfolio_pnl_from_scenarios(mc_asset_returns, mc_dvol, risk_tickers, equity_tickers, weights, data.portfolio_value, option_inventory, data.horizon_days)
        mc_returns = mc_pnl / data.portfolio_value
    else:
        historical_returns = _historical_horizon_returns(simple_returns[equity_tickers], weights, data.horizon_days)
        mc_returns = _equity_monte_carlo_returns(log_returns[equity_tickers], weights, data.horizon_days, data.simulations, data.seed)
        mc_pnl = mc_returns * data.portfolio_value

    historical = _loss_metrics(historical_returns, data.confidence, data.portfolio_value)
    monte_carlo = _loss_metrics(mc_returns, data.confidence, data.portfolio_value)
    attribution = _parametric_risk_attribution(simple_returns, equity_tickers, weights, data.portfolio_value, data.confidence, data.horizon_days)
    derivatives = _derivatives_summary(option_inventory)
    stress_testing = _stress_test_suite(
        equity_tickers,
        weights,
        data.portfolio_value,
        option_inventory,
        data.risk_free_rate,
        data.custom_stress,
    )
    correlation = simple_returns[risk_tickers].corr().round(4)

    sample_count = min(1500, len(mc_pnl))
    sample_idx = np.linspace(0, len(mc_pnl) - 1, sample_count, dtype=int)
    mc_pnl_sample = mc_pnl[sample_idx].round(2).tolist()

    return {
        "status": "success",
        "portfolio": {"portfolio_value": round(data.portfolio_value, 2), "confidence": data.confidence, "horizon_days": data.horizon_days, "lookback": data.lookback, "positions": [{"ticker": ticker, "weight_pct": round(float(weight) * 100, 2), "latest_price": round(float(latest_prices[ticker]), 2)} for ticker, weight in zip(equity_tickers, weights)], "option_count": len(option_inventory)},
        "models": {"historical": historical, "monte_carlo": monte_carlo},
        "attribution": attribution,
        "derivatives": derivatives,
        "stress_testing": stress_testing,
        "diagnostics": {"historical_observations": int(len(historical_returns)), "simulations": int(data.simulations), "correlation_matrix": {row: {col: float(correlation.loc[row, col]) for col in risk_tickers} for row in risk_tickers}},
        "distribution": {"monte_carlo_pnl_sample": mc_pnl_sample, "var_threshold_loss": monte_carlo["var_loss"], "expected_shortfall_loss": monte_carlo["expected_shortfall_loss"]},
    }
