"""Phase 3B verified historical series for company-specific operating drivers.

The filing discovery layer surfaces candidate Inline XBRL facts. This module promotes
only explicitly verified ticker/metric rules into chart-ready histories. It preserves
filing provenance, resolves duplicate comparative facts in favour of the latest filed
observation, classifies quarterly/YTD/annual durations, and may derive Q4 for flow
metrics strictly as FY minus 9M when both source observations share a fiscal start.

No interpolation or continuity manufacture is permitted here. Broader ticker/metric
coverage belongs to Phase 3C.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import company_driver_filings


DRIVER_HISTORY_VERSION = "3c-history-v1"

# These rules have been verified against live SEC Inline XBRL filings during Phase 3B.
# 3C expands this registry across all flagship companies and KPI definitions.
VERIFIED_EXTRACTION_RULES: Dict[Tuple[str, str], Dict[str, Any]] = {
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



def _compact(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _parse_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except Exception:
        return None


def _dimension_text(fact: Dict[str, Any]) -> str:
    return " ".join(
        f"{row.get('dimension') or ''} {row.get('member') or ''}"
        for row in (fact.get("dimensions") or [])
        if isinstance(row, dict)
    )


def _fact_matches_rule(fact: Dict[str, Any], metric: Dict[str, Any], rule: Dict[str, Any]) -> bool:
    if not fact.get("numeric") or fact.get("value") is None:
        return False
    if metric.get("period_semantics") and fact.get("period_semantics") != metric.get("period_semantics"):
        return False

    concept = _compact(f"{fact.get('taxonomy') or ''} {fact.get('concept') or ''} {fact.get('qualified_concept') or ''}")
    dimensions = _compact(_dimension_text(fact))

    concept_any = [_compact(term) for term in (rule.get("concept_any") or []) if _compact(term)]
    dimension_any = [_compact(term) for term in (rule.get("dimension_any") or []) if _compact(term)]
    concept_none = [_compact(term) for term in (rule.get("concept_none") or []) if _compact(term)]
    dimension_none = [_compact(term) for term in (rule.get("dimension_none") or []) if _compact(term)]

    if concept_any and not any(term in concept for term in concept_any):
        return False
    if dimension_any and not any(term in dimensions for term in dimension_any):
        return False
    if any(term in concept for term in concept_none):
        return False
    if any(term in dimensions for term in dimension_none):
        return False
    return True


def _duration_days(fact: Dict[str, Any]) -> Optional[int]:
    start = _parse_date(fact.get("start"))
    end = _parse_date(fact.get("end"))
    if not start or not end or end < start:
        return None
    return (end - start).days + 1


def classify_period(fact: Dict[str, Any]) -> str:
    """Classify a filing fact without assuming calendar fiscal years."""
    if fact.get("period_semantics") == "instant" or fact.get("instant"):
        return "instant"
    days = _duration_days(fact)
    if days is None:
        return "unknown"
    # 13/14-week quarters and 52/53-week years fit comfortably in these bands.
    if days <= 120:
        return "quarter"
    if days <= 225:
        return "ytd_6m"
    if days <= 320:
        return "ytd_9m"
    return "annual"


def _amendment_rank(form: Any) -> int:
    return 1 if str(form or "").upper().endswith("/A") else 0


def _candidate_rank(fact: Dict[str, Any]) -> Tuple[str, int, int, str]:
    """Latest filing wins exact-period duplicates; score then resolves same-filing ties."""
    return (
        str(fact.get("filing_date") or ""),
        _amendment_rank(fact.get("form")),
        int(fact.get("score") or 0),
        str(fact.get("accession") or ""),
    )


def _observation_key(fact: Dict[str, Any]) -> Tuple[Any, ...]:
    period_type = classify_period(fact)
    if period_type == "instant":
        return (period_type, fact.get("instant") or fact.get("end"))
    return (period_type, fact.get("start"), fact.get("end"))


def _promote_fact(fact: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "period_type": classify_period(fact),
        "start": fact.get("start"),
        "end": fact.get("end"),
        "instant": fact.get("instant"),
        "value": fact.get("value"),
        "unit_ref": fact.get("unit_ref"),
        "qualified_concept": fact.get("qualified_concept"),
        "dimensions": fact.get("dimensions") or [],
        "form": fact.get("form"),
        "filing_date": fact.get("filing_date"),
        "report_date": fact.get("report_date"),
        "accession": fact.get("accession"),
        "source_url": fact.get("source_url"),
        "extraction_method": "sec_inline_xbrl_verified_rule",
        "score": fact.get("score"),
        "derived": False,
    }


def _dedupe_reported_facts(facts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for fact in facts:
        key = _observation_key(fact)
        current = best.get(key)
        if current is None or _candidate_rank(fact) > _candidate_rank(current):
            best[key] = fact
    promoted = [_promote_fact(fact) for fact in best.values()]
    promoted.sort(key=lambda row: (str(row.get("end") or row.get("instant") or ""), str(row.get("start") or "")))
    return promoted


def _derive_q4(reported: Sequence[Dict[str, Any]], metric: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Derive Q4 only for additive flow metrics as FY minus 9M."""
    if metric.get("value_kind") != "flow" or metric.get("period_semantics") != "duration":
        return []

    annual_by_start = {
        row.get("start"): row for row in reported
        if row.get("period_type") == "annual" and row.get("start") and row.get("end")
    }
    ytd_by_start = {
        row.get("start"): row for row in reported
        if row.get("period_type") == "ytd_9m" and row.get("start") and row.get("end")
    }
    derived: List[Dict[str, Any]] = []
    for fiscal_start, annual in annual_by_start.items():
        ytd = ytd_by_start.get(fiscal_start)
        if not ytd:
            continue
        annual_end = _parse_date(annual.get("end"))
        ytd_end = _parse_date(ytd.get("end"))
        if not annual_end or not ytd_end or annual_end <= ytd_end:
            continue
        remaining_days = (annual_end - ytd_end).days
        if remaining_days < 60 or remaining_days > 125:
            continue
        try:
            value = float(annual["value"]) - float(ytd["value"])
        except Exception:
            continue
        derived.append({
            "period_type": "quarter",
            "start": (ytd_end + timedelta(days=1)).isoformat(),
            "end": annual_end.isoformat(),
            "instant": None,
            "value": value,
            "unit_ref": annual.get("unit_ref") or ytd.get("unit_ref"),
            "qualified_concept": annual.get("qualified_concept"),
            "dimensions": annual.get("dimensions") or ytd.get("dimensions") or [],
            "form": annual.get("form"),
            "filing_date": annual.get("filing_date"),
            "report_date": annual.get("report_date"),
            "accession": annual.get("accession"),
            "source_url": annual.get("source_url"),
            "extraction_method": "derived_q4_fy_minus_9m",
            "score": min(int(annual.get("score") or 0), int(ytd.get("score") or 0)),
            "derived": True,
            "derivation": {
                "formula": "FY - 9M",
                "annual": {
                    "value": annual.get("value"), "accession": annual.get("accession"),
                    "filing_date": annual.get("filing_date"), "source_url": annual.get("source_url"),
                },
                "ytd_9m": {
                    "value": ytd.get("value"), "accession": ytd.get("accession"),
                    "filing_date": ytd.get("filing_date"), "source_url": ytd.get("source_url"),
                },
            },
        })
    return derived


