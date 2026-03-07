"""Tests for devlens.github module."""
import pytest
from unittest.mock import patch, MagicMock
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
    def test_raises_without_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        with pytest.raises(EnvironmentError, match="GITHUB_TOKEN"):
            fetch_pr("owner/repo", 1)

    def test_parses_api_response(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

        fake_pr = {
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
        fake_files = [
            {
                "filename": "app.py",
                "status": "modified",
                "additions": 30,
                "deletions": 10,
                "patch": "-old\n+new",
            }
        ]

        with patch("devlens.github.httpx.get") as mock_get:
            def side_effect(url, **kwargs):
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                if "files" in url:
                    resp.json.return_value = fake_files
                else:
                    resp.json.return_value = fake_pr
                return resp

            mock_get.side_effect = side_effect
            pr = fetch_pr("owner/repo", 5)

        assert pr.number == 5
        assert pr.title == "Fix bug"
        assert pr.author == "bob"
        assert pr.labels == ["bugfix"]
        assert len(pr.files) == 1
        assert pr.files[0]["filename"] == "app.py"