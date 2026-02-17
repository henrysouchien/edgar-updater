import importlib
import json

import pytest


@pytest.fixture
def api_ctx(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_TESTING", "1")
    monkeypatch.setenv("FLASK_LIMITER_STORAGE_URI", "memory://")

    import app as app_module

    app_module = importlib.reload(app_module)
    app_module.app.config["TESTING"] = True

    export_dir = tmp_path / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    app_module.EXPORT_DIR = str(export_dir)

    # Keep tests deterministic and avoid writing runtime logs to repo folders.
    monkeypatch.setattr(app_module, "log_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_module, "log_usage", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_module, "log_error_json", lambda *args, **kwargs: None)

    # Avoid accidental rate-limit test flakiness.
    app_module.RATE_LIMITS["public"] = "1000 per day"
    app_module.RATE_LIMITS["registered"] = "1000 per day"
    app_module.RATE_LIMITS["paid"] = "1000 per day"

    preferred_key = next(
        (key for key, tier in app_module.TIER_MAP.items() if tier == "paid"),
        app_module.PUBLIC_KEY,
    )

    return {
        "client": app_module.app.test_client(),
        "app_module": app_module,
        "export_dir": export_dir,
        "key": preferred_key,
    }


def _query(ctx, **params):
    merged = {"key": ctx["key"], **params}
    return ctx["client"].get("/api/financials", query_string=merged)


def test_api_financials_rejects_invalid_key(api_ctx):
    response = api_ctx["client"].get(
        "/api/financials",
        query_string={"key": "not-a-real-key", "ticker": "AAPL", "year": 2025, "quarter": 4},
    )
    assert response.status_code == 401
    payload = response.get_json()
    assert payload["status"] == "error"
    assert "Invalid API key" in payload["message"]


def test_api_financials_rejects_invalid_ticker(api_ctx):
    response = _query(api_ctx, ticker="NOTREAL", year=2025, quarter=4)
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "error"
    assert "Invalid or unsupported ticker" in payload["message"]


def test_cik_lookup_failure_returns_503_and_is_not_cached(api_ctx, monkeypatch):
    def fake_pipeline(*args, **kwargs):
        raise ValueError("❌ No valid CIK provided. Please set CIK or lookup from TICKER.")

    monkeypatch.setattr(api_ctx["app_module"], "run_edgar_pipeline", fake_pipeline)

    response = _query(api_ctx, ticker="AAPL", year=2025, quarter=4)
    assert response.status_code == 503
    payload = response.get_json()
    assert payload["status"] == "error"
    assert "Failed to resolve ticker to CIK from SEC" in payload["message"]

    cache_path = api_ctx["export_dir"] / "AAPL_4Q25_financials.json"
    assert not cache_path.exists()


def test_stale_error_cache_is_ignored_and_replaced(api_ctx, monkeypatch):
    cache_path = api_ctx["export_dir"] / "AAPL_4Q25_financials.json"
    cache_path.write_text(json.dumps({"status": "error", "message": "stale"}))

    calls = {"count": 0}

    def fake_pipeline(*args, **kwargs):
        calls["count"] += 1
        return {
            "status": "success",
            "metadata": {"ticker": "AAPL", "source": {"filing_type": "10-Q"}},
            "facts": [],
        }

    monkeypatch.setattr(api_ctx["app_module"], "run_edgar_pipeline", fake_pipeline)

    response = _query(api_ctx, ticker="AAPL", year=2025, quarter=4)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "success"
    assert calls["count"] == 1

    cached_payload = json.loads(cache_path.read_text())
    assert cached_payload["status"] == "success"


def test_successful_response_is_cached(api_ctx, monkeypatch):
    def fake_pipeline(*args, **kwargs):
        return {
            "status": "success",
            "metadata": {"ticker": "AAPL", "source": {"filing_type": "10-Q"}},
            "facts": [{"tag": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"}],
        }

    monkeypatch.setattr(api_ctx["app_module"], "run_edgar_pipeline", fake_pipeline)

    response = _query(api_ctx, ticker="AAPL", year=2025, quarter=4)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "success"
    assert len(payload["facts"]) == 1

    cache_path = api_ctx["export_dir"] / "AAPL_4Q25_financials.json"
    assert cache_path.exists()


def test_financials_response_hydrates_current_and_prior_value_aliases(api_ctx):
    cache_path = api_ctx["export_dir"] / "AAPL_FY25_financials.json"
    cache_path.write_text(
        json.dumps(
            {
                "status": "success",
                "metadata": {"ticker": "AAPL", "source": {"filing_type": "10-K"}},
                "facts": [
                    {
                        "tag": "us-gaap:ProceedsFromIssuanceOfDebt",
                        "date_type": "FY",
                        "current_period_value": 2805963.0,
                        "prior_period_value": 556875.0,
                        "visual_current_value": None,
                        "visual_prior_value": None,
                    }
                ],
            }
        )
    )

    response = _query(api_ctx, ticker="AAPL", year=2025, quarter=4, full_year_mode="true")
    assert response.status_code == 200
    payload = response.get_json()
    fact = payload["facts"][0]
    assert fact["current_value"] == 2805963.0
    assert fact["prior_value"] == 556875.0


def test_financials_and_metric_agree_on_metric_values(api_ctx, monkeypatch):
    def fake_pipeline(*args, **kwargs):
        return {
            "status": "success",
            "metadata": {"ticker": "AAPL", "source": {"filing_type": "10-K"}},
            "facts": [
                {
                    "tag": "us-gaap:ProceedsFromIssuanceOfDebt",
                    "date_type": "FY",
                    "current_period_value": 2805963.0,
                    "prior_period_value": 556875.0,
                    "visual_current_value": None,
                    "visual_prior_value": None,
                }
            ],
        }

    monkeypatch.setattr(api_ctx["app_module"], "run_edgar_pipeline", fake_pipeline)

    financials_response = _query(api_ctx, ticker="AAPL", year=2025, quarter=4, full_year_mode="true")
    assert financials_response.status_code == 200
    financials_payload = financials_response.get_json()
    fact = financials_payload["facts"][0]

    metric_response = api_ctx["client"].get(
        "/api/metric",
        query_string={
            "key": api_ctx["key"],
            "ticker": "AAPL",
            "year": 2025,
            "quarter": 4,
            "metric_name": "ProceedsFromIssuanceOfDebt",
            "full_year_mode": "true",
        },
    )
    assert metric_response.status_code == 200
    metric_payload = metric_response.get_json()
    assert metric_payload["status"] == "success"
    assert metric_payload["matches"][0]["current_value"] == fact["current_value"]
    assert metric_payload["matches"][0]["prior_value"] == fact["prior_value"]
