"""SethiStock Phase 2D: classify normalized SEC facts into Annual / Quarterly / TTM series.

The Phase 2B normalizer deliberately preserves raw SEC periods. This module applies
conservative fiscal-period logic on top of those observations:

- Annual flows use full-year duration facts.
- Quarterly flows prefer reported ~3 month facts.
- YTD-only flows are converted to standalone quarters by subtraction.
- Q4 can be derived from FY less the first nine months.
- TTM flow metrics roll four consecutive standalone quarters.
- Ratio metrics are rebuilt from numerator/denominator components, never by adding
  or subtracting percentages.
- Instant metrics (cash, debt, shares) remain point-in-time for all views.
"""

from __future__ import annotations

from datetime import date, timedelta
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PERIOD_ENGINE_VERSION = "2d-v1"

ANNUAL_MIN_DAYS = 300
ANNUAL_MAX_DAYS = 430
QUARTER_MIN_DAYS = 60
QUARTER_MAX_DAYS = 130
FISCAL_START_TOLERANCE_DAYS = 14
PERIOD_END_TOLERANCE_DAYS = 7
QUARTER_END_SEARCH_TOLERANCE_DAYS = 50

ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
QUARTER_FORMS = {"10-Q", "10-Q/A", "6-K", "6-K/A"}

FISCAL_ANCHOR_METRICS = (
    "revenue",
    "net_income",
    "operating_cash_flow",
    "free_cash_flow",
)

RATIO_COMPONENTS = {
    "gross_margin": ("gross_profit", "revenue"),
    "operating_margin": ("operating_income", "revenue"),
    "net_margin": ("net_income", "revenue"),
}


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except Exception:
        return None


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _days_between(start: Any, end: Any) -> Optional[int]:
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    if not start_date or not end_date:
        return None
    days = (end_date - start_date).days
    return days if days >= 0 else None


def _duration(row: Dict[str, Any]) -> Optional[int]:
    raw = row.get("duration_days")
    if raw is not None:
        try:
            value = int(raw)
            if value >= 0:
                return value
        except Exception:
            pass
    return _days_between(row.get("start"), row.get("end"))


def _is_annual_flow(row: Dict[str, Any]) -> bool:
    duration = _duration(row)
    if duration is None or not (ANNUAL_MIN_DAYS <= duration <= ANNUAL_MAX_DAYS):
        return False
    form = str(row.get("form") or "").upper()
    fp = str(row.get("fp") or "").upper()
    frame = str(row.get("frame") or "")
    return form in ANNUAL_FORMS or fp == "FY" or bool(re.fullmatch(r"CY\d{4}", frame))


def _same_day_next_year(value: date) -> date:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        return value.replace(year=value.year + 1, day=28)


def _latest_filed(rows: Iterable[Dict[str, Any]]) -> Optional[str]:
    values = [str(row.get("filed")) for row in rows if row.get("filed")]
    return max(values) if values else None


def _best_row(rows: Sequence[Dict[str, Any]], score) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    return min(
        rows,
        key=lambda row: (
            score(row),
            -int(str(row.get("filed") or "0000-00-00").replace("-", "") or 0),
            str(row.get("accn") or ""),
        ),
    )


def _source_ref(row: Dict[str, Any]) -> Dict[str, Any]:
    ref = {
        "value": row.get("value"),
        "start": row.get("start"),
        "end": row.get("end"),
        "filed": row.get("filed"),
        "form": row.get("form"),
        "accn": row.get("accn"),
        "taxonomy": row.get("taxonomy"),
        "concept": row.get("concept"),
    }
    if row.get("calculation"):
        ref["calculation"] = row.get("calculation")
    if row.get("formula"):
        ref["formula"] = row.get("formula")
    return ref


