import importlib
import json

import anyio


def _run(awaitable):
    async def _runner():
        return await awaitable

    return anyio.run(_runner)


def test_call_tool_redirects_stdout_to_stderr(monkeypatch, capsys):
    import mcp_server as mcp_server_module

    mcp_server_module = importlib.reload(mcp_server_module)

    def fake_get_metric(**kwargs):
        print("stdout noise from tool")
        return {"status": "success", "matches": []}

    monkeypatch.setattr(mcp_server_module, "get_metric", fake_get_metric)

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

    def fake_get_filings(**kwargs):
        return {"status": "success", "opaque": NotJsonSerializable()}

    monkeypatch.setattr(mcp_server_module, "get_filings", fake_get_filings)

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

    def boom(**kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mcp_server_module, "get_filings", boom)

    response = _run(
        mcp_server_module.call_tool(
            "get_filings",
            {"ticker": "AAPL", "year": 2025, "quarter": 4},
        )
    )

    payload = json.loads(response[0].text)
    assert payload["status"] == "error"
    assert "kaboom" in payload["message"]
