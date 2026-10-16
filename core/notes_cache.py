"""
core/notes_cache.py -- the request layer of Create Notes. The cache itself (ResponseCache, CachedFetch, RequestStats)
is redactor_common's (redactor_common/core/notes_cache.py, shared with videoredactor); what stays here is what is
mp3redactor's own: where the cache file lives and the Discogs wrapper (Discogs is only used here).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from redactor_common.core import notes_cache as _shared
from redactor_common.core.notes_cache import (  # noqa: F401 -- re-exported: the engine and the tests import them from here
    MAX_BYTES, NEGATIVE_TTL_SECONDS, TTL_SECONDS, CachedFetch, RequestStats, ResponseCache, looks_empty,
)

CACHE_FILE_NAME = "mp3redactor_notes_cache.sqlite"

__all__ = ["CachedDiscogs", "CachedFetch", "RequestStats", "ResponseCache", "cache_path", "default_cache",
           "clear_default_cache", "looks_empty", "MAX_BYTES", "NEGATIVE_TTL_SECONDS", "TTL_SECONDS"]


def cache_path() -> Path:
    """Next to the settings file (never in a library folder)."""
    from core.app_paths import base_dir

    return base_dir() / CACHE_FILE_NAME


def default_cache(enabled: bool = True) -> Optional[ResponseCache]:
    """The process-wide cache at cache_path(), or None when switched off."""
    return _shared.default_cache(cache_path(), enabled)


def clear_default_cache() -> int:
    """Preferences > Clear the notes lookup cache: the entries removed (0 when there is none)."""
    return _shared.clear_default_cache(cache_path())


class CachedDiscogs:
    """The Discogs client's `get_json` with the same memo and cache. Only GET lookups: the key is the endpoint and its
    parameters, never the token (it is sent in a header by the client and is not part of the key or the stored data)."""

    def __init__(self, client, cache: Optional[ResponseCache] = None, stats: Optional[RequestStats] = None, phases=None):
        self._client = client
        self.phases = phases
        self.cache = cache
        self.stats = stats if stats is not None else RequestStats()
        self._memo: dict[str, dict] = {}
        self._lock = threading.Lock()

    def __getattr__(self, name):  # rate_limited and anything else the engine asks of the client
        return getattr(self._client, name)

    def get_json(self, path: str, **params) -> dict:
        key = "discogs|" + path + "?" + urlencode(sorted((k, str(v)) for k, v in params.items()))
        with self._lock:
            if key in self._memo:
                self.stats.from_memory += 1
                return self._memo[key]
        if self.cache is not None:
            found = self.cache.get(key)
            if found is not None and found[0] == 200:
                try:
                    data = json.loads(found[1])
                except (ValueError, UnicodeDecodeError):
                    data = None
                if isinstance(data, dict):
                    self.stats.from_cache += 1
                    with self._lock:
                        self._memo[key] = data
                    return data
        self.stats.requests += 1
        started = time.perf_counter()
        try:
            data = self._client.get_json(path, **params)
        finally:
            if self.phases is not None:
                self.phases.add("discogs", time.perf_counter() - started)
        with self._lock:
            self._memo[key] = data
        if self.cache is not None and isinstance(data, dict) and data:
            self.cache.put(key, json.dumps(data).encode("utf-8"), 200)
        return data
