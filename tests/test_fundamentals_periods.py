import unittest

from fundamentals_periods import build_period_view, infer_fiscal_periods


def flow_row(value, start, end, duration, *, form="10-Q", fp=None, filed="2025-05-01", concept="Metric", components=None):
    row = {
        "value": value,
        "unit": "USD",
        "start": start,
        "end": end,
        "filed": filed,
        "fy": int(end[:4]),
        "fp": fp,
        "form": form,
        "frame": None,
        "accn": f"{concept}-{end}",
        "taxonomy": "us-gaap",
        "concept": concept,
        "concept_rank": 0,
        "duration_days": duration,
        "derived": False,
        "source": "SEC EDGAR Company Facts",
    }
    if components is not None:
        row["components"] = components
    return row


def instant_row(value, end, *, form="10-Q", unit="USD"):
    return {
        "value": value,
        "unit": unit,
        "start": None,
        "end": end,
        "filed": "2025-05-01",
        "fy": int(end[:4]),
        "fp": "FY" if form == "10-K" else "Q1",
        "form": form,
        "frame": None,
        "accn": f"instant-{end}",
        "taxonomy": "us-gaap",
        "concept": "InstantMetric",
        "concept_rank": 0,
        "duration_days": None,
        "derived": False,
        "source": "SEC EDGAR Company Facts",
    }


def metric(name, label, kind, unit, observations, formula=None):
    payload = {
        "metric": name,
        "label": label,
        "kind": kind,
        "unit": unit,
        "derived": bool(formula),
        "observations": observations,
    }
    if formula:
        payload["formula"] = formula
    return payload


def margin_row(num, revenue, start, end, duration, *, form="10-Q", fp=None):
    return flow_row(
        num / revenue,
        start,
        end,
        duration,
        form=form,
        fp=fp,
        concept=None,
        components=[
            {"metric": "net_income", "value": num, "taxonomy": "us-gaap", "concept": "NetIncomeLoss", "accn": end},
            {"metric": "revenue", "value": revenue, "taxonomy": "us-gaap", "concept": "Revenues", "accn": end},
        ],
    )


def fixture():
    revenue = [
        flow_row(100, "2024-01-01", "2024-03-31", 90, fp="Q1", concept="Revenues"),
        flow_row(110, "2024-04-01", "2024-06-30", 90, fp="Q2", concept="Revenues"),
        flow_row(210, "2024-01-01", "2024-06-30", 181, fp="Q2", concept="Revenues"),
        flow_row(120, "2024-07-01", "2024-09-30", 91, fp="Q3", concept="Revenues"),
        flow_row(330, "2024-01-01", "2024-09-30", 273, fp="Q3", concept="Revenues"),
        flow_row(460, "2024-01-01", "2024-12-31", 365, form="10-K", fp="FY", filed="2025-02-01", concept="Revenues"),
    ]
    ocf = [
        flow_row(10, "2024-01-01", "2024-03-31", 90, fp="Q1", concept="OperatingCashFlow"),
        flow_row(25, "2024-01-01", "2024-06-30", 181, fp="Q2", concept="OperatingCashFlow"),
        flow_row(45, "2024-01-01", "2024-09-30", 273, fp="Q3", concept="OperatingCashFlow"),
        flow_row(60, "2024-01-01", "2024-12-31", 365, form="10-K", fp="FY", filed="2025-02-01", concept="OperatingCashFlow"),
    ]
    cash = [
        instant_row(50, "2024-03-31"),
        instant_row(55, "2024-06-30"),
        instant_row(60, "2024-09-30"),
        instant_row(65, "2024-12-31", form="10-K"),
    ]
    margins = [
        margin_row(10, 100, "2024-01-01", "2024-03-31", 90, fp="Q1"),
        margin_row(21, 210, "2024-01-01", "2024-06-30", 181, fp="Q2"),
        margin_row(30, 330, "2024-01-01", "2024-09-30", 273, fp="Q3"),
        margin_row(43, 460, "2024-01-01", "2024-12-31", 365, form="10-K", fp="FY"),
    ]
    return {
        "revenue": metric("revenue", "Revenue", "flow", "USD", revenue),
        "net_income": metric("net_income", "Net Income", "flow", "USD", []),
        "operating_cash_flow": metric("operating_cash_flow", "Operating Cash Flow", "flow", "USD", ocf),
        "capex": metric("capex", "Capital Expenditure", "flow", "USD", ocf),
        "free_cash_flow": metric("free_cash_flow", "Free Cash Flow", "flow", "USD", ocf, "ocf-capex"),
        "cash": metric("cash", "Cash", "instant", "USD", cash),
        "debt": metric("debt", "Debt", "instant", "USD", cash, "components"),
        "shares": metric("shares", "Shares", "instant", "shares", []),
        "gross_margin": metric("gross_margin", "Gross Margin", "flow", "ratio", []),
        "operating_margin": metric("operating_margin", "Operating Margin", "flow", "ratio", []),
        "net_margin": metric("net_margin", "Net Margin", "flow", "ratio", margins, "net_income / revenue"),
    }


