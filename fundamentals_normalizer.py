"""SethiStock Phase 2B: normalise SEC Company Facts into a stable metric schema.

The SEC Company Facts API exposes issuer facts under XBRL concepts that can change
across filing history. This module maps compatible standard-taxonomy concepts into
stable SethiStock metrics while preserving filing provenance and raw period dates.

Period classification (Annual / Quarterly / TTM) intentionally belongs to Phase 2D.
"""

from __future__ import annotations

from datetime import date
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


NORMALIZATION_SCHEMA_VERSION = "2b-v1"
SOURCE_NAME = "SEC EDGAR Company Facts"

ACCEPTED_FORMS = {
    "10-K",
    "10-K/A",
    "10-Q",
    "10-Q/A",
    "20-F",
    "20-F/A",
    "40-F",
    "40-F/A",
}

# Candidate order is meaningful. Earlier concepts win when two compatible concepts
# report the same economic period. Lower-priority concepts can still fill older gaps.
DIRECT_METRICS: Dict[str, Dict[str, Any]] = {
    "revenue": {
        "label": "Revenue",
        "kind": "flow",
        "unit": "USD",
        "candidates": [
            ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
            ("us-gaap", "Revenues"),
            ("us-gaap", "SalesRevenueNet"),
        ],
    },
    "net_income": {
        "label": "Net Income",
        "kind": "flow",
        "unit": "USD",
        "candidates": [
            ("us-gaap", "NetIncomeLoss"),
            ("us-gaap", "ProfitLoss"),
        ],
    },
    "operating_cash_flow": {
        "label": "Operating Cash Flow",
        "kind": "flow",
        "unit": "USD",
        "candidates": [
            ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
            ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
        ],
    },
    "capex": {
        "label": "Capital Expenditure",
        "kind": "flow",
        "unit": "USD",
        "candidates": [
            ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
            ("us-gaap", "PaymentsForAdditionsToPropertyPlantAndEquipment"),
            ("us-gaap", "PaymentsToAcquireProductiveAssets"),
        ],
    },
    "cash": {
        "label": "Cash & Cash Equivalents",
        "kind": "instant",
        "unit": "USD",
        "candidates": [
            ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
            ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
        ],
    },
    "shares": {
        "label": "Shares Outstanding",
        "kind": "instant",
        "unit": "shares",
        "candidates": [
            ("dei", "EntityCommonStockSharesOutstanding"),
            ("us-gaap", "CommonStockSharesOutstanding"),
        ],
    },
    "gross_profit": {
        "label": "Gross Profit",
        "kind": "flow",
        "unit": "USD",
        "candidates": [("us-gaap", "GrossProfit")],
    },
    "operating_income": {
        "label": "Operating Income",
        "kind": "flow",
        "unit": "USD",
        "candidates": [("us-gaap", "OperatingIncomeLoss")],
    },
}

# Debt is a derived instant metric. Each component group selects at most one concept
# per balance-sheet date, preventing synonym concepts from being double-counted.
DEBT_COMPONENTS: Dict[str, List[Tuple[str, str]]] = {
    "long_term_current": [
        ("us-gaap", "LongTermDebtCurrent"),
        ("us-gaap", "LongTermDebtAndFinanceLeaseObligationsCurrent"),
    ],
    "long_term_noncurrent": [
        ("us-gaap", "LongTermDebtNoncurrent"),
        ("us-gaap", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"),
    ],
    "short_term": [
        ("us-gaap", "ShortTermBorrowings"),
        ("us-gaap", "ShortTermDebt"),
    ],
}

NORMALIZED_METRIC_ORDER = [
    "revenue",
    "net_income",
    "operating_cash_flow",
    "capex",
    "free_cash_flow",
    "cash",
    "debt",
    "shares",
    "gross_margin",
    "operating_margin",
    "net_margin",
]

DERIVED_METRIC_INFO = {
    "free_cash_flow": {
        "label": "Free Cash Flow",
        "kind": "flow",
        "unit": "USD",
        "formula": "operating_cash_flow - abs(capex)",
    },
    "debt": {
        "label": "Debt",
        "kind": "instant",
        "unit": "USD",
        "formula": "long_term_current + long_term_noncurrent + short_term (available components)",
    },
    "gross_margin": {
        "label": "Gross Margin",
        "kind": "flow",
        "unit": "ratio",
        "formula": "gross_profit / revenue",
    },
    "operating_margin": {
        "label": "Operating Margin",
        "kind": "flow",
        "unit": "ratio",
        "formula": "operating_income / revenue",
    },
    "net_margin": {
        "label": "Net Margin",
        "kind": "flow",
        "unit": "ratio",
        "formula": "net_income / revenue",
    },
}


def _parse_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value))
    except Exception:
        return None


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _duration_days(start: Any, end: Any) -> Optional[int]:
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    if not start_date or not end_date:
        return None
    days = (end_date - start_date).days
    return days if days >= 0 else None


