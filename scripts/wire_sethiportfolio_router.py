from pathlib import Path

p = Path('app.py')
s = p.read_text(encoding='utf-8')

if 'import sethiportfolio' not in s:
    s = s.replace('import market_risk_lab\n', 'import market_risk_lab\nimport sethiportfolio\n', 1)

marker = 'app.include_router(market_risk_lab.router)\n'
replacement = '''app.include_router(market_risk_lab.router)\nsethiportfolio.configure_supabase(SUPABASE_URL, SUPABASE_KEY)\napp.include_router(sethiportfolio.router)\n'''
if 'app.include_router(sethiportfolio.router)' not in s:
    if marker not in s:
        raise SystemExit('FastAPI router marker not found')
    s = s.replace(marker, replacement, 1)

p.write_text(s, encoding='utf-8')
