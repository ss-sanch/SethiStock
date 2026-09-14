from pathlib import Path

path = Path('fundamentals_normalizer.py')
text = path.read_text()

text = text.replace('NORMALIZATION_SCHEMA_VERSION = "2b-v1"', 'NORMALIZATION_SCHEMA_VERSION = "2b-v2"', 1)

anchor = '''    "operating_income": {
        "label": "Operating Income",
        "kind": "flow",
        "unit": "USD",
        "candidates": [("us-gaap", "OperatingIncomeLoss")],
    },
}
'''
replacement = '''    "operating_income": {
        "label": "Operating Income",
        "kind": "flow",
        "unit": "USD",
        "candidates": [("us-gaap", "OperatingIncomeLoss")],
    },
    "depreciation_amortization": {
        "label": "Depreciation & Amortization",
        "kind": "flow",
        "unit": "USD",
        "candidates": [
            ("us-gaap", "DepreciationDepletionAndAmortization"),
            ("us-gaap", "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment"),
            ("us-gaap", "DepreciationAndAmortization"),
            ("us-gaap", "Depreciation"),
        ],
    },
}
'''
if anchor not in text:
    raise SystemExit('DIRECT_METRICS anchor not found')
text = text.replace(anchor, replacement, 1)

order_anchor = '''NORMALIZED_METRIC_ORDER = [
    "revenue",
    "net_income",
    "operating_cash_flow",
'''
order_replacement = '''NORMALIZED_METRIC_ORDER = [
    "revenue",
    "net_income",
    "ebitda",
    "operating_cash_flow",
'''
if order_anchor not in text:
    raise SystemExit('metric order anchor not found')
text = text.replace(order_anchor, order_replacement, 1)

derived_anchor = '''DERIVED_METRIC_INFO = {
    "free_cash_flow": {
'''
derived_replacement = '''DERIVED_METRIC_INFO = {
    "ebitda": {
        "label": "EBITDA",
        "kind": "flow",
        "unit": "USD",
        "formula": "operating_income + depreciation_amortization",
    },
    "free_cash_flow": {
'''
if derived_anchor not in text:
    raise SystemExit('derived metric anchor not found')
text = text.replace(derived_anchor, derived_replacement, 1)

fcf_anchor = '''def derive_free_cash_flow(
    operating_cash_flow: Dict[str, Any],
    capex: Dict[str, Any],
    limit: int = 250,
) -> Dict[str, Any]:
'''
if fcf_anchor not in text:
    raise SystemExit('FCF function anchor not found')

ebitda_fn = '''def derive_ebitda(
    operating_income: Dict[str, Any],
    depreciation_amortization: Dict[str, Any],
    limit: int = 250,
) -> Dict[str, Any]:
    operating_by_period = _by_period(operating_income["observations"])
    da_by_period = _by_period(depreciation_amortization["observations"])
    rows = []
    for period, operating_row in operating_by_period.items():
        da_row = da_by_period.get(period)
        if da_row is None:
            continue
        value = float(operating_row["value"]) + abs(float(da_row["value"]))
        if not math.isfinite(value):
            continue
        rows.append(
            _derived_flow_row(
                value=value,
                unit="USD",
                components=[
                    ("operating_income", operating_row),
                    ("depreciation_amortization", da_row),
                ],
                formula=DERIVED_METRIC_INFO["ebitda"]["formula"],
            )
        )
    rows.sort(key=lambda row: (str(row.get("end") or ""), str(row.get("start") or "")))
    if limit > 0 and len(rows) > limit:
        rows = rows[-limit:]
    return _metric_payload(
        metric="ebitda",
        label="EBITDA",
        kind="flow",
        unit="USD",
        observations=rows,
        derived=True,
        formula=DERIVED_METRIC_INFO["ebitda"]["formula"],
    )


'''
text = text.replace(fcf_anchor, ebitda_fn + fcf_anchor, 1)

req_anchor = '''        elif metric == "free_cash_flow":
            required_direct.update({"operating_cash_flow", "capex"})
'''
req_replacement = '''        elif metric == "ebitda":
            required_direct.update({"operating_income", "depreciation_amortization"})
        elif metric == "free_cash_flow":
            required_direct.update({"operating_cash_flow", "capex"})
'''
if req_anchor not in text:
    raise SystemExit('required direct anchor not found')
text = text.replace(req_anchor, req_replacement, 1)

out_anchor = '''        elif metric == "free_cash_flow":
            output[metric] = derive_free_cash_flow(
                direct["operating_cash_flow"], direct["capex"], limit=limit
            )
'''
out_replacement = '''        elif metric == "ebitda":
            output[metric] = derive_ebitda(
                direct["operating_income"], direct["depreciation_amortization"], limit=limit
            )
        elif metric == "free_cash_flow":
            output[metric] = derive_free_cash_flow(
                direct["operating_cash_flow"], direct["capex"], limit=limit
            )
'''
if out_anchor not in text:
    raise SystemExit('output anchor not found')
text = text.replace(out_anchor, out_replacement, 1)

path.write_text(text)