def _row_priority(row: Dict[str, Any]) -> Tuple[int, str, str]:
    """Prefer higher-priority concepts, then the latest filed/restated disclosure."""
    return (
        int(row.get("concept_rank", 9999)),
        str(row.get("filed") or "9999-99-99"),
        str(row.get("accn") or ""),
    )


def _should_replace(existing: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    existing_rank = int(existing.get("concept_rank", 9999))
    candidate_rank = int(candidate.get("concept_rank", 9999))
    if candidate_rank != existing_rank:
        return candidate_rank < existing_rank
    # For the same concept/period, keep the latest filed value so amendments and
    # subsequent comparative disclosures can supersede older disclosures.
    return (
        str(candidate.get("filed") or ""),
        str(candidate.get("accn") or ""),
    ) > (
        str(existing.get("filed") or ""),
        str(existing.get("accn") or ""),
    )


def _observation_key(row: Dict[str, Any], kind: str) -> Tuple[Any, ...]:
    if kind == "instant":
        return (row.get("end"), row.get("unit"))
    return (row.get("start"), row.get("end"), row.get("unit"))


def _clean_fact_row(
    raw: Dict[str, Any],
    *,
    taxonomy: str,
    concept: str,
    concept_rank: int,
    unit: str,
    kind: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    form = str(raw.get("form") or "").strip().upper()
    if form not in ACCEPTED_FORMS:
        return None
    value = _finite_number(raw.get("val"))
    end = str(raw.get("end") or "").strip()
    if value is None or not end or _parse_date(end) is None:
        return None

    start = str(raw.get("start") or "").strip() or None
    if kind == "flow" and (not start or _parse_date(start) is None):
        return None

    observation = {
        "value": value,
        "unit": unit,
        "start": start if kind == "flow" else None,
        "end": end,
        "filed": str(raw.get("filed") or "").strip() or None,
        "fy": raw.get("fy"),
        "fp": str(raw.get("fp") or "").strip() or None,
        "form": form,
        "frame": str(raw.get("frame") or "").strip() or None,
        "accn": str(raw.get("accn") or "").strip() or None,
        "taxonomy": taxonomy,
        "concept": concept,
        "concept_rank": concept_rank,
        "duration_days": _duration_days(start, end) if kind == "flow" else None,
        "derived": False,
        "source": SOURCE_NAME,
    }
    return observation


def _concept_rows(
    companyfacts: Dict[str, Any],
    taxonomy: str,
    concept: str,
    unit: str,
    kind: str,
    concept_rank: int,
) -> List[Dict[str, Any]]:
    payload = (((companyfacts.get("facts") or {}).get(taxonomy) or {}).get(concept) or {})
    units = payload.get("units") or {}
    rows = units.get(unit) or []
    output = []
    if not isinstance(rows, list):
        return output
    for raw in rows:
        clean = _clean_fact_row(
            raw,
            taxonomy=taxonomy,
            concept=concept,
            concept_rank=concept_rank,
            unit=unit,
            kind=kind,
        )
        if clean is not None:
            output.append(clean)
    return output


def _finalise_observations(rows: Iterable[Dict[str, Any]], kind: str, limit: int) -> List[Dict[str, Any]]:
    deduped: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for row in rows:
        key = _observation_key(row, kind)
        existing = deduped.get(key)
        if existing is None or _should_replace(existing, row):
            deduped[key] = row

    ordered = sorted(
        deduped.values(),
        key=lambda row: (
            str(row.get("end") or ""),
            str(row.get("start") or ""),
            str(row.get("filed") or ""),
        ),
    )
    if limit > 0 and len(ordered) > limit:
        ordered = ordered[-limit:]
    return ordered


def extract_direct_metric(companyfacts: Dict[str, Any], metric: str, limit: int = 250) -> Dict[str, Any]:
    definition = DIRECT_METRICS[metric]
    all_rows: List[Dict[str, Any]] = []
    for rank, (taxonomy, concept) in enumerate(definition["candidates"]):
        all_rows.extend(
            _concept_rows(
                companyfacts,
                taxonomy=taxonomy,
                concept=concept,
                unit=definition["unit"],
                kind=definition["kind"],
                concept_rank=rank,
            )
        )
    observations = _finalise_observations(all_rows, definition["kind"], limit)
    return _metric_payload(
        metric=metric,
        label=definition["label"],
        kind=definition["kind"],
        unit=definition["unit"],
        observations=observations,
        derived=False,
        formula=None,
    )


def _period_key(row: Dict[str, Any]) -> Tuple[Any, Any]:
    return (row.get("start"), row.get("end"))


def _by_period(observations: Sequence[Dict[str, Any]]) -> Dict[Tuple[Any, Any], Dict[str, Any]]:
    return {_period_key(row): row for row in observations if row.get("end")}


def _derived_flow_row(
    *,
    value: float,
    unit: str,
    components: Sequence[Tuple[str, Dict[str, Any]]],
    formula: str,
) -> Dict[str, Any]:
    anchor = components[0][1]
    filed_values = [str(row.get("filed") or "") for _, row in components if row.get("filed")]
    return {
        "value": value,
        "unit": unit,
        "start": anchor.get("start"),
        "end": anchor.get("end"),
        "filed": max(filed_values) if filed_values else None,
        "fy": anchor.get("fy"),
        "fp": anchor.get("fp"),
        "form": anchor.get("form"),
        "frame": anchor.get("frame"),
        "accn": anchor.get("accn"),
        "taxonomy": "sethistock",
        "concept": None,
        "concept_rank": None,
        "duration_days": anchor.get("duration_days"),
        "derived": True,
        "formula": formula,
        "components": [
            {
                "metric": metric,
                "value": row.get("value"),
                "taxonomy": row.get("taxonomy"),
                "concept": row.get("concept"),
                "accn": row.get("accn"),
            }
            for metric, row in components
        ],
        "source": SOURCE_NAME,
    }


def derive_free_cash_flow(
    operating_cash_flow: Dict[str, Any],
    capex: Dict[str, Any],
    limit: int = 250,
) -> Dict[str, Any]:
    ocf = _by_period(operating_cash_flow["observations"])
    capex_by_period = _by_period(capex["observations"])
    rows = []
    for period, ocf_row in ocf.items():
        capex_row = capex_by_period.get(period)
        if capex_row is None:
            continue
        value = float(ocf_row["value"]) - abs(float(capex_row["value"]))
        rows.append(
            _derived_flow_row(
                value=value,
                unit="USD",
                components=[("operating_cash_flow", ocf_row), ("capex", capex_row)],
                formula=DERIVED_METRIC_INFO["free_cash_flow"]["formula"],
            )
        )
    rows.sort(key=lambda row: (str(row.get("end") or ""), str(row.get("start") or "")))
    if limit > 0 and len(rows) > limit:
        rows = rows[-limit:]
    return _metric_payload(
        metric="free_cash_flow",
        label="Free Cash Flow",
        kind="flow",
        unit="USD",
        observations=rows,
        derived=True,
        formula=DERIVED_METRIC_INFO["free_cash_flow"]["formula"],
    )


def derive_margin(
    metric: str,
    numerator_metric: str,
    numerator: Dict[str, Any],
    revenue: Dict[str, Any],
    limit: int = 250,
) -> Dict[str, Any]:
    numerator_by_period = _by_period(numerator["observations"])
    revenue_by_period = _by_period(revenue["observations"])
    rows = []
    for period, numerator_row in numerator_by_period.items():
        revenue_row = revenue_by_period.get(period)
        if revenue_row is None:
            continue
        denominator = float(revenue_row["value"])
        if denominator == 0:
            continue
        value = float(numerator_row["value"]) / denominator
        if not math.isfinite(value):
            continue
        rows.append(
            _derived_flow_row(
                value=value,
                unit="ratio",
                components=[(numerator_metric, numerator_row), ("revenue", revenue_row)],
                formula=DERIVED_METRIC_INFO[metric]["formula"],
            )
        )
    rows.sort(key=lambda row: (str(row.get("end") or ""), str(row.get("start") or "")))
    if limit > 0 and len(rows) > limit:
        rows = rows[-limit:]
    info = DERIVED_METRIC_INFO[metric]
    return _metric_payload(
        metric=metric,
        label=info["label"],
        kind="flow",
        unit="ratio",
        observations=rows,
        derived=True,
        formula=info["formula"],
    )


def _extract_component_group(
    companyfacts: Dict[str, Any],
    candidates: Sequence[Tuple[str, str]],
    limit: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for rank, (taxonomy, concept) in enumerate(candidates):
        rows.extend(
            _concept_rows(
                companyfacts,
                taxonomy=taxonomy,
                concept=concept,
                unit="USD",
                kind="instant",
                concept_rank=rank,
            )
        )
    return _finalise_observations(rows, "instant", limit)


def derive_debt(companyfacts: Dict[str, Any], limit: int = 250) -> Dict[str, Any]:
    groups = {
        name: _extract_component_group(companyfacts, candidates, max(limit, 500))
        for name, candidates in DEBT_COMPONENTS.items()
    }
    by_group = {
        name: {row.get("end"): row for row in rows if row.get("end")}
        for name, rows in groups.items()
    }
    all_dates = sorted({end for values in by_group.values() for end in values.keys() if end})
    derived = []
    for end in all_dates:
        components: List[Tuple[str, Dict[str, Any]]] = []
        for group_name in ("long_term_current", "long_term_noncurrent", "short_term"):
            row = by_group[group_name].get(end)
            if row is not None:
                components.append((group_name, row))
        if not components:
            continue
        value = sum(float(row["value"]) for _, row in components)
        anchor = max(
            (row for _, row in components),
            key=lambda row: str(row.get("filed") or ""),
        )
        derived.append(
            {
                "value": value,
                "unit": "USD",
                "start": None,
                "end": end,
                "filed": max(str(row.get("filed") or "") for _, row in components) or None,
                "fy": anchor.get("fy"),
                "fp": anchor.get("fp"),
                "form": anchor.get("form"),
                "frame": anchor.get("frame"),
                "accn": anchor.get("accn"),
                "taxonomy": "sethistock",
                "concept": None,
                "concept_rank": None,
                "duration_days": None,
                "derived": True,
                "formula": DERIVED_METRIC_INFO["debt"]["formula"],
                "components": [
                    {
                        "metric": component_name,
                        "value": row.get("value"),
                        "taxonomy": row.get("taxonomy"),
                        "concept": row.get("concept"),
                        "accn": row.get("accn"),
                    }
                    for component_name, row in components
                ],
                "source": SOURCE_NAME,
            }
        )
    if limit > 0 and len(derived) > limit:
        derived = derived[-limit:]
    return _metric_payload(
        metric="debt",
        label="Debt",
        kind="instant",
        unit="USD",
        observations=derived,
        derived=True,
        formula=DERIVED_METRIC_INFO["debt"]["formula"],
    )


def _metric_payload(
    *,
    metric: str,
    label: str,
    kind: str,
    unit: str,
    observations: Sequence[Dict[str, Any]],
    derived: bool,
    formula: Optional[str],
) -> Dict[str, Any]:
    concepts_used = sorted(
        {
            f"{row.get('taxonomy')}:{row.get('concept')}"
            for row in observations
            if row.get("taxonomy") and row.get("concept")
        }
    )
    ends = [str(row.get("end")) for row in observations if row.get("end")]
    payload = {
        "metric": metric,
        "label": label,
        "kind": kind,
        "unit": unit,
        "derived": derived,
        "count": len(observations),
        "coverage": {
            "earliest_end": min(ends) if ends else None,
            "latest_end": max(ends) if ends else None,
            "concepts_used": concepts_used,
        },
        "observations": list(observations),
    }
    if formula:
        payload["formula"] = formula
    return payload


def build_normalized_fundamentals(
    companyfacts: Dict[str, Any],
    *,
    metrics: Optional[Sequence[str]] = None,
    limit: int = 250,
) -> Dict[str, Dict[str, Any]]:
    """Build the stable 2B schema from a raw SEC Company Facts payload."""
    limit = max(1, int(limit))
    requested = list(metrics) if metrics else list(NORMALIZED_METRIC_ORDER)
    unknown = [metric for metric in requested if metric not in NORMALIZED_METRIC_ORDER]
    if unknown:
        raise ValueError(f"Unknown normalised metrics: {', '.join(sorted(set(unknown)))}")

    # Derived metrics need internal source metrics even when the caller did not ask
    # to return those sources explicitly.
    required_direct = set()
    for metric in requested:
        if metric in DIRECT_METRICS:
            required_direct.add(metric)
        elif metric == "free_cash_flow":
            required_direct.update({"operating_cash_flow", "capex"})
        elif metric == "gross_margin":
            required_direct.update({"gross_profit", "revenue"})
        elif metric == "operating_margin":
            required_direct.update({"operating_income", "revenue"})
        elif metric == "net_margin":
            required_direct.update({"net_income", "revenue"})

    direct = {
        metric: extract_direct_metric(companyfacts, metric, limit=max(limit, 500))
        for metric in required_direct
    }

    output: Dict[str, Dict[str, Any]] = {}
    for metric in requested:
        if metric in DIRECT_METRICS:
            payload = direct[metric]
            observations = payload["observations"][-limit:]
            output[metric] = _metric_payload(
                metric=metric,
                label=payload["label"],
                kind=payload["kind"],
                unit=payload["unit"],
                observations=observations,
                derived=False,
                formula=None,
            )
        elif metric == "free_cash_flow":
            output[metric] = derive_free_cash_flow(
                direct["operating_cash_flow"], direct["capex"], limit=limit
            )
        elif metric == "debt":
            output[metric] = derive_debt(companyfacts, limit=limit)
        elif metric == "gross_margin":
            output[metric] = derive_margin(
                "gross_margin", "gross_profit", direct["gross_profit"], direct["revenue"], limit=limit
            )
        elif metric == "operating_margin":
            output[metric] = derive_margin(
                "operating_margin", "operating_income", direct["operating_income"], direct["revenue"], limit=limit
            )
        elif metric == "net_margin":
            output[metric] = derive_margin(
                "net_margin", "net_income", direct["net_income"], direct["revenue"], limit=limit
            )
    return output


def get_normalization_schema() -> Dict[str, Any]:
    direct = {}
    for metric, definition in DIRECT_METRICS.items():
        direct[metric] = {
            "label": definition["label"],
            "kind": definition["kind"],
            "unit": definition["unit"],
            "candidates": [
                {"taxonomy": taxonomy, "concept": concept, "priority": rank}
                for rank, (taxonomy, concept) in enumerate(definition["candidates"])
            ],
        }
    return {
        "schema_version": NORMALIZATION_SCHEMA_VERSION,
        "metrics": list(NORMALIZED_METRIC_ORDER),
        "direct_metrics": direct,
        "debt_components": {
            group: [
                {"taxonomy": taxonomy, "concept": concept, "priority": rank}
                for rank, (taxonomy, concept) in enumerate(candidates)
            ]
            for group, candidates in DEBT_COMPONENTS.items()
        },
        "derived_metrics": DERIVED_METRIC_INFO,
        "accepted_forms": sorted(ACCEPTED_FORMS),
        "period_classification": "deferred_to_phase_2d",
    }
