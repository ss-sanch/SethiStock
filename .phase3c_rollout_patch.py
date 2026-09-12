from pathlib import Path

path=Path('company_driver_history.py')
text=path.read_text(encoding='utf-8')
text=text.replace('DRIVER_HISTORY_VERSION = "3b-history-v1"','DRIVER_HISTORY_VERSION = "3c-history-v1"',1)
start=text.index('VERIFIED_EXTRACTION_RULES: Dict[Tuple[str, str], Dict[str, Any]] = {')
end=text.index('\n\n\ndef _compact',start)
new_rules='''VERIFIED_EXTRACTION_RULES: Dict[Tuple[str, str], Dict[str, Any]] = {
    # Apple product mix
    ("AAPL", "iphone_revenue"): {
        "concept_any": ["revenue", "revenues", "sales"],
        "dimension_any": ["iphonemember"],
        "verified_example": "IPhoneMember",
    },
    ("AAPL", "wearables_home_accessories_revenue"): {
        "concept_any": ["revenue", "revenues", "sales"],
        "dimension_any": ["wearableshomeandaccessoriesmember"],
        "verified_example": "WearablesHomeandAccessoriesMember",
    },

    # Microsoft cloud reporting
    ("MSFT", "intelligent_cloud_revenue"): {
        "concept_any": ["revenue", "revenues", "sales"],
        "dimension_any": ["intelligentcloudmember"],
        "verified_example": "IntelligentCloudMember",
    },
    ("MSFT", "microsoft_cloud_revenue"): {
        "concept_any": ["revenue", "revenues", "sales"],
        "dimension_any": ["microsoftcloudmember"],
        "verified_example": "MicrosoftCloudMember",
    },

    # Alphabet Search, YouTube and Cloud
    ("GOOGL", "google_search_revenue"): {
        "concept_any": ["revenue", "revenues", "sales"],
        "dimension_any": ["googlesearchothermember"],
        "verified_example": "GoogleSearchOtherMember",
    },
    ("GOOGL", "youtube_ads_revenue"): {
        "concept_any": ["revenue", "revenues", "sales"],
        "dimension_any": ["youtubeadvertisingrevenuemember"],
        "verified_example": "YouTubeAdvertisingRevenueMember",
    },
    ("GOOGL", "google_cloud_revenue"): {
        "concept_any": ["revenue", "revenues", "sales"],
        "dimension_any": ["googlecloudmember"],
        "verified_example": "GoogleCloudMember",
    },
    ("GOOGL", "google_cloud_operating_income"): {
        "concept_any": ["operatingincomeloss", "operatingprofit", "operatingloss"],
        "dimension_any": ["googlecloudmember"],
        "verified_example": "GoogleCloudMember + OperatingIncomeLoss",
    },

    # Amazon AWS / advertising / geography
    ("AMZN", "aws_revenue"): {
        "concept_any": ["revenue", "revenues", "sales"],
        "dimension_any": ["amazonwebservices"],
        "verified_example": "AmazonWebServicesSegmentMember",
    },
    ("AMZN", "aws_operating_income"): {
        "concept_any": ["operatingincomeloss", "operatingprofit", "operatingloss"],
        "dimension_any": ["amazonwebservices"],
        "verified_example": "AmazonWebServicesSegmentMember + OperatingIncomeLoss",
    },
    ("AMZN", "advertising_services_revenue"): {
        "concept_any": ["revenue", "revenues", "sales"],
        "dimension_any": ["advertisingservicesmember"],
        "verified_example": "AdvertisingServicesMember",
    },
    ("AMZN", "international_revenue"): {
        "concept_any": ["revenue", "revenues", "sales"],
        "dimension_any": ["internationalsegmentmember"],
        "verified_example": "InternationalSegmentMember",
    },

    # Meta segment economics
    ("META", "family_of_apps_revenue"): {
        "concept_any": ["revenue", "revenues", "sales"],
        "dimension_any": ["familyofappsmember"],
        "dimension_none": ["advertisingmember", "serviceothermember"],
        "verified_example": "FamilyOfAppsMember (segment total)",
    },
    ("META", "reality_labs_revenue"): {
        "concept_any": ["revenue", "revenues", "sales"],
        "dimension_any": ["realitylabsmember"],
        "verified_example": "RealityLabsMember",
    },
    ("META", "reality_labs_operating_income"): {
        "concept_any": ["operatingincomeloss", "operatingprofit", "operatingloss"],
        "dimension_any": ["realitylabsmember"],
        "verified_example": "RealityLabsMember + OperatingIncomeLoss",
    },

    # NVIDIA current platform disclosure
    ("NVDA", "data_center_revenue"): {
        "concept_any": ["revenue", "revenues", "sales"],
        "dimension_any": ["datacentermember"],
        "verified_example": "DataCenterMember",
    },

    # Tesla segment revenue
    ("TSLA", "automotive_revenue"): {
        "concept_any": ["revenue", "revenues", "sales"],
        "dimension_any": ["automotivesegmentmember"],
        "dimension_none": ["productmember", "serviceothermember"],
        "verified_example": "AutomotiveSegmentMember (segment total)",
    },
}


SOURCE_LIMITED_METRICS: Dict[Tuple[str, str], Dict[str, str]] = {
    ("AAPL", "services_revenue"): {"state": "official_filing_table", "reason": "Services is disclosed in filed revenue tables; exact XBRL rule is verified separately during 3C."},
    ("AAPL", "installed_device_base"): {"state": "official_ir_required", "reason": "Installed-base milestones are disclosed by Apple outside a continuous SEC fact series."},
    ("MSFT", "azure_growth"): {"state": "official_ir_required", "reason": "Azure growth is a company-reported growth KPI rather than a stable SEC numeric fact."},
    ("MSFT", "gaming_revenue"): {"state": "official_filing_table_or_ir_required", "reason": "Current filings do not expose a stable Gaming revenue fact under the registry definition."},
    ("META", "ad_impressions_growth"): {"state": "official_ir_required", "reason": "Ad-impression growth is disclosed in results commentary rather than a stable SEC fact."},
    ("META", "average_price_per_ad_growth"): {"state": "official_ir_required", "reason": "Average price per ad growth is disclosed in results commentary rather than a stable SEC fact."},
    ("NVDA", "gaming_revenue"): {"state": "historical_definition_changed", "reason": "NVIDIA changed its market-platform revenue presentation in FY2027; do not extend the old Gaming series across the new definition."},
    ("NVDA", "automotive_revenue"): {"state": "historical_definition_changed", "reason": "NVIDIA changed its market-platform revenue presentation in FY2027; do not extend the old Automotive series across the new definition."},
    ("NVDA", "gross_margin"): {"state": "derived_sec_metric", "reason": "Gross margin should be derived from matching Gross Profit and Revenue periods, not from an unrelated direct percentage fact."},
    ("TSLA", "vehicle_deliveries"): {"state": "official_ir_required", "reason": "Quarterly deliveries are operational disclosures; similarly named XBRL facts can refer to compensation milestones."},
    ("TSLA", "automotive_gross_margin"): {"state": "derived_sec_metric", "reason": "Automotive margin should be derived from matching automotive Gross Profit and Revenue facts."},
    ("TSLA", "energy_storage_deployments"): {"state": "official_ir_required", "reason": "GWh deployments are operational disclosures rather than a stable SEC numeric fact."},
    ("NFLX", "paid_memberships"): {"state": "historical_filing_series", "reason": "Netflix disclosed paid memberships through 2024 and later changed its KPI disclosure approach; the historical series must stop where disclosure stops."},
    ("NFLX", "average_revenue_per_membership"): {"state": "historical_filing_series", "reason": "Netflix historically disclosed this KPI but changed its KPI disclosure approach."},
    ("NFLX", "engagement_hours"): {"state": "official_ir_required", "reason": "Engagement is disclosed on an irregular official basis and must not be interpolated."},
    ("NFLX", "ad_tier_scale"): {"state": "official_ir_required", "reason": "Ad-tier scale is an irregular official disclosure and must not be interpolated."},
    ("JPM", "net_interest_income"): {"state": "official_filing_table", "reason": "JPMorgan reports NII in filed operating tables; generic fact ranking is insufficient."},
    ("JPM", "net_interest_margin"): {"state": "official_filing_table", "reason": "NIM is a basis-sensitive banking KPI reported in filed tables."},
    ("JPM", "cet1_ratio"): {"state": "official_filing_table", "reason": "CET1 has multiple legal-entity/methodology contexts and requires an explicit Firm standardized rule."},
    ("JPM", "deposits"): {"state": "official_filing_table", "reason": "Total firm deposits require an explicit consolidated-table rule to avoid structured-note/deposit subcomponents."},
    ("JPM", "loans"): {"state": "official_filing_table", "reason": "Total firm loans require an explicit consolidated-table rule to avoid portfolio subcomponents."},
    ("JPM", "provision_for_credit_losses"): {"state": "official_filing_table", "reason": "Provision requires an explicit consolidated credit-loss table rule."},
    ("V", "payments_volume"): {"state": "official_filing_table", "reason": "Visa reports nominal payments volume in operating tables with a one-quarter service-revenue lag."},
    ("V", "cross_border_volume_growth"): {"state": "official_filing_or_release", "reason": "Cross-border growth is reported as an operating KPI, with basis variants such as total and excluding intra-Europe."},
    ("V", "processed_transactions"): {"state": "official_filing_table", "reason": "Visa reports processed transactions in a dedicated operating table."},
    ("V", "payments_credentials"): {"state": "official_ir_required", "reason": "Credential scale is not a stable filed numeric history under the registry definition."},
}
'''
text=text[:start]+new_rules+text[end:]

