import asyncio
import gc
import time

import pytest

from citypulse.cache import Cache


def run(coro):
    return asyncio.run(coro)


def counter():
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        await asyncio.sleep(0.01)
        return {"call": calls["n"]}

    return calls, fetch


def test_miss_then_hit():
    c = Cache(":memory:")
    calls, fetch = counter()
    assert run(c.get_or_fetch("k", 60, fetch)) == ({"call": 1}, "miss")
    assert run(c.get_or_fetch("k", 60, fetch)) == ({"call": 1}, "hit")
    assert calls["n"] == 1


def test_expired_entry_is_refetched():
    c = Cache(":memory:")
    calls, fetch = counter()
    run(c.get_or_fetch("k", -1, fetch))  # already expired
    value, status = run(c.get_or_fetch("k", 60, fetch))
    assert (value, status) == ({"call": 2}, "miss")


def test_stale_value_served_when_refresh_fails():
    c = Cache(":memory:")
    c.memory["k"] = (time.time() - 1, {"old": True})

    async def broken():
        raise RuntimeError("upstream down")

    assert run(c.get_or_fetch("k", 60, broken)) == ({"old": True}, "stale")


def test_failure_without_cached_value_raises_and_is_not_cached():
    c = Cache(":memory:")

    async def broken():
        raise RuntimeError("upstream down")

    with pytest.raises(RuntimeError):
        run(c.get_or_fetch("k", 60, broken))
    calls, fetch = counter()
    assert run(c.get_or_fetch("k", 60, fetch))[1] == "miss"  # the failure was not stored


def test_concurrent_requests_share_one_upstream_call():
    c = Cache(":memory:")
    calls, fetch = counter()

    async def many():
        return await asyncio.gather(*[c.get_or_fetch("k", 60, fetch) for _ in range(20)])

    results = run(many())
    assert calls["n"] == 1
    assert {v["call"] for v, _ in results} == {1}


def test_memory_is_bounded_but_disk_keeps_everything():
    c = Cache(":memory:", memory_items=3)
    calls, fetch = counter()
    for i in range(6):
        run(c.get_or_fetch(f"k{i}", 60, fetch))
    assert list(c.memory) == ["k3", "k4", "k5"]
    # an evicted key is read back from SQLite without calling upstream
    assert run(c.get_or_fetch("k0", 60, fetch)) == ({"call": 1}, "hit")
    assert calls["n"] == 6


def test_locks_are_released_after_use():
    c = Cache(":memory:")
    _, fetch = counter()
    for i in range(10):
        run(c.get_or_fetch(f"k{i}", 60, fetch))
    gc.collect()
    assert len(c.locks) == 0
