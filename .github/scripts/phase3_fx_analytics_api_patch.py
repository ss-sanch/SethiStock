from pathlib import Path

path = Path('sethiportfolio.py')
text = path.read_text()

insert_anchor = '\n\ndef _attribution_history(\n'
if insert_anchor not in text:
    raise SystemExit('FX helper insertion anchor missing')

helpers = r'''


def _portfolio_fx_analytics(portfolio: Dict[str, Any], transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Transaction-aware local-price vs FX translation P&L decomposition.

    For each marked position segment, the exact base-currency value change is
    split sequentially as:
        q * (P1 - P0) * FX0   [local-price P&L]
      + q * P1 * (FX1 - FX0) [FX translation P&L]
    Trades create a new segment at their stored execution price and historical
    FX rate, so FX P&L is only accrued while capital is actually invested.
    """
    base_currency = str(portfolio.get("base_currency") or "GBP").upper()
    inception_date = date.fromisoformat(str(portfolio.get("inception_date")))
    initial_capital = float(portfolio.get("initial_capital") or 0.0)

    symbol_meta: Dict[str, Dict[str, str]] = {}
    for txn in transactions:
        instrument = txn.get("instruments") or {}
        symbol = instrument.get("symbol")
        if not symbol:
            continue
        symbol_meta[symbol] = {
            "name": instrument.get("name") or symbol,
            "currency": (instrument.get("currency") or txn.get("currency") or base_currency).upper(),
        }

    symbols = sorted(symbol_meta)
    if not symbols:
        return {
            "status": "success",
            "base_currency": base_currency,
            "method": "transaction_aware_price_fx_pnl_decomposition",
            "periods": {},
            "fx_series": {},
            "current_sensitivity": {},
        }

    foreign_currencies = sorted({
        meta["currency"] for meta in symbol_meta.values()
        if meta["currency"] != base_currency
    })
    fx_symbols = [
        symbol for symbol in (_fx_symbol(currency, base_currency) for currency in foreign_currencies)
        if symbol
    ]
    closes = _download_adjusted_close(list(dict.fromkeys(symbols + fx_symbols)), inception_date.isoformat())

    missing_prices = [symbol for symbol in symbols if symbol not in closes.columns]
    if missing_prices:
        raise HTTPException(status_code=502, detail=f"FX analytics price history is unavailable for: {', '.join(missing_prices)}.")
    missing_fx = [
        currency for currency in foreign_currencies
        if (_fx_symbol(currency, base_currency) not in closes.columns)
    ]
    if missing_fx:
        raise HTTPException(status_code=502, detail=f"FX analytics rate history is unavailable for: {', '.join(missing_fx)}.")

    trading_dates = closes[symbols].dropna(how="all").index
    if trading_dates.empty:
        raise HTTPException(status_code=502, detail="No trading dates are available for FX analytics.")

    local_series: Dict[str, pd.Series] = {
        symbol: closes[symbol].reindex(trading_dates).ffill()
        for symbol in symbols
    }
    fx_series: Dict[str, pd.Series] = {
        currency: _series_fx(closes, currency, base_currency, trading_dates)
        for currency in sorted({meta["currency"] for meta in symbol_meta.values()})
    }

    txns_by_date_symbol: Dict[date, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for txn in transactions:
        symbol = (txn.get("instruments") or {}).get("symbol")
        if symbol:
            txns_by_date_symbol[pd.Timestamp(txn["trade_date"]).date()][symbol].append(txn)

    quantities: Dict[str, float] = defaultdict(float)
    previous_price: Dict[str, float] = {}
    previous_fx: Dict[str, float] = {}
    cash = initial_capital
    previous_nav = initial_capital
    daily_rows: List[Dict[str, Any]] = []

    for raw_ts in trading_dates:
        ts = pd.Timestamp(raw_ts)
        day = ts.date()
        local_total = 0.0
        fx_total = 0.0
        fees_total = 0.0
        fx_by_currency: Dict[str, float] = defaultdict(float)
        fx_by_symbol: Dict[str, float] = defaultdict(float)

        for symbol in symbols:
            currency = symbol_meta[symbol]["currency"]
            current_price_raw = local_series[symbol].loc[ts]
            current_fx_raw = fx_series[currency].loc[ts]
            if pd.isna(current_price_raw) or pd.isna(current_fx_raw):
                continue
            current_price = float(current_price_raw)
            current_fx = float(current_fx_raw)
            quantity = float(quantities.get(symbol, 0.0))
            anchor_price = float(previous_price.get(symbol, current_price))
            anchor_fx = float(previous_fx.get(symbol, current_fx))

            for txn in txns_by_date_symbol.get(day, {}).get(symbol, []):
                trade_price = float(txn.get("price") or 0.0)
                trade_fx = _trade_fx(txn, base_currency)
                if quantity:
                    local_piece = quantity * (trade_price - anchor_price) * anchor_fx
                    fx_piece = quantity * trade_price * (trade_fx - anchor_fx)
                    local_total += local_piece
                    fx_total += fx_piece
                    fx_by_currency[currency] += fx_piece
                    fx_by_symbol[symbol] += fx_piece

                qty = float(txn.get("quantity") or 0.0)
                fees = float(txn.get("fees") or 0.0)
                side = str(txn.get("side") or "").upper()
                if side == "BUY":
                    quantity += qty
                    cash -= (qty * trade_price + fees) * trade_fx
                elif side == "SELL":
                    quantity -= qty
                    cash += (qty * trade_price - fees) * trade_fx
                if quantity < -1e-8:
                    raise HTTPException(status_code=500, detail=f"FX analytics reconstructed a negative {symbol} position.")
                if abs(quantity) < 1e-9:
                    quantity = 0.0
                fee_piece = -fees * trade_fx
                fees_total += fee_piece
                anchor_price = trade_price
                anchor_fx = trade_fx

            if quantity:
                local_piece = quantity * (current_price - anchor_price) * anchor_fx
                fx_piece = quantity * current_price * (current_fx - anchor_fx)
                local_total += local_piece
                fx_total += fx_piece
                fx_by_currency[currency] += fx_piece
                fx_by_symbol[symbol] += fx_piece

            quantities[symbol] = quantity
            previous_price[symbol] = current_price
            previous_fx[symbol] = current_fx

        nav = cash
        for symbol in symbols:
            quantity = float(quantities.get(symbol, 0.0))
            if not quantity:
                continue
            currency = symbol_meta[symbol]["currency"]
            price_raw = local_series[symbol].loc[ts]
            fx_raw = fx_series[currency].loc[ts]
            if pd.notna(price_raw) and pd.notna(fx_raw):
                nav += quantity * float(price_raw) * float(fx_raw)

        market_pnl = local_total + fx_total + fees_total
        reconciliation_error = (nav - previous_nav) - market_pnl
        daily_rows.append({
            "date": day.isoformat(),
            "nav": float(nav),
            "local_pnl": float(local_total),
            "fx_pnl": float(fx_total),
            "fees_pnl": float(fees_total),
            "reconciliation_error": float(reconciliation_error),
            "fx_by_currency": dict(fx_by_currency),
            "fx_by_symbol": dict(fx_by_symbol),
        })
        previous_nav = nav

    if not daily_rows:
        raise HTTPException(status_code=502, detail="FX analytics could not reconstruct daily portfolio P&L.")

    end_date = date.fromisoformat(daily_rows[-1]["date"])
    period_specs = {
        "1M": end_date - pd.DateOffset(months=1),
        "3M": end_date - pd.DateOffset(months=3),
        "YTD": date(end_date.year, 1, 1),
        "SI": inception_date,
    }
    period_labels = {"1M": "1 Month", "3M": "3 Months", "YTD": "Year to Date", "SI": "Since Inception"}
    periods: Dict[str, Any] = {}

    for key, requested_start_raw in period_specs.items():
        requested_start = requested_start_raw.date() if hasattr(requested_start_raw, "date") else requested_start_raw
        if key == "SI" or requested_start <= inception_date:
            start_date = inception_date
            start_nav = initial_capital
            selected = daily_rows
        else:
            start_index = next(
                (index for index, row in enumerate(daily_rows) if date.fromisoformat(row["date"]) >= requested_start),
                0,
            )
            start_date = date.fromisoformat(daily_rows[start_index]["date"])
            start_nav = float(daily_rows[start_index]["nav"])
            selected = daily_rows[start_index + 1:]

        end_nav = float(daily_rows[-1]["nav"])
        local_pnl = sum(float(row["local_pnl"]) for row in selected)
        fx_pnl = sum(float(row["fx_pnl"]) for row in selected)
        fees_pnl = sum(float(row["fees_pnl"]) for row in selected)
        reconciliation_error = (end_nav - start_nav) - (local_pnl + fx_pnl + fees_pnl)

        currency_pnl: Dict[str, float] = defaultdict(float)
        symbol_pnl: Dict[str, float] = defaultdict(float)
        for row in selected:
            for currency, value in (row.get("fx_by_currency") or {}).items():
                currency_pnl[currency] += float(value)
            for symbol, value in (row.get("fx_by_symbol") or {}).items():
                symbol_pnl[symbol] += float(value)

        currency_rows: List[Dict[str, Any]] = []
        for currency in foreign_currencies:
            series = fx_series[currency]
            start_candidates = series.loc[series.index >= pd.Timestamp(start_date)].dropna()
            end_candidates = series.dropna()
            fx_return = None
            if not start_candidates.empty and not end_candidates.empty and float(start_candidates.iloc[0]) != 0:
                fx_return = (float(end_candidates.iloc[-1]) / float(start_candidates.iloc[0]) - 1.0) * 100.0
            pnl = float(currency_pnl.get(currency, 0.0))
            currency_rows.append({
                "currency": currency,
                "pair": f"{currency}/{base_currency}",
                "fx_return_pct": None if fx_return is None else round(fx_return, 4),
                "fx_pnl": round(pnl, 2),
                "contribution_pp": round((pnl / start_nav * 100.0) if start_nav else 0.0, 4),
            })
        currency_rows.sort(key=lambda row: abs(float(row.get("contribution_pp") or 0.0)), reverse=True)

        holding_rows = [
            {
                "symbol": symbol,
                "name": symbol_meta[symbol]["name"],
                "currency": symbol_meta[symbol]["currency"],
                "fx_pnl": round(float(pnl), 2),
                "contribution_pp": round((float(pnl) / start_nav * 100.0) if start_nav else 0.0, 4),
            }
            for symbol, pnl in symbol_pnl.items()
            if symbol_meta.get(symbol, {}).get("currency") != base_currency and abs(float(pnl)) >= 0.005
        ]
        holding_rows.sort(key=lambda row: abs(float(row["contribution_pp"])), reverse=True)

        periods[key] = {
            "label": period_labels[key],
            "requested_start_date": requested_start.isoformat(),
            "start_date": start_date.isoformat(),
            "end_date": daily_rows[-1]["date"],
            "starting_nav": round(start_nav, 2),
            "ending_nav": round(end_nav, 2),
            "portfolio_return_pct": round(((end_nav / start_nav - 1.0) * 100.0) if start_nav else 0.0, 4),
            "local_price_pnl": round(local_pnl, 2),
            "local_price_contribution_pp": round((local_pnl / start_nav * 100.0) if start_nav else 0.0, 4),
            "fx_pnl": round(fx_pnl, 2),
            "fx_contribution_pp": round((fx_pnl / start_nav * 100.0) if start_nav else 0.0, 4),
            "fees_pnl": round(fees_pnl, 2),
            "fees_contribution_pp": round((fees_pnl / start_nav * 100.0) if start_nav else 0.0, 4),
            "reconciliation_error": round(reconciliation_error, 6),
            "currencies": currency_rows,
            "holdings": holding_rows,
        }

    snapshot = _holdings_snapshot(portfolio, transactions)
    portfolio_value = float(snapshot.get("portfolio_value") or 0.0)
    current_by_currency: Dict[str, float] = defaultdict(float)
    for holding in snapshot.get("holdings") or []:
        currency = str(holding.get("currency") or base_currency).upper()
        current_by_currency[currency] += float(holding.get("market_value") or 0.0)

    current_rows: List[Dict[str, Any]] = []
    foreign_value = 0.0
    for currency, value in current_by_currency.items():
        if currency == base_currency:
            continue
        foreign_value += value
        weight_pct = (value / portfolio_value * 100.0) if portfolio_value else 0.0
        current_rows.append({
            "currency": currency,
            "pair": f"{currency}/{base_currency}",
            "market_value": round(value, 2),
            "nav_weight_pct": round(weight_pct, 2),
            "impact_pp_if_currency_plus_1pct": round(weight_pct * 0.01, 4),
            "impact_pp_if_currency_plus_5pct": round(weight_pct * 0.05, 4),
            "impact_pp_if_currency_minus_5pct": round(-weight_pct * 0.05, 4),
        })
    current_rows.sort(key=lambda row: float(row["nav_weight_pct"]), reverse=True)
    foreign_weight_pct = (foreign_value / portfolio_value * 100.0) if portfolio_value else 0.0

    series_payload: Dict[str, Any] = {}
    for currency in foreign_currencies:
        series = fx_series[currency]
        values = [None if pd.isna(value) else round(float(value), 6) for value in series]
        series_payload[currency] = {
            "pair": f"{currency}/{base_currency}",
            "dates": [pd.Timestamp(ts).strftime("%Y-%m-%d") for ts in series.index],
            "values": values,
        }

    return {
        "status": "success",
        "base_currency": base_currency,
        "scope": "transaction_aware_quote_currency_fx_attribution",
        "method": "daily_mark_to_market_local_price_plus_fx_translation",
        "methodology": (
            "Daily base-currency P&L is decomposed exactly into local-price P&L plus FX-translation P&L using actual position quantities. "
            "Trades reset the mark at their stored execution price and historical FX rate, so currency P&L accrues only while positions are held. "
            "Fees are shown separately and the decomposition reconciles to reconstructed NAV. GBP-quoted funds have zero quote-currency FX translation here; embedded underlying-currency exposure inside an unhedged fund is not inferred without reliable fund-level hedge/look-through data."
        ),
        "periods": periods,
        "fx_series": series_payload,
        "current_sensitivity": {
            "portfolio_value": round(portfolio_value, 2),
            "foreign_quote_market_value": round(foreign_value, 2),
            "foreign_quote_weight_pct": round(foreign_weight_pct, 2),
            "impact_pp_if_foreign_currencies_plus_5pct": round(foreign_weight_pct * 0.05, 4),
            "impact_pp_if_foreign_currencies_minus_5pct": round(-foreign_weight_pct * 0.05, 4),
            "currencies": current_rows,
        },
    }
'''

text = text.replace(insert_anchor, helpers + insert_anchor, 1)

route_anchor = '''@router.get("/{slug}/risk-analytics")\ndef get_risk_analytics(slug: str) -> Dict[str, Any]:\n    portfolio = _portfolio(slug)\n    transactions = _effective_transactions(_transactions(portfolio["id"]))\n    return _portfolio_risk_analytics(portfolio, transactions)\n'''
if route_anchor not in text:
    raise SystemExit('FX route anchor missing')
route = '''@router.get("/{slug}/fx-analytics")\ndef get_fx_analytics(slug: str) -> Dict[str, Any]:\n    portfolio = _portfolio(slug)\n    transactions = _effective_transactions(_transactions(portfolio["id"]))\n    return _portfolio_fx_analytics(portfolio, transactions)\n\n\n'''
text = text.replace(route_anchor, route + route_anchor, 1)

path.write_text(text)
