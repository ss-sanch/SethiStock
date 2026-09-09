from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from time import monotonic
from typing import Any, Dict, List, Optional
from uuid import uuid4
import re

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from fastapi import APIRouter, HTTPException, Query, Header
from pydantic import BaseModel, Field

from market_risk_lab import _download_prices as _risk_download_prices, _parametric_risk_attribution


router = APIRouter(prefix="/api/portfolio", tags=["SethiPortfolio"])

SUPABASE_URL = ""
SUPABASE_KEY = ""
SUPABASE_SERVICE_KEY = ""
ADMIN_SECRET = ""

_EXPOSURE_META_CACHE: Dict[str, Dict[str, Any]] = {}
_EXPOSURE_META_CACHE_AT: Dict[str, float] = {}
_EXPOSURE_META_TTL_SECONDS = 6 * 60 * 60


def configure_supabase(url: str, key: str, service_key: str = "", admin_secret: str = "") -> None:
    global SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_KEY, ADMIN_SECRET
    SUPABASE_URL = (url or "").replace("/rest/v1", "").rstrip("/")
    SUPABASE_KEY = key or ""
    SUPABASE_SERVICE_KEY = service_key or ""
    ADMIN_SECRET = admin_secret or ""


def _headers(write: bool = False) -> Dict[str, str]:
    key = SUPABASE_SERVICE_KEY if write else SUPABASE_KEY
    if not SUPABASE_URL or not key:
        detail = "SethiPortfolio write service is not configured yet." if write else "SethiPortfolio database is not configured yet."
        raise HTTPException(status_code=503, detail=detail)
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _require_admin(x_admin_secret: Optional[str]) -> None:
    if not ADMIN_SECRET:
        raise HTTPException(status_code=503, detail="SethiPortfolio admin authentication is not configured.")
    if not x_admin_secret or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Invalid admin credentials.")


def _supabase_post(table: str, payload: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        headers = _headers(write=True)
        headers["Prefer"] = "return=representation"
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=headers,
            params=params or {},
            json=payload,
            timeout=12,
        )
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Portfolio write failed: {response.text[:300]}")
        rows = response.json() if response.content else []
        return rows[0] if isinstance(rows, list) and rows else {}
    except HTTPException:
        raise
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Portfolio write service unavailable: {exc}") from exc



def _supabase_post_many(table: str, payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        headers = _headers(write=True)
        headers["Prefer"] = "return=representation"
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=headers,
            json=payload,
            timeout=12,
        )
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Portfolio write failed: {response.text[:300]}")
        return response.json() if response.content else []
    except HTTPException:
        raise
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Portfolio write service unavailable: {exc}") from exc


def _supabase_patch(table: str, params: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        headers = _headers(write=True)
        headers["Prefer"] = "return=representation"
        response = requests.patch(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=headers,
            params=params,
            json=payload,
            timeout=12,
        )
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Portfolio update failed: {response.text[:300]}")
        rows = response.json() if response.content else []
        return rows[0] if isinstance(rows, list) and rows else {}
    except HTTPException:
        raise
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Portfolio update service unavailable: {exc}") from exc


def _supabase_delete(table: str, params: Dict[str, Any]) -> None:
    try:
        headers = _headers(write=True)
        response = requests.delete(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=headers,
            params=params,
            timeout=12,
        )
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Portfolio delete failed: {response.text[:300]}")
    except HTTPException:
        raise
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Portfolio delete service unavailable: {exc}") from exc


def _supabase_get(table: str, params: Dict[str, Any], admin: bool = False) -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=_headers(write=admin),
            params=params,
            timeout=12,
        )
        response.raise_for_status()
        return response.json()
    except HTTPException:
        raise
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Portfolio data service unavailable: {exc}") from exc