anchor='''def get_verified_rule(ticker: str, metric_key: str) -> Optional[Dict[str, Any]]:\n    return VERIFIED_EXTRACTION_RULES.get((str(ticker or "").strip().upper(), str(metric_key or "").strip().lower()))\n'''
assert anchor in text
addition=anchor+'''\n\ndef metric_coverage(ticker: str, metric_key: str) -> Dict[str, Any]:\n    key=(str(ticker or "").strip().upper(), str(metric_key or "").strip().lower())\n    rule=VERIFIED_EXTRACTION_RULES.get(key)\n    if rule:\n        return {\n            "state": "verified_sec_history",\n            "verified": True,\n            "history_version": DRIVER_HISTORY_VERSION,\n            "extraction_rule": rule,\n        }\n    limited=SOURCE_LIMITED_METRICS.get(key)\n    if limited:\n        return {"verified": False, **limited}\n    return {\n        "state": "not_verified",\n        "verified": False,\n        "reason": "No verified Phase 3C extraction rule has been approved for this KPI.",\n    }\n'''
text=text.replace(anchor,addition,1)

# Make unsupported history responses carry their coverage state.
old='''            "data_state": "not_verified_for_history",\n            "verified": False,\n            "observations": [],\n            "observation_count": 0,\n            "rule": "No Phase 3B verified SEC filing rule exists for this ticker/metric yet; Phase 3C expands coverage.",\n'''
new='''            "data_state": "not_verified_for_history",\n            "verified": False,\n            "coverage": metric_coverage(symbol, metric_key),\n            "observations": [],\n            "observation_count": 0,\n            "rule": "No verified SEC history rule exists for this ticker/metric; the coverage state explains the approved source path.",\n'''
assert old in text
text=text.replace(old,new,1)

