from pathlib import Path

path = Path('company_drivers.py')
text = path.read_text(encoding='utf-8')

old_import = 'import company_driver_filings\n'
new_import = 'import company_driver_filings\nimport company_driver_history\n'
assert old_import in text
text = text.replace(old_import, new_import, 1)

anchor = '''@router.get("/{ticker}")\ndef company_driver_registry(ticker: str):\n'''
assert anchor in text
insert = '''@router.get("/history/schema")\ndef company_driver_history_schema():\n    """Describe the Phase 3B verified historical KPI contract."""\n    return company_driver_history.history_schema()\n\n\n@router.get("/{ticker}/history/{metric_key}")\ndef company_driver_metric_history(\n    ticker: str,\n    metric_key: str,\n    filings: int = Query(16, ge=1, le=32),\n    period: str = Query("quarterly", pattern="^(quarterly|annual|reported)$"),\n    limit: int = Query(40, ge=1, le=100),\n):\n    """Return a verified, deduplicated historical series for one operating KPI."""\n    try:\n        registry, metric = _driver_metric_definition(ticker, metric_key)\n    except KeyError as exc:\n        raise HTTPException(\n            status_code=404,\n            detail=f"Company Drivers registry is not yet available for {str(ticker).strip().upper()}.",\n        ) from exc\n    try:\n        result = company_driver_history.build_verified_history(\n            registry["ticker"],\n            metric,\n            filing_limit=filings,\n            period=period,\n            observation_limit=limit,\n        )\n    except ValueError as exc:\n        raise HTTPException(status_code=400, detail=str(exc)) from exc\n    result.update({\n        "company": registry["company"],\n        "theme": registry["theme"],\n        "registry_schema_version": DRIVER_SCHEMA_VERSION,\n        "metric_definition": metric,\n    })\n    return result\n\n\n'''
text = text.replace(anchor, insert + anchor, 1)
path.write_text(text, encoding='utf-8')
print('PHASE3B_HISTORY_ROUTES_PATCHED')
