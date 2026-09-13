"""Phase 3D persistent Supabase storage and SWR caching for Company Drivers.

Verified Phase 3C history payloads are staged under immutable sync IDs. The active
snapshot pointer is advanced only after every observation is stored, so readers never
see a partially-written history. Fresh snapshots are served directly from Supabase;
stale snapshots are served immediately while one in-process refresh runs in the
background. Storage failures are fail-open and never make the SEC extractor unusable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
from uuid import uuid4

import requests


STORE_VERSION = "3d-driver-store-v1"
SUPABASE_URL = os.getenv("SUPABASE_URL", "").replace("/rest/v1", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
OBSERVATIONS_TABLE = os.getenv("SETHISTOCK_DRIVER_OBSERVATIONS_TABLE", "sethistock_driver_observations")
SYNCS_TABLE = os.getenv("SETHISTOCK_DRIVER_SYNCS_TABLE", "sethistock_driver_syncs")
DRIVER_TTL_SECONDS = max(3600, int(os.getenv("SETHISTOCK_DRIVER_TTL_SECONDS", "86400")))
HTTP_TIMEOUT = max(2.0, float(os.getenv("SETHISTOCK_DRIVER_STORE_HTTP_TIMEOUT", "8")))
WRITE_BATCH_SIZE = max(25, min(250, int(os.getenv("SETHISTOCK_DRIVER_WRITE_BATCH_SIZE", "100"))))
READ_PAGE_SIZE = max(100, min(1000, int(os.getenv("SETHISTOCK_DRIVER_READ_PAGE_SIZE", "500"))))
MAX_STORED_OBSERVATIONS = 100

_LOCKS_GUARD = threading.Lock()
_LOCKS: Dict[str, threading.Lock] = {}
_REFRESHING_GUARD = threading.Lock()
_REFRESHING: set[str] = set()


class DriverStoreError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _headers(prefer: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _table_url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"


def _request(
    method: str,
    table: str,
    *,
    params: Optional[Dict[str, str]] = None,
    payload: Any = None,
    prefer: Optional[str] = None,
):
    if not is_configured():
        raise DriverStoreError("Supabase Company Driver storage is not configured.")
    try:
        response = requests.request(
            method,
            _table_url(table),
            headers=_headers(prefer),
            params=params,
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
    except Exception as exc:
        raise DriverStoreError(f"Supabase Company Driver request failed: {exc}") from exc
    if response.status_code >= 400:
        raise DriverStoreError(
            f"Supabase Company Driver request returned {response.status_code}: {response.text[:500]}"
        )
    return response


def probe() -> bool:
    if not is_configured():
        return False
    try:
        _request("GET", SYNCS_TABLE, params={"select": "ticker", "limit": "1"})
        return True
    except DriverStoreError:
        return False


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _key(ticker: str, metric: str, period: str, filing_limit: int) -> str:
    return "|".join([
        str(ticker or "").strip().upper(),
        str(metric or "").strip().lower(),
        STORE_VERSION,
        str(period or "").strip().lower(),
        str(int(filing_limit)),
    ])


def _lock_for(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _chunks(rows: Sequence[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield list(rows[index:index + size])


def _observation_key(row: Dict[str, Any]) -> str:
    return "|".join([
        str(row.get("period_type") or ""),
        str(row.get("start") or ""),
        str(row.get("end") or ""),
        str(row.get("instant") or ""),
        str(row.get("unit_ref") or ""),
        str(row.get("extraction_method") or ""),
    ])


def _delete_sync_rows(sync_id: str) -> None:
    try:
        _request(
            "DELETE",
            OBSERVATIONS_TABLE,
            params={"sync_id": f"eq.{sync_id}"},
            prefer="return=minimal",
        )
    except DriverStoreError:
        pass


def get_sync_state(
    ticker: str,
    metric: str,
    period: str,
    filing_limit: int,
    *,
    store_version: str = STORE_VERSION,
) -> Optional[Dict[str, Any]]:
    if not is_configured():
        return None
    response = _request(
        "GET",
        SYNCS_TABLE,
        params={
            "ticker": f"eq.{str(ticker).strip().upper()}",
            "metric": f"eq.{str(metric).strip().lower()}",
            "history_version": f"eq.{store_version}",
            "period": f"eq.{str(period).strip().lower()}",
            "filing_limit": f"eq.{int(filing_limit)}",
            "select": "ticker,metric,history_version,period,filing_limit,active_sync_id,source_mode,data_state,history_meta,observation_count,coverage_start,coverage_end,source_latest_filing,synced_at,expires_at",
            "limit": "1",
        },
    )
    rows = response.json()
    return rows[0] if rows else None


def _read_observations(sync_id: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        response = _request(
            "GET",
            OBSERVATIONS_TABLE,
            params={
                "sync_id": f"eq.{sync_id}",
                "select": "period_type,period_start,period_end,instant,value,unit_ref,qualified_concept,dimensions,form,filing_date,report_date,accession,source_url,extraction_method,score,derived,derivation",
                "limit": str(READ_PAGE_SIZE),
                "offset": str(offset),
            },
        )
        page = response.json()
        if not page:
            break
        rows.extend(page)
        if len(page) < READ_PAGE_SIZE:
            break
        offset += len(page)
    rows.sort(key=lambda row: (
        str(row.get("period_end") or row.get("instant") or ""),
        str(row.get("period_start") or ""),
        str(row.get("filing_date") or ""),
    ))
    return rows


def _restore_observation(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        "period_type": row.get("period_type"),
        "start": row.get("period_start"),
        "end": row.get("period_end"),
        "instant": row.get("instant"),
        "value": row.get("value"),
        "unit_ref": row.get("unit_ref"),
        "qualified_concept": row.get("qualified_concept"),
        "dimensions": row.get("dimensions") or [],
        "form": row.get("form"),
        "filing_date": row.get("filing_date"),
        "report_date": row.get("report_date"),
        "accession": row.get("accession"),
        "source_url": row.get("source_url"),
        "extraction_method": row.get("extraction_method"),
        "score": row.get("score"),
        "derived": bool(row.get("derived")),
    }
    if row.get("derivation") is not None:
        out["derivation"] = row.get("derivation")
    return out


def load_history(
    *,
    ticker: str,
    metric: str,
    period: str,
    filing_limit: int,
    observation_limit: int,
) -> Optional[Dict[str, Any]]:
    state = get_sync_state(ticker, metric, period, filing_limit)
    if not state or not state.get("active_sync_id"):
        return None
    raw_rows = _read_observations(str(state["active_sync_id"]))
    observations = [_restore_observation(row) for row in raw_rows]
    observations = observations[-max(1, min(int(observation_limit), MAX_STORED_OBSERVATIONS)):]
    dates = [
        str(row.get("end") or row.get("instant") or "")
        for row in observations
        if row.get("end") or row.get("instant")
    ]
    payload = dict(state.get("history_meta") or {})
    payload.update({
        "ticker": str(ticker).strip().upper(),
        "metric": str(metric).strip().lower(),
        "period": str(period).strip().lower(),
        "data_state": state.get("data_state") or payload.get("data_state") or "verified_history",
        "source_mode": state.get("source_mode") or payload.get("source_mode"),
        "verified": True,
        "observations": observations,
        "observation_count": len(observations),
        "coverage": {
            "start": min(dates) if dates else None,
            "end": max(dates) if dates else None,
        },
        "cache_persistent": True,
        "cache_store_version": str(state.get("history_version") or STORE_VERSION),
        "cache_synced_at": state.get("synced_at"),
        "cache_expires_at": state.get("expires_at"),
        "cache_total_observation_count": int(state.get("observation_count") or 0),
    })
    expires = _parse_timestamp(state.get("expires_at"))
    payload["cache_fresh"] = bool(expires and expires > datetime.now(timezone.utc))
    return payload


def persist_history(
    history: Dict[str, Any],
    *,
    filing_limit: int,
    ttl_seconds: int = DRIVER_TTL_SECONDS,
) -> Dict[str, Any]:
    if not is_configured():
        raise DriverStoreError("Supabase Company Driver storage is not configured.")
    if not history.get("verified") or history.get("data_state") != "verified_history":
        raise DriverStoreError("Only verified Company Driver histories may be persisted.")

    ticker = str(history.get("ticker") or "").strip().upper()
    metric = str(history.get("metric") or "").strip().lower()
    extractor_history_version = str(history.get("history_version") or "").strip()
    period = str(history.get("period") or "").strip().lower()
    observations = list(history.get("observations") or [])
    if not ticker or not metric or not extractor_history_version or period not in {"quarterly", "annual", "reported"}:
        raise DriverStoreError("Company Driver history identity is incomplete.")
    if not observations:
        raise DriverStoreError("Refusing to persist an empty Company Driver history.")

    previous = get_sync_state(ticker, metric, period, filing_limit)
    previous_sync_id = previous.get("active_sync_id") if previous else None
    sync_id = str(uuid4())
    records: List[Dict[str, Any]] = []
    filing_dates: List[str] = []

    for row in observations[-MAX_STORED_OBSERVATIONS:]:
        period_end = row.get("end")
        instant = row.get("instant")
        if not (period_end or instant) or row.get("value") is None:
            continue
        if row.get("filing_date"):
            filing_dates.append(str(row.get("filing_date")))
        records.append({
            "sync_id": sync_id,
            "observation_key": _observation_key(row),
            "ticker": ticker,
            "metric": metric,
            "history_version": STORE_VERSION,
            "period": period,
            "filing_limit": int(filing_limit),
            "period_type": row.get("period_type"),
            "period_start": row.get("start"),
            "period_end": period_end,
            "instant": instant,
            "value": row.get("value"),
            "unit_ref": row.get("unit_ref"),
            "qualified_concept": row.get("qualified_concept"),
            "dimensions": row.get("dimensions") or [],
            "form": row.get("form"),
            "filing_date": row.get("filing_date"),
            "report_date": row.get("report_date"),
            "accession": row.get("accession"),
            "source_url": row.get("source_url"),
            "extraction_method": row.get("extraction_method") or "unknown",
            "score": row.get("score"),
            "derived": bool(row.get("derived")),
            "derivation": row.get("derivation"),
        })
    if not records:
        raise DriverStoreError("No persistable Company Driver observations were produced.")

    # Keep the extractor's own history_version here as provenance. The DB key uses
    # STORE_VERSION so Inline XBRL, derived ratios and filing-table histories all
    # share one persistence schema instead of fragmenting the cache by source mode.
    history_meta = {
        key: value for key, value in history.items()
        if key not in {
            "observations", "observation_count", "coverage", "ticker", "metric",
            "period", "cache_state", "cache_fresh", "cache_persistent",
            "cache_store_version", "cache_synced_at", "cache_expires_at",
            "cache_total_observation_count",
        }
    }
    coverage_dates = [
        str(row.get("end") or row.get("instant") or "")
        for row in observations
        if row.get("end") or row.get("instant")
    ]
    now = datetime.now(timezone.utc)
    sync_row = {
        "ticker": ticker,
        "metric": metric,
        "history_version": STORE_VERSION,
        "period": period,
        "filing_limit": int(filing_limit),
        "active_sync_id": sync_id,
        "source_mode": history.get("source_mode"),
        "data_state": history.get("data_state") or "verified_history",
        "history_meta": history_meta,
        "observation_count": len(records),
        "coverage_start": min(coverage_dates) if coverage_dates else None,
        "coverage_end": max(coverage_dates) if coverage_dates else None,
        "source_latest_filing": max(filing_dates) if filing_dates else None,
        "synced_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=max(3600, int(ttl_seconds)))).isoformat(),
        "updated_at": now.isoformat(),
    }

    try:
        for batch in _chunks(records, WRITE_BATCH_SIZE):
            _request("POST", OBSERVATIONS_TABLE, payload=batch, prefer="return=minimal")
        _request(
            "POST",
            SYNCS_TABLE,
            params={"on_conflict": "ticker,metric,history_version,period,filing_limit"},
            payload=sync_row,
            prefer="resolution=merge-duplicates,return=minimal",
        )
    except DriverStoreError:
        _delete_sync_rows(sync_id)
        raise

    # Delete only the snapshot that was active before this refresh began. A newer
    # snapshot activated by another worker can therefore never be removed here.
    if previous_sync_id and str(previous_sync_id) != sync_id:
        _delete_sync_rows(str(previous_sync_id))

    return {
        "sync_id": sync_id,
        "ticker": ticker,
        "metric": metric,
        "store_version": STORE_VERSION,
        "extractor_history_version": extractor_history_version,
        "period": period,
        "filing_limit": int(filing_limit),
        "observation_count": len(records),
        "synced_at": sync_row["synced_at"],
        "expires_at": sync_row["expires_at"],
    }


def _build_history(
    *,
    ticker: str,
    metric: Dict[str, Any],
    filing_limit: int,
    period: str,
    builder: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    return builder(
        ticker,
        metric,
        filing_limit=filing_limit,
        period=period,
        observation_limit=MAX_STORED_OBSERVATIONS,
    )


def _background_refresh(
    key: str,
    *,
    ticker: str,
    metric: Dict[str, Any],
    filing_limit: int,
    period: str,
    builder: Callable[..., Dict[str, Any]],
) -> None:
    try:
        lock = _lock_for(key)
        if not lock.acquire(blocking=False):
            return
        try:
            result = _build_history(
                ticker=ticker,
                metric=metric,
                filing_limit=filing_limit,
                period=period,
                builder=builder,
            )
            if result.get("verified") and result.get("data_state") == "verified_history" and result.get("observations"):
                persist_history(result, filing_limit=filing_limit)
        finally:
            lock.release()
    except Exception:
        # A stale snapshot is preferable to turning a background refresh failure
        # into a user-facing error. The next request can retry after the same TTL.
        pass
    finally:
        with _REFRESHING_GUARD:
            _REFRESHING.discard(key)


def get_or_refresh_history(
    *,
    ticker: str,
    metric: Dict[str, Any],
    filing_limit: int,
    period: str,
    observation_limit: int,
    builder: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    """Return persistent history with fresh-hit, SWR, miss-refresh and fail-open states."""
    symbol = str(ticker or "").strip().upper()
    metric_key = str(metric.get("key") or "").strip().lower()
    period_key = str(period or "quarterly").strip().lower()
    filing_limit = max(1, min(int(filing_limit), 32))
    observation_limit = max(1, min(int(observation_limit), MAX_STORED_OBSERVATIONS))

    if not is_configured():
        result = builder(
            symbol,
            metric,
            filing_limit=filing_limit,
            period=period_key,
            observation_limit=observation_limit,
        )
        result.update({
            "cache_state": "store_not_configured",
            "cache_persistent": False,
            "cache_store_version": STORE_VERSION,
        })
        return result

    cache_key = _key(symbol, metric_key, period_key, filing_limit)
    try:
        cached = load_history(
            ticker=symbol,
            metric=metric_key,
            period=period_key,
            filing_limit=filing_limit,
            observation_limit=observation_limit,
        )
    except DriverStoreError:
        cached = None

    if cached and cached.get("cache_fresh"):
        cached["cache_state"] = "fresh"
        return cached

    if cached:
        with _REFRESHING_GUARD:
            should_start = cache_key not in _REFRESHING
            if should_start:
                _REFRESHING.add(cache_key)
        if should_start:
            threading.Thread(
                target=_background_refresh,
                kwargs={
                    "key": cache_key,
                    "ticker": symbol,
                    "metric": metric,
                    "filing_limit": filing_limit,
                    "period": period_key,
                    "builder": builder,
                },
                daemon=True,
                name=f"driver-refresh-{symbol}-{metric_key}",
            ).start()
        cached["cache_state"] = "stale_while_revalidate"
        return cached

    lock = _lock_for(cache_key)
    with lock:
        # Another request in this Render process may have filled the cache while we
        # waited for the single-flight lock.
        try:
            cached = load_history(
                ticker=symbol,
                metric=metric_key,
                period=period_key,
                filing_limit=filing_limit,
                observation_limit=observation_limit,
            )
        except DriverStoreError:
            cached = None
        if cached and cached.get("cache_fresh"):
            cached["cache_state"] = "fresh_after_wait"
            return cached

        # Build once. If Supabase subsequently fails, return this already-built SEC
        # result rather than repeating an expensive filing extraction.
        result = _build_history(
            ticker=symbol,
            metric=metric,
            filing_limit=filing_limit,
            period=period_key,
            builder=builder,
        )
        if not (result.get("verified") and result.get("data_state") == "verified_history" and result.get("observations")):
            result.update({
                "cache_state": "not_persisted",
                "cache_persistent": False,
                "cache_store_version": STORE_VERSION,
            })
            result["observations"] = list(result.get("observations") or [])[-observation_limit:]
            result["observation_count"] = len(result["observations"])
            return result

        try:
            persist_history(result, filing_limit=filing_limit)
            persisted = load_history(
                ticker=symbol,
                metric=metric_key,
                period=period_key,
                filing_limit=filing_limit,
                observation_limit=observation_limit,
            )
        except DriverStoreError:
            persisted = None

        if persisted:
            persisted["cache_state"] = "miss_refreshed"
            return persisted

        result["observations"] = list(result.get("observations") or [])[-observation_limit:]
        result["observation_count"] = len(result["observations"])
        result.update({
            "cache_state": "store_error_fallback",
            "cache_persistent": False,
            "cache_store_version": STORE_VERSION,
        })
        return result


def storage_status() -> Dict[str, Any]:
    return {
        "configured": is_configured(),
        "reachable": probe() if is_configured() else False,
        "store_version": STORE_VERSION,
        "ttl_seconds": DRIVER_TTL_SECONDS,
        "max_stored_observations": MAX_STORED_OBSERVATIONS,
        "observations_table": OBSERVATIONS_TABLE,
        "syncs_table": SYNCS_TABLE,
        "strategy": "persistent immutable snapshots with stale-while-revalidate",
    }
