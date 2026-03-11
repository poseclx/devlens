"""Tests for devlens.ai_review — AI-powered code review engine."""
import pytest
import json
import time
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from dataclasses import dataclass

from devlens.ai_review import (
    AIProvider,
    ReviewMode,
    AIReviewConfig,
    TokenCounter,
    RateLimiter,
    ResponseCache,
    AIReviewer,
    run_ai_review_sync,
    configure_api_key,
)


# ── AIProvider enum ──────────────────────────────────────────────────

class TestAIProvider:
    """AIProvider enum values."""

    def test_values(self):
        assert AIProvider.OPENAI.value == "openai"
        assert AIProvider.ANTHROPIC.value == "anthropic"

    def test_is_str(self):
        assert isinstance(AIProvider.OPENAI, str)


# ── ReviewMode enum ──────────────────────────────────────────────────

class TestReviewMode:
    """ReviewMode enum values."""

    def test_all_modes(self):
        assert ReviewMode.REVIEW.value == "review"
        assert ReviewMode.SUGGEST_FIXES.value == "suggest_fixes"
        assert ReviewMode.EXPLAIN.value == "explain"
        assert ReviewMode.COMMIT_MSG.value == "commit_msg"
        assert ReviewMode.BUG_DETECT.value == "bug_detect"
        assert ReviewMode.REFACTOR.value == "refactor"

    def test_mode_count(self):
        assert len(ReviewMode) == 6


# ── AIReviewConfig ───────────────────────────────────────────────────

