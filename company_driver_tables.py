"""Phase 3C verified SEC filing-table extractors for operating KPIs.

Some high-value company drivers are disclosed in official 10-K/10-Q tables but are
not encoded as usable Inline XBRL facts. These adapters are intentionally explicit
per ticker/metric. They parse only verified row labels and preserve filing provenance;
no generic table-number guessing is exposed as trusted history.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

import company_driver_filings


TABLE_HISTORY_VERSION = "3c-table-v1"

VERIFIED_TABLE_RULES: Dict[Tuple[str, str], Dict[str, Any]] = {
    ("V", "processed_transactions"): {
        "extractor": "visa_processed_transactions",
        "source_state": "verified_filing_table_history",
        "verified_label": "Visa processed transactions",
    },
    ("JPM", "cet1_ratio"): {
        "extractor": "jpm_cet1_ratio",
        "source_state": "verified_filing_table_history",
        "verified_label": "Common equity Tier 1 (CET1) capital ratio - Standardized",
    },
    ("NFLX", "paid_memberships"): {
        "extractor": "netflix_paid_memberships",
        "source_state": "verified_historical_filing_table",
        "verified_label": "Paid memberships at end of period",
        "historical_only": True,
    },
}


def get_table_rule(ticker: str, metric_key: str) -> Optional[Dict[str, Any]]:
    return VERIFIED_TABLE_RULES.get((str(ticker or "").strip().upper(), str(metric_key or "").strip().lower()))


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\u00a0", " ")).strip()


def _number(text: Any) -> Optional[float]:
    raw = _clean(text)
    if not raw or raw in {"-", "—", "–"}:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = re.sub(r"[$£€¥,%\s,]", "", raw).strip("()")
    if not re.fullmatch(r"[-+]?\d*\.?\d+", cleaned):
        return None
    try:
        value = float(cleaned)
    except Exception:
        return None
    return -abs(value) if negative else value


def _row_cells(row: Any) -> List[str]:
    return [_clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]


def _label_index(cells: List[str], phrase: str) -> Optional[int]:
    target=phrase.lower()
    return next((i for i, cell in enumerate(cells) if target in cell.lower()), None)


def _table_rows(soup: BeautifulSoup):
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = _row_cells(row)
            if cells:
                yield table, cells


def _period_end(filing: Dict[str, Any]) -> Optional[str]:
    value = str(filing.get("report_date") or "").strip()
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except Exception:
        return None


def _base_observation(filing: Dict[str, Any], **extra) -> Dict[str, Any]:
    payload = {
        "form": filing.get("form"),
        "filing_date": filing.get("filing_date"),
        "report_date": filing.get("report_date"),
        "accession": filing.get("accession"),
        "source_url": filing.get("source_url"),
        "derived": False,
    }
    payload.update(extra)
    return payload


def _visa_processed_transactions(soup: BeautifulSoup, filing: Dict[str, Any]) -> List[Dict[str, Any]]:
    end = _period_end(filing)
    if not end:
        return []
    observations: List[Dict[str, Any]] = []
    for table, cells in _table_rows(soup):
        label_index=_label_index(cells, "visa processed transactions")
        if label_index is None:
            continue
        nums = [value for value in (_number(cell) for cell in cells[label_index + 1:]) if value is not None]
        if not nums:
            continue
        table_text = _clean(table.get_text(" ", strip=True)).lower()
        value = nums[0] * 1_000_000.0  # table is explicitly in millions
        if "three months ended" in table_text:
            observations.append(_base_observation(
                filing,
                period_type="quarter",
                start=None,
                end=end,
                instant=None,
                value=value,
                unit_ref="transactions",
                qualified_concept=None,
                dimensions=[],
                extraction_method="sec_filing_table_verified_row",
                source_label="Visa processed transactions",
            ))
        elif "year ended" in table_text:
            observations.append(_base_observation(
                filing,
                period_type="annual",
                start=None,
                end=end,
                instant=None,
                value=value,
                unit_ref="transactions",
                qualified_concept=None,
                dimensions=[],
                extraction_method="sec_filing_table_verified_row",
                source_label="Visa processed transactions",
            ))
        break
    return observations


def _jpm_cet1_ratio(soup: BeautifulSoup, filing: Dict[str, Any]) -> List[Dict[str, Any]]:
    end = _period_end(filing)
    if not end:
        return []
    patterns = ("common equity tier 1", "cet1")
    for table, cells in _table_rows(soup):
        if not cells:
            continue
        label_index=next((i for i, cell in enumerate(cells) if "capital ratio" in cell.lower() and any(term in cell.lower() for term in patterns)), None)
        table_text = _clean(table.get_text(" ", strip=True)).lower()
        if label_index is None or "standardized" not in table_text:
            continue
        nums = [value for value in (_number(cell) for cell in cells[label_index + 1:]) if value is not None]
        if not nums:
            continue
        value = nums[0]
        # Guard against required/minimum/subsidiary tables. The Firm selected-metrics row
        # is presented as a percentage in human units (e.g. 14.2), not decimal 0.142.
        if not (5.0 <= value <= 30.0):
            continue
        return [_base_observation(
            filing,
            period_type="instant",
            start=None,
            end=end,
            instant=end,
            value=value,
            unit_ref="percent",
            qualified_concept=None,
            dimensions=[],
            extraction_method="sec_filing_table_verified_row",
            source_label="Firm CET1 capital ratio - Standardized",
        )]
    return []


def _netflix_paid_memberships(soup: BeautifulSoup, filing: Dict[str, Any]) -> List[Dict[str, Any]]:
    end = _period_end(filing)
    if not end:
        return []
    region_tokens = {
        "ucan": ("united states and canada", "ucan"),
        "emea": ("europe, middle east", "emea"),
        "latam": ("latin america", "latam"),
        "apac": ("asia-pacific", "apac"),
    }
    by_region: Dict[str, float] = {}
    for table, cells in _table_rows(soup):
        label_index=_label_index(cells, "paid memberships at end of period")
        if label_index is None:
            continue
        table_text = _clean(table.get_text(" ", strip=True)).lower()
        region = next((key for key, tokens in region_tokens.items() if any(token in table_text for token in tokens)), None)
        if not region:
            previous=table.find_previous(string=re.compile(r"United States and Canada|UCAN|Europe, Middle East|EMEA|Latin America|LATAM|Asia-Pacific|APAC", re.I))
            context=(table_text + " " + _clean(previous)).lower()
            region = next((key for key, tokens in region_tokens.items() if any(token in context for token in tokens)), None)
        if not region:
            continue
        nums = [value for value in (_number(cell) for cell in cells[label_index + 1:]) if value is not None]
        if nums:
            # Filing tables state memberships in thousands.
            by_region[region] = nums[0] * 1_000.0
    if len(by_region) != 4:
        return []
    value = sum(by_region.values())
    return [_base_observation(
        filing,
        period_type="instant",
        start=None,
        end=end,
        instant=end,
        value=value,
        unit_ref="memberships",
        qualified_concept="nflx:NumberOfPaidMemberships (regional aggregate)",
        dimensions=[{"region": key, "value": amount} for key, amount in sorted(by_region.items())],
        extraction_method="sec_filing_table_regional_aggregate",
        source_label="Paid memberships at end of period (UCAN + EMEA + LATAM + APAC)",
    )]


_EXTRACTORS = {
    "visa_processed_transactions": _visa_processed_transactions,
    "jpm_cet1_ratio": _jpm_cet1_ratio,
    "netflix_paid_memberships": _netflix_paid_memberships,
}


def build_table_history(
    ticker: str,
    metric: Dict[str, Any],
    filing_limit: int = 16,
    period: str = "quarterly",
    observation_limit: int = 40,
) -> Dict[str, Any]:
    symbol = str(ticker or "").strip().upper()
    metric_key = str(metric.get("key") or "").strip().lower()
    rule = get_table_rule(symbol, metric_key)
    if not rule:
        raise ValueError("No verified filing-table rule exists for this ticker/metric.")
    extractor = _EXTRACTORS[rule["extractor"]]
    filings = company_driver_filings.recent_periodic_filings(
        symbol,
        limit=max(1, min(int(filing_limit), 32)),
        forms=("10-K", "10-Q", "10-K/A", "10-Q/A"),
    )
    rows: List[Dict[str, Any]] = []
    checked = []
    for filing in filings:
        html = company_driver_filings._sec_get_text(filing["source_url"])
        soup = BeautifulSoup(html, "html.parser")
        extracted = extractor(soup, filing)
        rows.extend(extracted)
        checked.append({
            "form": filing.get("form"), "filing_date": filing.get("filing_date"),
            "report_date": filing.get("report_date"), "accession": filing.get("accession"),
            "source_url": filing.get("source_url"), "observation_count": len(extracted),
        })

    # Latest filed observation wins for a repeated economic period.
    best: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("period_type") or ""), str(row.get("end") or row.get("instant") or ""))
        current = best.get(key)
        if current is None or (str(row.get("filing_date") or ""), str(row.get("accession") or "")) > (str(current.get("filing_date") or ""), str(current.get("accession") or "")):
            best[key] = row
    rows = sorted(best.values(), key=lambda row: str(row.get("end") or row.get("instant") or ""))

    period_key = str(period or "quarterly").strip().lower()
    if period_key == "annual":
        rows = [row for row in rows if row.get("period_type") == "annual"]
    elif period_key == "quarterly":
        # Instant operating KPIs such as CET1/memberships are quarter-end observations.
        rows = [row for row in rows if row.get("period_type") in {"quarter", "instant"}]
    elif period_key != "reported":
        raise ValueError("period must be one of: quarterly, annual, reported")

    rows = rows[-max(1, min(int(observation_limit), 100)):]
    dates = [str(row.get("end") or row.get("instant") or "") for row in rows if row.get("end") or row.get("instant")]
    return {
        "history_version": TABLE_HISTORY_VERSION,
        "ticker": symbol,
        "metric": metric_key,
        "metric_label": metric.get("label"),
        "period": period_key,
        "data_state": "verified_history",
        "verified": True,
        "source_mode": "sec_filing_table",
        "verified_rule": rule,
        "filings_checked": checked,
        "observation_count": len(rows),
        "coverage": {"start": min(dates) if dates else None, "end": max(dates) if dates else None},
        "observations": rows,
        "policies": {
            "missing_periods": "Never interpolated.",
            "duplicates": "Latest filed observation wins a repeated period.",
            "provenance": "Every observation retains its source filing and accession.",
        },
    }


def table_schema() -> Dict[str, Any]:
    return {
        "version": TABLE_HISTORY_VERSION,
        "phase": "3C",
        "rules": [
            {"ticker": ticker, "metric": metric, **rule}
            for (ticker, metric), rule in sorted(VERIFIED_TABLE_RULES.items())
        ],
    }