def _portfolio(slug: str) -> Dict[str, Any]:
    rows = _supabase_get(
        "portfolios",
        {
            "select": "id,slug,name,description,inception_date,base_currency,initial_capital,is_public",
            "slug": f"eq.{slug}",
            "is_public": "eq.true",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Portfolio not found.")
    return rows[0]


def _transactions(portfolio_id: str) -> List[Dict[str, Any]]:
    return _supabase_get(
        "portfolio_transactions",
        {
            "select": "id,trade_date,side,quantity,price,fees,currency,fx_rate_to_base,note,instruments(id,symbol,name,asset_type,currency)",
            "portfolio_id": f"eq.{portfolio_id}",
            "order": "trade_date.asc,created_at.asc",
        },
    )


def _benchmarks(portfolio_id: str) -> List[Dict[str, Any]]:
    return _supabase_get(
        "portfolio_benchmarks",
        {
            "select": "symbol,label,currency,is_primary,display_order",
            "portfolio_id": f"eq.{portfolio_id}",
            "order": "display_order.asc",
        },
    )


def _journal(portfolio_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    return _supabase_get(
        "portfolio_journal_entries",
        {
            "select": "id,slug,title,summary,body,category,effective_date,published_at,related_transaction_id",
            "portfolio_id": f"eq.{portfolio_id}",
            "is_published": "eq.true",
            "order": "effective_date.desc,published_at.desc",
            "limit": str(limit),
        },
    )


def _fx_symbol(currency: str, base_currency: str) -> str | None:
    currency = (currency or base_currency).upper()
    base_currency = base_currency.upper()
    return None if currency == base_currency else f"{currency}{base_currency}=X"


def _historical_fx_reference(currency: str, base_currency: str, requested_date: date) -> tuple[float, date, str]:
    """Return an auditable daily FX reference for transaction accounting.

    Historical transaction FX should not depend on Yahoo daily-bar timezone/close
    conventions. For GBP-base portfolios, pin the Bank of England provider exposed
    by Frankfurter. The API returns the provider date as well as the rate, so a
    weekend/holiday fallback remains explicit in the ledger UI.
    """
    currency = (currency or base_currency).upper()
    base_currency = base_currency.upper()
    if currency == base_currency:
        return 1.0, requested_date, 'Identity'

    provider = 'BOE' if base_currency == 'GBP' else 'ECB'
    try:
        response = requests.get(
            f'https://api.frankfurter.dev/v2/rate/{currency}/{base_currency}',
            params={'date': requested_date.isoformat(), 'providers': provider},
            timeout=8,
        )
        if response.status_code >= 400:
            raise ValueError(f'HTTP {response.status_code}: {response.text[:160]}')
        data = response.json()
        rate = float(data['rate'])
        rate_date = date.fromisoformat(str(data['date']))
        if rate <= 0:
            raise ValueError('non-positive FX rate')
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f'Historical FX reference unavailable for {currency}/{base_currency} on {requested_date.isoformat()}: {exc}',
        ) from exc

    return rate, rate_date, f'{provider} via Frankfurter'


def _latest_market_data(symbols: List[str]) -> Dict[str, float]:
    if not symbols:
        return {}
    try:
        data = yf.download(
            symbols,
            period="5d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="column",
        )
        if data.empty:
            return {}
        closes = data["Close"] if "Close" in data else data
        if isinstance(closes, pd.Series):
            closes = closes.to_frame(name=symbols[0])
        return {
            symbol: float(closes[symbol].dropna().iloc[-1])
            for symbol in symbols
            if symbol in closes.columns and not closes[symbol].dropna().empty
        }
    except Exception:
        return {}


def _trade_fx(txn: Dict[str, Any], base_currency: str) -> float:
    currency = ((txn.get("instruments") or {}).get("currency") or txn.get("currency") or base_currency).upper()
    if currency == base_currency.upper():
        return 1.0
    stored = txn.get("fx_rate_to_base")
    if stored is None:
        raise HTTPException(
            status_code=500,
            detail=f"Foreign-currency transaction {txn.get('id')} is missing fx_rate_to_base.",
        )
    return float(stored)


def _derive_book(transactions: List[Dict[str, Any]], base_currency: str) -> Dict[str, Dict[str, Any]]:
    """Derive open quantities and weighted-average base-currency cost from immutable trades."""
    book: Dict[str, Dict[str, Any]] = {}

    for txn in transactions:
        instrument = txn.get("instruments") or {}
        symbol = instrument.get("symbol")
        if not symbol:
            continue

        side = str(txn.get("side", "")).upper()
        qty = float(txn.get("quantity") or 0.0)
        price = float(txn.get("price") or 0.0)
        fees = float(txn.get("fees") or 0.0)
        if qty <= 0 or price < 0:
            continue

        fx = _trade_fx(txn, base_currency)
        item = book.setdefault(
            symbol,
            {
                "symbol": symbol,
                "name": instrument.get("name") or symbol,
                "asset_type": instrument.get("asset_type") or "equity",
                "currency": (instrument.get("currency") or txn.get("currency") or base_currency).upper(),
                "quantity": 0.0,
                "local_cost_basis": 0.0,
                "base_cost_basis": 0.0,
                "realised_pnl_base": 0.0,
            },
        )

        if side == "BUY":
            item["quantity"] += qty
            item["local_cost_basis"] += qty * price
            item["base_cost_basis"] += (qty * price + fees) * fx
        elif side == "SELL":
            if qty > item["quantity"] + 1e-9:
                raise HTTPException(status_code=500, detail=f"Transaction history sells more {symbol} than held.")
            avg_local = item["local_cost_basis"] / item["quantity"] if item["quantity"] else 0.0
            avg_base = item["base_cost_basis"] / item["quantity"] if item["quantity"] else 0.0
            proceeds_base = (qty * price - fees) * fx
            item["realised_pnl_base"] += proceeds_base - qty * avg_base
            item["quantity"] -= qty
            item["local_cost_basis"] -= qty * avg_local
            item["base_cost_basis"] -= qty * avg_base
            if abs(item["quantity"]) < 1e-9:
                item["quantity"] = 0.0
                item["local_cost_basis"] = 0.0
                item["base_cost_basis"] = 0.0

    return book


def _holdings_snapshot(portfolio: Dict[str, Any], transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    base_currency = str(portfolio.get("base_currency") or "GBP").upper()
    book = _derive_book(transactions, base_currency)
    open_items = [item for item in book.values() if item["quantity"] > 0]

    market_symbols = [item["symbol"] for item in open_items]
    fx_symbols = [
        fx for fx in {_fx_symbol(item["currency"], base_currency) for item in open_items}
        if fx is not None
    ]
    latest = _latest_market_data(list(dict.fromkeys(market_symbols + fx_symbols)))

    cash = float(portfolio.get("initial_capital") or 0.0)
    for txn in transactions:
        qty = float(txn.get("quantity") or 0.0)
        price = float(txn.get("price") or 0.0)
        fees = float(txn.get("fees") or 0.0)
        fx = _trade_fx(txn, base_currency)
        cash_move = (qty * price + fees) * fx if str(txn.get("side", "")).upper() == "BUY" else -(qty * price - fees) * fx
        cash -= cash_move

    holdings: List[Dict[str, Any]] = []
    invested_value = 0.0
    for item in open_items:
        local_price = latest.get(item["symbol"])
        if local_price is None:
            continue
        fx_symbol = _fx_symbol(item["currency"], base_currency)
        current_fx = 1.0 if fx_symbol is None else latest.get(fx_symbol)
        if current_fx is None:
            continue

        market_value_base = item["quantity"] * local_price * current_fx
        avg_cost_local = item["local_cost_basis"] / item["quantity"] if item["quantity"] else 0.0
        unrealised_base = market_value_base - item["base_cost_basis"]
        holdings.append(
            {
                "symbol": item["symbol"],
                "name": item["name"],
                "asset_type": item["asset_type"],
                "currency": item["currency"],
                "quantity": round(item["quantity"], 6),
                "average_cost": round(avg_cost_local, 4),
                "current_price": round(local_price, 4),
                "current_fx_to_base": round(float(current_fx), 6),
                "market_value": round(market_value_base, 2),
                "unrealised_pnl": round(unrealised_base, 2),
                "unrealised_return_pct": round((unrealised_base / item["base_cost_basis"] * 100) if item["base_cost_basis"] else 0.0, 2),
                "realised_pnl": round(item["realised_pnl_base"], 2),
            }
        )
        invested_value += market_value_base

    total_value = cash + invested_value
    for holding in holdings:
        holding["weight_pct"] = round((holding["market_value"] / total_value * 100) if total_value else 0.0, 2)

    holdings.sort(key=lambda row: row["market_value"], reverse=True)
    return {
        "base_currency": base_currency,
        "portfolio_value": round(total_value, 2),
        "cash": round(cash, 2),
        "cash_weight_pct": round((cash / total_value * 100) if total_value else 0.0, 2),
        "holdings": holdings,
        "pricing_timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


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


def _download_adjusted_close(symbols: List[str], start: str) -> pd.DataFrame:
    data = yf.download(
        symbols,
        start=start,
        end=(date.today() + pd.Timedelta(days=1)).isoformat(),
        auto_adjust=True,
        progress=False,
        group_by="column",
    )
    if data.empty:
        raise HTTPException(status_code=502, detail="Historical market data is unavailable.")
    closes = data["Close"] if "Close" in data else data
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(name=symbols[0])
    # Keep the raw market calendar here. Individual valuation series are
    # forward-filled only after the portfolio valuation calendar is chosen.
    return closes.sort_index()


def _series_fx(closes: pd.DataFrame, currency: str, base_currency: str, index: pd.Index) -> pd.Series:
    fx_symbol = _fx_symbol(currency, base_currency)
    if fx_symbol is None:
        return pd.Series(1.0, index=index)
    if fx_symbol not in closes.columns:
        raise HTTPException(status_code=502, detail=f"Historical FX series {fx_symbol} is unavailable.")
    return closes[fx_symbol].reindex(index).ffill().bfill()


def _performance_history(
    portfolio: Dict[str, Any],
    transactions: List[Dict[str, Any]],
    benchmarks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    base_currency = str(portfolio.get("base_currency") or "GBP").upper()
    symbol_currency: Dict[str, str] = {}
    for txn in transactions:
        instrument = txn.get("instruments") or {}
        symbol = instrument.get("symbol")
        if symbol:
            symbol_currency[symbol] = (instrument.get("currency") or txn.get("currency") or base_currency).upper()

    benchmark_currency = {row["symbol"]: (row.get("currency") or base_currency).upper() for row in benchmarks}
    portfolio_symbols = sorted(symbol_currency)
    benchmark_symbols = [row["symbol"] for row in benchmarks]
    currencies = set(symbol_currency.values()) | set(benchmark_currency.values())
    fx_symbols = [fx for fx in {_fx_symbol(currency, base_currency) for currency in currencies} if fx]
    download_symbols = list(dict.fromkeys(portfolio_symbols + benchmark_symbols + fx_symbols))
    if not download_symbols:
        return {"dates": [], "portfolio": [], "benchmarks": {}}

    inception = str(portfolio["inception_date"])
    closes = _download_adjusted_close(download_symbols, inception)

    # Drive NAV dates from actual portfolio-security sessions rather than the
    # union of US benchmarks, London securities and FX calendars. This avoids
    # synthetic flat points caused solely by another market being open.
    available_portfolio_symbols = [symbol for symbol in portfolio_symbols if symbol in closes.columns]
    if available_portfolio_symbols:
        trading_dates = closes[available_portfolio_symbols].dropna(how="all").index
    else:
        trading_dates = closes.dropna(how="all").index

    txns_by_date: Dict[date, List[Dict[str, Any]]] = defaultdict(list)
    for txn in transactions:
        txns_by_date[pd.Timestamp(txn["trade_date"]).date()].append(txn)

    quantities = defaultdict(float)
    cash = float(portfolio.get("initial_capital") or 0.0)
    nav_values: List[float] = []
    out_dates: List[str] = []

    for ts in trading_dates:
        day = ts.date()
        for txn in txns_by_date.get(day, []):
            symbol = (txn.get("instruments") or {}).get("symbol")
            if not symbol:
                continue
            qty = float(txn.get("quantity") or 0.0)
            price = float(txn.get("price") or 0.0)
            fees = float(txn.get("fees") or 0.0)
            fx = _trade_fx(txn, base_currency)
            if str(txn.get("side", "")).upper() == "BUY":
                quantities[symbol] += qty
                cash -= (qty * price + fees) * fx
            else:
                quantities[symbol] -= qty
                cash += (qty * price - fees) * fx

        nav = cash
        for symbol, qty in quantities.items():
            if not qty or symbol not in closes.columns:
                continue
            price_series = closes[symbol].reindex(trading_dates).ffill()
            if pd.isna(price_series.loc[ts]):
                continue
            fx_series = _series_fx(closes, symbol_currency.get(symbol, base_currency), base_currency, trading_dates)
            nav += qty * float(price_series.loc[ts]) * float(fx_series.loc[ts])

        out_dates.append(ts.strftime("%Y-%m-%d"))
        nav_values.append(float(nav))

    initial_capital = float(portfolio.get("initial_capital") or 0.0)
    portfolio_index = (
        []
        if not nav_values or initial_capital == 0
        else [round(100.0 * value / initial_capital, 4) for value in nav_values]
    )

    benchmark_data: Dict[str, Any] = {}
    for row in benchmarks:
        symbol = row["symbol"]
        if symbol not in closes.columns:
            continue
        fx_series = _series_fx(closes, benchmark_currency[symbol], base_currency, trading_dates)
        base_series = closes[symbol].reindex(trading_dates).ffill() * fx_series
        valid = base_series.dropna()
        if valid.empty:
            continue
        first = float(valid.iloc[0])
        benchmark_data[symbol] = {
            "label": row.get("label") or symbol,
            "is_primary": bool(row.get("is_primary")),
            "values": [round(100.0 * float(v) / first, 4) if pd.notna(v) else None for v in base_series],
        }

    return {
        "dates": out_dates,
        "portfolio": portfolio_index,
        "benchmarks": benchmark_data,
        "base_currency": base_currency,
        "method": "transaction_reconstructed_nav_with_fx_initial_capital_base",
        "inception_date": inception,
    }



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


def _attribution_history(
    portfolio: Dict[str, Any],
    transactions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Transaction-aware arithmetic performance attribution in base currency.

    Instrument P&L equals the change in marked market value less net capital
    invested after the opening mark. With no external portfolio cash flows,
    component P&L reconciles to the change in portfolio NAV. Fees are attributed
    to the instrument that incurred them and FX effects are captured in base marks.
    """
    base_currency = str(portfolio.get("base_currency") or "GBP").upper()
    initial_capital = float(portfolio.get("initial_capital") or 0.0)
    inception_date = date.fromisoformat(str(portfolio.get("inception_date")))

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

    portfolio_symbols = sorted(symbol_meta)
    if not portfolio_symbols:
        return {"base_currency": base_currency, "method": "transaction_reconciled_pnl_attribution", "periods": {}}

    fx_symbols = [
        fx for fx in {
            _fx_symbol(meta["currency"], base_currency)
            for meta in symbol_meta.values()
        } if fx
    ]
    closes = _download_adjusted_close(
        list(dict.fromkeys(portfolio_symbols + fx_symbols)),
        inception_date.isoformat(),
    )

    missing = [symbol for symbol in portfolio_symbols if symbol not in closes.columns]
    if missing:
        raise HTTPException(
            status_code=502,
            detail=f"Historical prices are unavailable for attribution: {', '.join(missing)}.",
        )

    trading_dates = closes[portfolio_symbols].dropna(how="all").index
    if trading_dates.empty:
        raise HTTPException(status_code=502, detail="No portfolio trading dates are available for attribution.")

    base_prices: Dict[str, pd.Series] = {}
    for symbol in portfolio_symbols:
        local = closes[symbol].reindex(trading_dates).ffill()
        fx = _series_fx(closes, symbol_meta[symbol]["currency"], base_currency, trading_dates)
        base_prices[symbol] = local * fx

    ordered_transactions = sorted(
        transactions,
        key=lambda txn: (str(txn.get("trade_date") or ""), str(txn.get("id") or "")),
    )
    transaction_dates = [pd.Timestamp(txn["trade_date"]).date() for txn in ordered_transactions]
    quantities: Dict[str, float] = defaultdict(float)
    cash = initial_capital
    cursor = 0
    nav_by_ts: Dict[pd.Timestamp, float] = {}
    values_by_ts: Dict[pd.Timestamp, Dict[str, float]] = {}

    for raw_ts in trading_dates:
        ts = pd.Timestamp(raw_ts)
        day = ts.date()
        while cursor < len(ordered_transactions) and transaction_dates[cursor] <= day:
            txn = ordered_transactions[cursor]
            symbol = (txn.get("instruments") or {}).get("symbol")
            if symbol:
                qty = float(txn.get("quantity") or 0.0)
                price = float(txn.get("price") or 0.0)
                fees = float(txn.get("fees") or 0.0)
                fx = _trade_fx(txn, base_currency)
                if str(txn.get("side") or "").upper() == "BUY":
                    quantities[symbol] += qty
                    cash -= (qty * price + fees) * fx
                else:
                    quantities[symbol] -= qty
                    cash += (qty * price - fees) * fx
            cursor += 1

        values: Dict[str, float] = {}
        nav = cash
        for symbol, qty in quantities.items():
            if abs(qty) < 1e-12:
                continue
            marked = base_prices.get(symbol)
            if marked is None or pd.isna(marked.loc[ts]):
                continue
            value = qty * float(marked.loc[ts])
            values[symbol] = value
            nav += value
        values_by_ts[ts] = values
        nav_by_ts[ts] = float(nav)

    end_ts = pd.Timestamp(trading_dates[-1])
    end_day = end_ts.date()
    end_nav = nav_by_ts[end_ts]
    end_values = values_by_ts[end_ts]

    period_specs = {
        "1M": (end_ts - pd.DateOffset(months=1)).date(),
        "3M": (end_ts - pd.DateOffset(months=3)).date(),
        "YTD": date(end_day.year, 1, 1),
        "SI": inception_date,
    }
    period_labels = {"1M": "1 Month", "3M": "3 Months", "YTD": "Year to Date", "SI": "Since Inception"}
    periods: Dict[str, Any] = {}
    trading_ts = [pd.Timestamp(ts) for ts in trading_dates]

    for key, requested_start in period_specs.items():
        use_inception = key == "SI" or requested_start <= inception_date
        if use_inception:
            start_day = inception_date
            start_nav = initial_capital
            start_values: Dict[str, float] = {}
            window_ts = trading_ts
        else:
            eligible = [ts for ts in trading_ts if ts.date() >= requested_start]
            start_ts = eligible[0] if eligible else trading_ts[0]
            start_day = start_ts.date()
            start_nav = nav_by_ts[start_ts]
            start_values = values_by_ts[start_ts]
            window_ts = [ts for ts in trading_ts if ts >= start_ts]

        if not start_nav:
            continue

        net_invested: Dict[str, float] = defaultdict(float)
        for txn in ordered_transactions:
            txn_day = pd.Timestamp(txn["trade_date"]).date()
            include = txn_day <= end_day if use_inception else start_day < txn_day <= end_day
            if not include:
                continue
            symbol = (txn.get("instruments") or {}).get("symbol")
            if not symbol:
                continue
            qty = float(txn.get("quantity") or 0.0)
            price = float(txn.get("price") or 0.0)
            fees = float(txn.get("fees") or 0.0)
            fx = _trade_fx(txn, base_currency)
            if str(txn.get("side") or "").upper() == "BUY":
                net_invested[symbol] += (qty * price + fees) * fx
            else:
                net_invested[symbol] -= (qty * price - fees) * fx

        component_symbols = set(start_values) | set(end_values) | set(net_invested)
        components: List[Dict[str, Any]] = []
        for symbol in component_symbols:
            start_value = float(start_values.get(symbol, 0.0))
            end_value = float(end_values.get(symbol, 0.0))
            flow = float(net_invested.get(symbol, 0.0))
            pnl = end_value - start_value - flow

            weights = []
            for ts in window_ts:
                nav = nav_by_ts.get(ts, 0.0)
                if nav:
                    weights.append(values_by_ts.get(ts, {}).get(symbol, 0.0) / nav * 100.0)
            avg_weight = sum(weights) / len(weights) if weights else 0.0

            if abs(pnl) < 0.005 and abs(start_value) < 0.005 and abs(end_value) < 0.005 and abs(flow) < 0.005:
                continue
            meta = symbol_meta.get(symbol) or {"name": symbol, "currency": base_currency}
            components.append({
                "symbol": symbol,
                "name": meta.get("name") or symbol,
                "currency": meta.get("currency") or base_currency,
                "average_weight_pct": round(avg_weight, 2),
                "starting_value": round(start_value, 2),
                "ending_value": round(end_value, 2),
                "net_invested": round(flow, 2),
                "pnl": round(pnl, 2),
                "contribution_pp": round(pnl / start_nav * 100.0, 4),
                "ending_weight_pct": round((end_value / end_nav * 100.0) if end_nav else 0.0, 2),
            })

        components.sort(key=lambda row: abs(row["contribution_pp"]), reverse=True)
        total_pnl = end_nav - start_nav
        component_pnl = sum(float(row["pnl"]) for row in components)
        periods[key] = {
            "label": period_labels[key],
            "requested_start_date": requested_start.isoformat(),
            "start_date": start_day.isoformat(),
            "end_date": end_day.isoformat(),
            "starting_nav": round(start_nav, 2),
            "ending_nav": round(end_nav, 2),
            "total_pnl": round(total_pnl, 2),
            "portfolio_return_pct": round(total_pnl / start_nav * 100.0, 4),
            "component_contribution_pp": round(sum(float(row["contribution_pp"]) for row in components), 4),
            "reconciliation_error": round(total_pnl - component_pnl, 6),
            "components": components,
        }

    return {
        "base_currency": base_currency,
        "method": "transaction_reconciled_pnl_attribution",
        "methodology": "End value minus start value minus net capital invested; contribution is instrument P&L divided by starting portfolio NAV.",
        "periods": periods,
    }


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


class AdminTransactionPayload(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=160)
    asset_type: str = Field(default="equity", min_length=1, max_length=40)
    currency: str = Field(default="GBP", min_length=3, max_length=3)
    trade_date: date
    side: str
    quantity: float = Field(gt=0)
    price: float = Field(ge=0)
    fees: float = Field(default=0, ge=0)
    fx_rate_to_base: Optional[float] = Field(default=None, gt=0)
    note: Optional[str] = Field(default=None, max_length=1000)


class AdminJournalPayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1000)
    body: str = Field(min_length=1)
    category: str = Field(default="Investment Note", min_length=1, max_length=80)
    effective_date: date
    related_transaction_id: Optional[str] = None
    slug: Optional[str] = Field(default=None, max_length=200)
    is_published: bool = True


class AdminTransactionCorrectionPayload(BaseModel):
    quantity: float = Field(gt=0)
    price: float = Field(ge=0)
    fees: float = Field(default=0, ge=0)
    fx_rate_to_base: Optional[float] = Field(default=None, gt=0)
    reason: str = Field(min_length=3, max_length=500)


class AdminActiveAllocationPayload(BaseModel):
    trade_date: date
    funding_symbol: str = Field(min_length=1, max_length=32)
    funding_quantity: float = Field(gt=0)
    funding_price: float = Field(ge=0)
    funding_fees: float = Field(default=0, ge=0)
    funding_fx_rate_to_base: Optional[float] = Field(default=None, gt=0)
    target_symbol: str = Field(min_length=1, max_length=32)
    target_name: str = Field(min_length=1, max_length=160)
    target_asset_type: str = Field(default="equity", min_length=1, max_length=40)
    target_currency: str = Field(default="GBP", min_length=3, max_length=3)
    target_quantity: float = Field(gt=0)
    target_price: float = Field(ge=0)
    target_fees: float = Field(default=0, ge=0)
    target_fx_rate_to_base: Optional[float] = Field(default=None, gt=0)
    reason: str = Field(min_length=3, max_length=1000)


class AdminAllocationCorrectionPayload(BaseModel):
    corrected_trade_date: date
    funding_quantity: float = Field(gt=0)
    funding_price: float = Field(ge=0)
    funding_fees: float = Field(default=0, ge=0)
    funding_fx_rate_to_base: Optional[float] = Field(default=None, gt=0)
    target_quantity: float = Field(gt=0)
    target_price: float = Field(ge=0)
    target_fees: float = Field(default=0, ge=0)
    target_fx_rate_to_base: Optional[float] = Field(default=None, gt=0)
    reason: str = Field(min_length=3, max_length=1000)


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:180] or "journal-entry"


def _find_instrument(symbol: str) -> Optional[Dict[str, Any]]:
    rows = _supabase_get("instruments", {"select": "id,symbol,name,asset_type,currency", "symbol": f"eq.{symbol}", "limit": "1"})
    return rows[0] if rows else None


def _cash_balance(portfolio: Dict[str, Any], transactions: List[Dict[str, Any]]) -> float:
    base_currency = str(portfolio.get("base_currency") or "GBP").upper()
    cash = float(portfolio.get("initial_capital") or 0.0)
    for txn in transactions:
        qty = float(txn.get("quantity") or 0.0)
        price = float(txn.get("price") or 0.0)
        fees = float(txn.get("fees") or 0.0)
        fx = _trade_fx(txn, base_currency)
        if str(txn.get("side", "")).upper() == "BUY":
            cash -= (qty * price + fees) * fx
        else:
            cash += (qty * price - fees) * fx
    return cash


_ALLOCATION_NOTE_RE = re.compile(r"^ALLOCATION ([a-f0-9]{12}) (FUNDING|TARGET):\s*(.*)$", re.IGNORECASE)
_ALLOCATION_REVERSAL_RE = re.compile(r"^ALLOCATION-REVERSAL ([a-f0-9]{12}) ORIGINAL ([a-f0-9]{12}) (FUNDING|TARGET):\s*(.*)$", re.IGNORECASE)
_ALLOCATION_CORRECTS_RE = re.compile(r"^\[CORRECTS ([a-f0-9]{12})\]\s*(.*)$", re.IGNORECASE)


def _allocation_note(txn: Dict[str, Any]) -> Optional[Dict[str, str]]:
    match = _ALLOCATION_NOTE_RE.match(str(txn.get("note") or ""))
    if not match:
        return None
    return {"decision_id": match.group(1).lower(), "leg": match.group(2).upper(), "reason": match.group(3).strip()}


def _allocation_reversal_note(txn: Dict[str, Any]) -> Optional[Dict[str, str]]:
    match = _ALLOCATION_REVERSAL_RE.match(str(txn.get("note") or ""))
    if not match:
        return None
    return {
        "correction_id": match.group(1).lower(),
        "original_decision_id": match.group(2).lower(),
        "leg": match.group(3).upper(),
        "reason": match.group(4).strip(),
    }


def _effective_transactions(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Project the immutable audit ledger into the economic/public transaction history.

    Allocation corrections keep the original and reversal rows privately, while this
    projection removes the superseded allocation and its reversal rows and retains
    only the corrected replacement allocation.
    """
    superseded = {
        meta["original_decision_id"]
        for txn in transactions
        if (meta := _allocation_reversal_note(txn)) is not None
    }
    projected: List[tuple[str, int, Dict[str, Any]]] = []
    for index, txn in enumerate(transactions):
        if _allocation_reversal_note(txn):
            continue
        allocation = _allocation_note(txn)
        if allocation and allocation["decision_id"] in superseded:
            continue
        row = dict(txn)
        if allocation:
            reason = allocation["reason"]
            corrected = _ALLOCATION_CORRECTS_RE.match(reason)
            if corrected:
                reason = corrected.group(2).strip()
                row["note"] = f"ALLOCATION {allocation['decision_id']} {allocation['leg']}: {reason}"
        projected.append((str(row.get("trade_date") or ""), index, row))
    projected.sort(key=lambda item: (item[0], item[1]))
    return [row for _, _, row in projected]


def _find_allocation(transactions: List[Dict[str, Any]], decision_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    decision_id = decision_id.strip().lower()
    if not re.fullmatch(r"[a-f0-9]{12}", decision_id):
        raise HTTPException(status_code=400, detail="Invalid allocation decision ID.")
    if any(
        meta and meta["original_decision_id"] == decision_id
        for meta in (_allocation_reversal_note(txn) for txn in transactions)
    ):
        raise HTTPException(status_code=409, detail="This allocation has already been superseded by an allocation correction.")
    legs: Dict[str, Dict[str, Any]] = {}
    for txn in transactions:
        meta = _allocation_note(txn)
        if meta and meta["decision_id"] == decision_id:
            if meta["leg"] in legs:
                raise HTTPException(status_code=409, detail=f"Allocation {decision_id} has duplicate {meta['leg']} rows.")
            legs[meta["leg"]] = txn
    if set(legs) != {"FUNDING", "TARGET"}:
        raise HTTPException(status_code=404, detail="A complete paired allocation was not found for this decision ID.")
    funding, target = legs["FUNDING"], legs["TARGET"]
    if str(funding.get("side") or "").upper() != "SELL" or str(target.get("side") or "").upper() != "BUY":
        raise HTTPException(status_code=409, detail="Allocation legs do not have the expected SELL funding / BUY target structure.")
    if funding.get("trade_date") != target.get("trade_date"):
        raise HTTPException(status_code=409, detail="Allocation legs do not share the same original trade date.")
    return funding, target


def _validate_effective_ledger(portfolio: Dict[str, Any], raw_transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    effective = _effective_transactions(raw_transactions)
    base_currency = str(portfolio.get("base_currency") or "GBP").upper()
    _derive_book(effective, base_currency)
    cash = float(portfolio.get("initial_capital") or 0.0)
    minimum_cash = cash
    for txn in effective:
        qty = float(txn.get("quantity") or 0.0)
        price = float(txn.get("price") or 0.0)
        fees = float(txn.get("fees") or 0.0)
        fx = _trade_fx(txn, base_currency)
        if str(txn.get("side") or "").upper() == "BUY":
            cash -= (qty * price + fees) * fx
        else:
            cash += (qty * price - fees) * fx
        minimum_cash = min(minimum_cash, cash)
        if cash < -0.01:
            raise HTTPException(status_code=400, detail=f"Allocation correction would create negative historical cash ({cash:.2f} {base_currency}).")
    return {"effective_transactions": effective, "ending_cash": cash, "minimum_cash": minimum_cash, "base_currency": base_currency}


def _build_allocation_correction(
    portfolio: Dict[str, Any],
    raw_transactions: List[Dict[str, Any]],
    decision_id: str,
    payload: AdminAllocationCorrectionPayload,
    correction_id: str,
) -> Dict[str, Any]:
    funding, target = _find_allocation(raw_transactions, decision_id)
    original_date = date.fromisoformat(str(funding["trade_date"]))
    inception_date = date.fromisoformat(str(portfolio.get("inception_date")))
    if payload.corrected_trade_date < inception_date:
        raise HTTPException(status_code=400, detail="Corrected trade date cannot precede portfolio inception.")
    if payload.corrected_trade_date > date.today():
        raise HTTPException(status_code=400, detail="Corrected trade date cannot be in the future.")
    if payload.corrected_trade_date == original_date:
        raise HTTPException(status_code=400, detail="Corrected trade date must differ from the original allocation date.")

    base_currency = str(portfolio.get("base_currency") or "GBP").upper()
    funding_instrument = funding.get("instruments") or {}
    target_instrument = target.get("instruments") or {}
    if not funding_instrument.get("id") or not target_instrument.get("id"):
        raise HTTPException(status_code=500, detail="Allocation instruments are unavailable.")

    funding_currency = str(funding_instrument.get("currency") or funding.get("currency") or base_currency).upper()
    funding_fx = 1.0 if funding_currency == base_currency else payload.funding_fx_rate_to_base
    if funding_fx is None:
        raise HTTPException(status_code=400, detail=f"funding_fx_rate_to_base is required for {funding_currency} corrected sales in a {base_currency} portfolio.")
    target_currency = str(target_instrument.get("currency") or target.get("currency") or base_currency).upper()
    target_fx = 1.0 if target_currency == base_currency else payload.target_fx_rate_to_base
    if target_fx is None:
        raise HTTPException(status_code=400, detail=f"target_fx_rate_to_base is required for {target_currency} corrected purchases.")

    reason = payload.reason.strip()
    funding_meta = _allocation_note(funding) or {}
    target_meta = _allocation_note(target) or {}
    funding_public_reason = str(funding_meta.get("reason") or "").strip() or reason
    target_public_reason = str(target_meta.get("reason") or "").strip() or reason
    decision_id = decision_id.lower()
    reversal_funding = {
        "portfolio_id": portfolio["id"], "instrument_id": funding_instrument["id"],
        "trade_date": funding["trade_date"], "side": "BUY",
        "quantity": float(funding.get("quantity") or 0.0), "price": float(funding.get("price") or 0.0),
        "fees": float(funding.get("fees") or 0.0), "currency": funding_currency,
        "fx_rate_to_base": float(funding.get("fx_rate_to_base") or 1.0),
        "note": f"ALLOCATION-REVERSAL {correction_id} ORIGINAL {decision_id} FUNDING: {reason}",
    }
    reversal_target = {
        "portfolio_id": portfolio["id"], "instrument_id": target_instrument["id"],
        "trade_date": target["trade_date"], "side": "SELL",
        "quantity": float(target.get("quantity") or 0.0), "price": float(target.get("price") or 0.0),
        "fees": float(target.get("fees") or 0.0), "currency": target_currency,
        "fx_rate_to_base": float(target.get("fx_rate_to_base") or 1.0),
        "note": f"ALLOCATION-REVERSAL {correction_id} ORIGINAL {decision_id} TARGET: {reason}",
    }
    corrected_funding = {
        "portfolio_id": portfolio["id"], "instrument_id": funding_instrument["id"],
        "trade_date": payload.corrected_trade_date.isoformat(), "side": "SELL",
        "quantity": payload.funding_quantity, "price": payload.funding_price,
        "fees": payload.funding_fees, "currency": funding_currency, "fx_rate_to_base": float(funding_fx),
        "note": f"ALLOCATION {correction_id} FUNDING: [CORRECTS {decision_id}] {funding_public_reason}",
    }
    corrected_target = {
        "portfolio_id": portfolio["id"], "instrument_id": target_instrument["id"],
        "trade_date": payload.corrected_trade_date.isoformat(), "side": "BUY",
        "quantity": payload.target_quantity, "price": payload.target_price,
        "fees": payload.target_fees, "currency": target_currency, "fx_rate_to_base": float(target_fx),
        "note": f"ALLOCATION {correction_id} TARGET: [CORRECTS {decision_id}] {target_public_reason}",
    }

    validation_rows = []
    for row, instrument in [
        (reversal_funding, funding_instrument), (reversal_target, target_instrument),
        (corrected_funding, funding_instrument), (corrected_target, target_instrument),
    ]:
        cloned = dict(row)
        cloned["instruments"] = instrument
        validation_rows.append(cloned)
    candidate = list(raw_transactions) + validation_rows
    validation = _validate_effective_ledger(portfolio, candidate)

    funding_proceeds = (payload.funding_quantity * payload.funding_price - payload.funding_fees) * float(funding_fx)
    target_cost_base = (payload.target_quantity * payload.target_price + payload.target_fees) * float(target_fx)
    return {
        "correction_id": correction_id,
        "original_decision_id": decision_id,
        "original_trade_date": original_date.isoformat(),
        "corrected_trade_date": payload.corrected_trade_date.isoformat(),
        "funding_symbol": funding_instrument.get("symbol"),
        "target_symbol": target_instrument.get("symbol"),
        "funding_proceeds_base": funding_proceeds,
        "target_cost_base": target_cost_base,
        "net_cash_impact": funding_proceeds - target_cost_base,
        "public_reason": target_public_reason,
        "rows": [reversal_funding, reversal_target, corrected_funding, corrected_target],
        "validation": {"ending_cash": validation["ending_cash"], "minimum_cash": validation["minimum_cash"], "base_currency": base_currency},
    }


@router.get("/admin/{slug}/instrument-lookup")
def admin_instrument_lookup(
    slug: str,
    symbol: str = Query(..., min_length=1, max_length=32),
    trade_date: date = Query(...),
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    resolved_symbol = symbol.strip().upper()
    if trade_date > date.today():
        raise HTTPException(status_code=400, detail="Trade date cannot be in the future.")

    existing = _find_instrument(resolved_symbol)
    ticker = yf.Ticker(resolved_symbol)
    start = trade_date - timedelta(days=10)
    end = trade_date + timedelta(days=1)
    try:
        history = ticker.history(start=start.isoformat(), end=end.isoformat(), auto_adjust=False)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Market data lookup failed for {resolved_symbol}: {exc}") from exc
    if history is None or history.empty or "Close" not in history:
        raise HTTPException(status_code=404, detail=f"Ticker {resolved_symbol} could not be verified from market data.")

    closes = history["Close"].dropna()
    if closes.empty:
        raise HTTPException(status_code=404, detail=f"Ticker {resolved_symbol} has no usable close around {trade_date.isoformat()}.")
    eligible = closes[closes.index.date <= trade_date]
    if eligible.empty:
        raise HTTPException(status_code=404, detail=f"No market close is available on or before {trade_date.isoformat()} for {resolved_symbol}.")
    price_ts = eligible.index[-1]
    raw_price = float(eligible.iloc[-1])

    info: Dict[str, Any] = {}
    try:
        info = ticker.get_info() or {}
    except Exception:
        info = {}
    raw_currency = str(info.get("currency") or (existing or {}).get("currency") or portfolio.get("base_currency") or "GBP")
    currency_upper = raw_currency.upper()
    price_scale = 0.01 if currency_upper in {"GBP", "GBX"} and raw_currency != "GBP" else 1.0
    # Yahoo commonly reports London equities in GBp/GBX (pence). Store portfolio transaction prices in GBP.
    if raw_currency in {"GBp", "GBX", "GBX"}:
        normalized_currency = "GBP"
        price_scale = 0.01
    else:
        normalized_currency = currency_upper
        price_scale = 1.0
    price = raw_price * price_scale

    name = (existing or {}).get("name") or info.get("longName") or info.get("shortName") or resolved_symbol
    exchange = info.get("fullExchangeName") or info.get("exchange") or None
    quote_type = str(info.get("quoteType") or (existing or {}).get("asset_type") or "equity").lower()

    transactions = _effective_transactions(_transactions(portfolio["id"]))
    base_currency = str(portfolio.get("base_currency") or "GBP").upper()
    book = _derive_book(transactions, base_currency)
    held_quantity = float((book.get(resolved_symbol) or {}).get("quantity") or 0.0)

    fx_rate_to_base = 1.0
    fx_price_date = trade_date
    fx_used_previous_session = False
    fx_source_symbol = None
    fx_source = 'Identity'
    if normalized_currency != base_currency:
        fx_rate_to_base, fx_price_date, fx_source = _historical_fx_reference(
            normalized_currency, base_currency, trade_date
        )
        fx_used_previous_session = fx_price_date != trade_date
        fx_source_symbol = f'{normalized_currency}/{base_currency}'


    return {
        "verified": True,
        "symbol": resolved_symbol,
        "name": name,
        "exchange": exchange,
        "asset_type": quote_type,
        "currency": normalized_currency,
        "raw_currency": raw_currency,
        "reference_close": round(price, 6),
        "raw_reference_close": round(raw_price, 6),
        "requested_date": trade_date.isoformat(),
        "price_date": price_ts.date().isoformat(),
        "used_previous_session": price_ts.date() != trade_date,
        "price_scale_applied": price_scale,
        "held_quantity": held_quantity,
        "fx_rate_to_base": round(fx_rate_to_base, 8),
        "fx_base_currency": base_currency,
        "fx_price_date": fx_price_date.isoformat(),
        "fx_used_previous_session": fx_used_previous_session,
        "fx_source_symbol": fx_source_symbol,
        "fx_source": fx_source,
        "source": "Yahoo Finance via yfinance",
    }


@router.get("/admin/health")
def admin_health(x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret")) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    return {
        "authenticated": True,
        "writes_configured": bool(SUPABASE_URL and SUPABASE_SERVICE_KEY),
        "admin_ledger": True,
        "allocation_corrections": True,
    }


@router.post("/admin/{slug}/transaction")
def create_transaction(slug: str, payload: AdminTransactionPayload, x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret")) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    base_currency = str(portfolio.get("base_currency") or "GBP").upper()
    symbol = payload.symbol.strip().upper()
    currency = payload.currency.strip().upper()
    side = payload.side.strip().upper()
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="side must be BUY or SELL.")
    if currency != base_currency and payload.fx_rate_to_base is None:
        raise HTTPException(status_code=400, detail=f"fx_rate_to_base is required for {currency} trades in a {base_currency} portfolio.")

    instrument = _find_instrument(symbol)
    if not instrument:
        instrument = _supabase_post("instruments", {
            "symbol": symbol,
            "name": payload.name.strip(),
            "asset_type": payload.asset_type.strip().lower(),
            "currency": currency,
        })
    elif str(instrument.get("currency") or "").upper() != currency:
        raise HTTPException(status_code=400, detail=f"{symbol} already exists with currency {instrument.get('currency')}.")

    transactions = _effective_transactions(_transactions(portfolio["id"]))
    fx = 1.0 if currency == base_currency else float(payload.fx_rate_to_base)
    if side == "BUY":
        required_cash = (payload.quantity * payload.price + payload.fees) * fx
        available_cash = _cash_balance(portfolio, transactions)
        if required_cash > available_cash + 0.01:
            raise HTTPException(status_code=400, detail=f"Insufficient cash. Required {required_cash:.2f} {base_currency}; available {available_cash:.2f} {base_currency}.")
    else:
        book = _derive_book(transactions, base_currency)
        held = float((book.get(symbol) or {}).get("quantity") or 0.0)
        if payload.quantity > held + 1e-9:
            raise HTTPException(status_code=400, detail=f"Cannot sell {payload.quantity:g} {symbol}; only {held:g} held.")

    row = _supabase_post("portfolio_transactions", {
        "portfolio_id": portfolio["id"],
        "instrument_id": instrument["id"],
        "trade_date": payload.trade_date.isoformat(),
        "side": side,
        "quantity": payload.quantity,
        "price": payload.price,
        "fees": payload.fees,
        "currency": currency,
        "fx_rate_to_base": fx,
        "note": payload.note,
    })
    return {"transaction": row}


@router.post("/admin/{slug}/active-allocation")
def create_active_allocation(
    slug: str,
    payload: AdminActiveAllocationPayload,
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    base_currency = str(portfolio.get("base_currency") or "GBP").upper()
    transactions = _effective_transactions(_transactions(portfolio["id"]))

    funding_symbol = payload.funding_symbol.strip().upper()
    target_symbol = payload.target_symbol.strip().upper()
    if funding_symbol == target_symbol:
        raise HTTPException(status_code=400, detail="Funding and target symbols must be different for an active allocation.")

    funding_instrument = _find_instrument(funding_symbol)
    if not funding_instrument:
        raise HTTPException(status_code=404, detail=f"Funding instrument {funding_symbol} was not found.")
    funding_currency = str(funding_instrument.get("currency") or base_currency).upper()
    funding_fx = 1.0 if funding_currency == base_currency else payload.funding_fx_rate_to_base
    if funding_fx is None:
        raise HTTPException(status_code=400, detail=f"funding_fx_rate_to_base is required for {funding_currency} sales in a {base_currency} portfolio.")

    target_currency = payload.target_currency.strip().upper()
    target_fx = 1.0 if target_currency == base_currency else payload.target_fx_rate_to_base
    if target_fx is None:
        raise HTTPException(status_code=400, detail=f"target_fx_rate_to_base is required for {target_currency} purchases in a {base_currency} portfolio.")

    existing_target = _find_instrument(target_symbol)
    if existing_target and str(existing_target.get("currency") or "").upper() != target_currency:
        raise HTTPException(status_code=400, detail=f"{target_symbol} already exists with currency {existing_target.get('currency')}.")

    book = _derive_book(transactions, base_currency)
    held = float((book.get(funding_symbol) or {}).get("quantity") or 0.0)
    if payload.funding_quantity > held + 1e-9:
        raise HTTPException(status_code=400, detail=f"Cannot sell {payload.funding_quantity:g} {funding_symbol}; only {held:g} held.")

    decision_id = uuid4().hex[:12]
    reason = payload.reason.strip()
    target_for_validation = existing_target or {
        "id": "pending-target",
        "symbol": target_symbol,
        "name": payload.target_name.strip(),
        "asset_type": payload.target_asset_type.strip().lower(),
        "currency": target_currency,
    }
    funding_row = {
        "portfolio_id": portfolio["id"],
        "instrument_id": funding_instrument["id"],
        "trade_date": payload.trade_date.isoformat(),
        "side": "SELL",
        "quantity": payload.funding_quantity,
        "price": payload.funding_price,
        "fees": payload.funding_fees,
        "currency": funding_currency,
        "fx_rate_to_base": funding_fx,
        "note": f"ALLOCATION {decision_id} FUNDING: {reason}",
    }
    target_row = {
        "portfolio_id": portfolio["id"],
        "instrument_id": target_for_validation["id"],
        "trade_date": payload.trade_date.isoformat(),
        "side": "BUY",
        "quantity": payload.target_quantity,
        "price": payload.target_price,
        "fees": payload.target_fees,
        "currency": target_currency,
        "fx_rate_to_base": float(target_fx),
        "note": f"ALLOCATION {decision_id} TARGET: {reason}",
    }

    candidate = list(transactions)
    funding_validation = dict(funding_row)
    target_validation = dict(target_row)
    funding_validation["instruments"] = funding_instrument
    target_validation["instruments"] = target_for_validation
    trade_date = payload.trade_date.isoformat()
    insert_at = max([i for i, txn in enumerate(candidate) if txn.get("trade_date") <= trade_date], default=-1) + 1
    candidate[insert_at:insert_at] = [funding_validation, target_validation]
    _derive_book(candidate, base_currency)

    cash = float(portfolio.get("initial_capital") or 0.0)
    for txn in candidate:
        qty = float(txn.get("quantity") or 0.0)
        price = float(txn.get("price") or 0.0)
        fees = float(txn.get("fees") or 0.0)
        fx = _trade_fx(txn, base_currency)
        if str(txn.get("side") or "").upper() == "BUY":
            cash -= (qty * price + fees) * fx
        else:
            cash += (qty * price - fees) * fx
        if cash < -0.01:
            raise HTTPException(status_code=400, detail=f"Active allocation would create negative historical cash ({cash:.2f} {base_currency}).")

    target_instrument = existing_target
    if not target_instrument:
        target_instrument = _supabase_post("instruments", {
            "symbol": target_symbol,
            "name": payload.target_name.strip(),
            "asset_type": payload.target_asset_type.strip().lower(),
            "currency": target_currency,
        })
    target_row["instrument_id"] = target_instrument["id"]

    rows = _supabase_post_many("portfolio_transactions", [funding_row, target_row])
    if len(rows) != 2:
        raise HTTPException(status_code=502, detail="Active allocation did not return both transaction rows.")
    return {
        "decision_id": decision_id,
        "funding_transaction": rows[0],
        "target_transaction": rows[1],
        "funding_currency": funding_currency,
        "funding_fx_rate_to_base": float(funding_fx),
        "target_currency": target_currency,
        "target_fx_rate_to_base": float(target_fx),
        "base_currency": base_currency,
    }


@router.get("/admin/{slug}/ledger")
@router.get("/admin/{slug}/transactions")
def get_admin_transactions(slug: str, x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret")) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    raw = _transactions(portfolio["id"])
    return {"transactions": raw, "effective_transactions": _effective_transactions(raw)}


@router.post("/admin/{slug}/allocation/{decision_id}/correct/preview")
def preview_allocation_correction(
    slug: str,
    decision_id: str,
    payload: AdminAllocationCorrectionPayload,
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    raw = _transactions(portfolio["id"])
    preview_id = uuid4().hex[:12]
    preview = _build_allocation_correction(portfolio, raw, decision_id, payload, preview_id)
    preview.pop("rows", None)
    preview["preview_only"] = True
    return preview


@router.post("/admin/{slug}/allocation/{decision_id}/correct")
def correct_allocation(
    slug: str,
    decision_id: str,
    payload: AdminAllocationCorrectionPayload,
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    raw = _transactions(portfolio["id"])
    correction_id = uuid4().hex[:12]
    correction = _build_allocation_correction(portfolio, raw, decision_id, payload, correction_id)
    rows = _supabase_post_many("portfolio_transactions", correction["rows"])
    if len(rows) != 4:
        raise HTTPException(status_code=502, detail="Allocation correction did not return all four audit rows.")
    effective = _effective_transactions(_transactions(portfolio["id"]))
    corrected_target = next((txn for txn in effective if (_allocation_note(txn) or {}).get("decision_id") == correction_id and (_allocation_note(txn) or {}).get("leg") == "TARGET"), None)
    corrected_funding = next((txn for txn in effective if (_allocation_note(txn) or {}).get("decision_id") == correction_id and (_allocation_note(txn) or {}).get("leg") == "FUNDING"), None)
    return {
        "correction_id": correction_id,
        "original_decision_id": decision_id.lower(),
        "audit_rows": rows,
        "funding_transaction": corrected_funding,
        "target_transaction": corrected_target,
        "corrected_trade_date": payload.corrected_trade_date.isoformat(),
    }


@router.get("/admin/{slug}/journal")
def get_admin_journal(slug: str, x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret")) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    rows = _supabase_get(
        "portfolio_journal_entries",
        {
            "select": "id,slug,title,summary,body,category,effective_date,published_at,related_transaction_id,is_published,created_at,updated_at",
            "portfolio_id": f"eq.{portfolio['id']}",
            "order": "effective_date.desc,created_at.desc",
            "limit": "100",
        },
        admin=True,
    )
    return {"journal": rows}


@router.post("/admin/{slug}/transaction/{transaction_id}/correct")
def correct_transaction(
    slug: str,
    transaction_id: str,
    payload: AdminTransactionCorrectionPayload,
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    transactions = _transactions(portfolio["id"])
    original = next((txn for txn in transactions if txn.get("id") == transaction_id), None)
    if not original:
        raise HTTPException(status_code=404, detail="Transaction not found in this portfolio.")

    instrument = original.get("instruments") or {}
    if not instrument.get("id") or not instrument.get("symbol"):
        raise HTTPException(status_code=500, detail="Original transaction instrument is unavailable.")
    base_currency = str(portfolio.get("base_currency") or "GBP").upper()
    currency = str(instrument.get("currency") or original.get("currency") or base_currency).upper()
    fx = 1.0 if currency == base_currency else payload.fx_rate_to_base
    if fx is None:
        raise HTTPException(status_code=400, detail=f"fx_rate_to_base is required for {currency} corrections in a {base_currency} portfolio.")

    original_side = str(original.get("side") or "").upper()
    reverse_side = "SELL" if original_side == "BUY" else "BUY"
    reason = payload.reason.strip()
    reversal = {
        "portfolio_id": portfolio["id"],
        "instrument_id": instrument["id"],
        "trade_date": original["trade_date"],
        "side": reverse_side,
        "quantity": float(original.get("quantity") or 0),
        "price": float(original.get("price") or 0),
        "fees": float(original.get("fees") or 0),
        "currency": currency,
        "fx_rate_to_base": float(original.get("fx_rate_to_base") or 1.0),
        "note": f"REVERSAL of {transaction_id}: {reason}",
    }
    replacement = {
        "portfolio_id": portfolio["id"],
        "instrument_id": instrument["id"],
        "trade_date": original["trade_date"],
        "side": original_side,
        "quantity": payload.quantity,
        "price": payload.price,
        "fees": payload.fees,
        "currency": currency,
        "fx_rate_to_base": float(fx),
        "note": f"CORRECTION of {transaction_id}: {reason}",
    }

    # Validate the reconstructed ledger before writing either row. New rows have
    # the same trade date and are appended after the original for that date.
    candidate = list(transactions)
    reversal_for_validation = dict(reversal)
    replacement_for_validation = dict(replacement)
    reversal_for_validation["instruments"] = instrument
    replacement_for_validation["instruments"] = instrument
    insert_at = max(i for i, txn in enumerate(candidate) if txn.get("trade_date") <= original["trade_date"]) + 1
    candidate[insert_at:insert_at] = [reversal_for_validation, replacement_for_validation]
    _derive_book(candidate, base_currency)
    cash = float(portfolio.get("initial_capital") or 0.0)
    for txn in candidate:
        qty = float(txn.get("quantity") or 0.0)
        price = float(txn.get("price") or 0.0)
        fees = float(txn.get("fees") or 0.0)
        txn_fx = _trade_fx(txn, base_currency)
        if str(txn.get("side") or "").upper() == "BUY":
            cash -= (qty * price + fees) * txn_fx
        else:
            cash += (qty * price - fees) * txn_fx
        if cash < -0.01:
            raise HTTPException(status_code=400, detail=f"Correction would create negative historical cash ({cash:.2f} {base_currency}).")

    rows = _supabase_post_many("portfolio_transactions", [reversal, replacement])
    return {"correction": rows, "original_transaction_id": transaction_id}


@router.patch("/admin/{slug}/journal/{journal_id}")
def update_journal_entry(
    slug: str,
    journal_id: str,
    payload: AdminJournalPayload,
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    existing = _supabase_get(
        "portfolio_journal_entries",
        {"select": "id,published_at", "id": f"eq.{journal_id}", "portfolio_id": f"eq.{portfolio['id']}", "limit": "1"},
        admin=True,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Journal entry not found in this portfolio.")
    if payload.related_transaction_id and not any(txn.get("id") == payload.related_transaction_id for txn in _transactions(portfolio["id"])):
        raise HTTPException(status_code=400, detail="related_transaction_id does not belong to this portfolio.")
    published_at = existing[0].get("published_at")
    if payload.is_published and not published_at:
        published_at = datetime.utcnow().isoformat() + "Z"
    if not payload.is_published:
        published_at = None
    row = _supabase_patch(
        "portfolio_journal_entries",
        {"id": f"eq.{journal_id}", "portfolio_id": f"eq.{portfolio['id']}"},
        {
            "slug": _slugify(payload.slug or payload.title),
            "title": payload.title.strip(),
            "summary": payload.summary.strip(),
            "body": payload.body.strip(),
            "category": payload.category.strip(),
            "effective_date": payload.effective_date.isoformat(),
            "published_at": published_at,
            "related_transaction_id": payload.related_transaction_id,
            "is_published": payload.is_published,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        },
    )
    return {"journal": row}


@router.delete("/admin/{slug}/journal/{journal_id}")
def delete_journal_entry(
    slug: str,
    journal_id: str,
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    existing = _supabase_get(
        "portfolio_journal_entries",
        {"select": "id,title", "id": f"eq.{journal_id}", "portfolio_id": f"eq.{portfolio['id']}", "limit": "1"},
        admin=True,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Journal entry not found in this portfolio.")
    _supabase_delete("portfolio_journal_entries", {"id": f"eq.{journal_id}", "portfolio_id": f"eq.{portfolio['id']}"})
    return {"deleted": True, "journal_id": journal_id, "title": existing[0].get("title")}


@router.post("/admin/{slug}/journal")
def create_journal_entry(slug: str, payload: AdminJournalPayload, x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret")) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    if payload.related_transaction_id:
        matches = [txn for txn in _transactions(portfolio["id"]) if txn.get("id") == payload.related_transaction_id]
        if not matches:
            raise HTTPException(status_code=400, detail="related_transaction_id does not belong to this portfolio.")
    row = _supabase_post("portfolio_journal_entries", {
        "portfolio_id": portfolio["id"],
        "slug": _slugify(payload.slug or payload.title),
        "title": payload.title.strip(),
        "summary": payload.summary.strip(),
        "body": payload.body.strip(),
        "category": payload.category.strip(),
        "effective_date": payload.effective_date.isoformat(),
        "published_at": datetime.utcnow().isoformat() + "Z" if payload.is_published else None,
        "related_transaction_id": payload.related_transaction_id,
        "is_published": payload.is_published,
    })
    return {"journal": row}


@router.get("")
def list_public_portfolios() -> Dict[str, Any]:
    rows = _supabase_get(
        "portfolios",
        {
            "select": "slug,name,description,inception_date,base_currency",
            "is_public": "eq.true",
            "order": "created_at.asc",
        },
    )
    return {"portfolios": rows}


@router.get("/{slug}")
def get_portfolio(slug: str) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    transactions = _effective_transactions(_transactions(portfolio["id"]))
    return {
        "portfolio": {k: v for k, v in portfolio.items() if k != "id"},
        "snapshot": _holdings_snapshot(portfolio, transactions),
        "benchmarks": _benchmarks(portfolio["id"]),
    }


@router.get("/{slug}/transactions")
def get_transactions(slug: str) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    return {"transactions": _effective_transactions(_transactions(portfolio["id"]))}


@router.get("/{slug}/journal")
def get_journal(slug: str, limit: int = Query(default=20, ge=1, le=100)) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    return {"journal": _journal(portfolio["id"], limit)}


@router.get("/{slug}/performance")
def get_performance(slug: str) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    transactions = _effective_transactions(_transactions(portfolio["id"]))
    benchmarks = _benchmarks(portfolio["id"])
    return _performance_history(portfolio, transactions, benchmarks)


@router.get("/{slug}/attribution")
def get_attribution(slug: str) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    transactions = _effective_transactions(_transactions(portfolio["id"]))
    return _attribution_history(portfolio, transactions)

@router.get("/{slug}/exposures")
def get_exposures(slug: str) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    transactions = _effective_transactions(_transactions(portfolio["id"]))
    return _portfolio_exposure_map(portfolio, transactions)


@router.get("/{slug}/fx-analytics")
def get_fx_analytics(slug: str) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    transactions = _effective_transactions(_transactions(portfolio["id"]))
    return _portfolio_fx_analytics(portfolio, transactions)


@router.get("/{slug}/fx-analytics")
def get_fx_analytics(slug: str) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    transactions = _effective_transactions(_transactions(portfolio["id"]))
    return _portfolio_fx_analytics(portfolio, transactions)


@router.get("/{slug}/risk-analytics")
def get_risk_analytics(slug: str) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    transactions = _effective_transactions(_transactions(portfolio["id"]))
    return _portfolio_risk_analytics(portfolio, transactions)

