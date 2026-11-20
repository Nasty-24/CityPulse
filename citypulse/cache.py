"""Two-tier cache: in-memory (L1) over SQLite (L2), with stale-on-error.

Every entry is (expires_at, value). A fresh entry is a "hit". An expired or
missing entry is refetched under a per-key lock (a "miss"). If the refetch
fails and an expired entry exists, the old value is served as "stale".
Failures are never cached.
"""

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = os.environ.get("CITYPULSE_CACHE_DB", str(ROOT / "cache.db"))
KEEP_EXPIRED_FOR = 7 * 86400  # expired rows are kept this long as stale fallbacks


class Cache:
    def __init__(self, path: str = DEFAULT_PATH):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("pragma journal_mode=wal")
        self.db.execute("create table if not exists c(k text primary key, exp real, val text)")
        self.db.execute("delete from c where exp < ?", (time.time() - KEEP_EXPIRED_FOR,))
        self.db.commit()
        self.memory: dict = {}
        self.locks: dict = {}

    def _read_disk(self, key):
        row = self.db.execute("select exp, val from c where k = ?", (key,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def _write_disk(self, key, expires_at, value):
        self.db.execute("insert or replace into c values (?, ?, ?)", (key, expires_at, json.dumps(value)))
        self.db.commit()

    def _lookup(self, key):
        entry = self.memory.get(key)
        if not entry:
            entry = self._read_disk(key)
            if entry:
                self.memory[key] = entry
        return entry

    async def get_or_fetch(self, key: str, ttl: float, fetch):
        """Return (value, status) where status is "hit", "miss" or "stale"."""
        entry = self._lookup(key)
        if entry and entry[0] > time.time():
            return entry[1], "hit"
        async with self.locks.setdefault(key, asyncio.Lock()):
            entry = self._lookup(key)  # another request may have just filled it
            if entry and entry[0] > time.time():
                return entry[1], "hit"
            try:
                value = await fetch()
            except Exception:
                if entry:
                    return entry[1], "stale"
                raise
            expires_at = time.time() + ttl
            self.memory[key] = (expires_at, value)
            self._write_disk(key, expires_at, value)
            return value, "miss"

    def close(self):
        self.db.close()


store = Cache()


def cached(key: str, ttl: float, fetch):
    """Shortcut for the shared cache used by the app."""
    return store.get_or_fetch(key, ttl, fetch)
