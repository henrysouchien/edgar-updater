import importlib
import json
import time


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _reload_utils(monkeypatch, tmp_path):
    import utils as utils_module

    utils_module = importlib.reload(utils_module)
    cache_path = tmp_path / "company_tickers_cache.json"
    monkeypatch.setattr(utils_module, "_TICKER_MAP_CACHE_PATH", str(cache_path))
    monkeypatch.setattr(utils_module, "_ticker_to_cik_cache", None)
    monkeypatch.setattr(utils_module, "_ticker_to_cik_loaded_at", 0.0)
    return utils_module, cache_path


def test_lookup_cik_uses_in_memory_ticker_map_cache(monkeypatch, tmp_path):
    utils_module, _cache_path = _reload_utils(monkeypatch, tmp_path)

    payload = {
        "0": {"ticker": "AAPL", "cik_str": 320193},
        "1": {"ticker": "MSCI", "cik_str": 1408198},
    }
    calls = {"count": 0}

    def fake_get(*args, **kwargs):
        calls["count"] += 1
        return _FakeResponse(payload)

    monkeypatch.setattr(utils_module.requests, "get", fake_get)

    assert utils_module.lookup_cik_from_ticker("AAPL") == "0000320193"
    assert utils_module.lookup_cik_from_ticker("MSCI") == "0001408198"
    assert calls["count"] == 1


def test_lookup_cik_falls_back_to_disk_cache_when_sec_unavailable(monkeypatch, tmp_path):
    utils_module, cache_path = _reload_utils(monkeypatch, tmp_path)

    disk_payload = {"0": {"ticker": "MSCI", "cik_str": 1408198}}
    cache_path.write_text(json.dumps(disk_payload))

    calls = {"count": 0}

    def failing_get(*args, **kwargs):
        calls["count"] += 1
        raise RuntimeError("SEC unavailable")

    monkeypatch.setattr(utils_module.requests, "get", failing_get)
    monkeypatch.setattr(utils_module.time, "sleep", lambda _: None)

    cik = utils_module.lookup_cik_from_ticker("MSCI")
    assert cik == "0001408198"
    assert calls["count"] == 3


def test_lookup_cik_uses_stale_memory_cache_if_refresh_fails(monkeypatch, tmp_path):
    utils_module, _cache_path = _reload_utils(monkeypatch, tmp_path)

    def failing_get(*args, **kwargs):
        raise RuntimeError("SEC unavailable")

    monkeypatch.setattr(utils_module.requests, "get", failing_get)
    monkeypatch.setattr(utils_module.time, "sleep", lambda _: None)

    stale_map = {"msci": "0001408198"}
    monkeypatch.setattr(utils_module, "_ticker_to_cik_cache", stale_map)
    monkeypatch.setattr(
        utils_module,
        "_ticker_to_cik_loaded_at",
        time.time() - utils_module._TICKER_MAP_TTL_SECONDS - 5,
    )

    assert utils_module.lookup_cik_from_ticker("MSCI") == "0001408198"
