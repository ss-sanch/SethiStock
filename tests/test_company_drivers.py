from company_drivers import (
    COMPANY_DRIVER_REGISTRY,
    DRIVER_SCHEMA_VERSION,
    REGISTRY_STATS,
    company_driver_companies,
    company_driver_schema,
    get_company_driver_registry,
    validate_registry,
)


def test_registry_shape():
    stats = validate_registry()
    assert stats == REGISTRY_STATS
    assert stats["company_count"] == 10
    assert stats["metric_count"] == 43
    assert set(COMPANY_DRIVER_REGISTRY) == {
        "AAPL", "MSFT", "GOOGL", "AMZN", "META",
        "NVDA", "TSLA", "NFLX", "JPM", "V",
    }


def test_flagships_have_distinct_driver_sets():
    assert {metric["key"] for metric in COMPANY_DRIVER_REGISTRY["GOOGL"]["metrics"]} == {
        "google_search_revenue",
        "youtube_ads_revenue",
        "google_cloud_revenue",
        "google_cloud_operating_income",
    }
    assert {metric["key"] for metric in COMPANY_DRIVER_REGISTRY["TSLA"]["metrics"]} == {
        "vehicle_deliveries",
        "automotive_revenue",
        "automotive_gross_margin",
        "energy_storage_deployments",
    }
    assert "cet1_ratio" in {metric["key"] for metric in COMPANY_DRIVER_REGISTRY["JPM"]["metrics"]}
    assert "processed_transactions" in {metric["key"] for metric in COMPANY_DRIVER_REGISTRY["V"]["metrics"]}


def test_google_share_class_alias():
    payload = get_company_driver_registry("goog")
    assert payload["ticker"] == "GOOGL"
    assert payload["requested_ticker"] == "GOOG"
    assert payload["company"] == "Alphabet"
    assert payload["schema_version"] == DRIVER_SCHEMA_VERSION
    assert payload["data_state"] == "registry_only"


def test_sparse_metrics_are_explicit():
    netflix = get_company_driver_registry("NFLX")
    metrics = {metric["key"]: metric for metric in netflix["metrics"]}
    assert metrics["paid_memberships"]["continuity"] == "historical"
    assert metrics["engagement_hours"]["continuity"] == "irregular"
    assert metrics["engagement_hours"]["expected_frequency"] == "irregular"


def test_schema_encodes_provenance_and_no_invention_rule():
    schema = company_driver_schema()
    assert schema["schema_version"] == DRIVER_SCHEMA_VERSION
    assert schema["company_count"] == 10
    assert "source_url" in schema["observation_contract"]["required"]
    assert "accession" in schema["observation_contract"]["provenance"]
    assert "Never interpolate or manufacture" in schema["observation_contract"]["rule"]


def test_company_list_is_stable():
    payload = company_driver_companies()
    assert payload["count"] == 10
    assert [row["ticker"] for row in payload["companies"]] == list(COMPANY_DRIVER_REGISTRY)


def test_unknown_ticker_is_not_silently_generalised():
    try:
        get_company_driver_registry("AMD")
    except KeyError as exc:
        assert exc.args[0] == "AMD"
    else:
        raise AssertionError("Unsupported ticker should not receive a fabricated registry")