def _merge_quarterly(reported: Sequence[Dict[str, Any]], derived: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # A directly reported quarter always beats an arithmetic Q4 for the same end date.
    by_end: Dict[str, Dict[str, Any]] = {}
    for row in derived:
        if row.get("end"):
            by_end[str(row["end"])] = row
    for row in reported:
        if row.get("period_type") == "quarter" and row.get("end"):
            by_end[str(row["end"])] = row
    return [by_end[key] for key in sorted(by_end)]


def get_verified_rule(ticker: str, metric_key: str) -> Optional[Dict[str, Any]]:
    return VERIFIED_EXTRACTION_RULES.get((str(ticker or "").strip().upper(), str(metric_key or "").strip().lower()))


def metric_coverage(ticker: str, metric_key: str) -> Dict[str, Any]:
    key=(str(ticker or "").strip().upper(), str(metric_key or "").strip().lower())
    rule=VERIFIED_EXTRACTION_RULES.get(key)
    if rule:
        return {
            "state": "verified_sec_history",
            "verified": True,
            "history_version": DRIVER_HISTORY_VERSION,
            "extraction_rule": rule,
        }
    limited=SOURCE_LIMITED_METRICS.get(key)
    if limited:
        return {"verified": False, **limited}
    return {
        "state": "not_verified",
        "verified": False,
        "reason": "No verified Phase 3C extraction rule has been approved for this KPI.",
    }


def build_verified_history(
    ticker: str,
    metric: Dict[str, Any],
    filing_limit: int = 16,
    period: str = "quarterly",
    observation_limit: int = 40,
) -> Dict[str, Any]:
    symbol = str(ticker or "").strip().upper()
    metric_key = str(metric.get("key") or "").strip().lower()
    rule = get_verified_rule(symbol, metric_key)
    if not rule:
        return {
            "history_version": DRIVER_HISTORY_VERSION,
            "ticker": symbol,
            "metric": metric_key,
            "data_state": "not_verified_for_history",
            "verified": False,
            "coverage": metric_coverage(symbol, metric_key),
            "observations": [],
            "observation_count": 0,
            "rule": "No verified SEC history rule exists for this ticker/metric; the coverage state explains the approved source path.",
        }

    filings = company_driver_filings.recent_periodic_filings(
        symbol,
        limit=max(1, min(int(filing_limit), 32)),
        forms=("10-K", "10-Q", "10-K/A", "10-Q/A"),
    )
    matched: List[Dict[str, Any]] = []
    filing_summaries = []
    for filing in filings:
        if filing.get("is_inline_xbrl") is False:
            continue
        parsed = company_driver_filings.get_parsed_filing(filing)
        filing_match_count = 0
        for fact in parsed.get("facts") or []:
            if not isinstance(fact, dict) or not _fact_matches_rule(fact, metric, rule):
                continue
            row = dict(fact)
            row["score"] = company_driver_filings.score_fact_for_metric(row, metric)
            row["period_type"] = classify_period(row)
            if row["period_type"] == "unknown":
                continue
            matched.append(row)
            filing_match_count += 1
        filing_summaries.append({
            "form": filing.get("form"),
            "filing_date": filing.get("filing_date"),
            "report_date": filing.get("report_date"),
            "accession": filing.get("accession"),
            "source_url": filing.get("source_url"),
            "matched_fact_count": filing_match_count,
        })

    reported = _dedupe_reported_facts(matched)
    q4 = _derive_q4(reported, metric)
    period_key = str(period or "quarterly").strip().lower()
    if period_key == "quarterly":
        observations = _merge_quarterly(reported, q4)
    elif period_key == "annual":
        observations = [row for row in reported if row.get("period_type") == "annual"]
    elif period_key == "reported":
        observations = list(reported)
    else:
        raise ValueError("period must be one of: quarterly, annual, reported")

    observations = observations[-max(1, min(int(observation_limit), 100)):]
    coverage_dates = [str(row.get("end") or row.get("instant") or "") for row in observations if row.get("end") or row.get("instant")]
    return {
        "history_version": DRIVER_HISTORY_VERSION,
        "ticker": symbol,
        "metric": metric_key,
        "metric_label": metric.get("label"),
        "period": period_key,
        "data_state": "verified_history",
        "verified": True,
        "verified_rule": rule,
        "filings_checked": filing_summaries,
        "matched_fact_count": len(matched),
        "reported_observation_count": len(reported),
        "derived_q4_count": len(q4),
        "observation_count": len(observations),
        "coverage": {
            "start": min(coverage_dates) if coverage_dates else None,
            "end": max(coverage_dates) if coverage_dates else None,
        },
        "observations": observations,
        "policies": {
            "duplicates": "For an exact reporting period, the latest filed observation wins; amendments outrank originals on a same-day tie.",
            "q4": "For additive flow KPIs only, Q4 may be derived as FY minus 9M when both facts share a fiscal start and imply a plausible quarter.",
            "missing_periods": "Never interpolated or backfilled.",
            "provenance": "Every reported or derived observation retains SEC accession/source provenance.",
        },
    }


def history_schema() -> Dict[str, Any]:
    return {
        "version": DRIVER_HISTORY_VERSION,
        "phase": "3C",
        "source": "SEC EDGAR Inline XBRL primary filings",
        "data_state": "verified_history",
        "periods": ["quarterly", "annual", "reported"],
        "verified_rules": [
            {"ticker": ticker, "metric": metric, **rule}
            for (ticker, metric), rule in sorted(VERIFIED_EXTRACTION_RULES.items())
        ],
        "observation_contract": {
            "period": ["period_type", "start", "end", "instant"],
            "value": ["value", "unit_ref"],
            "identity": ["qualified_concept", "dimensions"],
            "provenance": ["form", "filing_date", "report_date", "accession", "source_url", "extraction_method"],
            "derived_q4": "Includes formula and both source observations when FY - 9M is used.",
        },
        "guarantees": [
            "Only verified ticker/metric extraction rules are promoted to history.",
            "Latest filed duplicate/restated facts win exact-period conflicts.",
            "No missing observations are interpolated.",
        ],
    }
