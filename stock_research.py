"""SethiStock research endpoints.

Free-data research features built on yfinance:
1) Earnings Reaction Study: historical one-day and five-day price reactions.
2) Historical Valuation Bands: reconstructs post-earnings trailing P/E observations
   from reported quarterly EPS and compares today's P/E with its own history.
"""

import time

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import APIRouter

router = APIRouter(prefix="/api/research", tags=["SethiStock Research"])

_RESEARCH_CACHE = {}
_CACHE_TTL_SECONDS = 21600  # 6 hours; earnings history changes slowly.


def _safe_float(value):
    try:
        if value is None or pd.isna(value):
            return None
        value = float(value)
        return value if np.isfinite(value) else None
    except Exception:
        return None


def _naive_timestamp(value):
    ts = pd.Timestamp(value)
    try:
        if ts.tzinfo is not None:
            ts = ts.tz_localize(None)
    except Exception:
        pass
    return ts


def _normalise_price_history(history):
    if history is None or history.empty or "Close" not in history.columns:
        return pd.DataFrame()
    data = history.copy().sort_index()
    try:
        if getattr(data.index, "tz", None) is not None:
            data.index = data.index.tz_localize(None)
    except Exception:
        pass
    return data.dropna(subset=["Close"])


def _fetch_earnings_dates(stock, limit=20):
    earnings = pd.DataFrame()
    try:
        getter = getattr(stock, "get_earnings_dates", None)
        if callable(getter):
            earnings = getter(limit=limit)
    except Exception:
        earnings = pd.DataFrame()

    if earnings is None or earnings.empty:
        try:
            earnings = stock.earnings_dates
        except Exception:
            earnings = pd.DataFrame()

    if earnings is None or earnings.empty:
        return pd.DataFrame()

    earnings = earnings.copy()
    try:
        earnings.index = pd.DatetimeIndex([_naive_timestamp(idx) for idx in earnings.index])
    except Exception:
        return pd.DataFrame()
    return earnings.sort_index()


def _row_value(row, candidates):
    mapping = {str(col).strip().lower(): col for col in row.index}
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in mapping:
            value = _safe_float(row[mapping[key]])
            if value is not None:
                return value
    return None


def _event_sessions(prices, event_ts):
    """Return baseline and reaction-session integer positions.

    Yahoo often stores an approximate release time. Afternoon/evening releases use
    the event-day close as the baseline and the following session as the reaction.
    Morning/unknown releases use the prior close and event-day session.
    """
    if prices.empty:
        return None, None

    session_dates = pd.DatetimeIndex(prices.index).normalize()
    event_date = event_ts.normalize()
    after_close = int(event_ts.hour) >= 12

    if after_close:
        baseline_candidates = np.where(session_dates <= event_date)[0]
        reaction_candidates = np.where(session_dates > event_date)[0]
    else:
        baseline_candidates = np.where(session_dates < event_date)[0]
        reaction_candidates = np.where(session_dates >= event_date)[0]

    if len(baseline_candidates) == 0 or len(reaction_candidates) == 0:
        return None, None
    return int(baseline_candidates[-1]), int(reaction_candidates[0])


def _build_earnings_events(stock, prices):
    earnings = _fetch_earnings_dates(stock, limit=24)
    if earnings.empty or prices.empty:
        return []

    events = []
    for event_ts, row in earnings.iterrows():
        event_ts = _naive_timestamp(event_ts)
        if event_ts > pd.Timestamp.now() + pd.Timedelta(days=2):
            continue

        baseline_pos, reaction_pos = _event_sessions(prices, event_ts)
        if baseline_pos is None or reaction_pos is None:
            continue

        baseline_close = _safe_float(prices["Close"].iloc[baseline_pos])
        reaction_close = _safe_float(prices["Close"].iloc[reaction_pos])
        if not baseline_close or not reaction_close:
            continue

        five_day_pos = reaction_pos + 4
        five_day_close = _safe_float(prices["Close"].iloc[five_day_pos]) if five_day_pos < len(prices) else None
        move_1d = ((reaction_close / baseline_close) - 1.0) * 100.0
        move_5d = ((five_day_close / baseline_close) - 1.0) * 100.0 if five_day_close else None

        estimate = _row_value(row, ["EPS Estimate", "Estimate"])
        reported = _row_value(row, ["Reported EPS", "EPS Actual", "Actual"])
        surprise = _row_value(row, ["Surprise(%)", "Surprise %"])
        if surprise is None and reported is not None and estimate not in (None, 0):
            surprise = ((reported / estimate) - 1.0) * 100.0

        events.append({
            "earnings_date": event_ts.strftime("%Y-%m-%d"),
            "reaction_date": pd.Timestamp(prices.index[reaction_pos]).strftime("%Y-%m-%d"),
            "move_1d_pct": round(float(move_1d), 2),
            "move_5d_pct": round(float(move_5d), 2) if move_5d is not None else None,
            "eps_estimate": round(estimate, 3) if estimate is not None else None,
            "reported_eps": round(reported, 3) if reported is not None else None,
            "surprise_pct": round(surprise, 2) if surprise is not None else None,
            "reaction_price": round(reaction_close, 2),
        })

    events.sort(key=lambda item: item["earnings_date"])
    return events


