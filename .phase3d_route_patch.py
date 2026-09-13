from pathlib import Path

path = Path('company_drivers.py')
text = path.read_text(encoding='utf-8')

old_import = 'import company_driver_filings\nimport company_driver_history\n'
new_import = 'import company_driver_filings\nimport company_driver_history\nimport company_driver_store\n'
assert old_import in text
text = text.replace(old_import, new_import, 1)

schema_anchor = '''@router.get("/history/schema")\ndef company_driver_history_schema():\n    """Describe the Phase 3B verified historical KPI contract."""\n    return company_driver_history.history_schema()\n\n\n'''
assert schema_anchor in text
storage_route = schema_anchor + '''@router.get("/storage/status")\ndef company_driver_storage_status():\n    """Report Phase 3D persistent Company Driver storage readiness."""\n    return {\n        "phase": "3D",\n        **company_driver_store.storage_status(),\n    }\n\n\n'''
text = text.replace(schema_anchor, storage_route, 1)

old_build = '''        result = company_driver_history.build_verified_history(\n            registry["ticker"],\n            metric,\n            filing_limit=filings,\n            period=period,\n            observation_limit=limit,\n        )\n'''
new_build = '''        result = company_driver_store.get_or_refresh_history(\n            ticker=registry["ticker"],\n            metric=metric,\n            filing_limit=filings,\n            period=period,\n            observation_limit=limit,\n            builder=company_driver_history.build_verified_history,\n        )\n'''
assert old_build in text
text = text.replace(old_build, new_build, 1)

path.write_text(text, encoding='utf-8')
print('PHASE3D_ROUTE_PATCHED')
