import os
import sys
from pathlib import Path

import pytest

# never touch the real cache file during tests
os.environ["CITYPULSE_CACHE_DB"] = ":memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from citypulse import cache  # noqa: E402

NAIROBI = {"id": 184745, "name": "Nairobi", "country": "Kenya", "country_code": "KE",
           "latitude": -1.28, "longitude": 36.82, "population": 4397073, "elevation": 1684.0,
           "timezone": "Africa/Nairobi"}


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    """Each test gets an empty in-memory cache."""
    store = cache.Cache(":memory:")
    monkeypatch.setattr(cache, "store", store)
    yield store
    store.close()
