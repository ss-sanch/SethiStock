# Market Risk Lab V2 — integration notes

## What is implemented

`market_risk_lab.py` adds a standalone FastAPI `APIRouter` with:

- Portfolio input for 2–5 assets
- Portfolio value, confidence, lookback and horizon controls
- Historical VaR and Historical Expected Shortfall
- Correlated multi-asset Monte Carlo VaR and Expected Shortfall
- Log-return covariance modelling for Monte Carlo
- Correlation matrix output
- A bounded Monte Carlo P&L sample for frontend visualisation
- Fixed random seed support for reproducible testing

## Mounting into the existing API

The intended integration into `app.py` is deliberately only two lines:

```python
from market_risk_lab import router as market_risk_router
app.include_router(market_risk_router)
```

Place the import with the other imports and call `include_router` after the FastAPI `app` object is created.

The endpoint will then be:

`POST /api/quant/risk/portfolio`

## Example request

```json
{
  "positions": [
    {"ticker": "AAPL", "weight": 0.30},
    {"ticker": "NVDA", "weight": 0.25},
    {"ticker": "JPM", "weight": 0.25},
    {"ticker": "GLD", "weight": 0.20}
  ],
  "portfolio_value": 100000,
  "confidence": 0.99,
  "horizon_days": 10,
  "lookback": "2y",
  "simulations": 10000,
  "seed": 42
}
```

## Validation before merge

1. Mount the router on the feature branch.
2. Run the API locally or in a Render preview deployment.
3. Test valid portfolios and failure cases: duplicate tickers, bad weights, missing data.
4. Verify VaR/ES loss magnitudes against an independent notebook calculation.
5. Connect the feature-branch frontend prototype.
6. Only then merge into `main`.
