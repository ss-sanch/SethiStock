"""Persistent Supabase storage for SethiStock normalized SEC fundamentals.

Phase 2C stores complete normalized snapshots under immutable sync IDs. A newly
written snapshot becomes visible only after every observation has been persisted and
the sync pointer is atomically advanced. Failed refreshes therefore cannot expose a
partial company history.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import os
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import uuid4

import requests


SUPABASE_URL = os.getenv("SUPABASE_URL", "").replace("/rest/v1", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
OBSERVATIONS_TABLE = os.getenv(
    "SETHISTOCK_FUNDAMENTALS_OBSERVATIONS_TABLE",
    "sethistock_fundamental_observations",
)
SYNCS_TABLE = os.getenv(
    "SETHISTOCK_FUNDAMENTALS_SYNCS_TABLE",
    "sethistock_fundamental_syncs",
)
FUNDAMENTALS_TTL_SECONDS = max(
    3600,
    int(os.getenv("SETHISTOCK_FUNDAMENTALS_TTL_SECONDS", "86400")),
)
FUNDAMENTALS_SYNC_LIMIT = max(
    250,
    min(2000, int(os.getenv("SETHISTOCK_FUNDAMENTALS_SYNC_LIMIT", "1000"))),
)
HTTP_TIMEOUT = max(
    2.0,
    float(os.getenv("SETHISTOCK_FUNDAMENTALS_HTTP_TIMEOUT", "8")),
)
WRITE_BATCH_SIZE = max(
    50,
    min(500, int(os.getenv("SETHISTOCK_FUNDAMENTALS_WRITE_BATCH_SIZE", "250"))),
)
READ_PAGE_SIZE = max(
    100,
    min(1000, int(os.getenv("SETHISTOCK_FUNDAMENTALS_READ_PAGE_SIZE", "750"))),
)

_SYNC_LOCKS_GUARD = threading.Lock()
_SYNC_LOCKS: Dict[str, threading.Lock] = {}


class FundamentalsStoreError(RuntimeError):
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
        raise FundamentalsStoreError("Supabase fundamentals storage is not configured.")
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
        raise FundamentalsStoreError(f"Supabase fundamentals request failed: {exc}") from exc
    if response.status_code >= 400:
        body = response.text[:500]
        raise FundamentalsStoreError(
            f"Supabase fundamentals request returned {response.status_code}: {body}"
        )
    return response


def probe() -> bool:
    if not is_configured():
        return False
    try:
        _request(
            "GET",
            SYNCS_TABLE,
            params={"select": "ticker", "limit": "1"},
        )
        return True
    except FundamentalsStoreError:
        return False


def get_sync_lock(ticker: str) -> threading.Lock:
    symbol = str(ticker or "").strip().upper()
    with _SYNC_LOCKS_GUARD:
        lock = _SYNC_LOCKS.get(symbol)
        if lock is None:
            lock = threading.Lock()
            _SYNC_LOCKS[symbol] = lock
        return lock


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _collect_source_concepts(row: Dict[str, Any]) -> set[str]:
    concepts: set[str] = set()
    taxonomy = row.get("taxonomy")
    concept = row.get("concept")
    if taxonomy and concept:
        concepts.add(f"{taxonomy}:{concept}")
    for component in row.get("components") or []:
        if isinstance(component, dict):
            concepts.update(_collect_source_concepts(component))
    return concepts


def _observation_key(metric: str, row: Dict[str, Any]) -> str:
    start = row.get("start") or "instant"
    return "|".join(
        [
            str(metric),
            str(start),
            str(row.get("end") or ""),
            str(row.get("unit") or ""),
        ]
    )


def _chunks(rows: Sequence[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield list(rows[index : index + size])


def _normalise_manifest(normalized: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    manifest: Dict[str, Dict[str, Any]] = {}
    for metric, payload in normalized.items():
        manifest[metric] = {
            key: value
            for key, value in payload.items()
            if key not in {"observations", "count", "coverage"}
        }
    return manifest


def _flatten_snapshot(
    *,
    identity: Dict[str, Any],
    normalized: Dict[str, Dict[str, Any]],
    schema_version: str,
    sync_id: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    ticker = str(identity.get("ticker") or "").strip().upper()
    cik = int(identity.get("cik"))
    records: List[Dict[str, Any]] = []
    end_dates: List[str] = []
    filed_dates: List[str] = []

    for metric, metric_payload in normalized.items():
        for row in metric_payload.get("observations") or []:
            end = row.get("end")
            if not end:
                continue
            if end:
                end_dates.append(str(end))
            if row.get("filed"):
                filed_dates.append(str(row.get("filed")))
            records.append(
                {
                    "sync_id": sync_id,
                    "observation_key": _observation_key(metric, row),
                    "ticker": ticker,
                    "cik": cik,
                    "schema_version": schema_version,
                    "metric": metric,
                    "kind": metric_payload.get("kind") or "flow",
                    "unit": row.get("unit") or metric_payload.get("unit") or "",
                    "value": row.get("value"),
                    "period_start": row.get("start"),
                    "period_end": end,
                    "filed": row.get("filed"),
                    "fy": row.get("fy"),
                    "fp": row.get("fp"),
                    "form": row.get("form"),
                    "frame": row.get("frame"),
                    "accn": row.get("accn"),
                    "taxonomy": row.get("taxonomy"),
                    "concept": row.get("concept"),
                    "concept_rank": row.get("concept_rank"),
                    "duration_days": row.get("duration_days"),
                    "derived": bool(row.get("derived")),
                    "formula": row.get("formula"),
                    "components": row.get("components"),
                    "source_concepts": sorted(_collect_source_concepts(row)),
                    "source": row.get("source") or "SEC EDGAR Company Facts",
                }
            )

    stats = {
        "observation_count": len(records),
        "earliest_end": min(end_dates) if end_dates else None,
        "latest_end": max(end_dates) if end_dates else None,
        "source_latest_filed": max(filed_dates) if filed_dates else None,
    }
    return records, _normalise_manifest(normalized), stats


def _delete_staged_sync(sync_id: str) -> None:
    try:
        _request(
            "DELETE",
            OBSERVATIONS_TABLE,
            params={"sync_id": f"eq.{sync_id}"},
            prefer="return=minimal",
        )
    except FundamentalsStoreError:
        pass


def persist_snapshot(
    *,
    identity: Dict[str, Any],
    entity_name: Optional[str],
    normalized: Dict[str, Dict[str, Any]],
    schema_version: str,
    ttl_seconds: int = FUNDAMENTALS_TTL_SECONDS,
) -> Dict[str, Any]:
    """Stage a complete snapshot, atomically activate it, then purge older copies."""
    if not is_configured():
        raise FundamentalsStoreError("Supabase fundamentals storage is not configured.")

    ticker = str(identity.get("ticker") or "").strip().upper()
    if not ticker:
        raise FundamentalsStoreError("Ticker is required for fundamentals persistence.")

    sync_id = str(uuid4())
    records, manifest, stats = _flatten_snapshot(
        identity=identity,
        normalized=normalized,
        schema_version=schema_version,
        sync_id=sync_id,
    )
    if not records:
        raise FundamentalsStoreError("Refusing to activate an empty fundamentals snapshot.")

    try:
        for batch in _chunks(records, WRITE_BATCH_SIZE):
            _request(
                "POST",
                OBSERVATIONS_TABLE,
                payload=batch,
                prefer="return=minimal",
            )

        now = datetime.now(timezone.utc)
        sync_row = {
            "ticker": ticker,
            "schema_version": schema_version,
            "cik": int(identity.get("cik")),
            "title": identity.get("title"),
            "entity_name": entity_name or identity.get("title"),
            "active_sync_id": sync_id,
            "source": "SEC EDGAR Company Facts",
            "metric_manifest": manifest,
            **stats,
            "synced_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=max(3600, int(ttl_seconds)))).isoformat(),
            "updated_at": now.isoformat(),
        }
        _request(
            "POST",
            SYNCS_TABLE,
            params={"on_conflict": "ticker,schema_version"},
            payload=sync_row,
            prefer="resolution=merge-duplicates,return=minimal",
        )
    except FundamentalsStoreError:
        _delete_staged_sync(sync_id)
        raise

    # Cleanup happens only after activation. Failure here is harmless because reads are
    # always pinned to active_sync_id and will never expose an older/staged snapshot.
    try:
        _request(
            "DELETE",
            OBSERVATIONS_TABLE,
            params={
                "ticker": f"eq.{ticker}",
                "schema_version": f"eq.{schema_version}",
                "sync_id": f"neq.{sync_id}",
            },
            prefer="return=minimal",
        )
    except FundamentalsStoreError:
        pass

    return {
        "sync_id": sync_id,
        "ticker": ticker,
        "schema_version": schema_version,
        **stats,
        "synced_at": sync_row["synced_at"],
        "expires_at": sync_row["expires_at"],
    }


def get_sync_state(ticker: str, schema_version: str) -> Optional[Dict[str, Any]]:
    if not is_configured():
        return None
    symbol = str(ticker or "").strip().upper()
    response = _request(
        "GET",
        SYNCS_TABLE,
        params={
            "ticker": f"eq.{symbol}",
            "schema_version": f"eq.{schema_version}",
            "select": "ticker,schema_version,cik,title,entity_name,active_sync_id,source,metric_manifest,observation_count,earliest_end,latest_end,source_latest_filed,synced_at,expires_at",
            "limit": "1",
        },
    )
    rows = response.json()
    return rows[0] if rows else None


def _read_active_rows(
    sync_id: str,
    metrics: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        params: Dict[str, str] = {
            "sync_id": f"eq.{sync_id}",
            "select": "metric,kind,unit,value,period_start,period_end,filed,fy,fp,form,frame,accn,taxonomy,concept,concept_rank,duration_days,derived,formula,components,source_concepts,source",
            "order": "metric.asc,period_end.asc,period_start.asc,filed.asc",
            "limit": str(READ_PAGE_SIZE),
            "offset": str(offset),
        }
        if metrics:
            params["metric"] = f"in.({','.join(metrics)})"
        response = _request("GET", OBSERVATIONS_TABLE, params=params)
        page = response.json()
        if not page:
            break
        rows.extend(page)
        if len(page) < READ_PAGE_SIZE:
            break
        offset += len(page)
    return rows


def _row_to_observation(row: Dict[str, Any]) -> Dict[str, Any]:
    observation = {
        "value": row.get("value"),
        "unit": row.get("unit"),
        "start": row.get("period_start"),
        "end": row.get("period_end"),
        "filed": row.get("filed"),
        "fy": row.get("fy"),
        "fp": row.get("fp"),
        "form": row.get("form"),
        "frame": row.get("frame"),
        "accn": row.get("accn"),
        "taxonomy": row.get("taxonomy"),
        "concept": row.get("concept"),
        "concept_rank": row.get("concept_rank"),
        "duration_days": row.get("duration_days"),
        "derived": bool(row.get("derived")),
        "source": row.get("source") or "SEC EDGAR Company Facts",
    }
    if row.get("formula"):
        observation["formula"] = row.get("formula")
    if row.get("components") is not None:
        observation["components"] = row.get("components")
    return observation


def load_snapshot(
    *,
    ticker: str,
    schema_version: str,
    metric_order: Sequence[str],
    metrics: Optional[Sequence[str]] = None,
    limit: int = 250,
) -> Optional[Dict[str, Any]]:
    """Load the active immutable snapshot and rebuild the Phase 2B API payload."""
    state = get_sync_state(ticker, schema_version)
    if not state or not state.get("active_sync_id"):
        return None

    requested = list(metrics) if metrics else list(metric_order)
    raw_rows = _read_active_rows(str(state["active_sync_id"]), requested)
    grouped: Dict[str, List[Dict[str, Any]]] = {metric: [] for metric in requested}
    source_concepts: Dict[str, Dict[str, set[str]]] = {
        metric: {} for metric in requested
    }

    for row in raw_rows:
        metric = row.get("metric")
        if metric not in grouped:
            continue
        observation = _row_to_observation(row)
        grouped[metric].append(observation)
        period_key = "|".join(
            [
                str(observation.get("start") or "instant"),
                str(observation.get("end") or ""),
                str(observation.get("unit") or ""),
            ]
        )
        source_concepts[metric][period_key] = set(row.get("source_concepts") or [])

    manifest = state.get("metric_manifest") or {}
    output: Dict[str, Dict[str, Any]] = {}
    bounded_limit = max(1, int(limit))
    for metric in requested:
        ordered = sorted(
            grouped.get(metric) or [],
            key=lambda row: (
                str(row.get("end") or ""),
                str(row.get("start") or ""),
                str(row.get("filed") or ""),
            ),
        )
        selected = ordered[-bounded_limit:]
        concepts: set[str] = set()
        for row in selected:
            period_key = "|".join(
                [
                    str(row.get("start") or "instant"),
                    str(row.get("end") or ""),
                    str(row.get("unit") or ""),
                ]
            )
            concepts.update(source_concepts.get(metric, {}).get(period_key, set()))
        ends = [str(row.get("end")) for row in selected if row.get("end")]
        metadata = dict(manifest.get(metric) or {})
        metadata.update(
            {
                "metric": metric,
                "count": len(selected),
                "coverage": {
                    "earliest_end": min(ends) if ends else None,
                    "latest_end": max(ends) if ends else None,
                    "concepts_used": sorted(concepts),
                },
                "observations": selected,
            }
        )
        output[metric] = metadata

    expires_at = _parse_timestamp(state.get("expires_at"))
    now = datetime.now(timezone.utc)
    fresh = bool(expires_at and expires_at > now)
    return {
        "ticker": state.get("ticker"),
        "cik": state.get("cik"),
        "cik_padded": f"{int(state.get('cik')):010d}" if state.get("cik") is not None else None,
        "title": state.get("title"),
        "entity_name": state.get("entity_name") or state.get("title"),
        "schema_version": state.get("schema_version"),
        "metric_order": [metric for metric in metric_order if metric in output],
        "metrics": output,
        "period_classification": "deferred_to_phase_2d",
        "source": state.get("source") or "SEC EDGAR Company Facts",
        "storage": {
            "backend": "supabase",
            "state": "fresh" if fresh else "stale",
            "sync_id": state.get("active_sync_id"),
            "synced_at": state.get("synced_at"),
            "expires_at": state.get("expires_at"),
            "observation_count": state.get("observation_count"),
            "source_latest_filed": state.get("source_latest_filed"),
        },
        "fresh": fresh,
    }
