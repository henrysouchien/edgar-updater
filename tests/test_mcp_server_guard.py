import importlib
import json
from pathlib import Path

import anyio


def _run(awaitable):
    async def _runner():
        return await awaitable

    return anyio.run(_runner)


def test_call_tool_redirects_stdout_to_stderr(monkeypatch, capsys):
    import mcp_server as mcp_server_module

    mcp_server_module = importlib.reload(mcp_server_module)

    def fake_get_metric(args):
        print("stdout noise from tool")
        return {"status": "success", "matches": []}

    monkeypatch.setitem(mcp_server_module._TOOL_DISPATCH, "get_metric", fake_get_metric)

    response = _run(
        mcp_server_module.call_tool(
            "get_metric",
            {
                "ticker": "AAPL",
                "year": 2025,
                "quarter": 4,
                "metric_name": "revenue",
                "full_year_mode": True,
            },
        )
    )

    payload = json.loads(response[0].text)
    captured = capsys.readouterr()

    assert payload["status"] == "success"
    assert "stdout noise from tool" in captured.err
    assert "stdout noise from tool" not in captured.out


def test_call_tool_serializes_non_json_values(monkeypatch):
    import mcp_server as mcp_server_module

    mcp_server_module = importlib.reload(mcp_server_module)

    class NotJsonSerializable:
        pass

    def fake_get_filings(args):
        return {"status": "success", "opaque": NotJsonSerializable()}

    monkeypatch.setitem(mcp_server_module._TOOL_DISPATCH, "get_filings", fake_get_filings)

    response = _run(
        mcp_server_module.call_tool(
            "get_filings",
            {"ticker": "AAPL", "year": 2025, "quarter": 4},
        )
    )

    payload = json.loads(response[0].text)
    assert payload["status"] == "success"
    assert isinstance(payload["opaque"], str)


def test_call_tool_wraps_unhandled_exceptions(monkeypatch):
    import mcp_server as mcp_server_module

    mcp_server_module = importlib.reload(mcp_server_module)

    def boom(args):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(mcp_server_module._TOOL_DISPATCH, "get_filings", boom)

    response = _run(
        mcp_server_module.call_tool(
            "get_filings",
            {"ticker": "AAPL", "year": 2025, "quarter": 4},
        )
    )

    payload = json.loads(response[0].text)
    assert payload["status"] == "error"
    assert "kaboom" in payload["message"]


def test_proxy_defaults_source_to_auto(monkeypatch):
    import mcp_server as mcp_server_module

    mcp_server_module = importlib.reload(mcp_server_module)
    calls = []

    def fake_call_api(path, params, timeout=60):
        calls.append((path, params, timeout))
        return {"status": "success"}

    monkeypatch.setattr(mcp_server_module, "_call_api", fake_call_api)

    mcp_server_module._proxy_get_financials(
        {"ticker": "AAPL", "year": 2025, "quarter": 4, "output": "inline"}
    )
    mcp_server_module._proxy_get_metric(
        {"ticker": "AAPL", "year": 2025, "quarter": 4, "metric_name": "revenue"}
    )
    mcp_server_module._proxy_list_metrics(
        {"ticker": "AAPL", "year": 2025, "quarter": 4}
    )
    mcp_server_module._proxy_search_metrics(
        {"ticker": "AAPL", "year": 2025, "quarter": 4, "query": "revenue"}
    )

    assert calls[0][1]["source"] == "auto"
    assert calls[1][1]["source"] == "auto"
    assert calls[2][1]["source"] == "auto"
    assert calls[3][1]["source"] == "auto"


def test_proxy_list_metrics_returns_deduped_catalog(monkeypatch):
    import mcp_server as mcp_server_module

    mcp_server_module = importlib.reload(mcp_server_module)

    def fake_call_api(path, params, timeout=60):
        assert path == "/api/financials"
        return {
            "status": "success",
            "metadata": {"source": {"filing_type": "10-Q"}},
            "facts": [
                {
                    "tag": "us-gaap:Revenues",
                    "date_type": "Q",
                    "current_period_value": 100.0,
                    "prior_period_value": 90.0,
                },
                {
                    # Duplicate tag/date_type with no values should be dropped.
                    "tag": "us-gaap:Revenues",
                    "date_type": "Q",
                    "current_period_value": None,
                    "prior_period_value": None,
                },
                {
                    "tag": "us-gaap:OperatingIncomeLoss",
                    "date_type": "Q",
                    "current_period_value": 22.0,
                    "prior_period_value": 19.0,
                },
            ],
        }

    monkeypatch.setattr(mcp_server_module, "_call_api", fake_call_api)

    result = mcp_server_module._proxy_list_metrics(
        {
            "ticker": "AAPL",
            "year": 2025,
            "quarter": 4,
            "date_type": "Q",
            "include_values": False,
            "limit": 10,
        }
    )

    assert result["status"] == "success"
    assert result["date_type_filter"] == "Q"
    assert result["total_candidates"] == 2
    assert result["returned_candidates"] == 2
    assert [m["metric_name"] for m in result["metrics"]] == ["OperatingIncomeLoss", "Revenues"]
    assert "current_value" not in result["metrics"][0]
    assert "prior_value" not in result["metrics"][0]


