from pathlib import Path

path = Path('sec_fundamentals.py')
text = path.read_text()

old_import = '''import fundamentals_store\nfrom fundamentals_normalizer import (\n'''
new_import = '''import fundamentals_store\nfrom fundamentals_periods import (\n    PERIOD_ENGINE_VERSION,\n    build_period_view,\n    get_period_engine_schema,\n)\nfrom fundamentals_normalizer import (\n'''
if text.count(old_import) != 1:
    raise SystemExit('import anchor mismatch')
text = text.replace(old_import, new_import, 1)

old_status = '''        "fundamentals_storage": {\n            "configured": fundamentals_store.is_configured(),\n            "ready": fundamentals_store.probe(),\n            "ttl_seconds": fundamentals_store.FUNDAMENTALS_TTL_SECONDS,\n            "sync_limit": fundamentals_store.FUNDAMENTALS_SYNC_LIMIT,\n        },\n'''
new_status = '''        "fundamentals_storage": {\n            "configured": fundamentals_store.is_configured(),\n            "ready": fundamentals_store.probe(),\n            "ttl_seconds": fundamentals_store.FUNDAMENTALS_TTL_SECONDS,\n            "sync_limit": fundamentals_store.FUNDAMENTALS_SYNC_LIMIT,\n        },\n        "period_engine": {\n            "version": PERIOD_ENGINE_VERSION,\n            "periods": ["annual", "quarterly", "ttm"],\n        },\n'''
if text.count(old_status) != 1:
    raise SystemExit('status anchor mismatch')
text = text.replace(old_status, new_status, 1)

append = r'''


@router.get("/periods/schema")
def sec_period_engine_schema():
    """Describe Phase 2D Annual / Quarterly / TTM accounting semantics."""
    return get_period_engine_schema()


@router.get("/{ticker}/fundamentals/series")
def sec_fundamental_series(
    ticker: str,
    period: str = Query("annual", pattern="^(annual|quarterly|ttm)$"),
    metrics: Optional[str] = Query(None, max_length=300),
    limit: int = Query(80, ge=1, le=200),
):
    """Return chart-ready Annual, Quarterly or TTM fundamentals.

    Phase 2D derives these views from the canonical Phase 2B observations stored by
    Phase 2C. The stored data remains raw-period normalized SEC history; this layer
    classifies and derives accounting periods without mutating the database.
    """
    requested = _parse_requested_metrics(metrics)
    unknown = [metric for metric in (requested or []) if metric not in NORMALIZED_METRIC_ORDER]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown normalised metrics: {', '.join(sorted(set(unknown)))}",
        )

    # Always load the full metric set internally. Revenue/Net Income/OCF histories
    # provide the common fiscal calendar and ratio metrics need their source
    # components. The persisted 2C snapshot makes this a database read on warm paths.
    normalized_response = sec_normalized_fundamentals(
        ticker=ticker,
        metrics=None,
        limit=fundamentals_store.FUNDAMENTALS_SYNC_LIMIT,
    )

    try:
        classified = build_period_view(
            normalized_response.get("metrics") or {},
            period=period,
            metrics=requested or NORMALIZED_METRIC_ORDER,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "ticker": normalized_response.get("ticker"),
        "cik": normalized_response.get("cik"),
        "cik_padded": normalized_response.get("cik_padded"),
        "title": normalized_response.get("title"),
        "entity_name": normalized_response.get("entity_name"),
        "schema_version": normalized_response.get("schema_version"),
        **classified,
        "source": normalized_response.get("source") or "SEC EDGAR Company Facts",
        "storage": normalized_response.get("storage"),
    }
'''

if '@router.get("/{ticker}/fundamentals/series")' in text:
    raise SystemExit('series route already present')
text = text.rstrip() + append + '\n'
path.write_text(text)
