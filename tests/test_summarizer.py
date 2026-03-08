"""Tests for devlens.summarizer module."""
import pytest
from unittest.mock import patch, MagicMock

from devlens.summarizer import PRSummary, summarize_pr


# ---------------------------------------------------------------------------
# PRSummary tests
# ---------------------------------------------------------------------------

class TestPRSummary:
    def test_fields(self):
        summary = PRSummary(
            overview="This PR adds authentication.",
            key_changes=["Added JWT module", "Updated requirements"],
            impact="medium",
            categories=["feature"],
        )
        assert summary.overview == "This PR adds authentication."
        assert len(summary.key_changes) == 2
        assert summary.impact == "medium"

    def test_to_markdown(self):
        summary = PRSummary(
            overview="Fixes a critical login bug.",
            key_changes=["Fixed null pointer", "Added tests"],
            impact="high",
            categories=["bugfix", "test"],
        )
        md = summary.to_markdown()
        assert "Fixes a critical login bug" in md
        assert "Fixed null pointer" in md
        assert "high" in md.lower()

    def test_to_markdown_empty(self):
        summary = PRSummary(
            overview="", key_changes=[], impact="low", categories=[],
        )
        md = summary.to_markdown()
        assert isinstance(md, str)


# ---------------------------------------------------------------------------
# summarize_pr tests
# ---------------------------------------------------------------------------

class TestSummarizePr:
    def test_heuristic_mode(self, sample_pr_data):
        summary = summarize_pr(sample_pr_data, use_ai=False)
        assert isinstance(summary, PRSummary)
        assert len(summary.overview) > 0
        assert summary.impact in ("low", "medium", "high")

    def test_heuristic_includes_key_changes(self, sample_pr_data):
        summary = summarize_pr(sample_pr_data, use_ai=False)
        assert len(summary.key_changes) >= 1

    def test_heuristic_categories(self, sample_pr_data):
        summary = summarize_pr(sample_pr_data, use_ai=False)
        assert isinstance(summary.categories, list)

    @patch("devlens.summarizer._call_llm_summary")
    def test_ai_mode(self, mock_llm, sample_pr_data):
        mock_llm.return_value = PRSummary(
            overview="AI-generated summary of JWT auth.",
            key_changes=["JWT verification", "Token expiry"],
            impact="medium",
            categories=["feature", "security"],
        )
        summary = summarize_pr(sample_pr_data, use_ai=True, model="gpt-4o")
        assert "AI-generated" in summary.overview

    @patch("devlens.summarizer._call_llm_summary")
    def test_ai_fallback_on_error(self, mock_llm, sample_pr_data):
        mock_llm.side_effect = Exception("API error")
        summary = summarize_pr(sample_pr_data, use_ai=True)
        # Should fall back to heuristic
        assert isinstance(summary, PRSummary)
        assert len(summary.overview) > 0
