from pathlib import Path

path = Path('sethiportfolio.py')
text = path.read_text()

old_import = 'from collections import defaultdict\nfrom datetime import date, datetime, timedelta\n'
new_import = 'from collections import defaultdict\nfrom concurrent.futures import ThreadPoolExecutor, as_completed\nfrom datetime import date, datetime, timedelta\nfrom time import monotonic\n'
if old_import not in text:
    raise SystemExit('import anchor missing')
text = text.replace(old_import, new_import, 1)

old_globals = 'ADMIN_SECRET = ""\n\n\ndef configure_supabase'
new_globals = '''ADMIN_SECRET = ""\n\n_EXPOSURE_META_CACHE: Dict[str, Dict[str, Any]] = {}\n_EXPOSURE_META_CACHE_AT: Dict[str, float] = {}\n_EXPOSURE_META_TTL_SECONDS = 6 * 60 * 60\n\n\ndef configure_supabase'''
if old_globals not in text:
    raise SystemExit('global anchor missing')
text = text.replace(old_globals, new_globals, 1)

insert_anchor = '\n\ndef _download_adjusted_close(symbols: List[str], start: str) -> pd.DataFrame:\n'
if insert_anchor not in text:
    raise SystemExit('exposure helper insertion anchor missing')

helpers = r'''

_SECTOR_LABELS = {
    "basic_materials": "Basic Materials",
    "communication_services": "Communication Services",
    "consumer_cyclical": "Consumer Discretionary",
    "consumer_defensive": "Consumer Staples",
    "energy": "Energy",
    "financial_services": "Financials",
    "healthcare": "Health Care",
    "industrials": "Industrials",
    "realestate": "Real Estate",
    "technology": "Information Technology",
    "utilities": "Utilities",
}


def _sector_label(value: str) -> str:
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not key:
        return "Unclassified"
    return _SECTOR_LABELS.get(key, key.replace("_", " ").title())


def _normalise_fund_sector_weights(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    parsed: Dict[str, float] = {}
    for key, value in raw.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(number) or number <= 0:
            continue
        parsed[_sector_label(str(key))] = number
    if not parsed:
        return {}
    total = float(sum(parsed.values()))
    # yfinance usually returns fractions, but tolerate percentage-form payloads.
    if total > 1.5:
        parsed = {key: value / 100.0 for key, value in parsed.items()}
        total = float(sum(parsed.values()))
    if total <= 0:
        return {}
    # Normalise small residual cash/other differences so each fund holding is
    # fully allocated across the sector mix used in the portfolio chart.
    return {key: value / total for key, value in parsed.items()}


def _security_type_label(quote_type: str, fallback_asset_type: str) -> str:
    token = f"{quote_type} {fallback_asset_type}".upper()
    if "ETF" in token or "FUND" in token:
        return "ETF / Fund"
    if "EQUITY" in token or "STOCK" in token:
        return "Equity"
    fallback = str(fallback_asset_type or quote_type or "Other").strip()
    return fallback.replace("_", " ").title() or "Other"


def _ticker_exposure_metadata(symbol: str, fallback_asset_type: str, fallback_currency: str) -> Dict[str, Any]:
    cache_key = f"{symbol.upper()}|{str(fallback_asset_type).lower()}|{str(fallback_currency).upper()}"
    cached_at = _EXPOSURE_META_CACHE_AT.get(cache_key)
    if cached_at is not None and monotonic() - cached_at < _EXPOSURE_META_TTL_SECONDS:
        return dict(_EXPOSURE_META_CACHE[cache_key])

    ticker = yf.Ticker(symbol)
    info: Dict[str, Any] = {}
    try:
        info = ticker.get_info() or {}
    except Exception:
        info = {}

    quote_type = str(info.get("quoteType") or fallback_asset_type or "equity").upper()
    security_type = _security_type_label(quote_type, fallback_asset_type)
    is_fund = security_type == "ETF / Fund"
    sector_mix: Dict[str, float] = {}
    sector_source = "direct security sector"
    sector_lookthrough = False

    if is_fund:
        sector_source = "fund sector look-through unavailable"
        try:
            funds_data = ticker.funds_data
            raw_sector_weights = getattr(funds_data, "sector_weightings", None)
            sector_mix = _normalise_fund_sector_weights(raw_sector_weights)
        except Exception:
            sector_mix = {}
        if sector_mix:
            sector_source = "fund sector look-through"
            sector_lookthrough = True
        else:
            sector_mix = {"Diversified ETF / Fund": 1.0}
    else:
        sector = str(info.get("sector") or "Unclassified").strip() or "Unclassified"
        sector_mix = {_sector_label(sector): 1.0}
        sector_lookthrough = _sector_label(sector) != "Unclassified"

    if is_fund:
        country = "ETF / Fund — geographic look-through unavailable"
        geography_lookthrough = False
    else:
        country = str(info.get("country") or "Unclassified").strip() or "Unclassified"
        geography_lookthrough = country != "Unclassified"

    metadata = {
        "quote_type": quote_type,
        "security_type": security_type,
        "sector_mix": sector_mix,
        "sector_source": sector_source,
        "sector_lookthrough": sector_lookthrough,
        "country": country,
        "geography_lookthrough": geography_lookthrough,
        "industry": str(info.get("industry") or "").strip() or None,
        "fund_category": str(info.get("category") or "").strip() or None,
        "exchange": str(info.get("fullExchangeName") or info.get("exchange") or "").strip() or None,
        "metadata_available": bool(info) or sector_lookthrough,
    }
    _EXPOSURE_META_CACHE[cache_key] = dict(metadata)
    _EXPOSURE_META_CACHE_AT[cache_key] = monotonic()
    return metadata


def _exposure_rows(values: Dict[str, float]) -> List[Dict[str, Any]]:
    rows = [
        {"label": label, "weight_pct": round(float(weight), 2)}
        for label, weight in values.items()
        if np.isfinite(float(weight)) and float(weight) > 0.005
    ]
    rows.sort(key=lambda row: float(row["weight_pct"]), reverse=True)
    return rows


def _portfolio_exposure_map(portfolio: Dict[str, Any], transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    snapshot = _holdings_snapshot(portfolio, transactions)
    holdings = [
        row for row in (snapshot.get("holdings") or [])
        if row.get("symbol") and float(row.get("market_value") or 0.0) > 0
    ]
    portfolio_value = float(snapshot.get("portfolio_value") or 0.0)
    if portfolio_value <= 0:
        raise HTTPException(status_code=400, detail="Portfolio value is unavailable for exposure analytics.")
    invested_value = float(sum(float(row.get("market_value") or 0.0) for row in holdings))
    if invested_value <= 0:
        raise HTTPException(status_code=400, detail="No invested holdings are available for exposure analytics.")

    metadata_by_symbol: Dict[str, Dict[str, Any]] = {}
    if holdings:
        with ThreadPoolExecutor(max_workers=min(6, len(holdings))) as executor:
            futures = {
                executor.submit(
                    _ticker_exposure_metadata,
                    str(row["symbol"]),
                    str(row.get("asset_type") or "equity"),
                    str(row.get("currency") or portfolio.get("base_currency") or "GBP"),
                ): str(row["symbol"])
                for row in holdings
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    metadata_by_symbol[symbol] = future.result()
                except Exception:
                    metadata_by_symbol[symbol] = {
                        "quote_type": str(next((row.get("asset_type") for row in holdings if row["symbol"] == symbol), "equity")).upper(),
                        "security_type": _security_type_label("", str(next((row.get("asset_type") for row in holdings if row["symbol"] == symbol), "equity"))),
                        "sector_mix": {"Unclassified": 1.0},
                        "sector_source": "metadata unavailable",
                        "sector_lookthrough": False,
                        "country": "Unclassified",
                        "geography_lookthrough": False,
                        "industry": None,
                        "fund_category": None,
                        "exchange": None,
                        "metadata_available": False,
                    }

    currency_exposure: Dict[str, float] = defaultdict(float)
    security_type_exposure: Dict[str, float] = defaultdict(float)
    sector_exposure: Dict[str, float] = defaultdict(float)
    geography_exposure: Dict[str, float] = defaultdict(float)
    sector_coverage = 0.0
    geography_coverage = 0.0
    enriched_holdings: List[Dict[str, Any]] = []

    for holding in holdings:
        symbol = str(holding["symbol"])
        market_value = float(holding.get("market_value") or 0.0)
        total_weight = market_value / portfolio_value * 100.0
        invested_weight = market_value / invested_value * 100.0
        metadata = metadata_by_symbol.get(symbol) or {}

        currency = str(holding.get("currency") or portfolio.get("base_currency") or "GBP").upper()
        currency_exposure[currency] += total_weight
        security_type_exposure[str(metadata.get("security_type") or "Other")] += total_weight

        sector_mix = metadata.get("sector_mix") or {"Unclassified": 1.0}
        for sector, fraction in sector_mix.items():
            sector_exposure[str(sector)] += invested_weight * float(fraction)
        geography_exposure[str(metadata.get("country") or "Unclassified")] += invested_weight

        if metadata.get("sector_lookthrough"):
            sector_coverage += invested_weight
        if metadata.get("geography_lookthrough"):
            geography_coverage += invested_weight

        enriched_holdings.append({
            "symbol": symbol,
            "name": holding.get("name") or symbol,
            "total_portfolio_weight_pct": round(total_weight, 2),
            "invested_weight_pct": round(invested_weight, 2),
            "currency": currency,
            "security_type": metadata.get("security_type") or "Other",
            "quote_type": metadata.get("quote_type"),
            "sector_mix": {key: round(float(value) * 100.0, 2) for key, value in sector_mix.items()},
            "sector_source": metadata.get("sector_source"),
            "country": metadata.get("country") or "Unclassified",
            "industry": metadata.get("industry"),
            "fund_category": metadata.get("fund_category"),
            "exchange": metadata.get("exchange"),
            "metadata_available": bool(metadata.get("metadata_available")),
        })

    cash_weight = max(0.0, float(snapshot.get("cash_weight_pct") or 0.0))
    base_currency = str(snapshot.get("base_currency") or portfolio.get("base_currency") or "GBP").upper()
    if cash_weight > 0:
        currency_exposure[base_currency] += cash_weight
        security_type_exposure["Cash"] += cash_weight

    sector_rows = _exposure_rows(sector_exposure)
    currency_rows = _exposure_rows(currency_exposure)
    geography_rows = _exposure_rows(geography_exposure)
    security_rows = _exposure_rows(security_type_exposure)
    failed_symbols = [row["symbol"] for row in enriched_holdings if not row["metadata_available"]]

    return {
        "status": "success",
        "scope": "current_portfolio_exposure_map",
        "method": "live_security_classification_with_etf_sector_lookthrough_when_available",
        "methodology": (
            "Sector and geography weights are percentages of invested capital. Direct equities use issuer sector and country metadata. "
            "ETF/fund sector weights use Yahoo fund sector look-through when available; otherwise the holding remains in a diversified fund bucket. "
            "ETF/fund geography is not inferred without underlying-country data. Currency exposure is based on each security's portfolio quote currency plus base-currency cash, not issuer revenue currency."
        ),
        "portfolio": {
            "portfolio_value": round(portfolio_value, 2),
            "invested_value": round(invested_value, 2),
            "cash_weight_pct": round(cash_weight, 2),
            "holding_count": len(holdings),
            "base_currency": base_currency,
        },
        "coverage": {
            "sector_lookthrough_pct": round(sector_coverage, 2),
            "geography_lookthrough_pct": round(geography_coverage, 2),
            "metadata_failures": failed_symbols,
        },
        "largest": {
            "sector": sector_rows[0] if sector_rows else None,
            "currency": currency_rows[0] if currency_rows else None,
            "geography": geography_rows[0] if geography_rows else None,
            "security_type": security_rows[0] if security_rows else None,
        },
        "exposures": {
            "sector": sector_rows,
            "currency": currency_rows,
            "geography": geography_rows,
            "security_type": security_rows,
        },
        "holdings": enriched_holdings,
    }
'''
text = text.replace(insert_anchor, helpers + insert_anchor, 1)

route_anchor = '''@router.get("/{slug}/risk-analytics")\ndef get_risk_analytics(slug: str) -> Dict[str, Any]:\n    portfolio = _portfolio(slug)\n    transactions = _effective_transactions(_transactions(portfolio["id"]))\n    return _portfolio_risk_analytics(portfolio, transactions)\n'''
if route_anchor not in text:
    raise SystemExit('route anchor missing')
route = '''@router.get("/{slug}/exposures")\ndef get_exposures(slug: str) -> Dict[str, Any]:\n    portfolio = _portfolio(slug)\n    transactions = _effective_transactions(_transactions(portfolio["id"]))\n    return _portfolio_exposure_map(portfolio, transactions)\n\n\n'''
text = text.replace(route_anchor, route + route_anchor, 1)

path.write_text(text)
