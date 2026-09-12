from pathlib import Path

path = Path('company_drivers.py')
text = path.read_text(encoding='utf-8')

text = text.replace(
    'from fastapi import APIRouter, HTTPException\n',
    'from fastapi import APIRouter, HTTPException, Query\n\nimport company_driver_filings\n',
    1,
)

anchor = '''@router.get("/{ticker}")\ndef company_driver_registry(ticker: str):\n'''
assert anchor in text, 'dynamic registry route anchor missing'

insert = '''def _driver_metric_definition(ticker: str, metric_key: str):\n    registry = get_company_driver_registry(ticker)\n    key = str(metric_key or "").strip().lower()\n    for metric in registry["metrics"]:\n        if metric["key"].lower() == key:\n            return registry, metric\n    raise HTTPException(\n        status_code=404,\n        detail=f"Company Driver metric '{metric_key}' is not registered for {registry['ticker']}.",\n    )\n\n\n@router.get("/extraction/schema")\ndef company_driver_extraction_schema():\n    """Describe the Phase 3B filing extraction/discovery contract."""\n    return company_driver_filings.extraction_schema()\n\n\n@router.get("/{ticker}/filings")\ndef company_driver_filings_route(\n    ticker: str,\n    limit: int = Query(6, ge=1, le=12),\n):\n    """List recent periodic filings available to the Company Drivers extractor."""\n    try:\n        registry = get_company_driver_registry(ticker)\n    except KeyError as exc:\n        raise HTTPException(\n            status_code=404,\n            detail=f"Company Drivers registry is not yet available for {str(ticker).strip().upper()}.",\n        ) from exc\n    filings = company_driver_filings.recent_periodic_filings(registry["ticker"], limit=limit)\n    return {\n        "ticker": registry["ticker"],\n        "company": registry["company"],\n        "schema_version": DRIVER_SCHEMA_VERSION,\n        "extraction_version": company_driver_filings.DRIVER_EXTRACTION_VERSION,\n        "count": len(filings),\n        "filings": filings,\n    }\n\n\n@router.get("/{ticker}/discover/{metric_key}")\ndef company_driver_discover_metric(\n    ticker: str,\n    metric_key: str,\n    filings: int = Query(4, ge=1, le=12),\n    limit: int = Query(80, ge=1, le=500),\n):\n    """Return sourced Inline XBRL candidates for one registered operating KPI."""\n    try:\n        registry, metric = _driver_metric_definition(ticker, metric_key)\n    except KeyError as exc:\n        raise HTTPException(\n            status_code=404,\n            detail=f"Company Drivers registry is not yet available for {str(ticker).strip().upper()}.",\n        ) from exc\n    result = company_driver_filings.discover_metric_candidates(\n        registry["ticker"],\n        metric,\n        filing_limit=filings,\n        candidate_limit=limit,\n    )\n    result.update({\n        "company": registry["company"],\n        "theme": registry["theme"],\n        "registry_schema_version": DRIVER_SCHEMA_VERSION,\n        "metric_definition": metric,\n    })\n    return result\n\n\n'''

text = text.replace(anchor, insert + anchor, 1)
path.write_text(text, encoding='utf-8')
print('PHASE3B_ROUTES_PATCHED')
