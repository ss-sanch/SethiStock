from pathlib import Path
import ast

path = Path('sethiportfolio.py')
text = path.read_text()

tree = ast.parse(text)
removals = []
for name in ('_portfolio_fx_analytics', 'get_fx_analytics'):
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    if len(nodes) != 2:
        raise SystemExit(f'Expected exactly two {name} definitions before cleanup, found {len(nodes)}')
    stale = nodes[1]
    start = min([stale.lineno] + [decorator.lineno for decorator in stale.decorator_list])
    end = stale.end_lineno
    removals.append((start, end, name))

lines = text.splitlines(keepends=True)
for start, end, name in sorted(removals, reverse=True):
    del lines[start - 1:end]
text = ''.join(lines)

clean_tree = ast.parse(text)
for name in ('_portfolio_fx_analytics', 'get_fx_analytics'):
    nodes = [node for node in clean_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    if len(nodes) != 1:
        raise SystemExit(f'Expected exactly one {name} definition after cleanup, found {len(nodes)}')

if 'FX analytics reconstructed a negative' in text:
    raise SystemExit('Stale transaction-by-day FX engine still present')
if 'weighted_average_entry_fx_open_inventory_decomposition' not in text:
    raise SystemExit('Intended open-book FX engine was removed unexpectedly')

path.write_text(text)
