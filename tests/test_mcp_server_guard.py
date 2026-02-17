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

    mcp_server_module._proxy_get_financials({"ticker": "AAPL", "year": 2025, "quarter": 4})
    mcp_server_module._proxy_get_metric(
        {"ticker": "AAPL", "year": 2025, "quarter": 4, "metric_name": "revenue"}
    )

    assert calls[0][1]["source"] == "auto"
    assert calls[1][1]["source"] == "auto"


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
