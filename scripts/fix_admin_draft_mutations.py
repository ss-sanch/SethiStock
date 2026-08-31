from pathlib import Path
p=Path('sethiportfolio.py')
s=p.read_text(encoding='utf-8')
old='''    existing = _supabase_get(\n        "portfolio_journal_entries",\n        {"select": "id,published_at", "id": f"eq.{journal_id}", "portfolio_id": f"eq.{portfolio['id']}", "limit": "1"},\n    )\n'''
new='''    existing = _supabase_get(\n        "portfolio_journal_entries",\n        {"select": "id,published_at", "id": f"eq.{journal_id}", "portfolio_id": f"eq.{portfolio['id']}", "limit": "1"},\n        admin=True,\n    )\n'''
if old not in s: raise SystemExit('update lookup block missing')
s=s.replace(old,new,1)
old='''    existing = _supabase_get(\n        "portfolio_journal_entries",\n        {"select": "id,title", "id": f"eq.{journal_id}", "portfolio_id": f"eq.{portfolio['id']}", "limit": "1"},\n    )\n'''
new='''    existing = _supabase_get(\n        "portfolio_journal_entries",\n        {"select": "id,title", "id": f"eq.{journal_id}", "portfolio_id": f"eq.{portfolio['id']}", "limit": "1"},\n        admin=True,\n    )\n'''
if old not in s: raise SystemExit('delete lookup block missing')
s=s.replace(old,new,1)
p.write_text(s,encoding='utf-8')
print('admin draft mutation lookups patched')