text=text.replace('''        "phase": "3B",''','''        "phase": "3C",''',1)
path.write_text(text,encoding='utf-8')

# Add a coverage route to the registry API.
dp=Path('company_drivers.py')
dtext=dp.read_text(encoding='utf-8')
route_anchor='''\n\n@router.get("/{ticker}")\ndef company_driver_registry(ticker: str):\n'''
assert route_anchor in dtext
route='''\n\n@router.get("/{ticker}/coverage")\ndef company_driver_coverage(ticker: str):\n    """Return Phase 3C source/readiness state for every registered company driver."""\n    try:\n        registry=get_company_driver_registry(ticker)\n    except KeyError as exc:\n        raise HTTPException(status_code=404, detail=f"Company Drivers registry is not yet available for {str(ticker).strip().upper()}.") from exc\n    metrics=[]\n    for metric in registry["metrics"]:\n        metrics.append({\n            "key": metric["key"],\n            "label": metric["label"],\n            **company_driver_history.metric_coverage(registry["ticker"], metric["key"]),\n        })\n    return {\n        "ticker": registry["ticker"],\n        "company": registry["company"],\n        "history_version": company_driver_history.DRIVER_HISTORY_VERSION,\n        "metric_count": len(metrics),\n        "verified_count": sum(1 for row in metrics if row.get("verified")),\n        "metrics": metrics,\n    }\n'''
dtext=dtext.replace(route_anchor,route+route_anchor,1)
dp.write_text(dtext,encoding='utf-8')
print('PHASE3C_ROLLOUT_PATCHED')
