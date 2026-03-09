"""Tests for devlens.scoreboard module."""
import pytest
import json
from pathlib import Path
from unittest.mock import patch

from devlens.scoreboard import (
    ScoreEntry,
    ScoreHistory,
    LeaderboardRow,
    TrendPoint,
    record_score,
    load_history,
    reset_history,
    build_leaderboard,
    calculate_trends,
    generate_scoreboard_html,
    export_scoreboard,
)


# ---------------------------------------------------------------------------
# ScoreEntry tests
# ---------------------------------------------------------------------------

class TestScoreEntry:
    def test_defaults(self):
        entry = ScoreEntry()
        assert entry.author == "unknown"
        assert entry.project == ""
        assert entry.pr_number is None
        assert entry.metrics == {}
        # timestamp is auto-populated in __post_init__
        assert entry.timestamp != ""
        assert isinstance(entry.timestamp, str)
        assert len(entry.timestamp) > 0

    def test_custom_fields(self):
        entry = ScoreEntry(
            author="alice",
            project="devlens",
            pr_number=42,
            metrics={"reviews": 5, "security_issues": 0},
        )
        assert entry.author == "alice"
        assert entry.pr_number == 42
        assert entry.metrics["reviews"] == 5

    def test_auto_timestamp(self):
        entry = ScoreEntry(author="bob")
        # timestamp auto-populated, should be ISO format
        assert "T" in entry.timestamp  # ISO format has T separator

    def test_explicit_timestamp(self):
        entry = ScoreEntry(timestamp="2024-01-15T10:00:00+00:00")
        assert entry.timestamp == "2024-01-15T10:00:00+00:00"


# ---------------------------------------------------------------------------
# ScoreHistory tests
# ---------------------------------------------------------------------------

class TestScoreHistory:
    def test_empty_history(self):
        history = ScoreHistory()
        assert history.entries == []
        assert history.latest is None
        assert history.authors == []

    def test_latest_property(self):
        entries = [
            ScoreEntry(author="alice", timestamp="2024-01-01T00:00:00Z"),
            ScoreEntry(author="bob", timestamp="2024-01-02T00:00:00Z"),
        ]
        history = ScoreHistory(entries=entries)
        # latest is a property, not a method
        assert history.latest is not None
        assert history.latest.author == "bob"

    def test_authors(self):
        entries = [
            ScoreEntry(author="alice"),
            ScoreEntry(author="bob"),
            ScoreEntry(author="alice"),
        ]
        history = ScoreHistory(entries=entries)
        assert sorted(history.authors) == ["alice", "bob"]


# ---------------------------------------------------------------------------
# record_score and load_history tests
# ---------------------------------------------------------------------------

class TestRecordAndLoad:
    def test_record_creates_file(self, tmp_path):
        record_score(
            str(tmp_path), author="alice",
            metrics={"reviews": 1, "security_issues": 0},
        )
        history = load_history(str(tmp_path))
        assert len(history.entries) >= 1
        assert history.entries[0].author == "alice"

    def test_record_multiple(self, tmp_path):
        record_score(str(tmp_path), author="alice", metrics={"reviews": 1})
        record_score(str(tmp_path), author="bob", metrics={"reviews": 2})
        history = load_history(str(tmp_path))
        assert len(history.entries) >= 2

    def test_load_empty(self, tmp_path):
        history = load_history(str(tmp_path))
        assert len(history.entries) == 0

    def test_record_with_pr(self, tmp_path):
        record_score(
            str(tmp_path), author="alice", pr_number=42,
            metrics={"reviews": 1},
        )
        history = load_history(str(tmp_path))
        assert history.entries[0].pr_number == 42


# ---------------------------------------------------------------------------
# reset_history tests
# ---------------------------------------------------------------------------

class TestResetHistory:
    def test_reset_existing(self, tmp_path):
        record_score(str(tmp_path), author="alice", metrics={"reviews": 1})
        result = reset_history(str(tmp_path))
        assert result is True
        history = load_history(str(tmp_path))
        assert len(history.entries) == 0

    def test_reset_nonexistent(self, tmp_path):
        result = reset_history(str(tmp_path))
        assert result is False


# ---------------------------------------------------------------------------
# build_leaderboard tests
# ---------------------------------------------------------------------------

class TestBuildLeaderboard:
    def test_empty_history(self):
        history = ScoreHistory()
        leaderboard = build_leaderboard(history)
        assert leaderboard == []

    def test_single_author(self, tmp_path):
        record_score(str(tmp_path), author="alice", metrics={
            "reviews": 3, "security_issues": 1,
            "complexity_avg": 8.0, "rule_violations": 2,
            "fix_count": 5,
        })
        history = load_history(str(tmp_path))
        leaderboard = build_leaderboard(history)
        assert len(leaderboard) == 1
        assert leaderboard[0].author == "alice"

    def test_multiple_authors(self, tmp_path):
        record_score(str(tmp_path), author="alice", metrics={"reviews": 3})
        record_score(str(tmp_path), author="bob", metrics={"reviews": 5})
        history = load_history(str(tmp_path))
        leaderboard = build_leaderboard(history)
        assert len(leaderboard) == 2
        authors = [row.author for row in leaderboard]
        assert "alice" in authors
        assert "bob" in authors


# ---------------------------------------------------------------------------
# calculate_trends tests
# ---------------------------------------------------------------------------

class TestCalculateTrends:
    def test_empty_history(self):
        history = ScoreHistory()
        trends = calculate_trends(history, "reviews")
        assert trends == []

    def test_with_data(self, tmp_path):
        record_score(str(tmp_path), author="alice", metrics={"reviews": 1})
        record_score(str(tmp_path), author="alice", metrics={"reviews": 3})
        history = load_history(str(tmp_path))
        trends = calculate_trends(history, "reviews")
        assert isinstance(trends, list)


# ---------------------------------------------------------------------------
# generate_scoreboard_html tests
# ---------------------------------------------------------------------------

class TestGenerateScoreboardHtml:
    def test_empty_history(self):
        history = ScoreHistory(project="test-project")
        html = generate_scoreboard_html(history)
        assert isinstance(html, str)
        assert len(html) > 0

    def test_with_data(self, tmp_path):
        record_score(str(tmp_path), author="alice", metrics={"reviews": 3})
        record_score(str(tmp_path), author="bob", metrics={"reviews": 5})
        history = load_history(str(tmp_path))
        html = generate_scoreboard_html(history)
        assert "alice" in html
        assert "bob" in html

    def test_contains_html_structure(self):
        history = ScoreHistory(project="my-project")
        html = generate_scoreboard_html(history)
        assert "<html" in html.lower() or "<!doctype" in html.lower()


# ---------------------------------------------------------------------------
# export_scoreboard tests
# ---------------------------------------------------------------------------

class TestExportScoreboard:
    def test_creates_file(self, tmp_path):
        record_score(str(tmp_path), author="alice", metrics={"reviews": 1})
        output = str(tmp_path / "scoreboard.html")
        result = export_scoreboard(str(tmp_path), output=output)
        assert Path(result).exists()
        content = Path(result).read_text()
        assert len(content) > 0

    def test_default_output(self, tmp_path):
        record_score(str(tmp_path), author="alice", metrics={"reviews": 1})
        result = export_scoreboard(str(tmp_path))
        assert Path(result).exists()
