from pathlib import Path

path = Path('app.py')
data = path.read_bytes()

import_anchor = b'import sec_fundamentals\r\n'
import_line = b'import sec_fundamentals\r\nimport company_drivers\r\n'
if b'import company_drivers\r\n' not in data:
    if import_anchor not in data:
        raise AssertionError('sec_fundamentals import anchor missing')
    data = data.replace(import_anchor, import_line, 1)

router_anchor = b'app.include_router(sec_fundamentals.router)\r\n'
router_line = b'app.include_router(sec_fundamentals.router)\r\napp.include_router(company_drivers.router)\r\n'
if b'app.include_router(company_drivers.router)\r\n' not in data:
    if router_anchor not in data:
        raise AssertionError('sec_fundamentals router anchor missing')
    data = data.replace(router_anchor, router_line, 1)

if b'\r\n' not in data or data.count(b'\n') != data.count(b'\r\n'):
    raise AssertionError('app.py CRLF line endings were not preserved')

path.write_bytes(data)
print('PHASE3A_APP_WIRED')
