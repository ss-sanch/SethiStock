from pathlib import Path

# ---------- filing-table hardening ----------
tp=Path('company_driver_tables.py')
t=tp.read_text(encoding='utf-8')
old='''def _row_cells(row: Any) -> List[str]:\n    return [_clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]\n'''
new=old+'''\n\ndef _label_index(cells: List[str], phrase: str) -> Optional[int]:\n    target=phrase.lower()\n    return next((i for i, cell in enumerate(cells) if target in cell.lower()), None)\n'''
assert old in t
t=t.replace(old,new,1)

t=t.replace('''        if not cells or "visa processed transactions" not in cells[0].lower():\n            continue\n        nums = [value for value in (_number(cell) for cell in cells[1:]) if value is not None]\n''','''        label_index=_label_index(cells, "visa processed transactions")\n        if label_index is None:\n            continue\n        nums = [value for value in (_number(cell) for cell in cells[label_index + 1:]) if value is not None]\n''',1)

t=t.replace('''        label = cells[0].lower()\n        table_text = _clean(table.get_text(" ", strip=True)).lower()\n        if "standardized" not in table_text:\n            continue\n        if "capital ratio" not in label or not any(term in label for term in patterns):\n            continue\n        nums = [value for value in (_number(cell) for cell in cells[1:]) if value is not None]\n''','''        label_index=next((i for i, cell in enumerate(cells) if "capital ratio" in cell.lower() and any(term in cell.lower() for term in patterns)), None)\n        table_text = _clean(table.get_text(" ", strip=True)).lower()\n        if label_index is None or "standardized" not in table_text:\n            continue\n        nums = [value for value in (_number(cell) for cell in cells[label_index + 1:]) if value is not None]\n''',1)

t=t.replace('''        if not cells or "paid memberships at end of period" not in cells[0].lower():\n            continue\n        table_text = _clean(table.get_text(" ", strip=True)).lower()\n        region = next((key for key, tokens in region_tokens.items() if any(token in table_text for token in tokens)), None)\n        if not region:\n            continue\n        nums = [value for value in (_number(cell) for cell in cells[1:]) if value is not None]\n''','''        label_index=_label_index(cells, "paid memberships at end of period")\n        if label_index is None:\n            continue\n        table_text = _clean(table.get_text(" ", strip=True)).lower()\n        region = next((key for key, tokens in region_tokens.items() if any(token in table_text for token in tokens)), None)\n        if not region:\n            previous=table.find_previous(string=re.compile(r"United States and Canada|UCAN|Europe, Middle East|EMEA|Latin America|LATAM|Asia-Pacific|APAC", re.I))\n            context=(table_text + " " + _clean(previous)).lower()\n            region = next((key for key, tokens in region_tokens.items() if any(token in context for token in tokens)), None)\n        if not region:\n            continue\n        nums = [value for value in (_number(cell) for cell in cells[label_index + 1:]) if value is not None]\n''',1)
tp.write_text(t,encoding='utf-8')

# ---------- history expansion ----------
hp=Path('company_driver_history.py')
h=hp.read_text(encoding='utf-8')
h=h.replace('import company_driver_filings\n','import company_driver_filings\nimport company_driver_tables\n',1)

# Apple Services: verified directly from the filed ServiceMember fact.
anchor='''    ("AAPL", "iphone_revenue"): {\n        "concept_any": ["revenue", "revenues", "sales"],\n        "dimension_any": ["iphonemember"],\n        "verified_example": "IPhoneMember",\n    },\n'''
addition=anchor+'''    ("AAPL", "services_revenue"): {\n        "concept_any": ["revenue", "revenues", "sales"],\n        "dimension_any": ["servicemember"],\n        "verified_example": "us-gaap:ServiceMember",\n    },\n'''
assert anchor in h
h=h.replace(anchor,addition,1)

# Preserve NVIDIA Gaming/Automotive as historical-only after the presentation change.
anchor='''    ("NVDA", "data_center_revenue"): {\n        "concept_any": ["revenue", "revenues", "sales"],\n        "dimension_any": ["datacentermember"],\n        "verified_example": "DataCenterMember",\n    },\n'''
addition=anchor+'''    ("NVDA", "gaming_revenue"): {\n        "concept_any": ["revenue", "revenues", "sales"],\n        "dimension_any": ["gamingmember"],\n        "verified_example": "GamingMember",\n        "coverage_state": "verified_sec_historical",\n        "historical_end": "2026-01-25",\n    },\n    ("NVDA", "automotive_revenue"): {\n        "concept_any": ["revenue", "revenues", "sales"],\n        "dimension_any": ["automotivemember"],\n        "verified_example": "AutomotiveMember",\n        "coverage_state": "verified_sec_historical",\n        "historical_end": "2026-01-25",\n    },\n'''
assert anchor in h
h=h.replace(anchor,addition,1)