def test_proxy_search_metrics_returns_ranked_matches(monkeypatch):
    import mcp_server as mcp_server_module

    mcp_server_module = importlib.reload(mcp_server_module)

    def fake_call_api(path, params, timeout=60):
        assert path == "/api/financials"
        return {
            "status": "success",
            "metadata": {"source": {"filing_type": "8-K"}},
            "facts": [
                {
                    "tag": "Diluted net income per share",
                    "date_type": "Q",
                    "current_period_value": 2.4,
                    "prior_period_value": 2.2,
                },
                {
                    "tag": "Net income",
                    "date_type": "Q",
                    "current_period_value": 36.0,
                    "prior_period_value": 33.0,
                },
                {
                    "tag": "Total net sales",
                    "date_type": "Q",
                    "current_period_value": 124.0,
                    "prior_period_value": 118.0,
                },
            ],
        }

    monkeypatch.setattr(mcp_server_module, "_call_api", fake_call_api)

    result = mcp_server_module._proxy_search_metrics(
        {
            "ticker": "AAPL",
            "year": 2025,
            "quarter": 1,
            "query": "diluted net income per share",
            "date_type": "Q",
            "limit": 5,
        }
    )

    assert result["status"] == "success"
    assert result["query"] == "diluted net income per share"
    assert result["total_matches"] >= 1
    assert result["matches"][0]["metric_name"] == "Diluted net income per share"
    assert result["matches"][0]["match_score"] >= 90


def test_proxy_search_metrics_requires_query(monkeypatch):
    import mcp_server as mcp_server_module

    mcp_server_module = importlib.reload(mcp_server_module)

    result = mcp_server_module._proxy_search_metrics(
        {"ticker": "AAPL", "year": 2025, "quarter": 4, "query": "   "}
    )

    assert result["status"] == "error"
    assert "query" in result["message"].lower()


def test_proxy_search_metrics_handles_hyphen_phrase_and_abbreviation(monkeypatch):
    import mcp_server as mcp_server_module

    mcp_server_module = importlib.reload(mcp_server_module)

    def fake_call_api(path, params, timeout=60):
        assert path == "/api/financials"
        return {
            "status": "success",
            "metadata": {"source": {"filing_type": "10-K"}},
            "facts": [
                {
                    "tag": "us-gaap:LongTermDebt",
                    "date_type": "FY",
                    "current_period_value": 68836.0,
                    "prior_period_value": 67000.0,
                },
                {
                    "tag": "us-gaap:OperatingIncomeLoss",
                    "date_type": "FY",
                    "current_period_value": 71866.0,
                    "prior_period_value": 61200.0,
                },
                {
                    "tag": "us-gaap:EarningsPerShareDiluted",
                    "date_type": "FY",
                    "current_period_value": 7.17,
                    "prior_period_value": 6.90,
                },
            ],
        }

    monkeypatch.setattr(mcp_server_module, "_call_api", fake_call_api)

    debt = mcp_server_module._proxy_search_metrics(
        {"ticker": "AMZN", "year": 2025, "quarter": 4, "query": "long-term debt", "date_type": "FY"}
    )
    assert debt["status"] == "success"
    assert any(match["metric_name"] == "LongTermDebt" for match in debt["matches"])

    op_income = mcp_server_module._proxy_search_metrics(
        {"ticker": "AMZN", "year": 2025, "quarter": 4, "query": "operating income", "date_type": "FY"}
    )
    assert op_income["status"] == "success"
    assert any(match["metric_name"] == "OperatingIncomeLoss" for match in op_income["matches"])

    eps = mcp_server_module._proxy_search_metrics(
        {"ticker": "AMZN", "year": 2025, "quarter": 4, "query": "diluted eps", "date_type": "FY"}
    )
    assert eps["status"] == "success"
    assert any(match["metric_name"] == "EarningsPerShareDiluted" for match in eps["matches"])


