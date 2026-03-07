"""Tests for devlens.analyzer module."""
import json
import pytest
from unittest.mock import patch, MagicMock

from devlens.analyzer import (
    ReviewResult,
    _build_prompt,
    _call_llm,
    analyze_pr,
)


SAMPLE_LLM_RESPONSE = json.dumps({
    "summary": "Adds JWT authentication to the API.",
    "risk_items": [
        {"file": "auth/jwt.py", "reason": "No token expiry check", "severity": "high"}
    ],
    "safe_items": [
        {"file": "requirements.txt", "reason": "Dependency-only change"}
    ],
    "verdict": "Needs changes before merging.",
})


class TestReviewResult:
    def test_to_dict_round_trip(self):
        result = ReviewResult(
            pr_number=1,
            title="Test PR",
            summary="A summary",
            risk_items=[{"file": "foo.py", "reason": "risky", "severity": "high"}],
            safe_items=[{"file": "bar.py", "reason": "safe"}],
            verdict="Ready to merge",
        )
        d = result.to_dict()
        assert d["pr_number"] == 1
        assert d["title"] == "Test PR"
        assert len(d["risk_items"]) == 1
        assert len(d["safe_items"]) == 1

    def test_to_markdown_contains_key_sections(self):
        result = ReviewResult(
            pr_number=7,
            title="Fix login bug",
            summary="Fixes the login crash.",
            risk_items=[{"file": "login.py", "reason": "null check missing", "severity": "high"}],
            safe_items=[{"file": "README.md", "reason": "docs only"}],
            verdict="Ready to merge",
        )
        md = result.to_markdown()
        assert "## PR #7" in md
        assert "Fix login bug" in md
        assert "login.py" in md
        assert "README.md" in md
        assert "Ready to merge" in md
        assert "[HIGH]" in md

    def test_to_markdown_empty_items(self):
        result = ReviewResult(pr_number=1, title="T", summary="S")
        md = result.to_markdown()
        assert "## PR #1" in md


class TestBuildPrompt:
    def test_includes_pr_metadata(self, sample_pr_data):
        prompt = _build_prompt(sample_pr_data, detail="medium")
        assert "PR #42" in prompt
        assert "Add user authentication" in prompt
        assert "feature/auth" in prompt

    def test_includes_file_names(self, sample_pr_data):
        prompt = _build_prompt(sample_pr_data, detail="medium")
        assert "auth/jwt.py" in prompt
        assert "requirements.txt" in prompt

    def test_detail_low_truncates_patch(self, sample_pr_data):
        prompt_low = _build_prompt(sample_pr_data, detail="low")
        prompt_high = _build_prompt(sample_pr_data, detail="high")
        assert len(prompt_low) <= len(prompt_high)


class TestCallLlm:
    def test_routes_gpt_to_openai(self, sample_pr_data):
        with patch("devlens.analyzer._openai") as mock_openai:
            mock_openai.return_value = SAMPLE_LLM_RESPONSE
            _call_llm("gpt-4o", "test prompt")
            mock_openai.assert_called_once_with("gpt-4o", "test prompt")

    def test_routes_claude_to_anthropic(self, sample_pr_data):
        with patch("devlens.analyzer._anthropic") as mock_anthropic:
            mock_anthropic.return_value = SAMPLE_LLM_RESPONSE
            _call_llm("claude-3-5-sonnet-20241022", "test prompt")
            mock_anthropic.assert_called_once()

    def test_routes_gemini_to_gemini(self):
        with patch("devlens.analyzer._gemini") as mock_gemini:
            mock_gemini.return_value = SAMPLE_LLM_RESPONSE
            _call_llm("gemini-1.5-pro", "test prompt")
            mock_gemini.assert_called_once()

    def test_raises_on_unknown_model(self):
        with pytest.raises(ValueError, match="Unsupported model"):
            _call_llm("llama-3", "test prompt")


class TestAnalyzePr:
    def test_returns_review_result(self, sample_pr_data):
        with patch("devlens.analyzer._call_llm", return_value=SAMPLE_LLM_RESPONSE):
            result = analyze_pr(sample_pr_data, detail="medium", config={"model": "gpt-4o"})
        assert isinstance(result, ReviewResult)
        assert result.pr_number == 42
        assert "JWT" in result.summary
        assert len(result.risk_items) == 1
        assert len(result.safe_items) == 1

    def test_handles_malformed_json_gracefully(self, sample_pr_data):
        with patch("devlens.analyzer._call_llm", return_value="not json at all"):
            result = analyze_pr(sample_pr_data)
        assert isinstance(result, ReviewResult)
        assert result.summary == ""

    def test_handles_json_embedded_in_text(self, sample_pr_data):
        wrapped = f"Here is the result:\n{SAMPLE_LLM_RESPONSE}\nDone."
        with patch("devlens.analyzer._call_llm", return_value=wrapped):
            result = analyze_pr(sample_pr_data)
        assert result.summary != ""
