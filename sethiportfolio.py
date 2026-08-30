from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from fastapi import APIRouter, HTTPException, Query


router = APIRouter(prefix="/api/portfolio", tags=["SethiPortfolio"])


# Supabase settings are injected by app.py so SethiPortfolio can reuse the
# existing Render/Supabase configuration without duplicating secrets.
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
            "select": "id,trade_date,side,quantity,price,fees,currency,note,instruments(id,symbol,name,asset_type,currency)",
            "portfolio_id": f"eq.{portfolio_id}",
            "order": "trade_date.asc,created_at.asc",
        },
    )


def _benchmarks(portfolio_id: str) -> List[Dict[str, Any]]:
    return _supabase_get(
        "portfolio_benchmarks",
        {
            "select": "symbol,label,is_primary,display_order",
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


def _signed_quantity(txn: Dict[str, Any]) -> float:
    qty = float(txn.get("quantity") or 0.0)
    return qty if str(txn.get("side", "")).upper() == "BUY" else -qty


def _derive_book(transactions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Derive open quantities and weighted-average cost from immutable trades."""
    book: Dict[str, Dict[str, Any]] = {}

    for txn in transactions:
        instrument = txn.get("instruments") or {}
        symbol = instrument.get("symbol")
        if not symbol:
            continue

        side = str(txn.get("side", "")).upper()
        qty = float(txn.get("quantity") or 0.0)
        price = float(txn.get("price") or 0.0)
        if qty <= 0 or price < 0:
            continue

        item = book.setdefault(
            symbol,
            {
                "symbol": symbol,
                "name": instrument.get("name") or symbol,
                "asset_type": instrument.get("asset_type") or "equity",
                "currency": instrument.get("currency") or txn.get("currency") or "USD",
                "quantity": 0.0,
                "cost_basis": 0.0,
                "realised_pnl": 0.0,
            },
        )

        if side == "BUY":
            item["cost_basis"] += qty * price
            item["quantity"] += qty
        elif side == "SELL":
            if qty > item["quantity"] + 1e-9:
                raise HTTPException(status_code=500, detail=f"Transaction history sells more {symbol} than held.")
            avg_cost = item["cost_basis"] / item["quantity"] if item["quantity"] else 0.0
            item["realised_pnl"] += qty * (price - avg_cost)
            item["quantity"] -= qty
            item["cost_basis"] -= qty * avg_cost
            if abs(item["quantity"]) < 1e-9:
                item["quantity"] = 0.0
                item["cost_basis"] = 0.0

    return book


def _latest_prices(symbols: List[str]) -> Dict[str, float]:
    if not symbols:
        return {}
    try:
        data = yf.download(symbols, period="5d", interval="1d", auto_adjust=True, progress=False, group_by="column")
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


def _holdings_snapshot(portfolio: Dict[str, Any], transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    book = _derive_book(transactions)
    open_symbols = [symbol for symbol, item in book.items() if item["quantity"] > 0]
    prices = _latest_prices(open_symbols)

    initial_capital = float(portfolio.get("initial_capital") or 0.0)
    cash = initial_capital
    for txn in transactions:
        qty = float(txn.get("quantity") or 0.0)
        price = float(txn.get("price") or 0.0)
        fees = float(txn.get("fees") or 0.0)
        if str(txn.get("side", "")).upper() == "BUY":
            cash -= qty * price + fees
        else:
            cash += qty * price - fees

    holdings: List[Dict[str, Any]] = []
    invested_value = 0.0
    for symbol in open_symbols:
        item = book[symbol]
        current_price = prices.get(symbol)
        if current_price is None:
            continue
        market_value = item["quantity"] * current_price
        avg_cost = item["cost_basis"] / item["quantity"] if item["quantity"] else 0.0
        unrealised_pnl = market_value - item["cost_basis"]
        holdings.append(
            {
                "symbol": symbol,
                "name": item["name"],
                "asset_type": item["asset_type"],
                "quantity": round(item["quantity"], 6),
                "average_cost": round(avg_cost, 4),
                "current_price": round(current_price, 4),
                "market_value": round(market_value, 2),
                "unrealised_pnl": round(unrealised_pnl, 2),
                "unrealised_return_pct": round((unrealised_pnl / item["cost_basis"] * 100) if item["cost_basis"] else 0.0, 2),
                "realised_pnl": round(item["realised_pnl"], 2),
            }
        )
        invested_value += market_value

    total_value = cash + invested_value
    for holding in holdings:
        holding["weight_pct"] = round((holding["market_value"] / total_value * 100) if total_value else 0.0, 2)

    holdings.sort(key=lambda row: row["market_value"], reverse=True)
    return {
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
    return closes.sort_index().ffill()


def _performance_history(
    portfolio: Dict[str, Any],
    transactions: List[Dict[str, Any]],
    benchmarks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    portfolio_symbols = sorted({(txn.get("instruments") or {}).get("symbol") for txn in transactions if (txn.get("instruments") or {}).get("symbol")})
    benchmark_symbols = [row["symbol"] for row in benchmarks]
    symbols = list(dict.fromkeys(portfolio_symbols + benchmark_symbols))
    if not symbols:
        return {"dates": [], "portfolio": [], "benchmarks": {}}

    inception = str(portfolio["inception_date"])
    closes = _download_adjusted_close(symbols, inception)
    trading_dates = closes.index
    initial_capital = float(portfolio.get("initial_capital") or 0.0)

    txns_by_date: Dict[date, List[Dict[str, Any]]] = defaultdict(list)
    for txn in transactions:
        txns_by_date[pd.Timestamp(txn["trade_date"]).date()].append(txn)

    quantities = defaultdict(float)
    cash = initial_capital
    values: List[float] = []
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
            if str(txn.get("side", "")).upper() == "BUY":
                quantities[symbol] += qty
                cash -= qty * price + fees
            else:
                quantities[symbol] -= qty
                cash += qty * price - fees

        nav = cash
        for symbol, qty in quantities.items():
            if qty and symbol in closes.columns and pd.notna(closes.loc[ts, symbol]):
                nav += qty * float(closes.loc[ts, symbol])
        out_dates.append(ts.strftime("%Y-%m-%d"))
        values.append(float(nav))

    if not values or values[0] == 0:
        portfolio_index = []
    else:
        portfolio_index = [round(100.0 * value / values[0], 4) for value in values]

    benchmark_data: Dict[str, Any] = {}
    for row in benchmarks:
        symbol = row["symbol"]
        if symbol not in closes.columns:
            continue
        series = closes[symbol].dropna()
        if series.empty:
            continue
        first = float(series.iloc[0])
        aligned = closes[symbol].reindex(trading_dates).ffill()
        benchmark_data[symbol] = {
            "label": row.get("label") or symbol,
            "is_primary": bool(row.get("is_primary")),
            "values": [round(100.0 * float(v) / first, 4) if pd.notna(v) else None for v in aligned],
        }

    return {
        "dates": out_dates,
        "portfolio": portfolio_index,
        "benchmarks": benchmark_data,
        "method": "transaction_reconstructed_nav",
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
    snapshot = _holdings_snapshot(portfolio, transactions)
    benchmarks = _benchmarks(portfolio["id"])
    return {
        "portfolio": {k: v for k, v in portfolio.items() if k != "id"},
        "snapshot": snapshot,
        "benchmarks": benchmarks,
    }


@router.get("/{slug}/transactions")
def get_transactions(slug: str) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    rows = _transactions(portfolio["id"])
    return {"transactions": rows}


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