def _earnings_reaction_study(events):
    if not events:
        return {
            "available": False,
            "reason": "Historical earnings dates were unavailable for this security.",
            "events": [],
        }

    recent = events[-12:]
    moves_1d = np.array([e["move_1d_pct"] for e in recent if e["move_1d_pct"] is not None], dtype=float)
    moves_5d = np.array([e["move_5d_pct"] for e in recent if e["move_5d_pct"] is not None], dtype=float)
    surprises = [e for e in recent if e["surprise_pct"] is not None]

    summary = {
        "quarters": int(len(recent)),
        "average_1d_move_pct": round(float(np.mean(moves_1d)), 2) if moves_1d.size else None,
        "average_abs_1d_move_pct": round(float(np.mean(np.abs(moves_1d))), 2) if moves_1d.size else None,
        "median_abs_1d_move_pct": round(float(np.median(np.abs(moves_1d))), 2) if moves_1d.size else None,
        "positive_reaction_rate_pct": round(float(np.mean(moves_1d > 0) * 100.0), 1) if moves_1d.size else None,
        "average_5d_move_pct": round(float(np.mean(moves_5d)), 2) if moves_5d.size else None,
        "eps_beat_rate_pct": round(float(np.mean([e["surprise_pct"] > 0 for e in surprises]) * 100.0), 1) if surprises else None,
    }

    return {
        "available": True,
        "summary": summary,
        "events": list(reversed(recent)),
    }


def _historical_valuation_bands(stock, prices, events):
    reported_events = [e for e in events if e.get("reported_eps") is not None]
    observations = []

    # Reconstruct trailing-four-quarter EPS at each earnings event. This avoids
    # pretending that today's EPS existed historically and keeps the feature free.
    for i in range(3, len(reported_events)):
        window = reported_events[i - 3:i + 1]
        ttm_eps = float(sum(e["reported_eps"] for e in window))
        price = _safe_float(reported_events[i].get("reaction_price"))
        if ttm_eps <= 0 or not price:
            continue
        pe = price / ttm_eps
        if not np.isfinite(pe) or pe <= 0 or pe > 250:
            continue
        observations.append({
            "date": reported_events[i]["reaction_date"],
            "pe": round(float(pe), 2),
            "ttm_eps": round(ttm_eps, 3),
            "price": round(price, 2),
        })

    current_pe = None
    try:
        info = stock.info or {}
        current_pe = _safe_float(info.get("trailingPE"))
        if current_pe is None:
            trailing_eps = _safe_float(info.get("trailingEps"))
            current_price = _safe_float(prices["Close"].iloc[-1]) if not prices.empty else None
            if trailing_eps and trailing_eps > 0 and current_price:
                current_pe = current_price / trailing_eps
    except Exception:
        current_pe = None

    historical_values = np.array([obs["pe"] for obs in observations], dtype=float)
    if historical_values.size < 4:
        return {
            "available": False,
            "reason": "Not enough positive trailing-EPS observations to build a meaningful P/E history.",
            "current_pe": round(current_pe, 2) if current_pe else None,
            "observations": observations,
        }

    p10, p25, p50, p75, p90 = np.percentile(historical_values, [10, 25, 50, 75, 90])
    percentile = None
    label = "Historical range"
    if current_pe is not None and current_pe > 0:
        percentile = float(np.mean(historical_values <= current_pe) * 100.0)
        if percentile <= 25:
            label = "Discount to history"
        elif percentile <= 45:
            label = "Below historical median"
        elif percentile <= 55:
            label = "Near historical median"
        elif percentile <= 75:
            label = "Above historical median"
        else:
            label = "Premium to history"

    return {
        "available": True,
        "method": "post_earnings_trailing_pe",
        "current_pe": round(current_pe, 2) if current_pe is not None else None,
        "current_percentile": round(percentile, 1) if percentile is not None else None,
        "valuation_label": label,
        "bands": {
            "p10": round(float(p10), 2),
            "p25": round(float(p25), 2),
            "median": round(float(p50), 2),
            "p75": round(float(p75), 2),
            "p90": round(float(p90), 2),
            "min": round(float(np.min(historical_values)), 2),
            "max": round(float(np.max(historical_values)), 2),
        },
        "observations": observations,
    }


def _calculate_stock_research(ticker):
    stock = yf.Ticker(ticker)
    try:
        prices = _normalise_price_history(stock.history(period="5y", interval="1d", auto_adjust=False))
    except Exception:
        prices = pd.DataFrame()

    events = _build_earnings_events(stock, prices)
    return {
        "ticker": ticker,
        "earnings_reaction": _earnings_reaction_study(events),
        "valuation_bands": _historical_valuation_bands(stock, prices, events),
        "methodology": {
            "earnings_reaction": "Close-to-close reaction around historical earnings releases; five-day return is measured from the same pre-release baseline.",
            "valuation_bands": "P/E is reconstructed after each earnings release using the latest four reported quarterly EPS figures. It is an historical comparison tool, not a live consensus forecast.",
            "source": "Yahoo Finance via yfinance",
        },
    }


@router.get("/{raw_ticker}")
def get_stock_research(raw_ticker: str):
    ticker = raw_ticker.strip().upper()
    if not ticker or len(ticker) > 20:
        return {"ticker": ticker, "earnings_reaction": {"available": False}, "valuation_bands": {"available": False}}

    now = time.time()
    cached = _RESEARCH_CACHE.get(ticker)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        result = _calculate_stock_research(ticker)
    except Exception as exc:
        result = {
            "ticker": ticker,
            "earnings_reaction": {"available": False, "reason": "Research data temporarily unavailable."},
            "valuation_bands": {"available": False, "reason": "Research data temporarily unavailable."},
            "error": str(exc),
        }

    _RESEARCH_CACHE[ticker] = (now, result)
    return result
