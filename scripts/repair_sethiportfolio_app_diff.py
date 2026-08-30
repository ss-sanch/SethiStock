from pathlib import Path
import subprocess

subprocess.run(['git', 'checkout', 'origin/main', '--', 'app.py'], check=True)
p = Path('app.py')
b = p.read_bytes()
newline = b'\r\n' if b'\r\n' in b else b'\n'

import_marker = b'import market_risk_lab' + newline
if b'import sethiportfolio' not in b:
    if import_marker not in b:
        raise SystemExit('Import marker not found')
    b = b.replace(import_marker, import_marker + b'import sethiportfolio' + newline, 1)

router_marker = b'app.include_router(market_risk_lab.router)' + newline
if b'app.include_router(sethiportfolio.router)' not in b:
    if router_marker not in b:
        raise SystemExit('Router marker not found')
    replacement = (
        router_marker
        + b'sethiportfolio.configure_supabase(SUPABASE_URL, SUPABASE_KEY)' + newline
        + b'app.include_router(sethiportfolio.router)' + newline
    )
    b = b.replace(router_marker, replacement, 1)

p.write_bytes(b)
