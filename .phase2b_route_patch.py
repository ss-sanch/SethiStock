from pathlib import Path

path = Path('sec_fundamentals.py')
text = path.read_text()

old_import = 'from fastapi import APIRouter, HTTPException, Query\n'
new_import = '''from fastapi import APIRouter, HTTPException, Query\n\nfrom fundamentals_normalizer import (\n    NORMALIZATION_SCHEMA_VERSION,\n    NORMALIZED_METRIC_ORDER,\n    build_normalized_fundamentals,\n    get_normalization_schema,\n)\n'''
if old_import not in text:
    raise SystemExit('FastAPI import anchor not found')
text = text.replace(old_import, new_import, 1)

append = r'''

@router.get("/normalization/schema")
def sec_normalization_schema():
    """Describe the stable Phase 2B metric registry and concept priorities."""
    return get_normalization_schema()


@router.get("/{ticker}/fundamentals/normalized")
def sec_normalized_fundamentals(
    ticker: str,
    metrics: Optional[str] = Query(None, max_length=300),
    limit: int = Query(250, ge=1, le=1000),
):
    """Return SEC facts mapped into the stable SethiStock fundamentals schema.

    Raw period dates and filing metadata are preserved. Annual/Quarterly/TTM
    classification is intentionally deferred to Phase 2D.
    """
    requested = None
    if metrics is not None:
        requested = []
        seen = set()
        for raw_metric in metrics.split(","):
            metric = raw_metric.strip().lower()
            if not metric or metric in seen:
                continue
            requested.append(metric)
            seen.add(metric)
        if not requested:
            requested = None

    identity, companyfacts = get_company_facts(ticker)
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
    }
'''

if '@router.get("/normalization/schema")' in text:
    raise SystemExit('Phase 2B routes already present')
text = text.rstrip() + append + '\n'
path.write_text(text)
