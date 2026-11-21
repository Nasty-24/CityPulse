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
import threading
import time
import weakref
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = os.environ.get("CITYPULSE_CACHE_DB", str(ROOT / "cache.db"))
KEEP_EXPIRED_FOR = 7 * 86400  # expired rows are kept this long as stale fallbacks
MEMORY_ITEMS = 5000           # L1 holds at most this many entries (least recently used go first)


class Cache:
    def __init__(self, path: str = DEFAULT_PATH, memory_items: int = MEMORY_ITEMS):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("pragma journal_mode=wal")
        # with WAL this stays crash-safe; a power cut can only lose the newest few writes, which for a
        # cache just means refetching. Full sync made every write wait on the disk (~170 ms each).
        self.db.execute("pragma synchronous=normal")
        self.db.execute("create table if not exists c(k text primary key, exp real, val text)")
        self.db.execute("delete from c where exp < ?", (time.time() - KEEP_EXPIRED_FOR,))
        self.db.commit()
        self.db_lock = threading.Lock()  # SQLite calls run in worker threads
        self.memory: OrderedDict = OrderedDict()
        self.memory_items = memory_items
        # a lock lives only while someone holds or waits on it, so this never grows unbounded
        self.locks: weakref.WeakValueDictionary = weakref.WeakValueDictionary()

    # --- SQLite (L2), called through asyncio.to_thread so the event loop never blocks
    def _read_disk(self, key):
        with self.db_lock:
            row = self.db.execute("select exp, val from c where k = ?", (key,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def _write_disk(self, key, expires_at, value):
        payload = json.dumps(value)
        with self.db_lock:
            self.db.execute("insert or replace into c values (?, ?, ?)", (key, expires_at, payload))
            self.db.commit()

    # --- memory (L1)
    def _remember(self, key, entry):
        self.memory[key] = entry
        self.memory.move_to_end(key)
        while len(self.memory) > self.memory_items:
            self.memory.popitem(last=False)

    async def _lookup(self, key):
        entry = self.memory.get(key)
        if entry:
            self.memory.move_to_end(key)
            return entry
        entry = await asyncio.to_thread(self._read_disk, key)
        if entry:
            self._remember(key, entry)
        return entry

    def _lock_for(self, key):
        lock = self.locks.get(key)
        if lock is None:
            lock = self.locks[key] = asyncio.Lock()
        return lock

    async def get_or_fetch(self, key: str, ttl: float, fetch):
        """Return (value, status) where status is "hit", "miss" or "stale"."""
        entry = await self._lookup(key)
        if entry and entry[0] > time.time():
            return entry[1], "hit"
        lock = self._lock_for(key)  # strong reference held for the whole fetch
        async with lock:
            entry = await self._lookup(key)  # another request may have just filled it
            if entry and entry[0] > time.time():
                return entry[1], "hit"
            try:
                value = await fetch()
            except Exception:
                if entry:
                    return entry[1], "stale"
                raise
            expires_at = time.time() + ttl
            self._remember(key, (expires_at, value))
            await asyncio.to_thread(self._write_disk, key, expires_at, value)
            return value, "miss"

    def close(self):
        with self.db_lock:
            self.db.close()


store = Cache()


def cached(key: str, ttl: float, fetch):
    """Shortcut for the shared cache used by the app."""
    return store.get_or_fetch(key, ttl, fetch)
