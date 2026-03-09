"""Tests for devlens.summarizer module."""
import json
import pytest
from unittest.mock import patch, MagicMock, Mock

from devlens.summarizer import summarize_pr, PRSummary


# ---------------------------------------------------------------------------
# Mock PR data matching PRData structure
# ---------------------------------------------------------------------------

def _mock_pr_data():
    """Create a mock PRData-like object."""
    pr = Mock()
    pr.number = 42
    pr.title = "Fix authentication bug"
    pr.body = "This PR fixes a critical auth bypass.\n\nChanges:\n- Fixed token validation\n- Added test coverage"
    pr.author = "alice"
    pr.base_branch = "main"
    pr.head_branch = "fix/auth-bypass"
    pr.additions = 150
    pr.deletions = 30
    pr.changed_files = 5
    pr.labels = ["bugfix", "security"]
    pr.files = [
        {"filename": "auth.py", "status": "modified", "additions": 80, "deletions": 20, "changes": 100, "patch": "+new code"},
        {"filename": "test_auth.py", "status": "modified", "additions": 70, "deletions": 10, "changes": 80, "patch": "+tests"},
    ]
    return pr


# ---------------------------------------------------------------------------
# summarize_pr tests (no AI)
# ---------------------------------------------------------------------------

class TestSummarizePr:
    def test_basic_summary(self):
        pr = _mock_pr_data()
        summary = summarize_pr(pr, use_ai=False)
        assert isinstance(summary, PRSummary)
        assert len(summary.overview) > 0

    def test_summary_contains_pr_info(self):
        pr = _mock_pr_data()
        summary = summarize_pr(pr, use_ai=False)
        assert isinstance(summary, PRSummary)
        assert isinstance(summary.key_changes, list)
        assert isinstance(summary.impact, str)
        assert isinstance(summary.categories, list)

    def test_summary_to_markdown(self):
        pr = _mock_pr_data()
        summary = summarize_pr(pr, use_ai=False)
        md = summary.to_markdown()
        assert isinstance(md, str)
        assert len(md) > 0


# ---------------------------------------------------------------------------
# summarize_pr with AI tests (mocked)
# ---------------------------------------------------------------------------

class TestSummarizePrAI:
    @patch("devlens.analyzer._call_llm")
    def test_ai_summary(self, mock_llm):
        mock_llm.return_value = json.dumps({
            "overview": "This PR fixes auth issues with improved token validation.",
            "key_changes": ["Fixed token check", "Added tests"],
            "impact": "high",
            "categories": ["bugfix", "security"],
        })
        pr = _mock_pr_data()
        summary = summarize_pr(pr, use_ai=True, model="gpt-4o")
        assert isinstance(summary, PRSummary)
        assert summary.overview == "This PR fixes auth issues with improved token validation."
        mock_llm.assert_called_once()

    @patch("devlens.analyzer._call_llm")
    def test_ai_fallback_on_error(self, mock_llm):
        mock_llm.side_effect = Exception("API error")
        pr = _mock_pr_data()
        # Should fall back to heuristic summary gracefully
        summary = summarize_pr(pr, use_ai=True)
        assert isinstance(summary, PRSummary)
        # Heuristic summary still produces valid output
        assert len(summary.overview) > 0
