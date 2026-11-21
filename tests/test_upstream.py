import asyncio

import httpx
import pytest

from citypulse import upstream


@pytest.fixture
def fake_upstream(monkeypatch):
    """Replace the shared client with one whose responses the test scripts, step by step."""
    script, calls = [], []

    def handler(request):
        calls.append(request)
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return httpx.Response(step, json={"ok": step})

    monkeypatch.setattr(upstream, "client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(upstream, "BACKOFF_SECONDS", 0)
    monkeypatch.setattr(upstream, "_host_limits", upstream.defaultdict(lambda: asyncio.Semaphore(2)))
    return script, calls


def run(coro):
    return asyncio.run(coro)


def test_dropped_connection_is_retried(fake_upstream):
    script, calls = fake_upstream
    script += [httpx.ReadError("dropped"), httpx.RemoteProtocolError("disconnected"), 200]
    assert run(upstream.get_json("https://api.example/x")) == {"ok": 200}
    assert len(calls) == 3


def test_temporary_server_error_is_retried(fake_upstream):
    script, calls = fake_upstream
    script += [503, 429, 200]
    assert run(upstream.get_json("https://api.example/x")) == {"ok": 200}
    assert len(calls) == 3


def test_gives_up_after_retries(fake_upstream):
    script, calls = fake_upstream
    script += [httpx.ReadTimeout("slow")] * 3
    with pytest.raises(httpx.ReadTimeout):
        run(upstream.get_json("https://api.example/x"))
    assert len(calls) == 3

    script += [503, 503, 503]
    with pytest.raises(httpx.HTTPStatusError):
        run(upstream.get_json("https://api.example/x"))


def test_client_errors_are_not_retried(fake_upstream):
    script, calls = fake_upstream
    script += [404]
    with pytest.raises(httpx.HTTPStatusError):
        run(upstream.get_json("https://api.example/missing"))
    assert len(calls) == 1


def test_per_host_limit(fake_upstream, monkeypatch):
    script, _ = fake_upstream
    in_flight = {"now": 0, "max": 0}

    async def slow_get(url, params=None):
        in_flight["now"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["now"])
        await asyncio.sleep(0.01)
        in_flight["now"] -= 1
        return httpx.Response(200, json={}, request=httpx.Request("GET", url))

    monkeypatch.setattr(upstream.client, "get", slow_get)

    async def many():
        await asyncio.gather(*[upstream.get_json("https://one-host.example/") for _ in range(10)])

    run(many())
    assert in_flight["max"] == 2  # the fixture caps each host at 2
