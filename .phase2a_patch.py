from pathlib import Path

path = Path('app.py')
raw = path.read_bytes()
newline = '\r\n' if b'\r\n' in raw else '\n'
text = raw.decode('utf-8').replace('\r\n', '\n')


def replace_once(old, new):
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'Expected one match, found {count}: {old!r}')
    text = text.replace(old, new, 1)


replace_once(
    'import stock_research\nimport sethiportfolio\n',
    'import stock_research\nimport sec_fundamentals\nimport sethiportfolio\n',
)

replace_once(
    'app.include_router(stock_research.router)\nsethiportfolio.configure_supabase',
    'app.include_router(stock_research.router)\napp.include_router(sec_fundamentals.router)\nsethiportfolio.configure_supabase',
)

path.write_bytes(text.replace('\n', newline).encode('utf-8'))
