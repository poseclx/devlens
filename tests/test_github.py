"""Tests for devlens.github module."""
import pytest
from unittest.mock import patch, Mock, MagicMock
import os

from devlens.github import PRData, fetch_pr


class TestPRData:
    def test_dataclass_fields(self):
        pr = PRData(
            number=1,
            title="Test",
            body="Body",
            author="alice",
            base_branch="main",
            head_branch="feature",
            additions=10,
            deletions=5,
            changed_files=2,
            labels=[],
            files=[],
        )
        assert pr.number == 1
        assert pr.author == "alice"
        assert pr.additions == 10


class TestFetchPr:
    @patch("devlens.github.httpx.Client")
    def test_raises_on_404(self, mock_client_cls, monkeypatch):
        """fetch_pr raises SystemExit when the API returns 404."""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        # Build a mock client that returns a 404 response
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = Mock(return_value=False)

        pr_resp = Mock()
        pr_resp.status_code = 404
        mock_client.get.return_value = pr_resp

        with pytest.raises(SystemExit):
            fetch_pr("owner/repo", 1)

    @patch("devlens.github.httpx.Client")
    def test_parses_api_response(self, mock_client_cls, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

        # Build mock client instance for context manager
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = Mock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = Mock(return_value=False)

        # PR response
        pr_resp = Mock()
        pr_resp.status_code = 200
        pr_resp.raise_for_status = Mock()
        pr_resp.json.return_value = {
            "number": 5,
            "title": "Fix bug",
            "body": "Fixes #4",
            "user": {"login": "bob"},
            "base": {"ref": "main"},
            "head": {"ref": "fix/bug"},
            "additions": 30,
            "deletions": 10,
            "changed_files": 3,
            "labels": [{"name": "bugfix"}],
        }

        # Files response
        files_resp = Mock()
        files_resp.status_code = 200
        files_resp.raise_for_status = Mock()
        files_resp.json.return_value = [
            {
                "filename": "app.py",
                "status": "modified",
                "additions": 30,
                "deletions": 10,
                "patch": "-old\n+new",
            }
        ]

        # First get() call -> PR data, second -> files
        mock_client.get.side_effect = [pr_resp, files_resp]

        pr = fetch_pr("owner/repo", 5)

        assert pr.number == 5
        assert pr.title == "Fix bug"
        assert pr.author == "bob"
        assert pr.labels == ["bugfix"]
        assert len(pr.files) == 1
        assert pr.files[0]["filename"] == "app.py"
