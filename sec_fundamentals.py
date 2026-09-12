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
    """Expose SEC readiness without leaking the configured contact address."""
    return {
        "configured": _sec_user_agent_configured(),
        "source": "SEC EDGAR Company Facts",
        "max_requests_per_second": round(1.0 / SEC_MIN_REQUEST_INTERVAL, 2),
        "ticker_map_ttl_seconds": SEC_TICKER_MAP_TTL,
        "companyfacts_ttl_seconds": SEC_COMPANYFACTS_TTL,
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
