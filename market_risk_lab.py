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
        # Defensive fallback for a single-column yfinance response.
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

    # Present VaR and ES as positive loss magnitudes.
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

    # Rolling realised portfolio return over the requested horizon.
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

    # Small diagonal jitter protects Cholesky/eigendecomposition from numerical noise.
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

    # Convert each simulated asset's cumulative log return into a simple return,
    # then revalue the fixed-weight portfolio at the horizon.
    cumulative_asset_log_returns = shocks.sum(axis=1)
    cumulative_asset_simple_returns = np.exp(cumulative_asset_log_returns) - 1.0
    portfolio_returns = cumulative_asset_simple_returns @ weights
    return portfolio_returns


@router.post("/portfolio")
def calculate_portfolio_risk(data: PortfolioRiskInput):
    """Compare Historical VaR and correlated Monte Carlo VaR for a portfolio."""
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

    correlation = simple_returns.corr().round(4)
    latest_prices = prices.iloc[-1]

    # Return a bounded sample for Plotly rather than all 10k observations.
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
