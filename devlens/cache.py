"""Incremental file cache for DevLens analyzers.

Uses SHA-256 file hashes to skip re-analysis of unchanged files.
Cache is stored as JSON in a configurable directory (default: .devlens-cache/).

Usage:
    from devlens.cache import CacheManager

    cache = CacheManager(root=".", ttl_days=7)
    cached = cache.get("src/app.py", analyzer="security", version="0.5.0")
    if cached is not None:
        return cached  # skip analysis
    result = run_analysis(...)
    cache.set("src/app.py", analyzer="security", version="0.5.0", data=result)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


__version__ = "0.5.0"

DEFAULT_CACHE_DIR = ".devlens-cache"
DEFAULT_TTL_DAYS = 7


@dataclass
class CacheStats:
    """Cache statistics."""
    total_entries: int = 0
    valid_entries: int = 0
    expired_entries: int = 0
    size_bytes: int = 0
    analyzers: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total_entries": self.total_entries,
            "valid_entries": self.valid_entries,
            "expired_entries": self.expired_entries,
            "size_bytes": self.size_bytes,
            "size_human": _human_size(self.size_bytes),
            "analyzers": self.analyzers,
        }


def _human_size(nbytes: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


class CacheManager:
    """SHA-256 based incremental cache for file analysis results.

    Cache key = file_hash + analyzer_name + analyzer_version + config_hash
    This ensures cache invalidation when:
      - File content changes (different hash)
      - Analyzer logic changes (different version)
      - Config changes (different config hash)
    """

    def __init__(
        self,
        root: str | Path = ".",
        cache_dir: str = DEFAULT_CACHE_DIR,
        ttl_days: int = DEFAULT_TTL_DAYS,
        enabled: bool = True,
    ):
        self.root = Path(root).resolve()
        self.cache_path = self.root / cache_dir
        self.ttl_seconds = ttl_days * 86400
        self.enabled = enabled
        self._store: dict[str, dict] = {}
        self._dirty = False

        if self.enabled:
            self._load()

    # -- Core API --

    def get(
        self,
        filepath: str,
        *,
        analyzer: str,
        version: str = __version__,
        config_hash: str = "",
    ) -> Any | None:
        """Retrieve cached result for a file, or None if miss/expired."""
        if not self.enabled:
            return None

        file_hash = self._hash_file(filepath)
        if not file_hash:
            return None

        cache_key = self._make_key(filepath, analyzer)
        entry = self._store.get(cache_key)
        if entry is None:
            return None

        # Validate hash, version, config
        if entry.get("file_hash") != file_hash:
            return None
        if entry.get("version") != version:
            return None
        if config_hash and entry.get("config_hash") != config_hash:
            return None

        # Check TTL
        cached_at = entry.get("timestamp", 0)
        if time.time() - cached_at > self.ttl_seconds:
            return None

        return entry.get("data")

    def set(
        self,
        filepath: str,
        *,
        analyzer: str,
        data: Any,
        version: str = __version__,
        config_hash: str = "",
    ) -> None:
        """Store analysis result in cache."""
        if not self.enabled:
            return

        file_hash = self._hash_file(filepath)
        if not file_hash:
            return

        cache_key = self._make_key(filepath, analyzer)
        self._store[cache_key] = {
            "file_hash": file_hash,
            "version": version,
            "config_hash": config_hash,
            "timestamp": time.time(),
            "analyzer": analyzer,
            "filepath": filepath,
            "data": data,
        }
        self._dirty = True

    def invalidate(self, filepath: str, *, analyzer: str | None = None) -> int:
        """Remove cache entries for a file. Returns count removed."""
        removed = 0
        keys_to_remove = []

        for key, entry in self._store.items():
            if entry.get("filepath") == filepath:
                if analyzer is None or entry.get("analyzer") == analyzer:
                    keys_to_remove.append(key)

        for key in keys_to_remove:
            del self._store[key]
            removed += 1

        if removed:
            self._dirty = True
        return removed

    def clear(self) -> int:
        """Clear all cache entries. Returns count removed."""
        count = len(self._store)
        self._store.clear()
        self._dirty = True
        self.save()

        # Also remove the cache directory
        if self.cache_path.exists():
            import shutil
            shutil.rmtree(self.cache_path, ignore_errors=True)

        return count

    def stats(self) -> CacheStats:
        """Get cache statistics."""
        st = CacheStats()
        now = time.time()

        for entry in self._store.values():
            st.total_entries += 1
            analyzer = entry.get("analyzer", "unknown")
            st.analyzers[analyzer] = st.analyzers.get(analyzer, 0) + 1

            cached_at = entry.get("timestamp", 0)
            if now - cached_at <= self.ttl_seconds:
                st.valid_entries += 1
            else:
                st.expired_entries += 1

        # Calculate size on disk
        if self.cache_path.exists():
            for fp in self.cache_path.rglob("*"):
                if fp.is_file():
                    st.size_bytes += fp.stat().st_size

        return st

    def save(self) -> None:
        """Persist cache to disk."""
        if not self.enabled or not self._dirty:
            return

        self.cache_path.mkdir(parents=True, exist_ok=True)
        cache_file = self.cache_path / "cache.json"

        try:
            cache_file.write_text(json.dumps(self._store, indent=2, default=str))
            self._dirty = False
        except (OSError, TypeError) as exc:
            # Non-fatal: cache write failure shouldn't break analysis
            pass

    def __enter__(self) -> "CacheManager":
        return self

    def __exit__(self, *args: Any) -> None:
        self.save()

    # -- Internal helpers --

    def _load(self) -> None:
        """Load cache from disk."""
        cache_file = self.cache_path / "cache.json"
        if cache_file.exists():
            try:
                self._store = json.loads(cache_file.read_text())
            except (json.JSONDecodeError, OSError):
                self._store = {}

    def _hash_file(self, filepath: str) -> str | None:
        """Compute SHA-256 hash of file content."""
        try:
            p = Path(filepath)
            if not p.is_absolute():
                p = self.root / p
            if not p.exists() or p.stat().st_size > 10_000_000:  # skip >10MB
                return None
            return hashlib.sha256(p.read_bytes()).hexdigest()
        except (OSError, PermissionError):
            return None

    def _make_key(self, filepath: str, analyzer: str) -> str:
        """Create a cache key from filepath and analyzer name."""
        return f"{analyzer}::{filepath}"

    @staticmethod
    def config_hash(config: dict) -> str:
        """Create a hash from a config dict for cache invalidation."""
        serialized = json.dumps(config, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]


# -- Convenience wrapper for analyzer integration --

def cached_analysis(
    cache: CacheManager | None,
    filepath: str,
    analyzer: str,
    analyze_fn: Any,
    *,
    version: str = __version__,
    config_hash: str = "",
    **kwargs: Any,
) -> Any:
    """Run analysis with caching. If cache hit, return cached result.
    Otherwise, call analyze_fn(filepath, **kwargs) and cache the result.

    Usage:
        result = cached_analysis(
            cache, "src/app.py", "security",
            lambda fp, **kw: scan_file(fp),
            version="0.5.0",
        )
    """
    if cache:
        cached = cache.get(filepath, analyzer=analyzer, version=version, config_hash=config_hash)
        if cached is not None:
            return cached

    result = analyze_fn(filepath, **kwargs)

    if cache and result is not None:
        # Convert to dict if possible for JSON serialization
        data = result.to_dict() if hasattr(result, "to_dict") else result
        cache.set(filepath, analyzer=analyzer, data=data, version=version, config_hash=config_hash)

    return result
