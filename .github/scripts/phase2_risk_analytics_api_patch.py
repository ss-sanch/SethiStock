from pathlib import Path

path = Path('sethiportfolio.py')
text = path.read_text()

old_import = "import re\n\nimport pandas as pd"
new_import = "import re\n\nimport numpy as np\nimport pandas as pd"
if old_import not in text:
    raise SystemExit('numpy import anchor not found')
text = text.replace(old_import, new_import, 1)

old_market_import = "from pydantic import BaseModel, Field\n\n\nrouter = APIRouter"
new_market_import = "from pydantic import BaseModel, Field\n\nfrom market_risk_lab import _download_prices as _risk_download_prices, _parametric_risk_attribution\n\n\nrouter = APIRouter"
if old_market_import not in text:
    raise SystemExit('market risk import anchor not found')
text = text.replace(old_market_import, new_market_import, 1)

helper = r'''

def _portfolio_risk_analytics_from_prices(
    snapshot: Dict[str, Any],
    prices: pd.DataFrame,
    lookback: str = "2y",
    confidence: float = 0.99,
    horizon_days: int = 10,
) -> Dict[str, Any]:
    holdings = [
        row for row in (snapshot.get("holdings") or [])
        if row.get("symbol") and float(row.get("market_value") or 0.0) > 0
    ]
    if len(holdings) < 2:
        raise HTTPException(status_code=400, detail="At least two invested holdings are required for portfolio risk analytics.")
    if len(holdings) > 50:
        raise HTTPException(status_code=400, detail="Portfolio risk analytics currently support up to 50 invested holdings.")

    tickers = [str(row["symbol"]).upper() for row in holdings]
    missing = [ticker for ticker in tickers if ticker not in prices.columns]
    if missing:
        raise HTTPException(status_code=502, detail=f"Risk history is unavailable for: {', '.join(missing)}.")

    invested_values = np.array([float(row.get("market_value") or 0.0) for row in holdings], dtype=float)
    invested_value = float(invested_values.sum())
    if invested_value <= 0:
        raise HTTPException(status_code=400, detail="No invested capital is available for risk analytics.")
    weights = invested_values / invested_value

    simple_returns = prices[tickers].pct_change().dropna()
    if len(simple_returns) < 100:
        raise HTTPException(status_code=502, detail="At least 100 aligned daily observations are required for portfolio risk analytics.")

    attribution = _parametric_risk_attribution(
        simple_returns,
        tickers,
        weights,
        invested_value,
        confidence,
        horizon_days,
    )
    correlation = simple_returns[tickers].corr()

    pair_rows: List[Dict[str, Any]] = []
    weighted_corr_numerator = 0.0
    weighted_corr_denominator = 0.0
    for i, left in enumerate(tickers):
        for j in range(i + 1, len(tickers)):
            right = tickers[j]
            corr = float(correlation.loc[left, right])
            if not np.isfinite(corr):
                continue
            pair_weight = float(weights[i] * weights[j])
            weighted_corr_numerator += pair_weight * corr
            weighted_corr_denominator += pair_weight
            pair_rows.append({"left": left, "right": right, "correlation": corr})

    weighted_average_correlation = (
        weighted_corr_numerator / weighted_corr_denominator
        if weighted_corr_denominator > 0 else 0.0
    )
    highest_pair = max(pair_rows, key=lambda row: row["correlation"]) if pair_rows else None
    lowest_pair = min(pair_rows, key=lambda row: row["correlation"]) if pair_rows else None

    hhi = float(np.square(weights).sum())
    effective_holdings = 1.0 / hhi if hhi > 0 else 0.0
    sorted_weights = sorted((float(weight) for weight in weights), reverse=True)
    top_5 = sum(sorted_weights[:5]) * 100.0
    top_10 = sum(sorted_weights[:10]) * 100.0

    holding_lookup = {str(row["symbol"]).upper(): row for row in holdings}
    enriched_components = []
    for component in attribution.get("components") or []:
        ticker = str(component.get("ticker") or "").upper()
        holding = holding_lookup.get(ticker) or {}
        invested_weight_pct = float(component.get("weight_pct") or 0.0)
        contribution_pct = float(component.get("contribution_pct") or 0.0)
        enriched = dict(component)
        enriched.update({
            "name": holding.get("name") or ticker,
            "total_portfolio_weight_pct": round(float(holding.get("weight_pct") or 0.0), 2),
            "invested_weight_pct": round(invested_weight_pct, 2),
            "risk_minus_weight_pp": round(contribution_pct - invested_weight_pct, 2),
        })
        enriched_components.append(enriched)
    enriched_components.sort(key=lambda row: float(row.get("contribution_pct") or 0.0), reverse=True)
    attribution = dict(attribution)
    attribution["components"] = enriched_components

    top_risk = enriched_components[0] if enriched_components else None
    largest_risk_gap = max(
        enriched_components,
        key=lambda row: abs(float(row.get("risk_minus_weight_pp") or 0.0)),
        default=None,
    )

    portfolio_value = float(snapshot.get("portfolio_value") or 0.0)
    invested_portfolio_weight = (invested_value / portfolio_value * 100.0) if portfolio_value > 0 else 0.0

    return {
        "status": "success",
        "scope": "invested_book_price_risk",
        "method": "parametric_euler_var_with_pairwise_correlation",
        "methodology": "Risk contribution uses the same variance-covariance Euler VaR decomposition as SethiQuant. Holdings are reweighted to 100% of invested capital; cash is excluded. Correlations use aligned daily local-price returns, so a separate FX risk factor is not modelled.",
        "parameters": {
            "lookback": lookback,
            "confidence": confidence,
            "horizon_days": horizon_days,
            "observations": int(len(simple_returns)),
        },
        "portfolio": {
            "portfolio_value": round(portfolio_value, 2),
            "invested_value": round(invested_value, 2),
            "invested_weight_pct": round(invested_portfolio_weight, 2),
            "cash_weight_pct": round(float(snapshot.get("cash_weight_pct") or 0.0), 2),
            "holding_count": len(holdings),
        },
        "concentration": {
            "top_5_invested_weight_pct": round(top_5, 2),
            "top_10_invested_weight_pct": round(top_10, 2),
            "hhi": round(hhi, 4),
            "effective_holdings": round(effective_holdings, 2),
            "weighted_average_correlation": round(weighted_average_correlation, 4),
            "highest_correlation_pair": None if not highest_pair else {
                "left": highest_pair["left"], "right": highest_pair["right"],
                "correlation": round(float(highest_pair["correlation"]), 4),
            },
            "lowest_correlation_pair": None if not lowest_pair else {
                "left": lowest_pair["left"], "right": lowest_pair["right"],
                "correlation": round(float(lowest_pair["correlation"]), 4),
            },
            "top_risk_contributor": top_risk,
            "largest_risk_weight_gap": largest_risk_gap,
        },
        "attribution": attribution,
        "correlation": {
            "tickers": tickers,
            "matrix": {
                row: {col: round(float(correlation.loc[row, col]), 4) for col in tickers}
                for row in tickers
            },
        },
    }


def _portfolio_risk_analytics(portfolio: Dict[str, Any], transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    snapshot = _holdings_snapshot(portfolio, transactions)
    holdings = [
        row for row in (snapshot.get("holdings") or [])
        if row.get("symbol") and float(row.get("market_value") or 0.0) > 0
    ]
    tickers = [str(row["symbol"]).upper() for row in holdings]
    if len(tickers) < 2:
        raise HTTPException(status_code=400, detail="At least two invested holdings are required for portfolio risk analytics.")
    prices = _risk_download_prices(tickers, "2y")
    return _portfolio_risk_analytics_from_prices(snapshot, prices)
'''

anchor = "\n\nclass AdminTransactionPayload(BaseModel):"
if anchor not in text:
    raise SystemExit('helper insertion anchor not found')
text = text.replace(anchor, helper + anchor, 1)

route = r'''

@router.get("/{slug}/risk-analytics")
def get_risk_analytics(slug: str) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    transactions = _effective_transactions(_transactions(portfolio["id"]))
    return _portfolio_risk_analytics(portfolio, transactions)
'''

route_anchor = "\n\n@router.get(\"/{slug}/attribution\")\ndef get_attribution(slug: str) -> Dict[str, Any]:\n    portfolio = _portfolio(slug)\n    transactions = _effective_transactions(_transactions(portfolio[\"id\"]))\n    return _attribution_history(portfolio, transactions)"
if route_anchor not in text:
    raise SystemExit('public route anchor not found')
text = text.replace(route_anchor, route_anchor + route, 1)

path.write_text(text)
