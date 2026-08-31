from pathlib import Path
import subprocess

raw = subprocess.check_output(['git', 'show', 'origin/main:app.py'])
old = b'SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")\r\nADMIN_SECRET = os.getenv("ADMIN_SECRET", "admin123")'
new = b'SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")\r\nSUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")\r\nADMIN_SECRET = os.getenv("ADMIN_SECRET", "")'
if old not in raw:
    raise SystemExit('env block not found in main app.py')
raw = raw.replace(old, new, 1)
old2 = b'sethiportfolio.configure_supabase(SUPABASE_URL, SUPABASE_KEY)'
new2 = b'sethiportfolio.configure_supabase(SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_KEY, ADMIN_SECRET)'
if old2 not in raw:
    raise SystemExit('configure call not found in main app.py')
raw = raw.replace(old2, new2, 1)
Path('app.py').write_bytes(raw)
print('restored CRLF app.py with minimal admin config changes')
