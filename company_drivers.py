"""SethiStock Phase 3A — company-specific operating KPI registry.

Phase 2 built a universal historical fundamentals engine. Phase 3 adds a second,
company-specific layer describing the operating metrics that actually drive each
business. This module is intentionally metadata-only: it defines what should be
collected and how it should be interpreted, without inventing historical values.

Later Phase 3 steps will attach extractors and persisted observations to these
stable metric keys. Disclosures that are sparse, renamed, or discontinued must
remain explicitly marked rather than being interpolated into false continuity.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException


router = APIRouter(prefix="/api/drivers", tags=["Company Drivers"])

DRIVER_SCHEMA_VERSION = "3a-v1"

SOURCE_TYPES = {
    "sec_custom_xbrl": {
        "label": "SEC custom XBRL",
        "description": "Issuer-specific XBRL concepts or dimensions from 10-K/10-Q filings.",
    },
    "sec_inline_xbrl": {
        "label": "SEC inline XBRL",
        "description": "Inline XBRL facts parsed from the filed report when Company Facts is insufficient.",
    },
    "sec_filing_table": {
        "label": "SEC filing table",
        "description": "Structured table or disclosure parsed from the filed 10-K/10-Q document.",
    },
    "earnings_release": {
        "label": "Earnings release",
        "description": "Company earnings release or shareholder letter filed or published with results.",
    },
    "investor_relations": {
        "label": "Investor relations",
        "description": "Official company presentation, supplemental schedule, KPI workbook or IR release.",
    },
}

ALLOWED_VALUE_KINDS = {"flow", "ratio", "count", "growth", "balance"}
ALLOWED_PERIOD_SEMANTICS = {"duration", "instant"}
ALLOWED_FREQUENCIES = {"quarterly", "annual", "mixed", "irregular"}
ALLOWED_CONTINUITY = {"continuous", "historical", "irregular"}
ALLOWED_FORMATS = {"currency", "percentage", "count", "multiple"}


def _metric(
    key: str,
    label: str,
    category: str,
    description: str,
    unit: str,
    value_kind: str,
    period_semantics: str,
    expected_frequency: str,
    continuity: str,
    display_format: str,
    source_priority: List[str],
    notes: str | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "key": key,
        "label": label,
        "category": category,
        "description": description,
        "unit": unit,
        "value_kind": value_kind,
        "period_semantics": period_semantics,
        "expected_frequency": expected_frequency,
        "continuity": continuity,
        "display_format": display_format,
        "source_priority": source_priority,
    }
    if notes:
        payload["notes"] = notes
    return payload


# Phase 3 launches with ten deliberately varied issuers. The registry describes the
# target KPIs; Phase 3B determines the actual extraction rule and available history
# empirically from filings and official IR disclosures.
COMPANY_DRIVER_REGISTRY: Dict[str, Dict[str, Any]] = {
    "AAPL": {
        "company": "Apple",
        "theme": "Products, Services and installed ecosystem",
        "metrics": [
            _metric("iphone_revenue", "iPhone Revenue", "product_mix", "Revenue attributed to iPhone products.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("services_revenue", "Services Revenue", "services", "Revenue attributed to Apple's Services category.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("wearables_home_accessories_revenue", "Wearables, Home & Accessories Revenue", "product_mix", "Revenue attributed to Wearables, Home and Accessories where disclosed under a comparable definition.", "USD", "flow", "duration", "quarterly", "historical", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"], "Category naming and composition can change; preserve disclosed definitions by period."),
            _metric("installed_device_base", "Installed Device Base", "ecosystem", "Company-disclosed active installed base of devices.", "devices", "count", "instant", "mixed", "irregular", "count", ["earnings_release", "investor_relations", "sec_filing_table"], "Do not interpolate between company-disclosed milestones."),
        ],
    },
    "MSFT": {
        "company": "Microsoft",
        "theme": "Cloud, software subscriptions and gaming",
        "metrics": [
            _metric("intelligent_cloud_revenue", "Intelligent Cloud Revenue", "cloud", "Revenue of the Intelligent Cloud reporting segment where that segment definition is disclosed.", "USD", "flow", "duration", "quarterly", "historical", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("azure_growth", "Azure Growth", "cloud", "Company-reported Azure revenue growth rate under the disclosed basis.", "percent", "growth", "duration", "quarterly", "continuous", "percentage", ["earnings_release", "investor_relations", "sec_filing_table"]),
            _metric("microsoft_cloud_revenue", "Microsoft Cloud Revenue", "cloud", "Company-disclosed Microsoft Cloud revenue aggregation.", "USD", "flow", "duration", "quarterly", "historical", "currency", ["earnings_release", "investor_relations", "sec_filing_table"]),
            _metric("gaming_revenue", "Gaming Revenue", "gaming", "Revenue attributed to Microsoft's gaming business under the period's disclosed definition.", "USD", "flow", "duration", "quarterly", "historical", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
        ],
    },
    "GOOGL": {
        "company": "Alphabet",
        "theme": "Search, YouTube and Google Cloud",
        "metrics": [
            _metric("google_search_revenue", "Google Search & Other Revenue", "advertising", "Revenue from Google Search & other advertising properties.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("youtube_ads_revenue", "YouTube Ads Revenue", "advertising", "Advertising revenue attributed to YouTube.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("google_cloud_revenue", "Google Cloud Revenue", "cloud", "Revenue attributed to the Google Cloud reporting segment.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("google_cloud_operating_income", "Google Cloud Operating Income", "cloud", "Operating income or loss attributed to Google Cloud.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
        ],
    },
    "AMZN": {
        "company": "Amazon",
        "theme": "AWS, advertising and geographic retail segments",
        "metrics": [
            _metric("aws_revenue", "AWS Revenue", "cloud", "Net sales attributed to Amazon Web Services.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("aws_operating_income", "AWS Operating Income", "cloud", "Operating income attributed to Amazon Web Services.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("advertising_services_revenue", "Advertising Services Revenue", "advertising", "Revenue attributed to advertising services under the company's disclosed category definition.", "USD", "flow", "duration", "quarterly", "historical", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("international_revenue", "International Revenue", "geography", "Net sales attributed to Amazon's International segment.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
        ],
    },
    "META": {
        "company": "Meta Platforms",
        "theme": "Advertising economics and Reality Labs",
        "metrics": [
            _metric("family_of_apps_revenue", "Family of Apps Revenue", "segment", "Revenue attributed to Meta's Family of Apps segment where separately disclosed.", "USD", "flow", "duration", "quarterly", "historical", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("reality_labs_revenue", "Reality Labs Revenue", "segment", "Revenue attributed to Reality Labs.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("reality_labs_operating_income", "Reality Labs Operating Income", "segment", "Operating income or loss attributed to Reality Labs.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("ad_impressions_growth", "Ad Impressions Growth", "advertising", "Company-reported growth in ad impressions delivered across applicable surfaces.", "percent", "growth", "duration", "quarterly", "historical", "percentage", ["earnings_release", "investor_relations", "sec_filing_table"], "Preserve the company's disclosed scope because covered surfaces have changed through time."),
            _metric("average_price_per_ad_growth", "Average Price per Ad Growth", "advertising", "Company-reported growth in average price per ad.", "percent", "growth", "duration", "quarterly", "historical", "percentage", ["earnings_release", "investor_relations", "sec_filing_table"]),
        ],
    },
    "NVDA": {
        "company": "NVIDIA",
        "theme": "Compute end markets and platform mix",
        "metrics": [
            _metric("data_center_revenue", "Data Center Revenue", "end_market", "Revenue attributed to NVIDIA's Data Center market platform.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("gaming_revenue", "Gaming Revenue", "end_market", "Revenue attributed to NVIDIA's Gaming market platform.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("automotive_revenue", "Automotive Revenue", "end_market", "Revenue attributed to NVIDIA's Automotive market platform.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("gross_margin", "Gross Margin", "profitability", "Company gross margin, included as an operating driver because product and platform mix materially affect NVIDIA economics.", "percent", "ratio", "duration", "quarterly", "continuous", "percentage", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
        ],
    },
    "TSLA": {
        "company": "Tesla",
        "theme": "Vehicle scale, automotive economics and energy storage",
        "metrics": [
            _metric("vehicle_deliveries", "Vehicle Deliveries", "volume", "Company-reported vehicle deliveries for the period.", "vehicles", "count", "duration", "quarterly", "continuous", "count", ["investor_relations", "earnings_release", "sec_filing_table"]),
            _metric("automotive_revenue", "Automotive Revenue", "automotive", "Revenue attributed to automotive operations.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("automotive_gross_margin", "Automotive Gross Margin", "automotive", "Automotive gross margin under the company's disclosed basis.", "percent", "ratio", "duration", "quarterly", "historical", "percentage", ["earnings_release", "investor_relations", "sec_filing_table"], "Preserve whether regulatory credits are included or excluded where the disclosure distinguishes them."),
            _metric("energy_storage_deployments", "Energy Storage Deployments", "energy", "Company-reported energy storage deployments.", "GWh", "count", "duration", "quarterly", "continuous", "count", ["investor_relations", "earnings_release", "sec_filing_table"]),
        ],
    },
    "NFLX": {
        "company": "Netflix",
        "theme": "Membership, monetisation and engagement",
        "metrics": [
            _metric("paid_memberships", "Paid Memberships", "membership", "Paid memberships where Netflix historically disclosed a comparable period-end count.", "memberships", "count", "instant", "quarterly", "historical", "count", ["earnings_release", "investor_relations", "sec_filing_table"], "Netflix changed its KPI disclosure approach; do not extend this series beyond disclosed comparable periods."),
            _metric("average_revenue_per_membership", "Average Revenue per Membership", "monetisation", "Average revenue per membership under Netflix's historical disclosed methodology.", "USD", "ratio", "duration", "quarterly", "historical", "currency", ["earnings_release", "investor_relations", "sec_filing_table"]),
            _metric("engagement_hours", "Engagement Hours", "engagement", "Officially disclosed viewing or engagement hours when a comparable measurement is published.", "hours", "count", "duration", "irregular", "irregular", "count", ["investor_relations", "earnings_release"], "Only plot disclosed observations with the original measurement window; never interpolate missing quarters."),
            _metric("ad_tier_scale", "Ad-tier Scale", "advertising", "Official company disclosure describing the scale of the advertising-supported tier when quantified comparably.", "memberships", "count", "instant", "irregular", "irregular", "count", ["investor_relations", "earnings_release"], "Definition and availability may change as the advertising business develops."),
        ],
    },
    "JPM": {
        "company": "JPMorgan Chase",
        "theme": "Net interest economics, balance-sheet scale and capital strength",
        "metrics": [
            _metric("net_interest_income", "Net Interest Income", "banking", "Net interest income for the reporting period.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("net_interest_margin", "Net Interest Margin", "banking", "Company-reported net interest margin or equivalent taxable-equivalent measure under the disclosed basis.", "percent", "ratio", "duration", "quarterly", "historical", "percentage", ["earnings_release", "investor_relations", "sec_filing_table"]),
            _metric("cet1_ratio", "CET1 Ratio", "capital", "Common Equity Tier 1 capital ratio under the disclosed regulatory basis.", "percent", "ratio", "instant", "quarterly", "continuous", "percentage", ["investor_relations", "earnings_release", "sec_filing_table"]),
            _metric("deposits", "Deposits", "balance_sheet", "Total deposits under the company's reported definition.", "USD", "balance", "instant", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("loans", "Loans", "balance_sheet", "Total loans under the company's reported definition.", "USD", "balance", "instant", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
            _metric("provision_for_credit_losses", "Provision for Credit Losses", "credit", "Provision for credit losses recognised during the reporting period.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["sec_custom_xbrl", "sec_filing_table", "earnings_release"]),
        ],
    },
    "V": {
        "company": "Visa",
        "theme": "Payments volume, cross-border activity and network transactions",
        "metrics": [
            _metric("payments_volume", "Payments Volume", "payments", "Company-reported payments volume under Visa's disclosed measurement convention.", "USD", "flow", "duration", "quarterly", "continuous", "currency", ["investor_relations", "earnings_release", "sec_filing_table"]),
            _metric("cross_border_volume_growth", "Cross-border Volume Growth", "payments", "Company-reported growth in cross-border volume under the disclosed constant-dollar or nominal basis.", "percent", "growth", "duration", "quarterly", "continuous", "percentage", ["investor_relations", "earnings_release", "sec_filing_table"], "Store the disclosed basis with each observation because Visa publishes multiple cross-border measures."),
            _metric("processed_transactions", "Processed Transactions", "network", "Transactions processed over Visa's network for the reporting period.", "transactions", "count", "duration", "quarterly", "continuous", "count", ["investor_relations", "earnings_release", "sec_filing_table"]),
            _metric("payments_credentials", "Payments Credentials", "network", "Company-disclosed cards or payment credentials where a comparable period-end measure is available.", "credentials", "count", "instant", "mixed", "historical", "count", ["investor_relations", "earnings_release", "sec_filing_table"]),
        ],
    },
}

TICKER_ALIASES = {
    "GOOG": "GOOGL",
}


def _canonical_ticker(ticker: str) -> str:
    symbol = str(ticker or "").strip().upper()
    return TICKER_ALIASES.get(symbol, symbol)


def validate_registry() -> Dict[str, int]:
    """Validate registry invariants and return simple coverage statistics."""
    metric_keys = set()
    metric_count = 0
    for ticker, company in COMPANY_DRIVER_REGISTRY.items():
        if ticker != ticker.upper() or not ticker:
            raise ValueError(f"Invalid registry ticker: {ticker!r}")
        if not company.get("company") or not company.get("theme"):
            raise ValueError(f"Registry company metadata incomplete for {ticker}")
        metrics = company.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise ValueError(f"Registry metrics missing for {ticker}")

        local_keys = set()
        for metric in metrics:
            key = metric.get("key")
            if not isinstance(key, str) or not key or key in local_keys:
                raise ValueError(f"Duplicate/invalid metric key for {ticker}: {key!r}")
            local_keys.add(key)
            metric_keys.add(key)
            metric_count += 1

            if metric.get("value_kind") not in ALLOWED_VALUE_KINDS:
                raise ValueError(f"Invalid value_kind for {ticker}.{key}")
            if metric.get("period_semantics") not in ALLOWED_PERIOD_SEMANTICS:
                raise ValueError(f"Invalid period_semantics for {ticker}.{key}")
            if metric.get("expected_frequency") not in ALLOWED_FREQUENCIES:
                raise ValueError(f"Invalid expected_frequency for {ticker}.{key}")
            if metric.get("continuity") not in ALLOWED_CONTINUITY:
                raise ValueError(f"Invalid continuity for {ticker}.{key}")
            if metric.get("display_format") not in ALLOWED_FORMATS:
                raise ValueError(f"Invalid display_format for {ticker}.{key}")
            sources = metric.get("source_priority")
            if not isinstance(sources, list) or not sources:
                raise ValueError(f"Missing source_priority for {ticker}.{key}")
            unknown_sources = [source for source in sources if source not in SOURCE_TYPES]
            if unknown_sources:
                raise ValueError(f"Unknown sources for {ticker}.{key}: {unknown_sources}")

    return {
        "company_count": len(COMPANY_DRIVER_REGISTRY),
        "metric_count": metric_count,
        "unique_metric_key_count": len(metric_keys),
    }


REGISTRY_STATS = validate_registry()


def get_company_driver_registry(ticker: str) -> Dict[str, Any]:
    requested = str(ticker or "").strip().upper()
    canonical = _canonical_ticker(requested)
    company = COMPANY_DRIVER_REGISTRY.get(canonical)
    if company is None:
        raise KeyError(canonical)
    payload = deepcopy(company)
    payload.update(
        {
            "ticker": canonical,
            "requested_ticker": requested,
            "schema_version": DRIVER_SCHEMA_VERSION,
            "metric_count": len(payload["metrics"]),
            "data_state": "registry_only",
        }
    )
    return payload


@router.get("/schema")
def company_driver_schema():
    """Describe the stable Phase 3A company-driver metadata contract."""
    return {
        "schema_version": DRIVER_SCHEMA_VERSION,
        "phase": "3A",
        "data_state": "registry_only",
        "source_types": deepcopy(SOURCE_TYPES),
        "allowed_value_kinds": sorted(ALLOWED_VALUE_KINDS),
        "allowed_period_semantics": sorted(ALLOWED_PERIOD_SEMANTICS),
        "allowed_frequencies": sorted(ALLOWED_FREQUENCIES),
        "allowed_continuity": sorted(ALLOWED_CONTINUITY),
        "allowed_display_formats": sorted(ALLOWED_FORMATS),
        "observation_contract": {
            "required": ["ticker", "metric", "period_end", "value", "unit", "source_type", "source_url"],
            "provenance": ["filing_date", "form", "accession", "source_title", "definition", "extraction_method"],
            "rule": "Never interpolate or manufacture company KPI continuity. Store only sourced disclosed observations or explicitly derived values with provenance.",
        },
        **REGISTRY_STATS,
    }


@router.get("/companies")
def company_driver_companies():
    """List the initial Phase 3 flagship coverage and registry size."""
    companies = []
    for ticker, company in COMPANY_DRIVER_REGISTRY.items():
        companies.append(
            {
                "ticker": ticker,
                "company": company["company"],
                "theme": company["theme"],
                "metric_count": len(company["metrics"]),
            }
        )
    return {
        "schema_version": DRIVER_SCHEMA_VERSION,
        "count": len(companies),
        "companies": companies,
    }


@router.get("/{ticker}")
def company_driver_registry(ticker: str):
    """Return the company-specific KPI registry for a supported flagship ticker."""
    try:
        return get_company_driver_registry(ticker)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Company Drivers registry is not yet available for {str(ticker).strip().upper()}.",
        ) from exc
