"""SethiQuant Market Risk Lab V2.

Standalone FastAPI router for portfolio-level Historical VaR and correlated
Monte Carlo VaR. Designed to be mounted from app.py with:

    from market_risk_lab import router as market_risk_router
    app.include_router(market_risk_router)

The module deliberately keeps calculation deterministic apart from Monte Carlo
sampling and does not use an LLM for any risk metric.
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


class PortfolioRiskInput(BaseModel):
    positions: List[PortfolioPosition]
    portfolio_value: float = Field(default=100000.0, gt=0)
    confidence: float = Field(default=0.99, ge=0.90, lt=1.0)
    horizon_days: int = Field(default=10, ge=1, le=60)
    lookback: str = "2y"
    simulations: int = Field(default=10000, ge=1000, le=50000)
    seed: int = 42


def _clean_input(data: PortfolioRiskInput):
    if not 2 <= len(data.positions) <= 5:
        raise HTTPException(status_code=400, detail="Use between 2 and 5 assets.")

    tickers = [p.ticker.strip().upper() for p in data.positions]
    if any(not ticker for ticker in tickers):
        raise HTTPException(status_code=400, detail="Ticker symbols cannot be blank.")
    if len(set(tickers)) != len(tickers):
        raise HTTPException(status_code=400, detail="Each ticker must be unique.")

    weights = np.array([p.weight for p in data.positions], dtype=float)
    weight_sum = float(weights.sum())
    if not np.isclose(weight_sum, 1.0, atol=0.005):
        raise HTTPException(
            status_code=400,
            detail=f"Portfolio weights must sum to 1.00. Current sum: {weight_sum:.4f}.",
        )
    weights = weights / weight_sum
    return tickers, weights


def _download_prices(tickers: List[str], lookback: str) -> pd.DataFrame:
    try:
        raw = yf.download(
            tickers,
            period=lookback,
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="column",
        )
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
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient price history for: {', '.join(missing)}.",
        )
    if len(prices) < 100:
        raise HTTPException(status_code=400, detail="Insufficient historical observations.")

    return prices[tickers]


def _loss_metrics(returns: np.ndarray, confidence: float, portfolio_value: float):
    returns = np.asarray(returns, dtype=float)
    returns = returns[np.isfinite(returns)]
    if returns.size == 0:
        raise HTTPException(status_code=500, detail="Risk distribution was empty.")

    tail_probability = 1.0 - confidence
    threshold_return = float(np.quantile(returns, tail_probability))
    tail = returns[returns <= threshold_return]
    es_return = float(tail.mean()) if tail.size else threshold_return

    var_loss = max(0.0, -threshold_return * portfolio_value)
    es_loss = max(0.0, -es_return * portfolio_value)

    return {
        "var_return_pct": round(max(0.0, -threshold_return) * 100, 3),
        "expected_shortfall_pct": round(max(0.0, -es_return) * 100, 3),
        "var_loss": round(var_loss, 2),
        "expected_shortfall_loss": round(es_loss, 2),
    }


def _historical_horizon_returns(
    asset_simple_returns: pd.DataFrame,
    weights: np.ndarray,
    horizon_days: int,
) -> np.ndarray:
    portfolio_daily = asset_simple_returns.to_numpy() @ weights
    portfolio_daily = pd.Series(portfolio_daily, index=asset_simple_returns.index)

    if horizon_days == 1:
        return portfolio_daily.dropna().to_numpy()

    horizon_returns = (
        (1.0 + portfolio_daily)
        .rolling(horizon_days)
        .apply(np.prod, raw=True)
        - 1.0
    )
    return horizon_returns.dropna().to_numpy()


def _monte_carlo_returns(
    asset_log_returns: pd.DataFrame,
    weights: np.ndarray,
    horizon_days: int,
    simulations: int,
    seed: int,
) -> np.ndarray:
    mu = asset_log_returns.mean().to_numpy(dtype=float)
    cov = asset_log_returns.cov().to_numpy(dtype=float)
    cov = cov + np.eye(cov.shape[0]) * 1e-12
    rng = np.random.default_rng(seed)

    try:
        shocks = rng.multivariate_normal(
            mean=mu,
            cov=cov,
            size=(simulations, horizon_days),
            check_valid="warn",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Monte Carlo simulation failed: {exc}")

    cumulative_asset_log_returns = shocks.sum(axis=1)
    cumulative_asset_simple_returns = np.exp(cumulative_asset_log_returns) - 1.0
    portfolio_returns = cumulative_asset_simple_returns @ weights
    return portfolio_returns


def _parametric_risk_attribution(
    asset_simple_returns: pd.DataFrame,
    tickers: List[str],
    weights: np.ndarray,
    portfolio_value: float,
    confidence: float,
    horizon_days: int,
):
    """Euler decomposition of delta-normal VaR across dollar exposures.

    The covariance matrix is scaled linearly with the requested horizon. For a
    linear equity portfolio, VaR is homogeneous in exposures, so Euler's theorem
    gives an additive allocation: portfolio VaR equals the sum of Component VaR.
    """
    daily_cov = asset_simple_returns.cov().to_numpy(dtype=float)
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
    diversification_pct = (
        (diversification_benefit / standalone_total) * 100.0
        if standalone_total > 0
        else 0.0
    )

    components = []
    for i, ticker in enumerate(tickers):
        contribution_pct = (
            float(component_var[i] / portfolio_var * 100.0)
            if portfolio_var > 0
            else 0.0
        )
        components.append(
            {
                "ticker": ticker,
                "weight_pct": round(float(weights[i]) * 100.0, 2),
                "exposure": round(float(exposures[i]), 2),
                "standalone_var": round(float(standalone_var[i]), 2),
                "marginal_var_per_1000": round(float(marginal_var_per_pound[i]) * 1000.0, 2),
                "component_var": round(float(component_var[i]), 2),
                "contribution_pct": round(contribution_pct, 2),
            }
        )

    return {
        "method": "variance_covariance_euler",
        "label": "Parametric Euler VaR",
        "confidence": confidence,
        "horizon_days": horizon_days,
        "portfolio_var": round(float(portfolio_var), 2),
        "sum_component_var": round(float(component_var.sum()), 2),
        "standalone_var_sum": round(standalone_total, 2),
        "diversification_benefit": round(diversification_benefit, 2),
        "diversification_pct": round(diversification_pct, 2),
        "components": components,
    }


@router.post("/portfolio")
def calculate_portfolio_risk(data: PortfolioRiskInput):
    """Compare Historical and Monte Carlo VaR, with parametric risk attribution."""
    tickers, weights = _clean_input(data)
    prices = _download_prices(tickers, data.lookback)

    simple_returns = prices.pct_change().dropna()
    log_returns = np.log(prices / prices.shift(1)).dropna()

    historical_returns = _historical_horizon_returns(
        simple_returns,
        weights,
        data.horizon_days,
    )
    mc_returns = _monte_carlo_returns(
        log_returns,
        weights,
        data.horizon_days,
        data.simulations,
        data.seed,
    )

    historical = _loss_metrics(historical_returns, data.confidence, data.portfolio_value)
    monte_carlo = _loss_metrics(mc_returns, data.confidence, data.portfolio_value)
    attribution = _parametric_risk_attribution(
        simple_returns,
        tickers,
        weights,
        data.portfolio_value,
        data.confidence,
        data.horizon_days,
    )

    correlation = simple_returns.corr().round(4)
    latest_prices = prices.iloc[-1]

    sample_count = min(1500, len(mc_returns))
    sample_idx = np.linspace(0, len(mc_returns) - 1, sample_count, dtype=int)
    mc_pnl_sample = (mc_returns[sample_idx] * data.portfolio_value).round(2).tolist()

    return {
        "status": "success",
        "portfolio": {
            "portfolio_value": round(data.portfolio_value, 2),
            "confidence": data.confidence,
            "horizon_days": data.horizon_days,
            "lookback": data.lookback,
            "positions": [
                {
                    "ticker": ticker,
                    "weight_pct": round(float(weight) * 100, 2),
                    "latest_price": round(float(latest_prices[ticker]), 2),
                }
                for ticker, weight in zip(tickers, weights)
            ],
        },
        "models": {
            "historical": historical,
            "monte_carlo": monte_carlo,
        },
        "attribution": attribution,
        "diagnostics": {
            "historical_observations": int(len(historical_returns)),
            "simulations": int(data.simulations),
            "correlation_matrix": {
                row: {col: float(correlation.loc[row, col]) for col in tickers}
                for row in tickers
            },
        },
        "distribution": {
            "monte_carlo_pnl_sample": mc_pnl_sample,
            "var_threshold_loss": monte_carlo["var_loss"],
            "expected_shortfall_loss": monte_carlo["expected_shortfall_loss"],
        },
    }
