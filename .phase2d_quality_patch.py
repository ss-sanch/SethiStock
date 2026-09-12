from pathlib import Path

path = Path('fundamentals_periods.py')
text = path.read_text()

if 'annual_reconciliation_gap_pct' in text:
    print('Phase 2D quality patch already present')
    raise SystemExit(0)

old_output = '''        for quarter in sorted(quarter_values):
            point = quarter_values[quarter]
            point["_sequence"] = period["sequence"] * 4 + quarter
            output.append(point)
'''
new_output = '''        # A concept/taxonomy change can leave historical standalone quarters on a
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
'''
if text.count(old_output) != 1:
    raise SystemExit('quarter output anchor mismatch')
text = text.replace(old_output, new_output, 1)

old_windows = '''    ordered = sorted(
        [point for point in points if point.get("_sequence") is not None],
        key=lambda point: int(point["_sequence"]),
    )
'''
new_windows = '''    ordered = sorted(
        [
            point
            for point in points
            if point.get("_sequence") is not None
            and point.get("reconciliation_status") != "basis_mismatch"
        ],
        key=lambda point: int(point["_sequence"]),
    )
'''
if text.count(old_windows) != 1:
    raise SystemExit('TTM window anchor mismatch')
text = text.replace(old_windows, new_windows, 1)

old_ratio = '''        if "_sequence" in numerator:
            point["_sequence"] = numerator["_sequence"]
        output.append(point)
'''
new_ratio = '''        if "_sequence" in numerator:
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
'''
if text.count(old_ratio) != 1:
    raise SystemExit('ratio propagation anchor mismatch')
text = text.replace(old_ratio, new_ratio, 1)

old_result = '''    calculations = sorted({str(point.get("calculation")) for point in clean if point.get("calculation")})
    result = {
'''
new_result = '''    calculations = sorted({str(point.get("calculation")) for point in clean if point.get("calculation")})
    reconciliation_counts = {
        "reconciled": sum(1 for point in clean if point.get("reconciliation_status") == "reconciled"),
        "basis_mismatch": sum(1 for point in clean if point.get("reconciliation_status") == "basis_mismatch"),
    }
    result = {
'''
if text.count(old_result) != 1:
    raise SystemExit('metric result anchor mismatch')
text = text.replace(old_result, new_result, 1)

old_calc = '''        "calculations": calculations,
        "series": clean,
    }
'''
new_calc = '''        "calculations": calculations,
        "quality": {"reconciliation_points": reconciliation_counts},
        "series": clean,
    }
'''
if text.count(old_calc) != 1:
    raise SystemExit('quality result anchor mismatch')
text = text.replace(old_calc, new_calc, 1)

path.write_text(text)

# Add a regression test for accounting-basis transitions where a later-restated
# annual concept is incompatible with older reported quarter concepts.
test_path = Path('tests/test_fundamentals_periods.py')
tests = test_path.read_text()
if 'test_basis_transition_is_flagged_not_fabricated' not in tests:
    marker = '''    def test_open_fiscal_year_supports_partial_quarters(self):\n'''
    addition = '''    def test_basis_transition_is_flagged_not_fabricated(self):\n        normalized = fixture()\n        # Add an explicitly reported Q4 on the older concept basis while the FY value\n        # is later restated upward under a different concept. The engine must preserve\n        # the reported Q4, flag the year, and not manufacture a balancing quarter.\n        normalized["revenue"]["observations"].append(\n            flow_row(115, "2024-10-01", "2024-12-31", 91, form="10-K", fp="FY", concept="LegacyRevenue")\n        )\n        annual = next(\n            row for row in normalized["revenue"]["observations"]\n            if row["start"] == "2024-01-01" and row["end"] == "2024-12-31" and row["duration_days"] == 365\n        )\n        annual["value"] = 500\n        annual["concept"] = "RestatedRevenue"\n\n        quarterly = build_period_view(normalized, period="quarterly", metrics=["revenue"])\n        rows = quarterly["metrics"]["revenue"]["series"]\n        self.assertEqual([point["value"] for point in rows], [100, 110, 120, 115])\n        self.assertTrue(all(point["reconciliation_status"] == "basis_mismatch" for point in rows))\n        self.assertTrue(all("concept_basis_change" in point["quality_flags"] for point in rows))\n        self.assertEqual(quarterly["metrics"]["revenue"]["quality"]["reconciliation_points"]["basis_mismatch"], 4)\n\n        ttm = build_period_view(normalized, period="ttm", metrics=["revenue"])\n        self.assertEqual(ttm["metrics"]["revenue"]["series"], [])\n\n'''
    if marker not in tests:
        raise SystemExit('unit test insertion anchor mismatch')
    test_path.write_text(tests.replace(marker, addition + marker, 1))
