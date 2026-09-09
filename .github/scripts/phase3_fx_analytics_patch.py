from pathlib import Path

path = Path('sethiportfolio.py')
text = path.read_text()

insert_anchor = '\n\ndef _download_adjusted_close(symbols: List[str], start: str) -> pd.DataFrame:\n'
if insert_anchor not in text:
    raise SystemExit('FX helper insertion anchor missing')

helpers = r'''


def _derive_open_fx_basis(transactions: List[Dict[str, Any]], base_currency: str) -> Dict[str, Dict[str, Any]]:
    """Track the weighted-average local principal, base principal and remaining buy-fee basis of open inventory."""
    book: Dict[str, Dict[str, Any]] = {}
    for txn in transactions:
        instrument = txn.get("instruments") or {}
        symbol = instrument.get("symbol")
        if not symbol:
            continue
        side = str(txn.get("side") or "").upper()
        qty = float(txn.get("quantity") or 0.0)
        price = float(txn.get("price") or 0.0)
        fees = float(txn.get("fees") or 0.0)
        if qty <= 0 or price < 0:
            continue
        fx = _trade_fx(txn, base_currency)
        row = book.setdefault(symbol, {
            "symbol": symbol,
            "name": instrument.get("name") or symbol,
            "currency": str(instrument.get("currency") or txn.get("currency") or base_currency).upper(),
            "quantity": 0.0,
            "local_principal": 0.0,
            "base_principal": 0.0,
            "fee_basis_base": 0.0,
        })
        if side == "BUY":
            row["quantity"] += qty
            row["local_principal"] += qty * price
            row["base_principal"] += qty * price * fx
            row["fee_basis_base"] += fees * fx
        elif side == "SELL":
            held = float(row["quantity"])
            if qty > held + 1e-9:
                raise HTTPException(status_code=500, detail=f"Transaction history sells more {symbol} than held.")
            proportion = qty / held if held > 0 else 0.0
            row["quantity"] -= qty
            row["local_principal"] *= (1.0 - proportion)
            row["base_principal"] *= (1.0 - proportion)
            row["fee_basis_base"] *= (1.0 - proportion)
            if abs(row["quantity"]) < 1e-9:
                row["quantity"] = 0.0
                row["local_principal"] = 0.0
                row["base_principal"] = 0.0
                row["fee_basis_base"] = 0.0
    return book


def _fx_period_changes(series: pd.Series, inception_date: date) -> Dict[str, Any]:
    clean = series.dropna().sort_index()
    if clean.empty:
        return {}
    end_ts = pd.Timestamp(clean.index[-1])
    end_value = float(clean.iloc[-1])
    specs = {
        "1M": end_ts - pd.DateOffset(months=1),
        "3M": end_ts - pd.DateOffset(months=3),
        "YTD": pd.Timestamp(date(end_ts.year, 1, 1)),
        "SI": pd.Timestamp(inception_date),
    }
    result: Dict[str, Any] = {}
    for key, cutoff in specs.items():
        eligible = clean[clean.index >= cutoff]
        if eligible.empty:
            eligible = clean
        start_value = float(eligible.iloc[0])
        start_ts = pd.Timestamp(eligible.index[0])
        change = (end_value / start_value - 1.0) * 100.0 if start_value else 0.0
        result[key] = {
            "start_date": start_ts.date().isoformat(),
            "end_date": end_ts.date().isoformat(),
            "start_rate": round(start_value, 6),
            "end_rate": round(end_value, 6),
            "change_pct": round(change, 4),
        }
    return result


def _portfolio_fx_analytics(portfolio: Dict[str, Any], transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    base_currency = str(portfolio.get("base_currency") or "GBP").upper()
    snapshot = _holdings_snapshot(portfolio, transactions)
    holdings = [row for row in (snapshot.get("holdings") or []) if row.get("symbol") and float(row.get("market_value") or 0.0) > 0]
    portfolio_value = float(snapshot.get("portfolio_value") or 0.0)
    if portfolio_value <= 0:
        raise HTTPException(status_code=400, detail="Portfolio value is unavailable for FX analytics.")

    basis = _derive_open_fx_basis(transactions, base_currency)
    holding_rows: List[Dict[str, Any]] = []
    foreign_value = 0.0
    local_price_effect_total = 0.0
    fx_effect_total = 0.0
    fee_drag_total = 0.0
    unrealised_total = 0.0
    currency_values: Dict[str, float] = defaultdict(float)

    for holding in holdings:
        symbol = str(holding["symbol"])
        currency = str(holding.get("currency") or base_currency).upper()
        market_value = float(holding.get("market_value") or 0.0)
        currency_values[currency] += market_value
        if currency != base_currency:
            foreign_value += market_value

        open_basis = basis.get(symbol) or {}
        qty = float(open_basis.get("quantity") or holding.get("quantity") or 0.0)
        local_principal = float(open_basis.get("local_principal") or 0.0)
        base_principal = float(open_basis.get("base_principal") or 0.0)
        fee_basis = float(open_basis.get("fee_basis_base") or 0.0)
        current_price = float(holding.get("current_price") or 0.0)
        current_fx = float(holding.get("current_fx_to_base") or 1.0)
        avg_cost_local = local_principal / qty if qty > 0 else 0.0
        avg_entry_fx = base_principal / local_principal if local_principal > 0 else current_fx

        local_price_effect = qty * (current_price - avg_cost_local) * avg_entry_fx
        fx_effect = 0.0 if currency == base_currency else qty * current_price * (current_fx - avg_entry_fx)
        fee_drag = -fee_basis
        decomposed = local_price_effect + fx_effect + fee_drag
        unrealised = float(holding.get("unrealised_pnl") or 0.0)
        reconciliation = unrealised - decomposed

        local_price_effect_total += local_price_effect
        fx_effect_total += fx_effect
        fee_drag_total += fee_drag
        unrealised_total += unrealised
        holding_rows.append({
            "symbol": symbol,
            "name": holding.get("name") or symbol,
            "currency": currency,
            "market_value": round(market_value, 2),
            "portfolio_weight_pct": round(market_value / portfolio_value * 100.0, 2),
            "average_entry_fx": round(avg_entry_fx, 6),
            "current_fx_to_base": round(current_fx, 6),
            "fx_rate_change_since_entry_pct": round((current_fx / avg_entry_fx - 1.0) * 100.0, 4) if avg_entry_fx else 0.0,
            "local_price_effect": round(local_price_effect, 2),
            "fx_effect": round(fx_effect, 2),
            "fee_drag": round(fee_drag, 2),
            "unrealised_pnl": round(unrealised, 2),
            "reconciliation_error": round(reconciliation, 4),
        })

    foreign_currencies = sorted(currency for currency, value in currency_values.items() if currency != base_currency and value > 0)
    inception_date = date.fromisoformat(str(portfolio.get("inception_date")))
    currency_rows: List[Dict[str, Any]] = []
    history: Dict[str, Any] = {}
    if foreign_currencies:
        fx_symbols = [_fx_symbol(currency, base_currency) for currency in foreign_currencies]
        valid_symbols = [symbol for symbol in fx_symbols if symbol]
        closes = _download_adjusted_close(valid_symbols, inception_date.isoformat()) if valid_symbols else pd.DataFrame()
        for currency in foreign_currencies:
            symbol = _fx_symbol(currency, base_currency)
            if not symbol or symbol not in closes.columns:
                continue
            series = closes[symbol].dropna()
            if series.empty:
                continue
            periods = _fx_period_changes(series, inception_date)
            current_rate = float(series.iloc[-1])
            value = float(currency_values[currency])
            currency_rows.append({
                "currency": currency,
                "pair": f"{currency}/{base_currency}",
                "yahoo_symbol": symbol,
                "exposure_value": round(value, 2),
                "portfolio_weight_pct": round(value / portfolio_value * 100.0, 2),
                "current_rate": round(current_rate, 6),
                "nav_impact_per_1pct_currency_move_pp": round(value / portfolio_value, 4),
                "periods": periods,
            })
            first = float(series.iloc[0])
            history[currency] = {
                "dates": [pd.Timestamp(ts).date().isoformat() for ts in series.index],
                "rebased": [round(float(value_) / first * 100.0, 4) for value_ in series],
                "rates": [round(float(value_), 6) for value_ in series],
            }

    scenarios = []
    for gbp_move_pct in (-5.0, -2.0, -1.0, 1.0, 2.0, 5.0):
        gbp_move = gbp_move_pct / 100.0
        factor = 1.0 / (1.0 + gbp_move)
        pnl = foreign_value * (factor - 1.0)
        scenarios.append({
            "gbp_move_pct": gbp_move_pct,
            "foreign_currency_rate_factor": round(factor, 6),
            "pnl_impact": round(pnl, 2),
            "nav_impact_pct": round(pnl / portfolio_value * 100.0, 4),
        })

    holding_rows.sort(key=lambda row: abs(float(row.get("fx_effect") or 0.0)), reverse=True)
    currency_rows.sort(key=lambda row: float(row.get("exposure_value") or 0.0), reverse=True)
    largest_fx_effect = holding_rows[0] if holding_rows else None

    return {
        "status": "success",
        "scope": "current_open_book_fx_analytics",
        "method": "weighted_average_entry_fx_open_inventory_decomposition",
        "methodology": (
            "For each open position, unrealised P&L is decomposed into a local-price effect valued at weighted-average entry FX, "
            "an FX translation effect valued on the current local price, and remaining buy-fee drag. These components reconcile to current unrealised P&L apart from rounding. "
            "FX scenarios shock all non-base quote currencies simultaneously against the portfolio base currency and hold local security prices constant. "
            "This is quote-currency translation analysis, not issuer revenue or economic-currency exposure."
        ),
        "portfolio": {
            "base_currency": base_currency,
            "portfolio_value": round(portfolio_value, 2),
            "foreign_quote_value": round(foreign_value, 2),
            "foreign_quote_weight_pct": round(foreign_value / portfolio_value * 100.0, 2),
            "foreign_currency_count": len(foreign_currencies),
        },
        "decomposition": {
            "local_price_effect": round(local_price_effect_total, 2),
            "fx_effect": round(fx_effect_total, 2),
            "fee_drag": round(fee_drag_total, 2),
            "unrealised_pnl": round(unrealised_total, 2),
            "reconciliation_error": round(unrealised_total - (local_price_effect_total + fx_effect_total + fee_drag_total), 4),
            "largest_fx_effect": largest_fx_effect,
        },
        "currencies": currency_rows,
        "holdings": holding_rows,
        "scenarios": scenarios,
        "history": history,
    }
'''

text = text.replace(insert_anchor, helpers + insert_anchor, 1)

route_anchor = '''@router.get("/{slug}/risk-analytics")\ndef get_risk_analytics(slug: str) -> Dict[str, Any]:\n    portfolio = _portfolio(slug)\n    transactions = _effective_transactions(_transactions(portfolio["id"]))\n    return _portfolio_risk_analytics(portfolio, transactions)\n'''
if route_anchor not in text:
    raise SystemExit('FX route anchor missing')
route = '''@router.get("/{slug}/fx-analytics")\ndef get_fx_analytics(slug: str) -> Dict[str, Any]:\n    portfolio = _portfolio(slug)\n    transactions = _effective_transactions(_transactions(portfolio["id"]))\n    return _portfolio_fx_analytics(portfolio, transactions)\n\n\n'''
text = text.replace(route_anchor, route + route_anchor, 1)

path.write_text(text)