# Direct rule filters can explicitly require consolidated/no-dimension facts.
needle='''    if dimension_any and not any(term in dimensions for term in dimension_any):\n        return False\n'''
replace=needle+'''    if rule.get("require_no_dimensions") and fact.get("dimensions"):\n        return False\n'''
assert needle in h
h=h.replace(needle,replace,1)

# Add derived ratio rules before SOURCE_LIMITED_METRICS.
marker='''\n\nSOURCE_LIMITED_METRICS: Dict[Tuple[str, str], Dict[str, str]] = {\n'''
derived='''\n\nDERIVED_RATIO_RULES: Dict[Tuple[str, str], Dict[str, Any]] = {\n    ("NVDA", "gross_margin"): {\n        "numerator": {"concept_any": ["grossprofit"], "require_no_dimensions": True},\n        "denominator": {"concept_any": ["revenue", "revenues", "sales"], "require_no_dimensions": True},\n        "formula": "Gross Profit / Revenue * 100",\n    },\n    ("TSLA", "automotive_gross_margin"): {\n        "numerator": {"concept_any": ["grossprofit"], "dimension_any": ["automotivesegmentmember"], "dimension_none": ["productmember", "serviceothermember"]},\n        "denominator": {"concept_any": ["revenue", "revenues", "sales"], "dimension_any": ["automotivesegmentmember"], "dimension_none": ["productmember", "serviceothermember"]},\n        "formula": "Automotive Gross Profit / Automotive Revenue * 100",\n    },\n}\n'''
assert marker in h
h=h.replace(marker,derived+marker,1)

# Coverage prioritises implemented direct/derived/table sources over descriptive fallbacks.
old='''def metric_coverage(ticker: str, metric_key: str) -> Dict[str, Any]:\n    key=(str(ticker or "").strip().upper(), str(metric_key or "").strip().lower())\n    rule=VERIFIED_EXTRACTION_RULES.get(key)\n    if rule:\n        return {\n            "state": "verified_sec_history",\n            "verified": True,\n            "history_version": DRIVER_HISTORY_VERSION,\n            "extraction_rule": rule,\n        }\n    limited=SOURCE_LIMITED_METRICS.get(key)\n'''
new='''def metric_coverage(ticker: str, metric_key: str) -> Dict[str, Any]:\n    key=(str(ticker or "").strip().upper(), str(metric_key or "").strip().lower())\n    rule=VERIFIED_EXTRACTION_RULES.get(key)\n    if rule:\n        return {\n            "state": rule.get("coverage_state", "verified_sec_history"),\n            "verified": True,\n            "history_version": DRIVER_HISTORY_VERSION,\n            "extraction_rule": rule,\n        }\n    ratio_rule=DERIVED_RATIO_RULES.get(key)\n    if ratio_rule:\n        return {\n            "state": "verified_derived_sec_history",\n            "verified": True,\n            "history_version": DRIVER_HISTORY_VERSION,\n            "extraction_rule": ratio_rule,\n        }\n    table_rule=company_driver_tables.get_table_rule(*key)\n    if table_rule:\n        return {\n            "state": table_rule.get("source_state", "verified_filing_table_history"),\n            "verified": True,\n            "history_version": company_driver_tables.TABLE_HISTORY_VERSION,\n            "extraction_rule": table_rule,\n        }\n    limited=SOURCE_LIMITED_METRICS.get(key)\n'''
assert old in h
h=h.replace(old,new,1)