class TestAIReviewConfig:
    """AIReviewConfig defaults and from_dict."""

    def test_defaults(self):
        c = AIReviewConfig()
        assert c.provider == AIProvider.OPENAI
        assert c.max_tokens == 4096
        assert c.temperature == 0.3
        assert c.timeout == 60.0
        assert c.max_retries == 3
        assert c.cache_enabled is True
        assert c.enabled is True

    def test_default_model_openai(self):
        c = AIReviewConfig(provider=AIProvider.OPENAI)
        assert c.default_model == "gpt-4o"

    def test_default_model_anthropic(self):
        c = AIReviewConfig(provider=AIProvider.ANTHROPIC)
        assert "claude" in c.default_model

    def test_custom_model_overrides(self):
        c = AIReviewConfig(model="gpt-3.5-turbo")
        assert c.default_model == "gpt-3.5-turbo"

    def test_from_dict_basic(self):
        d = {"provider": "openai", "model": "gpt-4o", "api_key": "sk-test"}
        c = AIReviewConfig.from_dict(d)
        assert c.provider == AIProvider.OPENAI
        assert c.model == "gpt-4o"
        assert c.api_key == "sk-test"

    def test_from_dict_anthropic(self):
        d = {"provider": "anthropic", "api_key": "sk-ant-test"}
        c = AIReviewConfig.from_dict(d)
        assert c.provider == AIProvider.ANTHROPIC

    def test_from_dict_env_fallback(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        d = {"provider": "openai"}
        c = AIReviewConfig.from_dict(d)
        assert c.api_key == "sk-from-env"

    def test_from_dict_devlens_env(self, monkeypatch):
        monkeypatch.setenv("DEVLENS_AI_API_KEY", "sk-devlens")
        d = {"provider": "openai"}
        c = AIReviewConfig.from_dict(d)
        assert c.api_key == "sk-devlens"


# ── TokenCounter ─────────────────────────────────────────────────────

class TestTokenCounter:
    """TokenCounter tracks token usage."""

    def test_initial_state(self):
        tc = TokenCounter()
        assert tc.total_tokens == 0
        stats = tc.stats
        assert stats["total_requests"] == 0

    def test_record(self):
        tc = TokenCounter()
        tc.record(100, 50)
        assert tc.total_tokens == 150
        assert tc.stats["prompt_tokens"] == 100
        assert tc.stats["completion_tokens"] == 50
        assert tc.stats["total_requests"] == 1

    def test_multiple_records(self):
        tc = TokenCounter()
        tc.record(100, 50)
        tc.record(200, 100)
        assert tc.total_tokens == 450
        assert tc.stats["total_requests"] == 2

    def test_cached_records(self):
        tc = TokenCounter()
        tc.record(100, 50, cached=True)
        assert tc.stats["cached_requests"] == 1

    def test_estimate_tokens(self):
        tc = TokenCounter()
        est = tc.estimate_tokens("Hello world, this is a test.")
        assert est > 0
        # ~4 chars per token
        assert est == pytest.approx(len("Hello world, this is a test.") / 4, abs=5)

    def test_reset(self):
        tc = TokenCounter()
        tc.record(100, 50)
        tc.reset()
        assert tc.total_tokens == 0
        assert tc.stats["total_requests"] == 0


# ── RateLimiter ──────────────────────────────────────────────────────

class TestRateLimiter:
    """RateLimiter sliding window mechanics."""

    def test_creation(self):
        rl = RateLimiter(rpm=30, tpm=50000)
        assert rl is not None

    @pytest.mark.asyncio
    async def test_acquire_under_limit(self):
        rl = RateLimiter(rpm=100, tpm=100000)
        # Should not block
        await rl.acquire(estimated_tokens=100)

    def test_record_tokens(self):
        rl = RateLimiter(rpm=60, tpm=100000)
        rl.record_tokens(500)
        # No assertion needed -- just verify no error


# ── ResponseCache ────────────────────────────────────────────────────

class TestResponseCache:
    """ResponseCache file-based caching."""

    def test_put_and_get(self, tmp_path):
        cache = ResponseCache(str(tmp_path / "cache"), ttl=3600)
        cache.put("prompt1", "gpt-4o", {"result": "ok"})
        result = cache.get("prompt1", "gpt-4o")
        assert result is not None
        assert result["result"] == "ok"

    def test_miss(self, tmp_path):
        cache = ResponseCache(str(tmp_path / "cache"), ttl=3600)
        result = cache.get("nonexistent", "gpt-4o")
        assert result is None

    def test_different_models_different_keys(self, tmp_path):
        cache = ResponseCache(str(tmp_path / "cache"), ttl=3600)
        cache.put("prompt1", "gpt-4o", {"model": "4o"})
        cache.put("prompt1", "gpt-3.5", {"model": "3.5"})
        r1 = cache.get("prompt1", "gpt-4o")
        r2 = cache.get("prompt1", "gpt-3.5")
        assert r1["model"] == "4o"
        assert r2["model"] == "3.5"

    def test_clear(self, tmp_path):
        cache = ResponseCache(str(tmp_path / "cache"), ttl=3600)
        cache.put("p1", "m1", {"a": 1})
        cache.put("p2", "m2", {"b": 2})
        count = cache.clear()
        assert count >= 2
        assert cache.get("p1", "m1") is None

    def test_prune_expired(self, tmp_path):
        cache = ResponseCache(str(tmp_path / "cache"), ttl=0)  # immediate expiry
        cache.put("p1", "m1", {"a": 1})
        time.sleep(0.1)
        pruned = cache.prune_expired()
        assert pruned >= 1


# ── AIReviewer ───────────────────────────────────────────────────────

class TestAIReviewer:
    """AIReviewer core engine."""

    def _make_reviewer(self, **kwargs):
        defaults = {
            "provider": "openai",
            "api_key": "sk-test-key",
            "cache_enabled": False,
        }
        defaults.update(kwargs)
        return AIReviewer(defaults)

    def test_creation(self):
        r = self._make_reviewer()
        assert r.config.provider == AIProvider.OPENAI
        assert r.available is True

    def test_unavailable_without_key(self):
        r = AIReviewer({"provider": "openai", "api_key": "", "enabled": True})
        assert r.available is False

    def test_disabled(self):
        r = AIReviewer({"provider": "openai", "api_key": "sk-test", "enabled": False})
        assert r.available is False

    def test_detect_language(self):
        r = self._make_reviewer()
        assert r.detect_language(Path("app.py")) == "python"
        assert r.detect_language(Path("app.js")) == "javascript"
        assert r.detect_language(Path("App.java")) == "java"
        assert r.detect_language(Path("main.go")) == "go"
        assert r.detect_language(Path("lib.rs")) == "rust"

    def test_detect_language_unknown(self):
        r = self._make_reviewer()
        lang = r.detect_language(Path("data.csv"))
        assert lang in ("unknown", "text")

    def test_token_stats(self):
        r = self._make_reviewer()
        stats = r.token_stats
        assert stats["total_tokens"] == 0

    def test_language_map(self):
        assert ".py" in AIReviewer.LANGUAGE_MAP
        assert ".js" in AIReviewer.LANGUAGE_MAP
        assert ".ts" in AIReviewer.LANGUAGE_MAP
        assert ".go" in AIReviewer.LANGUAGE_MAP
        assert ".rs" in AIReviewer.LANGUAGE_MAP
        assert ".java" in AIReviewer.LANGUAGE_MAP

    @pytest.mark.asyncio
    async def test_review_file_calls_llm(self):
        r = self._make_reviewer()
        mock_response = {
            "issues": [
                {"title": "Bug", "severity": "high", "line": 5,
                 "description": "Issue found", "suggestion": "Fix it"}
            ]
        }
        with patch.object(r, "_call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await r.review_file(Path("test.py"), code="x = 1\ny = 2\n")
            assert len(result) >= 1
            assert result[0]["title"] == "Bug"

    @pytest.mark.asyncio
    async def test_generate_commit_message(self):
        r = self._make_reviewer()
        mock_response = {
            "subject": "feat: add user auth",
            "body": "Implemented login and signup endpoints.",
        }
        with patch.object(r, "_call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await r.generate_commit_message("diff content here")
            assert "subject" in result
            assert result["subject"] == "feat: add user auth"

    @pytest.mark.asyncio
    async def test_explain_code(self):
        r = self._make_reviewer()
        mock_response = {
            "summary": "A simple function",
            "components": ["variable x"],
            "patterns": [],
            "notes": "Nothing special",
        }
        with patch.object(r, "_call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await r.explain_code("x = 1", language="python")
            assert result["summary"] == "A simple function"

    @pytest.mark.asyncio
    async def test_detect_bugs(self):
        r = self._make_reviewer()
        mock_response = {
            "bugs": [{"title": "Off by one", "severity": "medium",
                       "line": 3, "description": "Loop bound", "suggestion": "Use <="}]
        }
        with patch.object(r, "_call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await r.detect_bugs(Path("app.py"), code="for i in range(n):\n    pass\n")
            assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_suggest_refactoring(self):
        r = self._make_reviewer()
        mock_response = {
            "suggestions": [{"title": "Extract method", "description": "Too long",
                              "before": "old", "after": "new"}]
        }
        with patch.object(r, "_call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await r.suggest_refactoring(Path("app.py"), code="x = 1\n" * 50)
            assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_suggest_fixes(self):
        r = self._make_reviewer()
        issues = [{"title": "Bug", "severity": "high", "line": 5,
                    "description": "Issue", "suggestion": "Fix"}]
        mock_response = {
            "fixes": [{"issue_title": "Bug", "fix": "corrected code",
                        "explanation": "Fixed the bug"}]
        }
        with patch.object(r, "_call_llm", new_callable=AsyncMock, return_value=mock_response):
            result = await r.suggest_fixes(Path("app.py"), issues, code="buggy\n")
            assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_review_files_batch(self):
        r = self._make_reviewer()
        mock_response = {"issues": []}
        with patch.object(r, "_call_llm", new_callable=AsyncMock, return_value=mock_response):
            paths = [Path("a.py"), Path("b.py")]
            # Create temp files for reading
            for p in paths:
                p.write_text("x = 1\n") if not p.exists() else None
            try:
                result = await r.review_files(paths, concurrency=2)
                assert isinstance(result, dict)
            finally:
                for p in paths:
                    p.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_close(self):
        r = self._make_reviewer()
        await r.close()  # should not raise


# ── run_ai_review_sync ───────────────────────────────────────────────

class TestRunAIReviewSync:
    """run_ai_review_sync synchronous CLI wrapper."""

    @patch("devlens.ai_review.AIReviewer")
    def test_review_mode(self, MockReviewer, tmp_path):
        mock_instance = MagicMock()
        mock_instance.available = True
        mock_instance.review_file = AsyncMock(return_value=[])
        mock_instance.review_files = AsyncMock(return_value={})
        mock_instance.token_stats = {"total_tokens": 0}
        mock_instance.close = AsyncMock()
        MockReviewer.return_value = mock_instance

        f = tmp_path / "test.py"
        f.write_text("x = 1\n")
        result = run_ai_review_sync([f], {"ai_review": {"api_key": "test"}}, mode=ReviewMode.REVIEW)
        assert "results" in result or "result" in result
        assert result["mode"] == "review"

    @patch("devlens.ai_review.AIReviewer")
    def test_commit_msg_mode(self, MockReviewer, tmp_path):
        mock_instance = MagicMock()
        mock_instance.available = True
        mock_instance.generate_commit_message = AsyncMock(
            return_value={"subject": "fix: typo", "body": "Fixed typo in readme"}
        )
        mock_instance.token_stats = {"total_tokens": 50}
        mock_instance.close = AsyncMock()
        MockReviewer.return_value = mock_instance

        f = tmp_path / "diff.txt"
        f.write_text("- old\n+ new\n")
        result = run_ai_review_sync([f], {"ai_review": {"api_key": "test"}}, mode=ReviewMode.COMMIT_MSG)
        assert result["mode"] == "commit_msg"


# ── configure_api_key ────────────────────────────────────────────────

class TestConfigureAPIKey:
    """configure_api_key saves keys to config file."""

    def test_saves_openai_key(self, tmp_path):
        config_path = tmp_path / ".devlens.yml"
        result = configure_api_key("openai", "sk-test-key", config_path)
        assert result is True
        content = config_path.read_text()
        assert "sk-test-key" in content

    def test_saves_anthropic_key(self, tmp_path):
        config_path = tmp_path / ".devlens.yml"
        result = configure_api_key("anthropic", "sk-ant-test", config_path)
        assert result is True
        content = config_path.read_text()
        assert "sk-ant-test" in content

    def test_updates_existing_config(self, tmp_path):
        config_path = tmp_path / ".devlens.yml"
        configure_api_key("openai", "old-key", config_path)
        configure_api_key("openai", "new-key", config_path)
        content = config_path.read_text()
        assert "new-key" in content
