from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List

import pandas as pd
import requests
import yfinance as yf
from fastapi import APIRouter, HTTPException, Query


router = APIRouter(prefix="/api/portfolio", tags=["SethiPortfolio"])

SUPABASE_URL = ""
SUPABASE_KEY = ""


def configure_supabase(url: str, key: str) -> None:
    global SUPABASE_URL, SUPABASE_KEY
    SUPABASE_URL = (url or "").replace("/rest/v1", "").rstrip("/")
    SUPABASE_KEY = key or ""


def _headers() -> Dict[str, str]:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=503, detail="SethiPortfolio database is not configured yet.")
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def _supabase_get(table: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=_headers(),
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
    transactions = _transactions(portfolio["id"])
    return {
        "portfolio": {k: v for k, v in portfolio.items() if k != "id"},
        "snapshot": _holdings_snapshot(portfolio, transactions),
        "benchmarks": _benchmarks(portfolio["id"]),
    }


@router.get("/{slug}/transactions")
def get_transactions(slug: str) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    return {"transactions": _transactions(portfolio["id"])}


@router.get("/{slug}/journal")
def get_journal(slug: str, limit: int = Query(default=20, ge=1, le=100)) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    return {"journal": _journal(portfolio["id"], limit)}


@router.get("/{slug}/performance")
def get_performance(slug: str) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    transactions = _transactions(portfolio["id"])
    benchmarks = _benchmarks(portfolio["id"])
    return _performance_history(portfolio, transactions, benchmarks)