def test_file_output_sanitizes_untrusted_filename_parts(monkeypatch, tmp_path):
    import mcp_server as mcp_server_module

    mcp_server_module = importlib.reload(mcp_server_module)
    monkeypatch.setattr(mcp_server_module, "FILE_OUTPUT_DIR", tmp_path)

    def fake_call_api(path, params, timeout=60):
        return {
            "status": "success",
            "filing_type": "10-Q",
            "sections": {
                "part1_item2": {
                    "header": "MD&A",
                    "word_count": 3,
                    "text": "sample text here",
                    "tables": [],
                }
            },
        }

    monkeypatch.setattr(mcp_server_module, "_call_api", fake_call_api)

    result = mcp_server_module._proxy_get_filing_sections(
        {
            "ticker": "../../AAPL",
            "year": 2025,
            "quarter": 4,
            "sections": ["../etc/passwd", "part1/item2"],
            "output": "file",
        }
    )

    assert result["status"] == "success"
    output_path = Path(result["file_path"])
    assert output_path.is_relative_to(tmp_path.resolve())
    assert output_path.exists()
    assert ".." not in output_path.name
    assert result["sections"]["part1_item2"]["table_count"] == 0
    assert result["metadata"]["total_table_count"] == 0


def test_financials_file_output_uses_metadata_source_and_fact_count(monkeypatch, tmp_path):
    import mcp_server as mcp_server_module

    mcp_server_module = importlib.reload(mcp_server_module)
    monkeypatch.setattr(mcp_server_module, "FILE_OUTPUT_DIR", tmp_path)

    def fake_call_api(path, params, timeout=60):
        return {
            "status": "success",
            "facts": [{"tag": "a"}, {"tag": "b"}, {"tag": "c"}],
            "metadata": {"source": {"filing_type": "10-K", "accession": "x"}},
        }

    monkeypatch.setattr(mcp_server_module, "_call_api", fake_call_api)

    result = mcp_server_module._proxy_get_financials(
        {"ticker": "AAPL", "year": 2025, "quarter": 4, "output": "file"}
    )

    assert result["status"] == "success"
    assert result["filing_type"] == "10-K"
    assert result["metadata"]["total_facts"] == 3
    output_path = Path(result["file_path"])
    assert output_path.exists()
    saved = json.loads(output_path.read_text())
    assert len(saved["facts"]) == 3


def test_financials_file_output_skips_write_if_deadline_expired(monkeypatch, tmp_path):
    import mcp_server as mcp_server_module

    mcp_server_module = importlib.reload(mcp_server_module)
    monkeypatch.setattr(mcp_server_module, "FILE_OUTPUT_DIR", tmp_path)

    def fake_call_api(path, params, timeout=60):
        return {
            "status": "success",
            "facts": [{"tag": "a"}],
            "metadata": {"source": {"filing_type": "10-K"}},
        }

    monkeypatch.setattr(mcp_server_module, "_call_api", fake_call_api)

    result = mcp_server_module._proxy_get_financials(
        {
            "ticker": "AAPL",
            "year": 2025,
            "quarter": 4,
            "output": "file",
            "__deadline_monotonic": 0.0,
        }
    )

    assert result["status"] == "error"
    assert "timed out" in result["message"].lower()
    assert not any(tmp_path.iterdir())


def test_sections_file_output_skips_write_if_deadline_expired(monkeypatch, tmp_path):
    import mcp_server as mcp_server_module

    mcp_server_module = importlib.reload(mcp_server_module)
    monkeypatch.setattr(mcp_server_module, "FILE_OUTPUT_DIR", tmp_path)

    def fake_call_api(path, params, timeout=60):
        return {
            "status": "success",
            "filing_type": "10-Q",
            "sections": {
                "part1_item2": {"header": "MD&A", "word_count": 2, "text": "x y", "tables": []}
            },
        }

    monkeypatch.setattr(mcp_server_module, "_call_api", fake_call_api)

    result = mcp_server_module._proxy_get_filing_sections(
        {
            "ticker": "AAPL",
            "year": 2025,
            "quarter": 4,
            "sections": ["part1_item2"],
            "output": "file",
            "__deadline_monotonic": 0.0,
        }
    )

    assert result["status"] == "error"
    assert "timed out" in result["message"].lower()
    assert not any(tmp_path.iterdir())


def test_tables_only_removes_text_and_uses_table_word_counts(monkeypatch):
    import mcp_server as mcp_server_module

    mcp_server_module = importlib.reload(mcp_server_module)

    def fake_call_api(path, params, timeout=60):
        return {
            "status": "success",
            "filing_type": "10-Q",
            "sections": {
                "part1_item2": {
                    "header": "MD&A",
                    "word_count": 99,
                    "text": "narrative that should be stripped",
                    "tables": ["A B C", "1 2 3"],
                }
            },
        }

    monkeypatch.setattr(mcp_server_module, "_call_api", fake_call_api)

    result = mcp_server_module._proxy_get_filing_sections(
        {
            "ticker": "AAPL",
            "year": 2025,
            "quarter": 4,
            "output": "inline",
            "tables_only": True,
        }
    )

    assert result["status"] == "success"
    section = result["sections"]["part1_item2"]
    assert "text" not in section
    assert section["word_count"] == 6
    assert section["table_count"] == 2
    assert result["metadata"]["total_table_count"] == 2
