"""SEC EDGAR data foundation for SethiStock Phase 2A.

This module intentionally exposes raw, standard-taxonomy SEC Company Facts rather
than trying to normalise accounting concepts. Concept mapping belongs to Phase 2B.

Official SEC sources used:
- https://www.sec.gov/files/company_tickers.json
- https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json

Automated requests are rate-limited below the SEC's published 10 requests/second
fair-access ceiling and use a declared User-Agent. Set SEC_USER_AGENT in Render to
an organisation/contact string for production, e.g. "SethiStock admin@example.com".
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional

import requests
from fastapi import APIRouter, HTTPException, Query

import fundamentals_store
from fundamentals_normalizer import (
    NORMALIZATION_SCHEMA_VERSION,
    NORMALIZED_METRIC_ORDER,
    build_normalized_fundamentals,
    get_normalization_schema,
)

router = APIRouter(prefix="/api/sec", tags=["SEC Fundamentals"])

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "").strip()
SEC_REQUEST_TIMEOUT = float(os.getenv("SEC_REQUEST_TIMEOUT", "12"))
SEC_MIN_REQUEST_INTERVAL = max(0.11, float(os.getenv("SEC_MIN_REQUEST_INTERVAL", "0.125")))
SEC_TICKER_MAP_TTL = int(os.getenv("SEC_TICKER_MAP_TTL", "86400"))
SEC_COMPANYFACTS_TTL = int(os.getenv("SEC_COMPANYFACTS_TTL", "21600"))
SEC_MAX_RETRIES = max(0, int(os.getenv("SEC_MAX_RETRIES", "2")))

_SEC_HEADERS = {
    "User-Agent": SEC_USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json",
}
_SEC_RATE_LOCK = threading.Lock()
_SEC_LAST_REQUEST_AT = 0.0
_SEC_CACHE_LOCK = threading.Lock()
_SEC_CACHE: Dict[str, Dict[str, Any]] = {}


def _sec_user_agent_configured():
    """SEC asks automated clients to declare an organisation and contact email."""
    return bool(SEC_USER_AGENT and "@" in SEC_USER_AGENT and " " in SEC_USER_AGENT)


def _require_sec_user_agent():
    if not _sec_user_agent_configured():
        raise HTTPException(
            status_code=503,
            detail="SEC_USER_AGENT is not configured. Set it to an organisation name and monitored contact email before enabling SEC requests.",
        )


def _cache_get(key: str, ttl_seconds: int):
    now = time.monotonic()
    with _SEC_CACHE_LOCK:
        entry = _SEC_CACHE.get(key)
        if not entry:
            return None
        if now - entry["stored_at"] > ttl_seconds:
            _SEC_CACHE.pop(key, None)
            return None
        return entry["value"]


def _cache_set(key: str, value: Any):
    with _SEC_CACHE_LOCK:
        _SEC_CACHE[key] = {"stored_at": time.monotonic(), "value": value}
    return value


def _wait_for_rate_slot():
    global _SEC_LAST_REQUEST_AT
    with _SEC_RATE_LOCK:
        now = time.monotonic()
        wait_for = SEC_MIN_REQUEST_INTERVAL - (now - _SEC_LAST_REQUEST_AT)
        if wait_for > 0:
            time.sleep(wait_for)
        _SEC_LAST_REQUEST_AT = time.monotonic()


def _sec_get_json(url: str):
    """Fetch SEC JSON with fair-access pacing and bounded retry/backoff."""
    _require_sec_user_agent()
    last_error: Optional[Exception] = None
    for attempt in range(SEC_MAX_RETRIES + 1):
        try:
            _wait_for_rate_slot()
            response = requests.get(url, headers=_SEC_HEADERS, timeout=SEC_REQUEST_TIMEOUT)

            if response.status_code == 404:
                raise HTTPException(status_code=404, detail="SEC resource not found.")

            if response.status_code == 429 or response.status_code >= 500:
                if attempt < SEC_MAX_RETRIES:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = max(float(retry_after), 0.5) if retry_after else 0.75 * (2 ** attempt)
                    except Exception:
                        delay = 0.75 * (2 ** attempt)
                    time.sleep(min(delay, 6.0))
                    continue

            if response.status_code >= 400:
                raise HTTPException(
                    status_code=502,
                    detail=f"SEC request failed with status {response.status_code}.",
                )

            payload = response.json()
            if not isinstance(payload, (dict, list)):
                raise ValueError("Unexpected SEC JSON payload.")
            return payload
        except HTTPException:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < SEC_MAX_RETRIES:
                time.sleep(0.75 * (2 ** attempt))
                continue

    raise HTTPException(status_code=502, detail=f"SEC data temporarily unavailable: {last_error}")


def _normalise_ticker_map(payload):
    """Convert SEC company_tickers.json into ticker -> identity records."""
    records: Dict[str, Dict[str, Any]] = {}
    rows = payload.values() if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        try:
            cik = int(row.get("cik_str"))
        except Exception:
            continue
        if not ticker or cik <= 0:
            continue
        records[ticker] = {
            "ticker": ticker,
            "cik": cik,
            "cik_padded": f"{cik:010d}",
            "title": str(row.get("title") or "").strip() or None,
        }
    return records


def get_ticker_map(force_refresh: bool = False):
    key = "ticker_map"
    if not force_refresh:
        cached = _cache_get(key, SEC_TICKER_MAP_TTL)
        if cached is not None:
            return cached
    payload = _sec_get_json(SEC_TICKERS_URL)
    mapping = _normalise_ticker_map(payload)
    if not mapping:
        raise HTTPException(status_code=502, detail="SEC ticker map was empty or malformed.")
    return _cache_set(key, mapping)


def resolve_sec_identity(ticker: str):
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Ticker is required.")
    identity = get_ticker_map().get(symbol)
    if not identity:
        raise HTTPException(status_code=404, detail=f"No SEC CIK mapping found for {symbol}.")
    return identity


def get_company_facts_by_cik(cik: int, force_refresh: bool = False):
    cik_padded = f"{int(cik):010d}"
    key = f"companyfacts:{cik_padded}"
    if not force_refresh:
        cached = _cache_get(key, SEC_COMPANYFACTS_TTL)
        if cached is not None:
            return cached
    payload = _sec_get_json(SEC_COMPANYFACTS_URL.format(cik=cik_padded))
    if not isinstance(payload, dict) or not isinstance(payload.get("facts"), dict):
        raise HTTPException(status_code=502, detail="SEC Company Facts payload was malformed.")
    return _cache_set(key, payload)


def get_company_facts(ticker: str, force_refresh: bool = False):
    identity = resolve_sec_identity(ticker)
    payload = get_company_facts_by_cik(identity["cik"], force_refresh=force_refresh)
    return identity, payload


def _taxonomy_summary(companyfacts: Dict[str, Any]):
    summary = []
    for taxonomy, concepts in (companyfacts.get("facts") or {}).items():
        if not isinstance(concepts, dict):
            continue
        unit_names = set()
        fact_rows = 0
        for concept in concepts.values():
            if not isinstance(concept, dict):
                continue
            for unit, rows in (concept.get("units") or {}).items():
                unit_names.add(str(unit))
                if isinstance(rows, list):
                    fact_rows += len(rows)
        summary.append(
            {
                "taxonomy": taxonomy,
                "concept_count": len(concepts),
                "fact_count": fact_rows,
                "units": sorted(unit_names),
            }
        )
    return sorted(summary, key=lambda item: item["taxonomy"])


def _concept_payload(companyfacts: Dict[str, Any], taxonomy: str, concept: str, limit: int):
    taxonomies = companyfacts.get("facts") or {}
    taxonomy_payload = taxonomies.get(taxonomy)
    if not isinstance(taxonomy_payload, dict):
        raise HTTPException(status_code=404, detail=f"SEC taxonomy '{taxonomy}' not found.")
    concept_payload = taxonomy_payload.get(concept)
    if not isinstance(concept_payload, dict):
        raise HTTPException(status_code=404, detail=f"SEC concept '{taxonomy}:{concept}' not found.")

    units_out = {}
    for unit, rows in (concept_payload.get("units") or {}).items():
        if not isinstance(rows, list):
            continue
        clean_rows = [row for row in rows if isinstance(row, dict)]
        clean_rows.sort(key=lambda row: (str(row.get("filed") or ""), str(row.get("end") or "")), reverse=True)
        units_out[str(unit)] = clean_rows[:limit]

    return {
        "label": concept_payload.get("label"),
        "description": concept_payload.get("description"),
        "units": units_out,
    }


@router.get("/status")
def sec_status():
    """Expose SEC and Phase 2C storage readiness without leaking secrets."""
    return {
        "configured": _sec_user_agent_configured(),
        "source": "SEC EDGAR Company Facts",
        "max_requests_per_second": round(1.0 / SEC_MIN_REQUEST_INTERVAL, 2),
        "ticker_map_ttl_seconds": SEC_TICKER_MAP_TTL,
        "companyfacts_ttl_seconds": SEC_COMPANYFACTS_TTL,
        "fundamentals_storage": {
            "configured": fundamentals_store.is_configured(),
            "ready": fundamentals_store.probe(),
            "ttl_seconds": fundamentals_store.FUNDAMENTALS_TTL_SECONDS,
            "sync_limit": fundamentals_store.FUNDAMENTALS_SYNC_LIMIT,
        },
    }


@router.get("/storage/status")
def sec_storage_status():
    """Expose Phase 2C persistent fundamentals storage readiness."""
    return {
        "configured": fundamentals_store.is_configured(),
        "ready": fundamentals_store.probe(),
        "observations_table": fundamentals_store.OBSERVATIONS_TABLE,
        "syncs_table": fundamentals_store.SYNCS_TABLE,
        "ttl_seconds": fundamentals_store.FUNDAMENTALS_TTL_SECONDS,
        "sync_limit": fundamentals_store.FUNDAMENTALS_SYNC_LIMIT,
        "schema_version": NORMALIZATION_SCHEMA_VERSION,
    }


@router.get("/{ticker}/overview")
def sec_company_overview(ticker: str):
    """Resolve ticker->CIK and prove Company Facts coverage without returning the huge raw payload."""
    identity, companyfacts = get_company_facts(ticker)
    return {
        **identity,
        "entity_name": companyfacts.get("entityName") or identity.get("title"),
        "sec_cik": companyfacts.get("cik") or identity["cik"],
        "taxonomies": _taxonomy_summary(companyfacts),
        "source": "SEC EDGAR Company Facts",
    }


@router.get("/{ticker}/concept")
def sec_company_concept(
    ticker: str,
    concept: str = Query(..., min_length=1, max_length=160),
    taxonomy: str = Query("us-gaap", min_length=1, max_length=80),
    limit: int = Query(80, ge=1, le=500),
):
    """Return raw SEC facts for one standard taxonomy concept. No 2B normalisation is applied."""
    identity, companyfacts = get_company_facts(ticker)
    payload = _concept_payload(companyfacts, taxonomy=taxonomy, concept=concept, limit=limit)
    return {
        **identity,
        "entity_name": companyfacts.get("entityName") or identity.get("title"),
        "taxonomy": taxonomy,
        "concept": concept,
        **payload,
        "source": "SEC EDGAR Company Facts",
    }


@router.get("/{ticker}/concepts")
def sec_company_concepts(
    ticker: str,
    taxonomy: str = Query("us-gaap", min_length=1, max_length=80),
    q: Optional[str] = Query(None, max_length=100),
    limit: int = Query(250, ge=1, le=1000),
):
    """List available concepts so Phase 2B mappings can be built empirically per issuer."""
    identity, companyfacts = get_company_facts(ticker)
    taxonomy_payload = (companyfacts.get("facts") or {}).get(taxonomy)
    if not isinstance(taxonomy_payload, dict):
        raise HTTPException(status_code=404, detail=f"SEC taxonomy '{taxonomy}' not found.")

    needle = str(q or "").strip().lower()
    concepts = []
    for name, payload in taxonomy_payload.items():
        if not isinstance(payload, dict):
            continue
        label = str(payload.get("label") or "")
        description = str(payload.get("description") or "")
        if needle and needle not in name.lower() and needle not in label.lower() and needle not in description.lower():
            continue
        units = payload.get("units") or {}
        fact_count = sum(len(rows) for rows in units.values() if isinstance(rows, list))
        concepts.append(
            {
                "concept": name,
                "label": label or None,
                "description": description or None,
                "units": sorted(str(unit) for unit in units.keys()),
                "fact_count": fact_count,
            }
        )

    concepts.sort(key=lambda item: item["concept"])
    return {
        **identity,
        "taxonomy": taxonomy,
        "query": q,
        "count": min(len(concepts), limit),
        "concepts": concepts[:limit],
        "source": "SEC EDGAR Company Facts",
    }

@router.get("/normalization/schema")
def sec_normalization_schema():
    """Describe the stable Phase 2B metric registry and concept priorities."""
    return get_normalization_schema()

def _parse_requested_metrics(metrics: Optional[str]):
    if metrics is None:
        return None
    requested = []
    seen = set()
    for raw_metric in metrics.split(","):
        metric = raw_metric.strip().lower()
        if not metric or metric in seen:
            continue
        requested.append(metric)
        seen.add(metric)
    return requested or None


def _live_normalized_response(identity, companyfacts, requested, limit, storage_state):
    try:
        normalized = build_normalized_fundamentals(
            companyfacts,
            metrics=requested,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        **identity,
        "entity_name": companyfacts.get("entityName") or identity.get("title"),
        "schema_version": NORMALIZATION_SCHEMA_VERSION,
        "metric_order": [metric for metric in NORMALIZED_METRIC_ORDER if metric in normalized],
        "metrics": normalized,
        "period_classification": "deferred_to_phase_2d",
        "source": "SEC EDGAR Company Facts",
        "storage": {"backend": "live", "state": storage_state},
    }


def _clean_store_payload(snapshot, state_override=None):
    if snapshot is None:
        return None
    payload = dict(snapshot)
    payload.pop("fresh", None)
    storage = dict(payload.get("storage") or {})
    if state_override:
        storage["state"] = state_override
    payload["storage"] = storage
    return payload


@router.get("/{ticker}/fundamentals/normalized")
def sec_normalized_fundamentals(
    ticker: str,
    metrics: Optional[str] = Query(None, max_length=300),
    limit: int = Query(250, ge=1, le=1000),
):
    """Return normalized SEC fundamentals, preferring the Phase 2C Supabase store.

    A complete new snapshot is staged under an immutable sync ID and only activated
    after every observation is written. Annual/Quarterly/TTM classification remains
    intentionally deferred to Phase 2D.
    """
    requested = _parse_requested_metrics(metrics)
    unknown = [metric for metric in (requested or []) if metric not in NORMALIZED_METRIC_ORDER]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown normalised metrics: {', '.join(sorted(set(unknown)))}",
        )

    symbol = str(ticker or "").strip().upper()
    stale_snapshot = None

    if fundamentals_store.is_configured():
        try:
            snapshot = fundamentals_store.load_snapshot(
                ticker=symbol,
                schema_version=NORMALIZATION_SCHEMA_VERSION,
                metric_order=NORMALIZED_METRIC_ORDER,
                metrics=requested,
                limit=limit,
            )
        except fundamentals_store.FundamentalsStoreError:
            snapshot = None
        if snapshot and snapshot.get("fresh"):
            return _clean_store_payload(snapshot)
        stale_snapshot = snapshot

        # Recheck after taking the per-ticker lock so simultaneous first requests do
        # not both download and persist the same SEC history.
        with fundamentals_store.get_sync_lock(symbol):
            try:
                snapshot = fundamentals_store.load_snapshot(
                    ticker=symbol,
                    schema_version=NORMALIZATION_SCHEMA_VERSION,
                    metric_order=NORMALIZED_METRIC_ORDER,
                    metrics=requested,
                    limit=limit,
                )
            except fundamentals_store.FundamentalsStoreError:
                snapshot = None
            if snapshot and snapshot.get("fresh"):
                return _clean_store_payload(snapshot)
            if snapshot is not None:
                stale_snapshot = snapshot

            try:
                identity, companyfacts = get_company_facts(
                    ticker,
                    force_refresh=bool(stale_snapshot),
                )
            except HTTPException:
                if stale_snapshot is not None:
                    return _clean_store_payload(stale_snapshot, "stale_fallback")
                raise

            try:
                normalized_full = build_normalized_fundamentals(
                    companyfacts,
                    metrics=None,
                    limit=fundamentals_store.FUNDAMENTALS_SYNC_LIMIT,
                )
                fundamentals_store.persist_snapshot(
                    identity=identity,
                    entity_name=companyfacts.get("entityName") or identity.get("title"),
                    normalized=normalized_full,
                    schema_version=NORMALIZATION_SCHEMA_VERSION,
                )
                stored = fundamentals_store.load_snapshot(
                    ticker=identity["ticker"],
                    schema_version=NORMALIZATION_SCHEMA_VERSION,
                    metric_order=NORMALIZED_METRIC_ORDER,
                    metrics=requested,
                    limit=limit,
                )
                if stored is not None:
                    return _clean_store_payload(stored, "refreshed")
            except (fundamentals_store.FundamentalsStoreError, ValueError):
                # Persistence is an optimisation/data-layer upgrade, not a reason to
                # take the SEC endpoint down. Return the freshly normalized live data.
                pass

            return _live_normalized_response(
                identity,
                companyfacts,
                requested,
                limit,
                "persist_failed",
            )

    identity, companyfacts = get_company_facts(ticker)
    return _live_normalized_response(
        identity,
        companyfacts,
        requested,
        limit,
        "unconfigured",
    )

