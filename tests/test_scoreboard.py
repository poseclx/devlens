"""Tests for devlens.scoreboard module."""
import pytest
import json
from pathlib import Path

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
    def test_default_fields(self):
        entry = ScoreEntry()
        assert entry.author == "unknown"
        assert entry.project == ""
        assert entry.pr_number is None
        assert entry.metrics == {}

    def test_with_fields(self):
        entry = ScoreEntry(
            author="alice",
            project="devlens",
            pr_number=42,
            metrics={"complexity_avg": 5.2, "security_issues": 1},
        )
        assert entry.author == "alice"
        assert entry.pr_number == 42
        assert entry.metrics["complexity_avg"] == 5.2

    def test_finalize_timestamp(self):
        entry = ScoreEntry()
        assert entry.timestamp == ""
        entry.finalize_timestamp()
        assert entry.timestamp != ""
        assert len(entry.timestamp) > 0


# ---------------------------------------------------------------------------
# ScoreHistory tests
# ---------------------------------------------------------------------------

class TestScoreHistory:
    def test_empty_history(self):
        history = ScoreHistory()
        assert history.entries == []
        assert history.latest() is None

    def test_latest(self):
        entries = [
            ScoreEntry(author="a", timestamp="2024-01-01"),
            ScoreEntry(author="b", timestamp="2024-01-02"),
        ]
        history = ScoreHistory(entries=entries)
        assert history.latest().author == "b"


# ---------------------------------------------------------------------------
# LeaderboardRow tests
# ---------------------------------------------------------------------------

class TestLeaderboardRow:
    def test_fields(self):
        row = LeaderboardRow(author="alice", total_reviews=10, score=85.5)
        assert row.author == "alice"
        assert row.total_reviews == 10
        assert row.score == 85.5

    def test_defaults(self):
        row = LeaderboardRow(author="bob")
        assert row.total_reviews == 0
        assert row.avg_complexity == 0.0
        assert row.score == 0.0


# ---------------------------------------------------------------------------
# record_score tests
# ---------------------------------------------------------------------------

class TestRecordScore:
    def test_creates_score_entry(self, tmp_path):
        entry = record_score(
            str(tmp_path),
            author="tester",
            metrics={"complexity_avg": 3.5},
        )
        assert isinstance(entry, ScoreEntry)
        assert entry.author == "tester"
        assert entry.metrics["complexity_avg"] == 3.5

    def test_score_persisted(self, tmp_path):
        record_score(str(tmp_path), author="dev1", metrics={"score": 90})
        history = load_history(str(tmp_path))
        assert len(history.entries) >= 1


# ---------------------------------------------------------------------------
# load_history / reset_history tests
# ---------------------------------------------------------------------------

class TestLoadHistory:
    def test_load_empty(self, tmp_path):
        history = load_history(str(tmp_path))
        assert isinstance(history, ScoreHistory)
        assert len(history.entries) == 0

    def test_load_after_records(self, tmp_path):
        record_score(str(tmp_path), author="a", metrics={"x": 1})
        record_score(str(tmp_path), author="b", metrics={"x": 2})
        history = load_history(str(tmp_path))
        assert len(history.entries) == 2

    def test_reset_history(self, tmp_path):
        record_score(str(tmp_path), author="a", metrics={"x": 1})
        result = reset_history(str(tmp_path))
        assert result is True
        history = load_history(str(tmp_path))
        assert len(history.entries) == 0

    def test_reset_empty(self, tmp_path):
        result = reset_history(str(tmp_path))
        # Should not raise on empty
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# build_leaderboard tests
# ---------------------------------------------------------------------------

class TestBuildLeaderboard:
    def test_empty_history(self):
        history = ScoreHistory()
        board = build_leaderboard(history)
        assert board == []

    def test_single_author(self, tmp_path):
        record_score(str(tmp_path), author="alice", metrics={"complexity_avg": 5.0})
        record_score(str(tmp_path), author="alice", metrics={"complexity_avg": 3.0})
        history = load_history(str(tmp_path))
        board = build_leaderboard(history)
        assert len(board) == 1
        assert board[0].author == "alice"
        assert board[0].total_reviews == 2

    def test_multiple_authors_sorted(self, tmp_path):
        record_score(str(tmp_path), author="alice", metrics={"complexity_avg": 2.0})
        record_score(str(tmp_path), author="bob", metrics={"complexity_avg": 8.0})
        record_score(str(tmp_path), author="alice", metrics={"complexity_avg": 3.0})
        history = load_history(str(tmp_path))
        board = build_leaderboard(history)
        assert len(board) == 2
        # Should be sorted by score
        assert isinstance(board[0], LeaderboardRow)


# ---------------------------------------------------------------------------
# calculate_trends tests
# ---------------------------------------------------------------------------

class TestCalculateTrends:
    def test_empty_history(self):
        history = ScoreHistory()
        trends = calculate_trends(history)
        assert trends == []

    def test_with_data(self, tmp_path):
        record_score(str(tmp_path), author="dev", metrics={"complexity_avg": 5.0})
        record_score(str(tmp_path), author="dev", metrics={"complexity_avg": 4.0})
        record_score(str(tmp_path), author="dev", metrics={"complexity_avg": 3.0})
        history = load_history(str(tmp_path))
        trends = calculate_trends(history, metric="complexity_avg")
        assert len(trends) >= 1
        for t in trends:
            assert isinstance(t, TrendPoint)


# ---------------------------------------------------------------------------
# HTML export tests
# ---------------------------------------------------------------------------

class TestScoreboardExport:
    def test_generate_html(self, tmp_path):
        record_score(str(tmp_path), author="alice", metrics={"complexity_avg": 3.0})
        history = load_history(str(tmp_path))
        html = generate_scoreboard_html(history)
        assert "<html" in html.lower() or "<div" in html.lower()
        assert "alice" in html

    def test_export_creates_file(self, tmp_path):
        record_score(str(tmp_path), author="bob", metrics={"score": 85})
        output_path = export_scoreboard(str(tmp_path), output=str(tmp_path / "board.html"))
        assert Path(output_path).exists()
        content = Path(output_path).read_text()
        assert "bob" in content
