"""Import the actual deployment entry point, and exercise CORS without upstream IO."""
from fastapi.testclient import TestClient

import app


def test_runtime_probe_has_cors_and_reports_memory_limits():
    response = TestClient(app.app).get("/api/runtime/status", headers={"Origin": "https://sethiway.com"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.json()["sec_cache"]["max_bytes"] == 32 * 1024 * 1024


def test_telemetry_preflight_accepts_site_origin():
    response = TestClient(app.app).options("/api/telemetry/sethistock-load", headers={
        "Origin": "https://sethiway.com", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_snapshot_telemetry_fields_are_inside_the_payload(monkeypatch):
    class Result:
        status_code = 201
    captured = []
    monkeypatch.setattr(app, "SUPABASE_SERVICE_KEY", "test-only")
    monkeypatch.setattr(app, "SUPABASE_URL", "https://example.invalid")
    monkeypatch.setattr(app.requests, "post", lambda *args, **kwargs: captured.append(kwargs["json"]) or Result())
    response = TestClient(app.app).post("/api/telemetry/sethistock-load", json={
        "ticker": "MSFT", "status": "full", "snapshot_hit": True, "snapshot_ms": 12,
    })
    assert response.status_code == 200
    assert captured[0]["snapshot_hit"] is True
    assert captured[0]["snapshot_ms"] == 12
