from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4
import re

import pandas as pd
import requests
import yfinance as yf
from fastapi import APIRouter, HTTPException, Query, Header
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api/portfolio", tags=["SethiPortfolio"])

SUPABASE_URL = ""
SUPABASE_KEY = ""
SUPABASE_SERVICE_KEY = ""
ADMIN_SECRET = ""


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
    if funding_currency != base_currency:
        raise HTTPException(status_code=400, detail="Allocation-date correction currently requires a base-currency funding instrument.")
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
        "fees": payload.funding_fees, "currency": funding_currency, "fx_rate_to_base": 1.0,
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

    funding_proceeds = (payload.funding_quantity * payload.funding_price - payload.funding_fees)
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
    if normalized_currency != base_currency:
        fx_source_symbol = _fx_symbol(normalized_currency, base_currency)
        fx_ticker = yf.Ticker(fx_source_symbol)
        try:
            fx_history = fx_ticker.history(start=start.isoformat(), end=end.isoformat(), auto_adjust=False)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"FX lookup failed for {normalized_currency}/{base_currency}: {exc}") from exc
        if fx_history is None or fx_history.empty or "Close" not in fx_history:
            raise HTTPException(status_code=404, detail=f"No FX reference rate is available for {normalized_currency}/{base_currency} around {trade_date.isoformat()}.")
        fx_closes = fx_history["Close"].dropna()
        fx_eligible = fx_closes[fx_closes.index.date <= trade_date]
        if fx_eligible.empty:
            raise HTTPException(status_code=404, detail=f"No FX reference rate is available on or before {trade_date.isoformat()} for {normalized_currency}/{base_currency}.")
        fx_ts = fx_eligible.index[-1]
        fx_rate_to_base = float(fx_eligible.iloc[-1])
        fx_price_date = fx_ts.date()
        fx_used_previous_session = fx_price_date != trade_date

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
    funding_fx = 1.0 if funding_currency == base_currency else None
    if funding_fx is None:
        raise HTTPException(status_code=400, detail=f"Funding instrument {funding_symbol} is not base-currency denominated; active-allocation funding FX is not yet supported.")

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
