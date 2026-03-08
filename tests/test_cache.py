"""Tests for devlens.cache module."""
import pytest
import time
import json
from pathlib import Path
from unittest.mock import patch

from devlens.cache import CacheManager, CacheStats, cached_analysis


# ---------------------------------------------------------------------------
# CacheManager basic operations
# ---------------------------------------------------------------------------

class TestCacheManagerBasic:
    def test_init(self, mock_cache_manager):
        assert mock_cache_manager is not None

    def test_set_and_get(self, mock_cache_manager):
        mock_cache_manager.set(
            "test.py", analyzer="complexity",
            data={"cyclomatic": 5},
        )
        result = mock_cache_manager.get("test.py", analyzer="complexity")
        assert result is not None
        assert result["cyclomatic"] == 5

    def test_get_missing_key(self, mock_cache_manager):
        result = mock_cache_manager.get("nonexistent.py", analyzer="complexity")
        assert result is None

    def test_set_overwrite(self, mock_cache_manager):
        mock_cache_manager.set("f.py", analyzer="security", data={"count": 1})
        mock_cache_manager.set("f.py", analyzer="security", data={"count": 2})
        result = mock_cache_manager.get("f.py", analyzer="security")
        assert result["count"] == 2

    def test_different_analyzers(self, mock_cache_manager):
        mock_cache_manager.set("f.py", analyzer="complexity", data={"c": 1})
        mock_cache_manager.set("f.py", analyzer="security", data={"s": 2})
        c = mock_cache_manager.get("f.py", analyzer="complexity")
        s = mock_cache_manager.get("f.py", analyzer="security")
        assert c["c"] == 1
        assert s["s"] == 2


# ---------------------------------------------------------------------------
# Invalidation and clearing
# ---------------------------------------------------------------------------

class TestCacheInvalidation:
    def test_invalidate_specific(self, mock_cache_manager):
        mock_cache_manager.set("a.py", analyzer="complexity", data={"x": 1})
        mock_cache_manager.set("b.py", analyzer="complexity", data={"x": 2})
        count = mock_cache_manager.invalidate("a.py", analyzer="complexity")
        assert count >= 1
        assert mock_cache_manager.get("a.py", analyzer="complexity") is None
        assert mock_cache_manager.get("b.py", analyzer="complexity") is not None

    def test_invalidate_all_analyzers(self, mock_cache_manager):
        mock_cache_manager.set("f.py", analyzer="complexity", data={"c": 1})
        mock_cache_manager.set("f.py", analyzer="security", data={"s": 2})
        count = mock_cache_manager.invalidate("f.py")
        assert count >= 2
        assert mock_cache_manager.get("f.py", analyzer="complexity") is None
        assert mock_cache_manager.get("f.py", analyzer="security") is None

    def test_clear_all(self, mock_cache_manager):
        mock_cache_manager.set("a.py", analyzer="complexity", data={"x": 1})
        mock_cache_manager.set("b.py", analyzer="security", data={"y": 2})
        count = mock_cache_manager.clear()
        assert count >= 2
        assert mock_cache_manager.get("a.py", analyzer="complexity") is None
        assert mock_cache_manager.get("b.py", analyzer="security") is None

    def test_invalidate_nonexistent(self, mock_cache_manager):
        count = mock_cache_manager.invalidate("ghost.py", analyzer="complexity")
        assert count == 0


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestCacheStats:
    def test_stats_empty(self, mock_cache_manager):
        stats = mock_cache_manager.stats()
        assert isinstance(stats, CacheStats)

    def test_stats_after_set(self, mock_cache_manager):
        mock_cache_manager.set("a.py", analyzer="complexity", data={"x": 1})
        mock_cache_manager.set("b.py", analyzer="security", data={"y": 2})
        stats = mock_cache_manager.stats()
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
        with CacheManager(root=str(tmp_path), ttl_days=7) as cache:
            cache.set("test.py", analyzer="complexity", data={"val": 42})
            result = cache.get("test.py", analyzer="complexity")
            assert result["val"] == 42

    def test_context_manager_saves_on_exit(self, tmp_path):
        with CacheManager(root=str(tmp_path), ttl_days=7) as cache:
            cache.set("test.py", analyzer="complexity", data={"val": 1})
        # After exit, data should be persisted
        cache2 = CacheManager(root=str(tmp_path), ttl_days=7)
        result = cache2.get("test.py", analyzer="complexity")
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
    def test_calls_function_on_miss(self, mock_cache_manager):
        call_count = 0
        def analyze_fn(filepath, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"result": "fresh"}

        result = cached_analysis(
            mock_cache_manager, "test.py", "complexity", analyze_fn,
        )
        assert result["result"] == "fresh"
        assert call_count == 1

    def test_returns_cached_on_hit(self, mock_cache_manager):
        call_count = 0
        def analyze_fn(filepath, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"result": "fresh"}

        # First call - cache miss
        cached_analysis(mock_cache_manager, "test.py", "complexity", analyze_fn)
        # Second call - cache hit
        result = cached_analysis(
            mock_cache_manager, "test.py", "complexity", analyze_fn,
        )
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
