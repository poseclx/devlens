"""Tests for devlens.cache module."""
import pytest
import time
import json
from pathlib import Path
from unittest.mock import patch

from devlens.cache import CacheManager, CacheStats, cached_analysis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(tmp_path, relpath, content="print('hello')"):
    """Create a real file under tmp_path so _hash_file can compute SHA-256."""
    p = tmp_path / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return str(relpath)


@pytest.fixture
def cache(tmp_path):
    """Create a CacheManager rooted at tmp_path."""
    return CacheManager(root=str(tmp_path), ttl_days=7)


# ---------------------------------------------------------------------------
# CacheManager basic operations
# ---------------------------------------------------------------------------

class TestCacheManagerBasic:
    def test_init(self, cache):
        assert cache is not None

    def test_set_and_get(self, cache, tmp_path):
        fp = _make_file(tmp_path, "test.py")
        cache.set(fp, analyzer="complexity", data={"cyclomatic": 5})
        result = cache.get(fp, analyzer="complexity")
        assert result is not None
        assert result["cyclomatic"] == 5

    def test_get_missing_key(self, cache):
        result = cache.get("nonexistent.py", analyzer="complexity")
        assert result is None

    def test_set_overwrite(self, cache, tmp_path):
        fp = _make_file(tmp_path, "f.py")
        cache.set(fp, analyzer="security", data={"count": 1})
        cache.set(fp, analyzer="security", data={"count": 2})
        result = cache.get(fp, analyzer="security")
        assert result["count"] == 2

    def test_different_analyzers(self, cache, tmp_path):
        fp = _make_file(tmp_path, "f.py")
        cache.set(fp, analyzer="complexity", data={"c": 1})
        cache.set(fp, analyzer="security", data={"s": 2})
        c = cache.get(fp, analyzer="complexity")
        s = cache.get(fp, analyzer="security")
        assert c["c"] == 1
        assert s["s"] == 2


# ---------------------------------------------------------------------------
# Invalidation and clearing
# ---------------------------------------------------------------------------

class TestCacheInvalidation:
    def test_invalidate_specific(self, cache, tmp_path):
        a = _make_file(tmp_path, "a.py", "aaa")
        b = _make_file(tmp_path, "b.py", "bbb")
        cache.set(a, analyzer="complexity", data={"x": 1})
        cache.set(b, analyzer="complexity", data={"x": 2})
        count = cache.invalidate(a, analyzer="complexity")
        assert count >= 1
        assert cache.get(a, analyzer="complexity") is None
        assert cache.get(b, analyzer="complexity") is not None

    def test_invalidate_all_analyzers(self, cache, tmp_path):
        fp = _make_file(tmp_path, "f.py")
        cache.set(fp, analyzer="complexity", data={"c": 1})
        cache.set(fp, analyzer="security", data={"s": 2})
        count = cache.invalidate(fp)
        assert count >= 2
        assert cache.get(fp, analyzer="complexity") is None
        assert cache.get(fp, analyzer="security") is None

    def test_clear_all(self, cache, tmp_path):
        a = _make_file(tmp_path, "a.py", "aaa")
        b = _make_file(tmp_path, "b.py", "bbb")
        cache.set(a, analyzer="complexity", data={"x": 1})
        cache.set(b, analyzer="security", data={"y": 2})
        count = cache.clear()
        assert count >= 2
        assert cache.get(a, analyzer="complexity") is None
        assert cache.get(b, analyzer="security") is None

    def test_invalidate_nonexistent(self, cache):
        count = cache.invalidate("ghost.py", analyzer="complexity")
        assert count == 0


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestCacheStats:
    def test_stats_empty(self, cache):
        stats = cache.stats()
        assert isinstance(stats, CacheStats)

    def test_stats_after_set(self, cache, tmp_path):
        a = _make_file(tmp_path, "a.py", "aaa")
        b = _make_file(tmp_path, "b.py", "bbb")
        cache.set(a, analyzer="complexity", data={"x": 1})
        cache.set(b, analyzer="security", data={"y": 2})
        stats = cache.stats()
        d = stats.to_dict()
        assert isinstance(d, dict)

    def test_stats_to_dict(self):
        stats = CacheStats()
        d = stats.to_dict()
        assert isinstance(d, dict)


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

class TestCacheContextManager:
    def test_context_manager(self, tmp_path):
        fp = _make_file(tmp_path, "test.py")
        with CacheManager(root=str(tmp_path), ttl_days=7) as cm:
            cm.set(fp, analyzer="complexity", data={"val": 42})
            result = cm.get(fp, analyzer="complexity")
            assert result["val"] == 42

    def test_context_manager_saves_on_exit(self, tmp_path):
        fp = _make_file(tmp_path, "test.py")
        with CacheManager(root=str(tmp_path), ttl_days=7) as cm:
            cm.set(fp, analyzer="complexity", data={"val": 1})
        # After exit, data should be persisted
        cache2 = CacheManager(root=str(tmp_path), ttl_days=7)
        result = cache2.get(fp, analyzer="complexity")
        assert result is not None


# ---------------------------------------------------------------------------
# config_hash static method
# ---------------------------------------------------------------------------

class TestConfigHash:
    def test_same_config_same_hash(self):
        cfg = {"model": "gpt-4o", "detail": "high"}
        h1 = CacheManager.config_hash(cfg)
        h2 = CacheManager.config_hash(cfg)
        assert h1 == h2

    def test_different_config_different_hash(self):
        h1 = CacheManager.config_hash({"model": "gpt-4o"})
        h2 = CacheManager.config_hash({"model": "claude-3"})
        assert h1 != h2

    def test_hash_is_string(self):
        h = CacheManager.config_hash({"key": "value"})
        assert isinstance(h, str)
        assert len(h) > 0


# ---------------------------------------------------------------------------
# cached_analysis wrapper
# ---------------------------------------------------------------------------

class TestCachedAnalysis:
    def test_calls_function_on_miss(self, tmp_path):
        fp = _make_file(tmp_path, "test.py")
        cm = CacheManager(root=str(tmp_path), ttl_days=7)
        call_count = 0
        def analyze_fn(filepath, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"result": "fresh"}

        result = cached_analysis(cm, fp, "complexity", analyze_fn)
        assert result["result"] == "fresh"
        assert call_count == 1

    def test_returns_cached_on_hit(self, tmp_path):
        fp = _make_file(tmp_path, "test.py")
        cm = CacheManager(root=str(tmp_path), ttl_days=7)
        call_count = 0
        def analyze_fn(filepath, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"result": "fresh"}

        # First call - cache miss
        cached_analysis(cm, fp, "complexity", analyze_fn)
        # Second call - cache hit
        result = cached_analysis(cm, fp, "complexity", analyze_fn)
        assert call_count == 1  # Function only called once
        assert result["result"] == "fresh"

    def test_no_cache_always_calls(self):
        call_count = 0
        def analyze_fn(filepath, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"result": call_count}

        cached_analysis(None, "test.py", "complexity", analyze_fn)
        cached_analysis(None, "test.py", "complexity", analyze_fn)
        assert call_count == 2

    def test_disabled_cache(self, tmp_path):
        cache = CacheManager(root=str(tmp_path), enabled=False)
        call_count = 0
        def analyze_fn(filepath, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"result": call_count}

        cached_analysis(cache, "test.py", "complexity", analyze_fn)
        cached_analysis(cache, "test.py", "complexity", analyze_fn)
        assert call_count == 2
