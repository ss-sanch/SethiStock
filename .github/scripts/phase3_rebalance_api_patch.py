from pathlib import Path

path = Path('sethiportfolio.py')
text = path.read_text()

if 'class PortfolioRebalanceWeight' in text or '/rebalance-simulator' in text:
    raise SystemExit('Rebalance simulator already present')

insert_before_models = r'''

class PortfolioRebalanceWeight(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    weight_pct: float = Field(ge=0.0, le=100.0)


class PortfolioRebalancePayload(BaseModel):
    weights: List[PortfolioRebalanceWeight]
    cash_weight_pct: float = Field(default=0.0, ge=0.0, le=100.0)


def _rebalance_risk_state(
    simple_returns: pd.DataFrame,
    target_weights_pct: Dict[str, float],
    portfolio_value: float,
    confidence: float = 0.99,
    horizon_days: int = 10,
) -> Dict[str, Any]:
    active = [symbol for symbol, weight in target_weights_pct.items() if float(weight) > 1e-9]
    if len(active) < 2:
        raise HTTPException(status_code=400, detail='A simulated portfolio must keep at least two invested holdings.')

    invested_weight_pct = float(sum(float(target_weights_pct[symbol]) for symbol in active))
    if invested_weight_pct <= 0:
        raise HTTPException(status_code=400, detail='The simulated invested weight must be positive.')
    invested_value = float(portfolio_value) * invested_weight_pct / 100.0
    risky_weights = np.array([float(target_weights_pct[symbol]) / invested_weight_pct for symbol in active], dtype=float)

    attribution = _parametric_risk_attribution(
        simple_returns,
        active,
        risky_weights,
        invested_value,
        confidence,
        horizon_days,
    )
    correlation = simple_returns[active].corr()

    weighted_corr_numerator = 0.0
    weighted_corr_denominator = 0.0
    for i, left in enumerate(active):
        for j in range(i + 1, len(active)):
            corr = float(correlation.loc[left, active[j]])
            if not np.isfinite(corr):
                continue
            pair_weight = float(risky_weights[i] * risky_weights[j])
            weighted_corr_numerator += pair_weight * corr
            weighted_corr_denominator += pair_weight
    weighted_average_correlation = (
        weighted_corr_numerator / weighted_corr_denominator
        if weighted_corr_denominator > 0 else 0.0
    )

    hhi = float(np.square(risky_weights).sum())
    effective_holdings = 1.0 / hhi if hhi > 0 else 0.0
    sorted_weights = sorted((float(weight) for weight in risky_weights), reverse=True)
    top_5 = sum(sorted_weights[:5]) * 100.0

    components = []
    for component in attribution.get('components') or []:
        ticker = str(component.get('ticker') or '').upper()
        contribution_pct = float(component.get('contribution_pct') or 0.0)
        invested_component_weight = float(component.get('weight_pct') or 0.0)
        row = dict(component)
        row.update({
            'total_portfolio_weight_pct': round(float(target_weights_pct.get(ticker, 0.0)), 2),
            'invested_weight_pct': round(invested_component_weight, 2),
            'risk_minus_weight_pp': round(contribution_pct - invested_component_weight, 2),
        })
        components.append(row)
    components.sort(key=lambda row: float(row.get('contribution_pct') or 0.0), reverse=True)

    portfolio_var = float(attribution.get('portfolio_var') or 0.0)
    return {
        'invested_weight_pct': round(invested_weight_pct, 2),
        'cash_weight_pct': round(max(0.0, 100.0 - invested_weight_pct), 2),
        'invested_value': round(invested_value, 2),
        'holding_count': len(active),
        'top_5_invested_weight_pct': round(top_5, 2),
        'hhi': round(hhi, 4),
        'effective_holdings': round(effective_holdings, 2),
        'weighted_average_correlation': round(weighted_average_correlation, 4),
        'portfolio_var': round(portfolio_var, 2),
        'portfolio_var_nav_pct': round(portfolio_var / portfolio_value * 100.0, 4) if portfolio_value > 0 else 0.0,
        'diversification_pct': round(float(attribution.get('diversification_pct') or 0.0), 2),
        'top_risk_contributor': components[0] if components else None,
        'components': components,
    }


def _portfolio_rebalance_simulation(
    portfolio: Dict[str, Any],
    transactions: List[Dict[str, Any]],
    payload: PortfolioRebalancePayload,
) -> Dict[str, Any]:
    snapshot = _holdings_snapshot(portfolio, transactions)
    holdings = [
        row for row in (snapshot.get('holdings') or [])
        if row.get('symbol') and float(row.get('market_value') or 0.0) > 0
    ]
    if len(holdings) < 2:
        raise HTTPException(status_code=400, detail='At least two current holdings are required for the rebalance simulator.')
    if len(holdings) > 50:
        raise HTTPException(status_code=400, detail='The rebalance simulator currently supports up to 50 holdings.')
    if not 1 <= len(payload.weights) <= 50:
        raise HTTPException(status_code=400, detail='Provide between 1 and 50 target holding weights.')

    tickers = [str(row['symbol']).upper() for row in holdings]
    current_symbols = set(tickers)
    target_weights: Dict[str, float] = {symbol: 0.0 for symbol in tickers}
    seen = set()
    for row in payload.weights:
        symbol = row.symbol.strip().upper()
        if not symbol:
            raise HTTPException(status_code=400, detail='Target symbols cannot be blank.')
        if symbol in seen:
            raise HTTPException(status_code=400, detail=f'Duplicate target weight for {symbol}.')
        if symbol not in current_symbols:
            raise HTTPException(status_code=400, detail=f'{symbol} is not a current holding. Version 1 simulates current holdings plus cash only.')
        seen.add(symbol)
        target_weights[symbol] = float(row.weight_pct)

    cash_weight = float(payload.cash_weight_pct)
    requested_total = float(sum(target_weights.values()) + cash_weight)
    if not np.isclose(requested_total, 100.0, atol=0.05):
        raise HTTPException(status_code=400, detail=f'Target holding weights plus cash must sum to 100%. Current sum: {requested_total:.2f}%.')
    # Remove tiny input-rounding drift while preserving the explicitly requested cash allocation.
    invested_target = 100.0 - cash_weight
    raw_invested = float(sum(target_weights.values()))
    if raw_invested <= 0:
        raise HTTPException(status_code=400, detail='At least some capital must remain invested.')
    scale = invested_target / raw_invested
    target_weights = {symbol: max(0.0, weight * scale) for symbol, weight in target_weights.items()}

    portfolio_value = float(snapshot.get('portfolio_value') or 0.0)
    if portfolio_value <= 0:
        raise HTTPException(status_code=400, detail='Portfolio value is unavailable for the rebalance simulator.')

    current_weights = {
        str(row['symbol']).upper(): float(row.get('market_value') or 0.0) / portfolio_value * 100.0
        for row in holdings
    }
    current_cash_weight = float(snapshot.get('cash_weight_pct') or max(0.0, 100.0 - sum(current_weights.values())))

    prices = _risk_download_prices(tickers, '2y')
    simple_returns = prices[tickers].pct_change().dropna()
    if len(simple_returns) < 100:
        raise HTTPException(status_code=502, detail='At least 100 aligned daily observations are required for rebalance risk analysis.')

    current_state = _rebalance_risk_state(simple_returns, current_weights, portfolio_value)
    proposed_state = _rebalance_risk_state(simple_returns, target_weights, portfolio_value)

    trade_rows = []
    for symbol in tickers:
        current_weight = float(current_weights.get(symbol, 0.0))
        target_weight = float(target_weights.get(symbol, 0.0))
        delta = target_weight - current_weight
        trade_rows.append({
            'symbol': symbol,
            'current_weight_pct': round(current_weight, 2),
            'target_weight_pct': round(target_weight, 2),
            'change_pp': round(delta, 2),
            'estimated_value_change': round(delta / 100.0 * portfolio_value, 2),
        })
    trade_rows.sort(key=lambda row: abs(float(row['change_pp'])), reverse=True)
    turnover_pct = 0.5 * (
        sum(abs(float(row['change_pp'])) for row in trade_rows)
        + abs(cash_weight - current_cash_weight)
    )
    active_trades = [row for row in trade_rows if abs(float(row['change_pp'])) >= 0.05]

    return {
        'status': 'success',
        'scope': 'current_holdings_plus_cash_weight_simulation',
        'method': 'static_weight_rebalance_with_parametric_euler_var',
        'methodology': (
            'Target weights are applied to the current portfolio NAV without executing trades. '
            'Risk is recomputed from the same two-year aligned local-price return history and 99% / 10-day variance-covariance Euler VaR used by SethiPortfolio risk analytics. '
            'Cash has zero price risk in this simulator. Estimated trade values ignore fees, taxes, slippage and market impact. FX risk is not modelled separately here.'
        ),
        'parameters': {'lookback': '2y', 'confidence': 0.99, 'horizon_days': 10, 'observations': int(len(simple_returns))},
        'portfolio_value': round(portfolio_value, 2),
        'current': {'weights': current_weights, 'cash_weight_pct': round(current_cash_weight, 2), 'risk': current_state},
        'proposed': {'weights': {symbol: round(weight, 4) for symbol, weight in target_weights.items()}, 'cash_weight_pct': round(cash_weight, 2), 'risk': proposed_state},
        'changes': {
            'turnover_pct': round(turnover_pct, 2),
            'trade_count': len(active_trades),
            'cash_change_pp': round(cash_weight - current_cash_weight, 2),
            'portfolio_var_change': round(float(proposed_state['portfolio_var']) - float(current_state['portfolio_var']), 2),
            'portfolio_var_nav_change_pp': round(float(proposed_state['portfolio_var_nav_pct']) - float(current_state['portfolio_var_nav_pct']), 4),
            'top_5_change_pp': round(float(proposed_state['top_5_invested_weight_pct']) - float(current_state['top_5_invested_weight_pct']), 2),
            'effective_holdings_change': round(float(proposed_state['effective_holdings']) - float(current_state['effective_holdings']), 2),
            'diversification_change_pp': round(float(proposed_state['diversification_pct']) - float(current_state['diversification_pct']), 2),
            'weighted_correlation_change': round(float(proposed_state['weighted_average_correlation']) - float(current_state['weighted_average_correlation']), 4),
            'largest_trade': active_trades[0] if active_trades else None,
            'trades': active_trades,
        },
    }
'''

anchor_models = '\n\nclass AdminTransactionPayload(BaseModel):'
if anchor_models not in text:
    raise SystemExit('Model insertion anchor not found')
text = text.replace(anchor_models, insert_before_models + anchor_models, 1)

route = r'''

@router.post("/{slug}/rebalance-simulator")
def simulate_rebalance(slug: str, payload: PortfolioRebalancePayload) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    transactions = _effective_transactions(_transactions(portfolio["id"]))
    return _portfolio_rebalance_simulation(portfolio, transactions, payload)
'''

anchor_route = '''@router.get("/{slug}/risk-analytics")
def get_risk_analytics(slug: str) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    transactions = _effective_transactions(_transactions(portfolio["id"]))
    return _portfolio_risk_analytics(portfolio, transactions)
'''
if anchor_route not in text:
    raise SystemExit('Risk route insertion anchor not found')
text = text.replace(anchor_route, anchor_route + route, 1)

path.write_text(text)
