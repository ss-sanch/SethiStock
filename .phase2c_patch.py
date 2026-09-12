from pathlib import Path

path = Path('sec_fundamentals.py')
text = path.read_text()

old_import = '''from fastapi import APIRouter, HTTPException, Query\n\nfrom fundamentals_normalizer import (\n'''
new_import = '''from fastapi import APIRouter, HTTPException, Query\n\nimport fundamentals_store\nfrom fundamentals_normalizer import (\n'''
if old_import not in text:
    raise SystemExit('import anchor not found')
text = text.replace(old_import, new_import, 1)

old_status = '''@router.get("/status")\ndef sec_status():\n    """Expose SEC readiness without leaking the configured contact address."""\n    return {\n        "configured": _sec_user_agent_configured(),\n        "source": "SEC EDGAR Company Facts",\n        "max_requests_per_second": round(1.0 / SEC_MIN_REQUEST_INTERVAL, 2),\n        "ticker_map_ttl_seconds": SEC_TICKER_MAP_TTL,\n        "companyfacts_ttl_seconds": SEC_COMPANYFACTS_TTL,\n    }\n'''
new_status = '''@router.get("/status")\ndef sec_status():\n    """Expose SEC and Phase 2C storage readiness without leaking secrets."""\n    return {\n        "configured": _sec_user_agent_configured(),\n        "source": "SEC EDGAR Company Facts",\n        "max_requests_per_second": round(1.0 / SEC_MIN_REQUEST_INTERVAL, 2),\n        "ticker_map_ttl_seconds": SEC_TICKER_MAP_TTL,\n        "companyfacts_ttl_seconds": SEC_COMPANYFACTS_TTL,\n        "fundamentals_storage": {\n            "configured": fundamentals_store.is_configured(),\n            "ready": fundamentals_store.probe(),\n            "ttl_seconds": fundamentals_store.FUNDAMENTALS_TTL_SECONDS,\n            "sync_limit": fundamentals_store.FUNDAMENTALS_SYNC_LIMIT,\n        },\n    }\n\n\n@router.get("/storage/status")\ndef sec_storage_status():\n    """Expose Phase 2C persistent fundamentals storage readiness."""\n    return {\n        "configured": fundamentals_store.is_configured(),\n        "ready": fundamentals_store.probe(),\n        "observations_table": fundamentals_store.OBSERVATIONS_TABLE,\n        "syncs_table": fundamentals_store.SYNCS_TABLE,\n        "ttl_seconds": fundamentals_store.FUNDAMENTALS_TTL_SECONDS,\n        "sync_limit": fundamentals_store.FUNDAMENTALS_SYNC_LIMIT,\n        "schema_version": NORMALIZATION_SCHEMA_VERSION,\n    }\n'''
if old_status not in text:
    raise SystemExit('status anchor not found')
text = text.replace(old_status, new_status, 1)

start = text.index('@router.get("/{ticker}/fundamentals/normalized")')
text = text[:start].rstrip() + "\n\n" + r'''
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
'''.lstrip('\n') + '\n'
path.write_text(text)