class PeriodEngineTests(unittest.TestCase):
    def test_annual_and_quarterly_classification(self):
        normalized = fixture()
        periods = infer_fiscal_periods(normalized)
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0]["fiscal_year"], 2024)

        quarterly = build_period_view(normalized, period="quarterly", metrics=["revenue", "operating_cash_flow"])
        revenue = quarterly["metrics"]["revenue"]["series"]
        self.assertEqual([point["value"] for point in revenue], [100, 110, 120, 130])
        self.assertEqual(revenue[-1]["calculation"], "derived_from_fy_less_9m")

        ocf = quarterly["metrics"]["operating_cash_flow"]["series"]
        self.assertEqual([point["value"] for point in ocf], [10, 15, 20, 15])
        self.assertEqual(ocf[1]["calculation"], "derived_from_ytd")
        self.assertEqual(ocf[2]["calculation"], "derived_from_ytd")

    def test_ttm_flow_matches_full_year_when_four_quarters_exist(self):
        result = build_period_view(fixture(), period="ttm", metrics=["revenue", "operating_cash_flow"])
        self.assertEqual(result["metrics"]["revenue"]["series"][-1]["value"], 460)
        self.assertEqual(result["metrics"]["operating_cash_flow"]["series"][-1]["value"], 60)
        self.assertEqual(result["metrics"]["revenue"]["series"][-1]["calculation"], "sum_4_quarters")

    def test_ratio_is_rebuilt_from_components_not_percentages(self):
        normalized = fixture()
        quarterly = build_period_view(normalized, period="quarterly", metrics=["net_margin"])
        values = [round(point["value"], 6) for point in quarterly["metrics"]["net_margin"]["series"]]
        self.assertEqual(values, [0.1, 0.1, 0.075, 0.1])

        ttm = build_period_view(normalized, period="ttm", metrics=["net_margin"])
        self.assertAlmostEqual(ttm["metrics"]["net_margin"]["series"][-1]["value"], 43 / 460)
        self.assertEqual(ttm["metrics"]["net_margin"]["series"][-1]["calculation"], "ratio_of_ttm_components")

    def test_instant_metrics_are_never_summed(self):
        result = build_period_view(fixture(), period="ttm", metrics=["cash"])
        values = [point["value"] for point in result["metrics"]["cash"]["series"]]
        self.assertEqual(values, [50, 55, 60, 65])
        self.assertEqual(result["metrics"]["cash"]["ttm_semantics"], "point_in_time")
        self.assertTrue(all(point["calculation"] == "point_in_time" for point in result["metrics"]["cash"]["series"]))

    def test_basis_transition_is_flagged_not_fabricated(self):
        normalized = fixture()
        # Add an explicitly reported Q4 on the older concept basis while the FY value
        # is later restated upward under a different concept. The engine must preserve
        # the reported Q4, flag the year, and not manufacture a balancing quarter.
        normalized["revenue"]["observations"].append(
            flow_row(115, "2024-10-01", "2024-12-31", 91, form="10-K", fp="FY", concept="LegacyRevenue")
        )
        annual = next(
            row for row in normalized["revenue"]["observations"]
            if row["start"] == "2024-01-01" and row["end"] == "2024-12-31" and row["duration_days"] == 365
        )
        annual["value"] = 500
        annual["concept"] = "RestatedRevenue"

        quarterly = build_period_view(normalized, period="quarterly", metrics=["revenue"])
        rows = quarterly["metrics"]["revenue"]["series"]
        self.assertEqual([point["value"] for point in rows], [100, 110, 120, 115])
        self.assertTrue(all(point["reconciliation_status"] == "basis_mismatch" for point in rows))
        self.assertTrue(all("concept_basis_change" in point["quality_flags"] for point in rows))
        self.assertEqual(quarterly["metrics"]["revenue"]["quality"]["reconciliation_points"]["basis_mismatch"], 4)

        ttm = build_period_view(normalized, period="ttm", metrics=["revenue"])
        self.assertEqual(ttm["metrics"]["revenue"]["series"], [])

    def test_open_fiscal_year_supports_partial_quarters(self):
        normalized = fixture()
        normalized["revenue"]["observations"].extend(
            [
                flow_row(120, "2025-01-01", "2025-03-31", 89, fp="Q1", filed="2025-05-01", concept="Revenues"),
                flow_row(250, "2025-01-01", "2025-06-30", 180, fp="Q2", filed="2025-08-01", concept="Revenues"),
            ]
        )
        result = build_period_view(normalized, period="quarterly", metrics=["revenue"])
        latest = result["metrics"]["revenue"]["series"][-2:]
        self.assertEqual([(point["label"], point["value"]) for point in latest], [("FY2025 Q1", 120), ("FY2025 Q2", 130)])
        self.assertEqual(result["fiscal_calendar"]["open_fiscal_year"], 2025)


if __name__ == "__main__":
    unittest.main()