def _point(
    *,
    value: float,
    unit: str,
    fiscal_year: int,
    fiscal_quarter: Optional[int],
    start: Optional[date],
    end: date,
    calculation: str,
    components: Sequence[Dict[str, Any]],
    source_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    label = f"FY{fiscal_year}" if fiscal_quarter is None else f"FY{fiscal_year} Q{fiscal_quarter}"
    point = {
        "label": label,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "start": start.isoformat() if start else None,
        "end": end.isoformat(),
        "value": value,
        "unit": unit,
        "calculation": calculation,
        "derived": calculation != "reported",
        "filed": _latest_filed(components) if components else (source_row or {}).get("filed"),
        "source": "SEC EDGAR Company Facts",
        "components": [_source_ref(row) for row in components],
    }
    if source_row:
        point["form"] = source_row.get("form")
        point["accn"] = source_row.get("accn")
        point["taxonomy"] = source_row.get("taxonomy")
        point["concept"] = source_row.get("concept")
    else:
        point["form"] = "derived"
        point["accn"] = None
        point["taxonomy"] = "sethistock"
        point["concept"] = None
    return point


def _annual_anchor_rows(normalized: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    best_rows: List[Dict[str, Any]] = []
    best_metric_rank = 999
    for rank, metric in enumerate(FISCAL_ANCHOR_METRICS):
        payload = normalized.get(metric) or {}
        if payload.get("kind") != "flow":
            continue
        candidates = [row for row in payload.get("observations") or [] if _is_annual_flow(row)]
        if not candidates:
            continue
        by_end: Dict[str, List[Dict[str, Any]]] = {}
        for row in candidates:
            if _parse_date(row.get("start")) and _parse_date(row.get("end")):
                by_end.setdefault(str(row.get("end")), []).append(row)
        selected = []
        for rows in by_end.values():
            chosen = _best_row(
                rows,
                lambda row: (
                    0 if str(row.get("form") or "").upper() in ANNUAL_FORMS else 1,
                    abs((_duration(row) or 365) - 365),
                ),
            )
            if chosen:
                selected.append(chosen)
        if len(selected) > len(best_rows) or (len(selected) == len(best_rows) and rank < best_metric_rank):
            best_rows = selected
            best_metric_rank = rank
    return sorted(best_rows, key=lambda row: str(row.get("end") or ""))


def _max_observation_end(normalized: Dict[str, Dict[str, Any]]) -> Optional[date]:
    ends = []
    for payload in normalized.values():
        for row in payload.get("observations") or []:
            parsed = _parse_date(row.get("end"))
            if parsed:
                ends.append(parsed)
    return max(ends) if ends else None


def infer_fiscal_periods(normalized: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    anchors = _annual_anchor_rows(normalized)
    periods = []
    for row in anchors:
        start = _parse_date(row.get("start"))
        end = _parse_date(row.get("end"))
        if not start or not end or end <= start:
            continue
        periods.append(
            {
                "sequence": len(periods),
                "fiscal_year": end.year,
                "start": start,
                "end": end,
                "expected_end": end,
                "completed": True,
                "anchor": row,
            }
        )

    if not periods:
        return []

    latest_end = periods[-1]["end"]
    max_end = _max_observation_end(normalized)
    open_start = latest_end + timedelta(days=1)
    expected_end = _same_day_next_year(latest_end)
    if max_end and max_end >= open_start + timedelta(days=45):
        periods.append(
            {
                "sequence": len(periods),
                "fiscal_year": expected_end.year,
                "start": open_start,
                "end": expected_end,
                "expected_end": expected_end,
                "completed": False,
                "anchor": None,
            }
        )
    return periods


def _period_observation_end_candidates(
    normalized: Dict[str, Dict[str, Any]],
    period: Dict[str, Any],
) -> Dict[date, int]:
    start = period["start"]
    end = period["expected_end"]
    scores: Dict[date, int] = {}
    for metric in FISCAL_ANCHOR_METRICS:
        payload = normalized.get(metric) or {}
        if payload.get("kind") != "flow":
            continue
        seen_for_metric = set()
        for row in payload.get("observations") or []:
            row_end = _parse_date(row.get("end"))
            row_start = _parse_date(row.get("start"))
            duration = _duration(row)
            if not row_end or not row_start or duration is None:
                continue
            if not (start < row_end < end):
                continue
            if not (45 <= duration <= 330):
                continue
            if duration <= QUARTER_MAX_DAYS or abs((row_start - start).days) <= FISCAL_START_TOLERANCE_DAYS:
                seen_for_metric.add(row_end)
        for row_end in seen_for_metric:
            scores[row_end] = scores.get(row_end, 0) + 1
    return scores


def infer_quarter_ends(
    normalized: Dict[str, Dict[str, Any]],
    period: Dict[str, Any],
) -> Dict[int, date]:
    scores = _period_observation_end_candidates(normalized, period)
    if not scores:
        return {}

    fiscal_days = max(320, (period["expected_end"] - period["start"]).days)
    chosen: Dict[int, date] = {}
    used = set()
    previous = period["start"]

    for quarter in (1, 2, 3):
        expected = period["start"] + timedelta(days=round(fiscal_days * quarter / 4))
        candidates = [
            (candidate, count)
            for candidate, count in scores.items()
            if candidate not in used and candidate > previous
        ]
        if not candidates:
            break
        candidate, count = min(
            candidates,
            key=lambda item: (
                abs((item[0] - expected).days) - min(item[1], 4) * 2,
                -item[1],
                item[0],
            ),
        )
        if abs((candidate - expected).days) > QUARTER_END_SEARCH_TOLERANCE_DAYS:
            break
        chosen[quarter] = candidate
        used.add(candidate)
        previous = candidate

    if period["completed"]:
        chosen[4] = period["end"]
    return chosen


def _annual_flow_row(payload: Dict[str, Any], period: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    candidates = []
    for row in payload.get("observations") or []:
        if not _is_annual_flow(row):
            continue
        start = _parse_date(row.get("start"))
        end = _parse_date(row.get("end"))
        if not start or not end:
            continue
        if abs((end - period["end"]).days) > PERIOD_END_TOLERANCE_DAYS:
            continue
        if abs((start - period["start"]).days) > FISCAL_START_TOLERANCE_DAYS:
            continue
        candidates.append(row)
    return _best_row(
        candidates,
        lambda row: (
            abs((_parse_date(row.get("end")) - period["end"]).days),
            abs((_parse_date(row.get("start")) - period["start"]).days),
            0 if str(row.get("form") or "").upper() in ANNUAL_FORMS else 1,
            abs((_duration(row) or 365) - (period["end"] - period["start"]).days),
        ),
    )


def _direct_quarter_row(
    payload: Dict[str, Any],
    period: Dict[str, Any],
    quarter: int,
    quarter_end: date,
    previous_end: date,
) -> Optional[Dict[str, Any]]:
    expected_start = period["start"] if quarter == 1 else previous_end + timedelta(days=1)
    candidates = []
    for row in payload.get("observations") or []:
        end = _parse_date(row.get("end"))
        start = _parse_date(row.get("start"))
        duration = _duration(row)
        if not end or not start or duration is None:
            continue
        if abs((end - quarter_end).days) > PERIOD_END_TOLERANCE_DAYS:
            continue
        if not (QUARTER_MIN_DAYS <= duration <= QUARTER_MAX_DAYS):
            continue
        if abs((start - expected_start).days) > 28:
            continue
        candidates.append(row)
    return _best_row(
        candidates,
        lambda row: (
            abs((_parse_date(row.get("end")) - quarter_end).days),
            abs((_parse_date(row.get("start")) - expected_start).days),
            0 if str(row.get("form") or "").upper() in QUARTER_FORMS else 1,
            abs((_duration(row) or 91) - 91),
        ),
    )


def _cumulative_row(
    payload: Dict[str, Any],
    period: Dict[str, Any],
    quarter_end: date,
) -> Optional[Dict[str, Any]]:
    candidates = []
    for row in payload.get("observations") or []:
        end = _parse_date(row.get("end"))
        start = _parse_date(row.get("start"))
        duration = _duration(row)
        if not end or not start or duration is None:
            continue
        if abs((end - quarter_end).days) > PERIOD_END_TOLERANCE_DAYS:
            continue
        if abs((start - period["start"]).days) > FISCAL_START_TOLERANCE_DAYS:
            continue
        if duration > 330:
            continue
        candidates.append(row)
    return _best_row(
        candidates,
        lambda row: (
            abs((_parse_date(row.get("end")) - quarter_end).days),
            abs((_parse_date(row.get("start")) - period["start"]).days),
            -(_duration(row) or 0),
        ),
    )


def _flow_annual_series(
    payload: Dict[str, Any],
    periods: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    unit = payload.get("unit")
    output = []
    for period in periods:
        if not period["completed"]:
            continue
        row = _annual_flow_row(payload, period)
        if not row:
            continue
        value = _finite(row.get("value"))
        if value is None:
            continue
        output.append(
            _point(
                value=value,
                unit=unit,
                fiscal_year=period["fiscal_year"],
                fiscal_quarter=None,
                start=period["start"],
                end=period["end"],
                calculation="reported",
                components=[row],
                source_row=row,
            )
        )
    return output


def _flow_quarterly_series(
    payload: Dict[str, Any],
    normalized: Dict[str, Dict[str, Any]],
    periods: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    unit = payload.get("unit")
    output = []
    for period in periods:
        quarter_ends = infer_quarter_ends(normalized, period)
        if not quarter_ends:
            continue

        quarter_values: Dict[int, Dict[str, Any]] = {}
        cumulative_values: Dict[int, Dict[str, Any]] = {}
        previous_end = period["start"] - timedelta(days=1)

        for quarter in (1, 2, 3):
            quarter_end = quarter_ends.get(quarter)
            if not quarter_end:
                continue
            direct = _direct_quarter_row(payload, period, quarter, quarter_end, previous_end)
            cumulative = _cumulative_row(payload, period, quarter_end)
            if cumulative:
                cumulative_values[quarter] = cumulative

            if direct:
                value = _finite(direct.get("value"))
                if value is not None:
                    quarter_values[quarter] = _point(
                        value=value,
                        unit=unit,
                        fiscal_year=period["fiscal_year"],
                        fiscal_quarter=quarter,
                        start=period["start"] if quarter == 1 else previous_end + timedelta(days=1),
                        end=quarter_end,
                        calculation="reported",
                        components=[direct],
                        source_row=direct,
                    )
            elif cumulative:
                cumulative_value = _finite(cumulative.get("value"))
                previous_quarters = [q for q in range(1, quarter) if q in quarter_values]
                if cumulative_value is not None and len(previous_quarters) == quarter - 1:
                    prior_total = sum(float(quarter_values[q]["value"]) for q in previous_quarters)
                    value = cumulative_value - prior_total
                    if math.isfinite(value):
                        quarter_values[quarter] = _point(
                            value=value,
                            unit=unit,
                            fiscal_year=period["fiscal_year"],
                            fiscal_quarter=quarter,
                            start=period["start"] if quarter == 1 else previous_end + timedelta(days=1),
                            end=quarter_end,
                            calculation="derived_from_ytd",
                            components=[cumulative] + [quarter_values[q] for q in previous_quarters],
                        )
            previous_end = quarter_end

        if period["completed"] and 4 in quarter_ends:
            q4_end = quarter_ends[4]
            q3_end = quarter_ends.get(3) or previous_end
            direct_q4 = _direct_quarter_row(payload, period, 4, q4_end, q3_end)
            if direct_q4 and _finite(direct_q4.get("value")) is not None:
                quarter_values[4] = _point(
                    value=float(direct_q4["value"]),
                    unit=unit,
                    fiscal_year=period["fiscal_year"],
                    fiscal_quarter=4,
                    start=q3_end + timedelta(days=1),
                    end=q4_end,
                    calculation="reported",
                    components=[direct_q4],
                    source_row=direct_q4,
                )
            else:
                annual = _annual_flow_row(payload, period)
                annual_value = _finite((annual or {}).get("value"))
                q3_ytd = cumulative_values.get(3)
                q3_ytd_value = _finite((q3_ytd or {}).get("value"))
                if annual_value is not None and q3_ytd_value is not None:
                    value = annual_value - q3_ytd_value
                    if math.isfinite(value):
                        quarter_values[4] = _point(
                            value=value,
                            unit=unit,
                            fiscal_year=period["fiscal_year"],
                            fiscal_quarter=4,
                            start=q3_end + timedelta(days=1),
                            end=q4_end,
                            calculation="derived_from_fy_less_9m",
                            components=[annual, q3_ytd],
                        )
                elif annual_value is not None and all(q in quarter_values for q in (1, 2, 3)):
                    value = annual_value - sum(float(quarter_values[q]["value"]) for q in (1, 2, 3))
                    if math.isfinite(value):
                        quarter_values[4] = _point(
                            value=value,
                            unit=unit,
                            fiscal_year=period["fiscal_year"],
                            fiscal_quarter=4,
                            start=q3_end + timedelta(days=1),
                            end=q4_end,
                            calculation="derived_from_fy_less_q1_q2_q3",
                            components=[annual] + [quarter_values[q] for q in (1, 2, 3)],
                        )

        # A concept/taxonomy change can leave historical standalone quarters on a
        # different accounting basis from a later-restated annual fact. Never invent
        # a balancing quarter merely to force the identity. Preserve the reported
        # quarters, flag the mismatch, and let TTM skip contaminated windows.
        if period["completed"] and all(q in quarter_values for q in (1, 2, 3, 4)):
            annual_reference = _annual_flow_row(payload, period)
            annual_value = _finite((annual_reference or {}).get("value"))
            if annual_value is not None:
                quarter_total = sum(float(quarter_values[q]["value"]) for q in (1, 2, 3, 4))
                gap = quarter_total - annual_value
                tolerance = max(1.0, abs(annual_value) * 1e-6)
                status = "reconciled" if abs(gap) <= tolerance else "basis_mismatch"
                annual_concept = (
                    f"{annual_reference.get('taxonomy')}:{annual_reference.get('concept')}"
                    if annual_reference and annual_reference.get("taxonomy") and annual_reference.get("concept")
                    else None
                )
                quarter_concepts = sorted(
                    {
                        f"{component.get('taxonomy')}:{component.get('concept')}"
                        for q in (1, 2, 3, 4)
                        for component in (quarter_values[q].get("components") or [])
                        if isinstance(component, dict)
                        and component.get("taxonomy")
                        and component.get("concept")
                    }
                )
                basis_change = bool(
                    status == "basis_mismatch"
                    and annual_concept
                    and quarter_concepts
                    and annual_concept not in quarter_concepts
                )
                for q in (1, 2, 3, 4):
                    quarter_values[q]["reconciliation_status"] = status
                    quarter_values[q]["annual_reference_value"] = annual_value
                    quarter_values[q]["annual_reconciliation_gap"] = gap
                    quarter_values[q]["annual_reconciliation_gap_pct"] = (
                        gap / annual_value if annual_value != 0 else None
                    )
                    if status == "basis_mismatch":
                        flags = ["annual_reconciliation_mismatch"]
                        if basis_change:
                            flags.append("concept_basis_change")
                        quarter_values[q]["quality_flags"] = flags
                        quarter_values[q]["annual_reference_concept"] = annual_concept
                        quarter_values[q]["quarter_source_concepts"] = quarter_concepts

        for quarter in sorted(quarter_values):
            point = quarter_values[quarter]
            point["_sequence"] = period["sequence"] * 4 + quarter
            output.append(point)

    return sorted(output, key=lambda point: (point["end"], point["fiscal_quarter"] or 0))


def _instant_row(
    payload: Dict[str, Any],
    target_end: date,
    prefer_annual: bool,
) -> Optional[Dict[str, Any]]:
    candidates = []
    for row in payload.get("observations") or []:
        end = _parse_date(row.get("end"))
        if not end or abs((end - target_end).days) > PERIOD_END_TOLERANCE_DAYS:
            continue
        if _finite(row.get("value")) is None:
            continue
        candidates.append(row)
    preferred_forms = ANNUAL_FORMS if prefer_annual else QUARTER_FORMS
    return _best_row(
        candidates,
        lambda row: (
            abs((_parse_date(row.get("end")) - target_end).days),
            0 if str(row.get("form") or "").upper() in preferred_forms else 1,
        ),
    )


def _instant_annual_series(
    payload: Dict[str, Any],
    periods: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    output = []
    for period in periods:
        if not period["completed"]:
            continue
        row = _instant_row(payload, period["end"], True)
        if not row:
            continue
        output.append(
            _point(
                value=float(row["value"]),
                unit=payload.get("unit"),
                fiscal_year=period["fiscal_year"],
                fiscal_quarter=None,
                start=None,
                end=period["end"],
                calculation="reported",
                components=[row],
                source_row=row,
            )
        )
    return output


def _instant_quarterly_series(
    payload: Dict[str, Any],
    normalized: Dict[str, Dict[str, Any]],
    periods: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    output = []
    for period in periods:
        quarter_ends = infer_quarter_ends(normalized, period)
        for quarter in (1, 2, 3, 4):
            target = quarter_ends.get(quarter)
            if not target:
                continue
            row = _instant_row(payload, target, quarter == 4)
            if not row:
                continue
            point = _point(
                value=float(row["value"]),
                unit=payload.get("unit"),
                fiscal_year=period["fiscal_year"],
                fiscal_quarter=quarter,
                start=None,
                end=target,
                calculation="reported",
                components=[row],
                source_row=row,
            )
            point["_sequence"] = period["sequence"] * 4 + quarter
            output.append(point)
    return sorted(output, key=lambda point: (point["end"], point["fiscal_quarter"] or 0))


def _ratio_component_payload(
    ratio_payload: Dict[str, Any],
    component_metric: str,
) -> Dict[str, Any]:
    observations = []
    for row in ratio_payload.get("observations") or []:
        for component in row.get("components") or []:
            if not isinstance(component, dict) or component.get("metric") != component_metric:
                continue
            value = _finite(component.get("value"))
            if value is None:
                continue
            observations.append(
                {
                    "value": value,
                    "unit": "USD",
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "filed": row.get("filed"),
                    "fy": row.get("fy"),
                    "fp": row.get("fp"),
                    "form": row.get("form"),
                    "frame": row.get("frame"),
                    "accn": component.get("accn") or row.get("accn"),
                    "taxonomy": component.get("taxonomy") or row.get("taxonomy"),
                    "concept": component.get("concept"),
                    "concept_rank": row.get("concept_rank"),
                    "duration_days": row.get("duration_days"),
                    "derived": bool(component.get("formula")),
                    "formula": component.get("formula"),
                    "components": component.get("components"),
                    "source": row.get("source") or "SEC EDGAR Company Facts",
                }
            )
            break
    return {
        "metric": component_metric,
        "label": component_metric.replace("_", " ").title(),
        "kind": "flow",
        "unit": "USD",
        "observations": observations,
    }


def _combine_ratio_points(
    numerator_points: Sequence[Dict[str, Any]],
    denominator_points: Sequence[Dict[str, Any]],
    calculation: str,
) -> List[Dict[str, Any]]:
    denominator_by_key = {
        (point["fiscal_year"], point.get("fiscal_quarter")): point
        for point in denominator_points
    }
    output = []
    for numerator in numerator_points:
        key = (numerator["fiscal_year"], numerator.get("fiscal_quarter"))
        denominator = denominator_by_key.get(key)
        if not denominator:
            continue
        denom = _finite(denominator.get("value"))
        num = _finite(numerator.get("value"))
        if denom in (None, 0) or num is None:
            continue
        value = num / denom
        if not math.isfinite(value):
            continue
        point = _point(
            value=value,
            unit="ratio",
            fiscal_year=numerator["fiscal_year"],
            fiscal_quarter=numerator.get("fiscal_quarter"),
            start=_parse_date(numerator.get("start")),
            end=_parse_date(numerator.get("end")),
            calculation=calculation,
            components=[numerator, denominator],
        )
        if "_sequence" in numerator:
            point["_sequence"] = numerator["_sequence"]
        statuses = {
            value
            for value in (
                numerator.get("reconciliation_status"),
                denominator.get("reconciliation_status"),
            )
            if value
        }
        if "basis_mismatch" in statuses:
            point["reconciliation_status"] = "basis_mismatch"
            point["quality_flags"] = ["component_reconciliation_mismatch"]
        elif statuses == {"reconciled"}:
            point["reconciliation_status"] = "reconciled"
        output.append(point)
    return output


def _ratio_annual_series(
    metric: str,
    payload: Dict[str, Any],
    periods: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    numerator_metric, denominator_metric = RATIO_COMPONENTS[metric]
    numerator_payload = _ratio_component_payload(payload, numerator_metric)
    denominator_payload = _ratio_component_payload(payload, denominator_metric)
    numerator = _flow_annual_series(numerator_payload, periods)
    denominator = _flow_annual_series(denominator_payload, periods)
    return _combine_ratio_points(numerator, denominator, "ratio_of_annual_components")


def _ratio_quarterly_series(
    metric: str,
    payload: Dict[str, Any],
    normalized: Dict[str, Dict[str, Any]],
    periods: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    numerator_metric, denominator_metric = RATIO_COMPONENTS[metric]
    numerator_payload = _ratio_component_payload(payload, numerator_metric)
    denominator_payload = _ratio_component_payload(payload, denominator_metric)
    numerator = _flow_quarterly_series(numerator_payload, normalized, periods)
    denominator = _flow_quarterly_series(denominator_payload, normalized, periods)
    ratio = _combine_ratio_points(numerator, denominator, "ratio_of_quarter_components")
    return ratio, numerator, denominator


def _consecutive_windows(points: Sequence[Dict[str, Any]]) -> Iterable[List[Dict[str, Any]]]:
    ordered = sorted(
        [
            point
            for point in points
            if point.get("_sequence") is not None
            and point.get("reconciliation_status") != "basis_mismatch"
        ],
        key=lambda point: int(point["_sequence"]),
    )
    for index in range(3, len(ordered)):
        window = ordered[index - 3 : index + 1]
        sequences = [int(point["_sequence"]) for point in window]
        if sequences == list(range(sequences[0], sequences[0] + 4)):
            yield window


def _ttm_flow_series(quarterly: Sequence[Dict[str, Any]], unit: str) -> List[Dict[str, Any]]:
    output = []
    for window in _consecutive_windows(quarterly):
        value = sum(float(point["value"]) for point in window)
        latest = window[-1]
        point = _point(
            value=value,
            unit=unit,
            fiscal_year=latest["fiscal_year"],
            fiscal_quarter=latest["fiscal_quarter"],
            start=_parse_date(window[0].get("start")),
            end=_parse_date(latest.get("end")),
            calculation="sum_4_quarters",
            components=window,
        )
        point["label"] = f"TTM {latest['label']}"
        point["_sequence"] = latest["_sequence"]
        point["window_quarters"] = [item["label"] for item in window]
        output.append(point)
    return output


def _ttm_ratio_series(
    numerator_quarters: Sequence[Dict[str, Any]],
    denominator_quarters: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    numerator_windows = {
        window[-1]["_sequence"]: window for window in _consecutive_windows(numerator_quarters)
    }
    denominator_windows = {
        window[-1]["_sequence"]: window for window in _consecutive_windows(denominator_quarters)
    }
    output = []
    for sequence in sorted(set(numerator_windows) & set(denominator_windows)):
        numerator_window = numerator_windows[sequence]
        denominator_window = denominator_windows[sequence]
        numerator = sum(float(point["value"]) for point in numerator_window)
        denominator = sum(float(point["value"]) for point in denominator_window)
        if denominator == 0:
            continue
        value = numerator / denominator
        if not math.isfinite(value):
            continue
        latest = numerator_window[-1]
        point = _point(
            value=value,
            unit="ratio",
            fiscal_year=latest["fiscal_year"],
            fiscal_quarter=latest["fiscal_quarter"],
            start=_parse_date(numerator_window[0].get("start")),
            end=_parse_date(latest.get("end")),
            calculation="ratio_of_ttm_components",
            components=[*numerator_window, *denominator_window],
        )
        point["label"] = f"TTM {latest['label']}"
        point["_sequence"] = sequence
        point["window_quarters"] = [item["label"] for item in numerator_window]
        output.append(point)
    return output


def _ttm_instant_series(quarterly: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    output = []
    for point in quarterly:
        copied = dict(point)
        copied["label"] = f"TTM {point['label']}"
        copied["calculation"] = "point_in_time"
        copied["derived"] = False
        output.append(copied)
    return output


def _strip_internal(points: Sequence[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    selected = list(points)
    if limit > 0 and len(selected) > limit:
        selected = selected[-limit:]
    output = []
    for point in selected:
        cleaned = dict(point)
        cleaned.pop("_sequence", None)
        output.append(cleaned)
    return output


def _metric_result(
    metric: str,
    payload: Dict[str, Any],
    period_type: str,
    points: Sequence[Dict[str, Any]],
    limit: int,
) -> Dict[str, Any]:
    clean = _strip_internal(points, limit)
    ends = [point["end"] for point in clean if point.get("end")]
    calculations = sorted({str(point.get("calculation")) for point in clean if point.get("calculation")})
    reconciliation_counts = {
        "reconciled": sum(1 for point in clean if point.get("reconciliation_status") == "reconciled"),
        "basis_mismatch": sum(1 for point in clean if point.get("reconciliation_status") == "basis_mismatch"),
    }
    result = {
        "metric": metric,
        "label": payload.get("label"),
        "kind": payload.get("kind"),
        "unit": payload.get("unit"),
        "period": period_type,
        "count": len(clean),
        "coverage": {
            "earliest_end": min(ends) if ends else None,
            "latest_end": max(ends) if ends else None,
        },
        "calculations": calculations,
        "quality": {"reconciliation_points": reconciliation_counts},
        "series": clean,
    }
    if payload.get("formula"):
        result["formula"] = payload.get("formula")
    if payload.get("kind") == "instant" and period_type == "ttm":
        result["ttm_semantics"] = "point_in_time"
    return result


def build_period_view(
    normalized: Dict[str, Dict[str, Any]],
    *,
    period: str,
    metrics: Optional[Sequence[str]] = None,
    limit: int = 80,
) -> Dict[str, Any]:
    period_type = str(period or "").strip().lower()
    if period_type not in {"annual", "quarterly", "ttm"}:
        raise ValueError("period must be one of: annual, quarterly, ttm")

    requested = list(metrics) if metrics else list(normalized.keys())
    unknown = [metric for metric in requested if metric not in normalized]
    if unknown:
        raise ValueError(f"Unknown normalised metrics: {', '.join(sorted(set(unknown)))}")

    periods = infer_fiscal_periods(normalized)
    if not periods:
        raise ValueError("Unable to infer fiscal periods from the normalized SEC history.")

    output: Dict[str, Dict[str, Any]] = {}
    for metric in requested:
        payload = normalized[metric]
        kind = payload.get("kind")

        if metric in RATIO_COMPONENTS:
            if period_type == "annual":
                points = _ratio_annual_series(metric, payload, periods)
            else:
                quarterly, numerator, denominator = _ratio_quarterly_series(
                    metric, payload, normalized, periods
                )
                points = quarterly if period_type == "quarterly" else _ttm_ratio_series(
                    numerator, denominator
                )
        elif kind == "instant":
            annual = _instant_annual_series(payload, periods)
            quarterly = _instant_quarterly_series(payload, normalized, periods)
            if period_type == "annual":
                points = annual
            elif period_type == "quarterly":
                points = quarterly
            else:
                points = _ttm_instant_series(quarterly)
        else:
            annual = _flow_annual_series(payload, periods)
            quarterly = _flow_quarterly_series(payload, normalized, periods)
            if period_type == "annual":
                points = annual
            elif period_type == "quarterly":
                points = quarterly
            else:
                points = _ttm_flow_series(quarterly, payload.get("unit"))

        output[metric] = _metric_result(metric, payload, period_type, points, max(1, int(limit)))

    completed = [period for period in periods if period["completed"]]
    open_period = next((period for period in reversed(periods) if not period["completed"]), None)
    return {
        "period_engine_version": PERIOD_ENGINE_VERSION,
        "period": period_type,
        "metric_order": [metric for metric in requested if metric in output],
        "metrics": output,
        "fiscal_calendar": {
            "completed_years": len(completed),
            "earliest_fiscal_year": completed[0]["fiscal_year"] if completed else None,
            "latest_completed_fiscal_year": completed[-1]["fiscal_year"] if completed else None,
            "open_fiscal_year": open_period["fiscal_year"] if open_period else None,
        },
    }


def get_period_engine_schema() -> Dict[str, Any]:
    return {
        "period_engine_version": PERIOD_ENGINE_VERSION,
        "periods": ["annual", "quarterly", "ttm"],
        "annual": {
            "flow": "reported full-year duration fact",
            "instant": "fiscal-year-end point-in-time fact",
        },
        "quarterly": {
            "flow": "reported quarter preferred; otherwise derived from fiscal YTD differences; Q4 may be FY less 9M",
            "instant": "quarter-end point-in-time fact",
        },
        "ttm": {
            "flow": "sum of four consecutive standalone quarters",
            "ratio": "ratio of summed four-quarter numerator to summed four-quarter denominator",
            "instant": "point-in-time value; never summed",
        },
        "ratio_components": {
            metric: {"numerator": numerator, "denominator": denominator}
            for metric, (numerator, denominator) in RATIO_COMPONENTS.items()
        },
    }