# Derived-ratio history helper inserted before build_verified_history.
anchor='''\n\ndef build_verified_history(\n'''
helper=r'''


def _build_derived_ratio_history(
    ticker: str,
    metric: Dict[str, Any],
    rule: Dict[str, Any],
    filing_limit: int,
    period: str,
    observation_limit: int,
) -> Dict[str, Any]:
    filings=company_driver_filings.recent_periodic_filings(
        ticker, limit=max(1,min(int(filing_limit),32)), forms=("10-K","10-Q","10-K/A","10-Q/A")
    )
    component_rows={"numerator": [], "denominator": []}
    filing_summaries=[]
    component_metric={"period_semantics":"duration","value_kind":"flow"}
    for filing in filings:
        if filing.get("is_inline_xbrl") is False:
            continue
        parsed=company_driver_filings.get_parsed_filing(filing)
        counts={"numerator":0,"denominator":0}
        for name in ("numerator","denominator"):
            component_rule=rule[name]
            for fact in parsed.get("facts") or []:
                if not isinstance(fact,dict) or not _fact_matches_rule(fact,component_metric,component_rule):
                    continue
                row=dict(fact)
                row["score"]=1
                if classify_period(row)=="unknown":
                    continue
                component_rows[name].append(row)
                counts[name]+=1
        filing_summaries.append({
            "form":filing.get("form"),"filing_date":filing.get("filing_date"),
            "report_date":filing.get("report_date"),"accession":filing.get("accession"),
            "source_url":filing.get("source_url"),"component_matches":counts,
        })

    selected={}
    period_key=str(period or "quarterly").strip().lower()
    for name in ("numerator","denominator"):
        reported=_dedupe_reported_facts(component_rows[name])
        q4=_derive_q4(reported,component_metric)
        if period_key=="quarterly":
            series=_merge_quarterly(reported,q4)
        elif period_key=="annual":
            series=[row for row in reported if row.get("period_type")=="annual"]
        elif period_key=="reported":
            series=list(reported)
        else:
            raise ValueError("period must be one of: quarterly, annual, reported")
        selected[name]={str(row.get("end") or row.get("instant") or ""):row for row in series if row.get("end") or row.get("instant")}

    observations=[]
    for period_end in sorted(set(selected["numerator"]) & set(selected["denominator"])):
        num=selected["numerator"][period_end]
        den=selected["denominator"][period_end]
        try:
            denominator=float(den["value"])
            value=float(num["value"])/denominator*100.0
        except Exception:
            continue
        if denominator==0:
            continue
        filing_date=max(str(num.get("filing_date") or ""),str(den.get("filing_date") or "")) or None
        observations.append({
            "period_type":num.get("period_type") or den.get("period_type"),
            "start":num.get("start") or den.get("start"),"end":period_end,"instant":None,
            "value":value,"unit_ref":"percent","qualified_concept":"derived_ratio",
            "dimensions":num.get("dimensions") or den.get("dimensions") or [],
            "form":num.get("form") or den.get("form"),"filing_date":filing_date,
            "report_date":num.get("report_date") or den.get("report_date"),
            "accession":num.get("accession") or den.get("accession"),
            "source_url":num.get("source_url") or den.get("source_url"),
            "extraction_method":"derived_ratio_from_sec_facts","derived":True,
            "derivation":{
                "formula":rule.get("formula"),
                "numerator":{"value":num.get("value"),"concept":num.get("qualified_concept"),"accession":num.get("accession"),"source_url":num.get("source_url")},
                "denominator":{"value":den.get("value"),"concept":den.get("qualified_concept"),"accession":den.get("accession"),"source_url":den.get("source_url")},
            },
        })
    observations=observations[-max(1,min(int(observation_limit),100)):]
    dates=[row["end"] for row in observations if row.get("end")]
    return {
        "history_version":DRIVER_HISTORY_VERSION,"ticker":ticker,"metric":metric.get("key"),
        "metric_label":metric.get("label"),"period":period_key,"data_state":"verified_history",
        "verified":True,"source_mode":"derived_sec_ratio","verified_rule":rule,
        "filings_checked":filing_summaries,"observation_count":len(observations),
        "coverage":{"start":min(dates) if dates else None,"end":max(dates) if dates else None},
        "observations":observations,
        "policies":{"missing_periods":"Never interpolated.","provenance":"Both SEC source components are retained for every ratio observation."},
    }
'''
assert anchor in h
h=h.replace(anchor,helper+anchor,1)

# Dispatch table / derived rules before direct-rule fallback.
old='''    rule = get_verified_rule(symbol, metric_key)\n    if not rule:\n        return {\n'''
new='''    table_rule=company_driver_tables.get_table_rule(symbol,metric_key)\n    if table_rule:\n        return company_driver_tables.build_table_history(symbol,metric,filing_limit,period,observation_limit)\n    ratio_rule=DERIVED_RATIO_RULES.get((symbol,metric_key))\n    if ratio_rule:\n        return _build_derived_ratio_history(symbol,metric,ratio_rule,filing_limit,period,observation_limit)\n    rule = get_verified_rule(symbol, metric_key)\n    if not rule:\n        return {\n'''
assert old in h
h=h.replace(old,new,1)

# Schema publishes all trusted source modes.
h=h.replace('''        "source": "SEC EDGAR Inline XBRL primary filings",''','''        "source": "SEC EDGAR Inline XBRL primary filings and verified filing tables",''',1)
h=h.replace('''        "verified_rules": [\n            {"ticker": ticker, "metric": metric, **rule}\n            for (ticker, metric), rule in sorted(VERIFIED_EXTRACTION_RULES.items())\n        ],''','''        "verified_rules": [\n            {"ticker": ticker, "metric": metric, "source_mode":"inline_xbrl", **rule}\n            for (ticker, metric), rule in sorted(VERIFIED_EXTRACTION_RULES.items())\n        ] + [\n            {"ticker": ticker, "metric": metric, "source_mode":"derived_sec_ratio", **rule}\n            for (ticker, metric), rule in sorted(DERIVED_RATIO_RULES.items())\n        ] + [\n            {"ticker": ticker, "metric": metric, "source_mode":"filing_table", **rule}\n            for (ticker, metric), rule in sorted(company_driver_tables.VERIFIED_TABLE_RULES.items())\n        ],''',1)
hp.write_text(h,encoding='utf-8')
print('PHASE3C_COMPLETE_PATCHED')
