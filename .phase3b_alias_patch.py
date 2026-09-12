from pathlib import Path

path = Path('company_driver_filings.py')
text = path.read_text(encoding='utf-8')

anchor = '''_GENERIC_TOKENS = {
    "revenue", "revenues", "income", "operating", "company", "reported", "period",
    "segment", "services", "service", "total", "growth", "margin", "average",
    "payments", "sales", "net", "business", "under", "disclosed", "attributed",
}
'''
replacement = anchor + '''_METRIC_TOKEN_ALIASES = {
    "aws_revenue": ["aws", "amazon", "web", "services"],
    "aws_operating_income": ["aws", "amazon", "web", "services"],
    "google_cloud_revenue": ["google", "cloud"],
    "google_cloud_operating_income": ["google", "cloud"],
    "google_search_revenue": ["google", "search"],
    "youtube_ads_revenue": ["youtube"],
    "data_center_revenue": ["data", "center", "datacenter"],
    "vehicle_deliveries": ["vehicle", "vehicles", "deliveries", "delivery"],
    "energy_storage_deployments": ["energy", "storage", "deployments", "deployed"],
    "reality_labs_revenue": ["reality", "labs"],
    "reality_labs_operating_income": ["reality", "labs"],
    "intelligent_cloud_revenue": ["intelligent", "cloud"],
    "microsoft_cloud_revenue": ["microsoft", "cloud"],
    "payments_volume": ["payment", "payments", "volume"],
    "processed_transactions": ["processed", "transactions", "transaction"],
}
'''
assert anchor in text
text = text.replace(anchor, replacement, 1)

old = '''def metric_tokens(metric: Dict[str, Any]) -> List[str]:
    tokens: List[str] = []
    for source in (metric.get("key"), metric.get("label"), metric.get("category")):
        for token in _tokenise(str(source or "")):
            if len(token) >= 3 and token not in _GENERIC_TOKENS and token not in tokens:
                tokens.append(token)
    return tokens
'''
new = '''def metric_tokens(metric: Dict[str, Any]) -> List[str]:
    tokens: List[str] = []
    key = str(metric.get("key") or "").strip().lower()
    sources = [metric.get("key"), metric.get("label"), metric.get("category")]
    sources.extend(_METRIC_TOKEN_ALIASES.get(key, []))
    for source in sources:
        for token in _tokenise(str(source or "")):
            if len(token) >= 3 and token not in _GENERIC_TOKENS and token not in tokens:
                tokens.append(token)
    return tokens
'''
assert old in text
text = text.replace(old, new, 1)

path.write_text(text, encoding='utf-8')
print('PHASE3B_ALIASES_PATCHED')
