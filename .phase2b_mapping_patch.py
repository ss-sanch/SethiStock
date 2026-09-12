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
'''    "gross_profit": {
        "label": "Gross Profit",
        "kind": "flow",
        "unit": "USD",
        "candidates": [("us-gaap", "GrossProfit")],
    },
    "operating_income": {
''',
'''    "gross_profit": {
        "label": "Gross Profit",
        "kind": "flow",
        "unit": "USD",
        "candidates": [("us-gaap", "GrossProfit")],
    },
    "cost_of_revenue": {
        "label": "Cost of Revenue",
        "kind": "flow",
        "unit": "USD",
        "candidates": [
            ("us-gaap", "CostOfRevenue"),
            ("us-gaap", "CostOfGoodsAndServicesSold"),
            ("us-gaap", "CostOfGoodsSold"),
        ],
    },
    "operating_income": {
'''
)

replace_once(
'''    "short_term": [
        ("us-gaap", "ShortTermBorrowings"),
        ("us-gaap", "ShortTermDebt"),
    ],
''',
'''    "short_term": [
        ("us-gaap", "ShortTermBorrowings"),
        ("us-gaap", "CommercialPaper"),
        ("us-gaap", "ShortTermDebt"),
    ],
'''
)

replace_once(
'''def derive_margin(
''',
'''def derive_effective_gross_profit(
    gross_profit: Dict[str, Any],
    cost_of_revenue: Dict[str, Any],
    revenue: Dict[str, Any],
    limit: int = 250,
) -> Dict[str, Any]:
    """Prefer reported GrossProfit and fill missing periods from Revenue - Cost."""
    reported_by_period = _by_period(gross_profit["observations"])
    cost_by_period = _by_period(cost_of_revenue["observations"])
    revenue_by_period = _by_period(revenue["observations"])
    rows = list(gross_profit["observations"])

    for period, revenue_row in revenue_by_period.items():
        if period in reported_by_period:
            continue
        cost_row = cost_by_period.get(period)
        if cost_row is None:
            continue
        value = float(revenue_row["value"]) - abs(float(cost_row["value"]))
        if not math.isfinite(value):
            continue
        rows.append(
            _derived_flow_row(
                value=value,
                unit="USD",
                components=[("revenue", revenue_row), ("cost_of_revenue", cost_row)],
                formula="revenue - abs(cost_of_revenue)",
            )
        )

    rows.sort(key=lambda row: (str(row.get("end") or ""), str(row.get("start") or "")))
    if limit > 0 and len(rows) > limit:
        rows = rows[-limit:]
    return _metric_payload(
        metric="gross_profit",
        label="Gross Profit",
        kind="flow",
        unit="USD",
        observations=rows,
        derived=any(bool(row.get("derived")) for row in rows),
        formula="reported GrossProfit; fallback revenue - abs(cost_of_revenue)",
    )


def derive_margin(
'''
)

replace_once(
'''        elif metric == "gross_margin":
            required_direct.update({"gross_profit", "revenue"})
''',
'''        elif metric == "gross_margin":
            required_direct.update({"gross_profit", "cost_of_revenue", "revenue"})
'''
)

replace_once(
'''        elif metric == "gross_margin":
            output[metric] = derive_margin(
                "gross_margin", "gross_profit", direct["gross_profit"], direct["revenue"], limit=limit
            )
''',
'''        elif metric == "gross_margin":
            effective_gross_profit = derive_effective_gross_profit(
                direct["gross_profit"],
                direct["cost_of_revenue"],
                direct["revenue"],
                limit=max(limit, 500),
            )
            output[metric] = derive_margin(
                "gross_margin", "gross_profit", effective_gross_profit, direct["revenue"], limit=limit
            )
'''
)

path.write_text(text)
