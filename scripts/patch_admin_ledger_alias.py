from pathlib import Path

# Trigger the patch workflow after the workflow file itself exists on this branch.
p = Path('sethiportfolio.py')
s = p.read_text(encoding='utf-8')

old_health = '''@router.get("/admin/health")\ndef admin_health(x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret")) -> Dict[str, Any]:\n    _require_admin(x_admin_secret)\n    return {"authenticated": True, "writes_configured": bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)}\n'''
new_health = '''@router.get("/admin/health")\ndef admin_health(x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret")) -> Dict[str, Any]:\n    _require_admin(x_admin_secret)\n    return {\n        "authenticated": True,\n        "writes_configured": bool(SUPABASE_URL and SUPABASE_SERVICE_KEY),\n        "admin_ledger": True,\n        "allocation_corrections": True,\n    }\n'''
if old_health not in s:
    raise SystemExit('admin health marker missing')
s = s.replace(old_health, new_health, 1)

old_route = '''@router.get("/admin/{slug}/transactions")\ndef get_admin_transactions(slug: str, x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret")) -> Dict[str, Any]:\n'''
new_route = '''@router.get("/admin/{slug}/ledger")\n@router.get("/admin/{slug}/transactions")\ndef get_admin_transactions(slug: str, x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret")) -> Dict[str, Any]:\n'''
if old_route not in s:
    raise SystemExit('admin transactions marker missing')
s = s.replace(old_route, new_route, 1)

p.write_text(s, encoding='utf-8')
