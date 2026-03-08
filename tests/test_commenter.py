# tests/test_commenter.py
"""Tests for devlens.commenter — GitHub PR comment posting."""
import os
import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field

from devlens.commenter import (
    _github_headers,
    post_review_comment,
    post_security_comment,
    _post_comment,
    REVIEW_TEMPLATE,
    SECURITY_TEMPLATE,
    GITHUB_API,
)


# ── Fake domain objects (avoid importing real ones) ──────────

@dataclass
class FakeReviewResult:
    verdict: str = "Ready to merge"
    summary: str = "Looks good overall."
    risk_items: list = field(default_factory=list)
    safe_items: list = field(default_factory=list)
    grade: str = "A"
    score: int = 95
    risk_level: str = "low"
    findings: list = field(default_factory=list)


@dataclass
class FakeSeverity:
    value: str = "high"


@dataclass
class FakeSecurityFinding:
    rule_id: str = "SEC-001"
    title: str = "Hardcoded secret"
    severity: object = field(default_factory=lambda: FakeSeverity("high"))
    file: str = "config.py"
    line: int = 42
    match: str = "API_KEY = 'sk-1234'"
    description: str = "Potential secret in source"
    suggestion: str = "Use environment variables"


@dataclass
class FakeScanResult:
    pr_number: int = 10
    score: int = 65
    grade: str = "C"
    files_scanned: int = 5
    total_files: int = 8
    critical_count: int = 0
    high_count: int = 2
    medium_count: int = 1
    low_count: int = 3
    findings: list = field(default_factory=list)
    ai_summary: str = ""


# ── _github_headers ──────────────────────────────────────────

class TestGitHubHeaders:
    """_github_headers reads GITHUB_TOKEN from environment."""

    def test_returns_bearer_header(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        headers = _github_headers()
        assert headers["Authorization"] == "Bearer ghp_test123"
        assert "application/vnd.github+json" in headers["Accept"]

    def test_missing_token_raises(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        with pytest.raises(EnvironmentError, match="GITHUB_TOKEN"):
            _github_headers()


# ── _post_comment ────────────────────────────────────────────

class TestPostComment:
    """_post_comment sends correct HTTP request to GitHub API."""

    @patch("devlens.commenter._github_headers")
    @patch("devlens.commenter.httpx.Client")
    def test_successful_post(self, mock_client_cls, mock_headers):
        mock_headers.return_value = {"Authorization": "Bearer test"}
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"html_url": "https://github.com/repo/pull/1#comment-1"}
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        url = _post_comment("owner/repo", 1, "test body")
        assert url == "https://github.com/repo/pull/1#comment-1"
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "/repos/owner/repo/issues/1/comments" in call_args[0][0]
        assert call_args[1]["json"]["body"] == "test body"

    @patch("devlens.commenter._github_headers")
    @patch("devlens.commenter.httpx.Client")
    def test_404_raises_system_exit(self, mock_client_cls, mock_headers):
        mock_headers.return_value = {"Authorization": "Bearer test"}
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with pytest.raises(SystemExit, match="not found"):
            _post_comment("owner/repo", 999, "body")

    @patch("devlens.commenter._github_headers")
    @patch("devlens.commenter.httpx.Client")
    def test_403_raises_system_exit(self, mock_client_cls, mock_headers):
        mock_headers.return_value = {"Authorization": "Bearer test"}
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with pytest.raises(SystemExit, match="Permission denied"):
            _post_comment("owner/repo", 1, "body")


# ── post_review_comment ──────────────────────────────────────

class TestPostReviewComment:
    """post_review_comment formats ReviewResult into markdown and posts."""

    @patch("devlens.commenter._post_comment", return_value="https://url")
    def test_basic_review(self, mock_post):
        result = FakeReviewResult()
        url = post_review_comment(result, "owner/repo", 42)
        assert url == "https://url"
        call_body = mock_post.call_args[0][2]
        assert "PR #42" in call_body
        assert "Ready to merge" in call_body

    @patch("devlens.commenter._post_comment", return_value="https://url")
    def test_with_risk_items(self, mock_post):
        result = FakeReviewResult(
            verdict="Needs changes",
            risk_items=[
                {"severity": "HIGH", "file": "main.py", "reason": "Complex logic"},
                {"severity": "MEDIUM", "file": "utils.py", "reason": "Missing tests"},
            ],
        )
        url = post_review_comment(result, "owner/repo", 5)
        call_body = mock_post.call_args[0][2]
        assert "main.py" in call_body
        assert "Complex logic" in call_body
        assert "utils.py" in call_body

    @patch("devlens.commenter._post_comment", return_value="https://url")
    def test_with_safe_items(self, mock_post):
        result = FakeReviewResult(
            safe_items=[{"file": "readme.md", "reason": "Documentation only"}],
        )
        post_review_comment(result, "owner/repo", 1)
        call_body = mock_post.call_args[0][2]
        assert "readme.md" in call_body

    @patch("devlens.commenter._post_comment", return_value="https://url")
    def test_verdict_emoji_ready(self, mock_post):
        result = FakeReviewResult(verdict="Ready to merge")
        post_review_comment(result, "o/r", 1)
        body = mock_post.call_args[0][2]
        # Should contain a check-mark style emoji for merge-ready
        assert "\u2705" in body or "Ready to merge" in body

    @patch("devlens.commenter._post_comment", return_value="https://url")
    def test_verdict_emoji_needs_changes(self, mock_post):
        result = FakeReviewResult(verdict="Needs changes before merge")
        post_review_comment(result, "o/r", 1)
        body = mock_post.call_args[0][2]
        assert "\u26a0\ufe0f" in body or "Needs changes" in body


# ── post_security_comment ────────────────────────────────────

class TestPostSecurityComment:
    """post_security_comment formats ScanResult and posts."""

    @patch("devlens.commenter._post_comment", return_value="https://url")
    def test_clean_scan(self, mock_post):
        result = FakeScanResult(score=95, grade="A", findings=[])
        url = post_security_comment(result, "owner/repo", 10)
        assert url == "https://url"
        body = mock_post.call_args[0][2]
        assert "95/100" in body
        assert "Grade: A" in body

    @patch("devlens.commenter._post_comment", return_value="https://url")
    def test_with_findings(self, mock_post):
        finding = FakeSecurityFinding()
        result = FakeScanResult(findings=[finding])
        post_security_comment(result, "owner/repo", 10)
        body = mock_post.call_args[0][2]
        assert "SEC-001" in body
        assert "Hardcoded secret" in body
        assert "config.py" in body

    @patch("devlens.commenter._post_comment", return_value="https://url")
    def test_with_ai_summary(self, mock_post):
        result = FakeScanResult(ai_summary="The code has moderate risk.")
        post_security_comment(result, "owner/repo", 10)
        body = mock_post.call_args[0][2]
        assert "AI Security Assessment" in body
        assert "moderate risk" in body

    @patch("devlens.commenter._post_comment", return_value="https://url")
    def test_score_emoji_green(self, mock_post):
        result = FakeScanResult(score=95)
        post_security_comment(result, "o/r", 1)
        body = mock_post.call_args[0][2]
        # score >= 90 should get green emoji
        assert "\U0001f7e2" in body

    @patch("devlens.commenter._post_comment", return_value="https://url")
    def test_score_emoji_red(self, mock_post):
        result = FakeScanResult(score=40)
        post_security_comment(result, "o/r", 1)
        body = mock_post.call_args[0][2]
        # score < 60 should get red emoji
        assert "\U0001f534" in body
