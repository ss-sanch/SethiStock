from pathlib import Path

path = Path('fundamentals_normalizer.py')
text = path.read_text()


def replace_once(old, new):
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'Expected one match, found {count}: {old[:120]!r}')
    text = text.replace(old, new, 1)


replace_once(
'''def _derived_flow_row(
''',
'''def _component_reference(metric: str, row: Dict[str, Any]) -> Dict[str, Any]:
    reference = {
        "metric": metric,
        "value": row.get("value"),
        "taxonomy": row.get("taxonomy"),
        "concept": row.get("concept"),
        "accn": row.get("accn"),
    }
    if row.get("formula"):
        reference["formula"] = row.get("formula")
    if row.get("components"):
        reference["components"] = row.get("components")
    return reference


def _collect_source_concepts(row: Dict[str, Any]) -> set[str]:
    concepts: set[str] = set()
    taxonomy = row.get("taxonomy")
    concept = row.get("concept")
    if taxonomy and concept:
        concepts.add(f"{taxonomy}:{concept}")
    for component in row.get("components") or []:
        if isinstance(component, dict):
            concepts.update(_collect_source_concepts(component))
    return concepts


def _derived_flow_row(
'''
)

replace_once(
'''        "components": [
            {
                "metric": metric,
                "value": row.get("value"),
                "taxonomy": row.get("taxonomy"),
                "concept": row.get("concept"),
                "accn": row.get("accn"),
            }
            for metric, row in components
        ],
''',
'''        "components": [_component_reference(metric, row) for metric, row in components],
'''
)

replace_once(
'''    concepts_used = sorted(
        {
            f"{row.get('taxonomy')}:{row.get('concept')}"
            for row in observations
            if row.get("taxonomy") and row.get("concept")
        }
    )
''',
'''    concepts_used = sorted(
        {
            concept
            for row in observations
            for concept in _collect_source_concepts(row)
        }
    )
'''
)

path.write_text(text)
