from pathlib import Path

path = Path('company_driver_filings.py')
text = path.read_text(encoding='utf-8')

old = '''def score_fact_for_metric(fact: Dict[str, Any], metric: Dict[str, Any]) -> int:
    tokens = metric_tokens(metric)
    if not tokens:
        return 0
    concept_text = " ".join([
        str(fact.get("taxonomy") or ""),
        str(fact.get("concept") or ""),
        str(fact.get("qualified_concept") or ""),
    ]).lower()
    dimension_text = _dimension_text(fact).lower()
    score = 0
    for token in tokens:
        if token in concept_text:
            score += 4
        if token in dimension_text:
            score += 6
    expected_semantics = metric.get("period_semantics")
    if expected_semantics and fact.get("period_semantics") == expected_semantics:
        score += 2
    if fact.get("numeric"):
        score += 1
    if fact.get("taxonomy") and fact.get("taxonomy") not in _STANDARD_PREFIXES:
        score += 2
    return score
'''

new = '''def _accounting_semantic_score(concept_text: str, metric: Dict[str, Any]) -> int:
    """Reward facts whose accounting concept matches the KPI's economic meaning.

    Segment dimensions identify *which business* a fact belongs to, but are not enough
    to identify *what the fact measures*. For example, an AWS-tagged context can carry
    Revenue, CapEx and Assets. This guard prevents a strong AWS dimension match from
    outranking the actual revenue concept for the ``aws_revenue`` KPI.
    """
    key = str(metric.get("key") or "").strip().lower()
    compact = re.sub(r"[^a-z0-9]", "", concept_text.lower())

    if "revenue" in key:
        return 12 if any(term in compact for term in ("revenue", "revenues", "sales")) else -12
    if "operating_income" in key:
        operating = "operating" in compact
        result = any(term in compact for term in ("income", "profit", "loss"))
        return 12 if operating and result else -10
    if "gross_margin" in key or "operating_margin" in key or "net_margin" in key:
        return 8 if "margin" in compact else 0
    if "deliver" in key:
        return 8 if "deliver" in compact else 0
    if "deployment" in key:
        return 8 if "deploy" in compact else 0
    if "transaction" in key:
        return 8 if "transaction" in compact else 0
    if "volume" in key:
        return 8 if "volume" in compact else 0
    if "membership" in key:
        return 8 if any(term in compact for term in ("member", "subscriber")) else 0
    return 0


def score_fact_for_metric(fact: Dict[str, Any], metric: Dict[str, Any]) -> int:
    tokens = metric_tokens(metric)
    if not tokens:
        return 0
    concept_text = " ".join([
        str(fact.get("taxonomy") or ""),
        str(fact.get("concept") or ""),
        str(fact.get("qualified_concept") or ""),
    ]).lower()
    dimension_text = _dimension_text(fact).lower()
    score = _accounting_semantic_score(concept_text, metric)
    for token in tokens:
        if token in concept_text:
            score += 4
        if token in dimension_text:
            score += 6
    expected_semantics = metric.get("period_semantics")
    if expected_semantics and fact.get("period_semantics") == expected_semantics:
        score += 2
    if fact.get("numeric"):
        score += 1
    if fact.get("taxonomy") and fact.get("taxonomy") not in _STANDARD_PREFIXES:
        score += 2
    return score
'''

assert old in text, 'score function anchor missing'
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('PHASE3B_SEMANTIC_SCORING_PATCHED')
