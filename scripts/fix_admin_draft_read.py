from pathlib import Path
p=Path('sethiportfolio.py')
s=p.read_text(encoding='utf-8')
old='''def _supabase_get(table: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:\n    try:\n        response = requests.get(\n            f"{SUPABASE_URL}/rest/v1/{table}",\n            headers=_headers(),\n            params=params,\n            timeout=12,\n        )\n'''
new='''def _supabase_get(table: str, params: Dict[str, Any], admin: bool = False) -> List[Dict[str, Any]]:\n    try:\n        response = requests.get(\n            f"{SUPABASE_URL}/rest/v1/{table}",\n            headers=_headers(write=admin),\n            params=params,\n            timeout=12,\n        )\n'''
if old not in s: raise SystemExit('supabase get helper block missing')
s=s.replace(old,new,1)
old='''    rows = _supabase_get(\n        "portfolio_journal_entries",\n        {\n            "select": "id,slug,title,summary,body,category,effective_date,published_at,related_transaction_id,is_published,created_at,updated_at",\n            "portfolio_id": f"eq.{portfolio['id']}",\n            "order": "effective_date.desc,created_at.desc",\n            "limit": "100",\n        },\n    )\n'''
new='''    rows = _supabase_get(\n        "portfolio_journal_entries",\n        {\n            "select": "id,slug,title,summary,body,category,effective_date,published_at,related_transaction_id,is_published,created_at,updated_at",\n            "portfolio_id": f"eq.{portfolio['id']}",\n            "order": "effective_date.desc,created_at.desc",\n            "limit": "100",\n        },\n        admin=True,\n    )\n'''
if old not in s: raise SystemExit('admin journal read block missing')
s=s.replace(old,new,1)
p.write_text(s,encoding='utf-8')
print('admin draft read patched')
