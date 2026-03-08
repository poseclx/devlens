# tests/test_reporter.py
"""Tests for devlens.reporter — Markdown and HTML report generation."""
import pytest
from pathlib import Path
from dataclasses import dataclass, field
from unittest.mock import MagicMock

from devlens.reporter import (
    ReportData,
    generate_markdown,
    generate_html,
    export_report,
    _severity_icon,
    _score_color,
    _grade_letter,
    _sev_html,
)


# ── Fake domain objects ──────────────────────────────────────

@dataclass
class FakeReviewResult:
    grade: str = "B"
    score: int = 78
    risk_level: str = "medium"
    verdict: str = "Needs minor fixes"
    summary: str = "Overall solid code with some improvements needed."
    findings: list = field(default_factory=lambda: [
        {"severity": "high", "category": "security", "title": "SQL injection risk"},
        {"severity": "medium", "category": "style", "title": "Unused import"},
    ])
    risk_items: list = field(default_factory=list)
    safe_items: list = field(default_factory=list)


@dataclass
class FakeSeverity:
    value: str = "high"


@dataclass
class FakeSecurityFinding:
    rule_id: str = "SEC-001"
    title: str = "Hardcoded API key"
    severity: object = field(default_factory=lambda: FakeSeverity("critical"))
    file: str = "settings.py"
    line: int = 10
    match: str = "API_KEY='secret'"
    description: str = "Secret in source"
    suggestion: str = "Use env vars"


@dataclass
class FakeScanResult:
    grade: str = "C"
    score: int = 62
    files_scanned: int = 10
    total_files: int = 15
    findings: list = field(default_factory=list)
    ai_summary: str = ""
    critical_count: int = 1
    high_count: int = 2
    medium_count: int = 3
    low_count: int = 1


@dataclass
class FakePRSummary:
    categories: list = field(default_factory=lambda: ["refactor", "bugfix"])
    impact: str = "medium"
    overview: str = "Refactored auth module and fixed session bug."
    key_changes: list = field(default_factory=lambda: [
        "Rewrote session handler",
        "Fixed cookie expiry logic",
    ])

    def to_markdown(self):
        return f"**{self.overview}**\n- " + "\n- ".join(self.key_changes)


# ── ReportData ───────────────────────────────────────────────

class TestReportData:
    """ReportData dataclass initialization and defaults."""

    def test_auto_timestamp(self):
        d = ReportData(pr_number=1, pr_title="Test", repo="o/r")
        assert d.timestamp  # not empty
        assert "UTC" in d.timestamp

    def test_custom_timestamp(self):
        d = ReportData(pr_number=1, pr_title="T", repo="o/r", timestamp="2025-01-01 00:00 UTC")
        assert d.timestamp == "2025-01-01 00:00 UTC"

    def test_optional_fields_none(self):
        d = ReportData(pr_number=1, pr_title="T", repo="o/r")
        assert d.review is None
        assert d.scan_result is None
        assert d.summary is None


# ── _severity_icon ───────────────────────────────────────────

class TestSeverityIcon:
    """_severity_icon returns correct emoji for each severity level."""

    @pytest.mark.parametrize("severity,expected", [
        ("critical", "\U0001f534"),
        ("high", "\U0001f7e0"),
        ("medium", "\U0001f7e1"),
        ("low", "\U0001f535"),
        ("info", "\u26aa"),
        ("CRITICAL", "\U0001f534"),
        ("unknown", "\u26aa"),
    ])
    def test_severity_icons(self, severity, expected):
        assert _severity_icon(severity) == expected


# ── _score_color / _grade_letter / _sev_html ─────────────────

class TestHelpers:
    """Helper functions for HTML generation."""

    def test_score_color_green(self):
        assert "green" in _score_color(95)

    def test_score_color_yellow(self):
        assert "yellow" in _score_color(65)

    def test_score_color_red(self):
        assert "red" in _score_color(30)

    def test_grade_letter_extracts_first_char(self):
        assert _grade_letter("A+") == "A"
        assert _grade_letter("B") == "B"

    def test_grade_letter_empty(self):
        assert _grade_letter("") == "?"

    def test_sev_html_generates_span(self):
        result = _sev_html("critical")
        assert 'class="sev sev-critical"' in result
        assert "critical" in result


# ── generate_markdown ────────────────────────────────────────

class TestGenerateMarkdown:
    """generate_markdown produces correct Markdown report."""

    def test_header_contains_pr_info(self):
        d = ReportData(pr_number=42, pr_title="Add feature", repo="org/repo")
        md = generate_markdown(d)
        assert "PR #42" in md
        assert "Add feature" in md
        assert "org/repo" in md

    def test_review_section(self):
        d = ReportData(
            pr_number=1, pr_title="T", repo="o/r",
            review=FakeReviewResult(),
        )
        md = generate_markdown(d)
        assert "## Code Review" in md
        assert "Grade: B" in md
        assert "78/100" in md
        assert "SQL injection risk" in md

    def test_security_section(self):
        finding = FakeSecurityFinding()
        scan = FakeScanResult(findings=[finding])
        d = ReportData(pr_number=1, pr_title="T", repo="o/r", scan_result=scan)
        md = generate_markdown(d)
        assert "## Security Scan" in md
        assert "SEC-001" in md
        assert "settings.py" in md

    def test_summary_section(self):
        d = ReportData(
            pr_number=1, pr_title="T", repo="o/r",
            summary=FakePRSummary(),
        )
        md = generate_markdown(d)
        assert "## PR Summary" in md
        assert "Refactored auth module" in md

    def test_footer(self):
        d = ReportData(pr_number=1, pr_title="T", repo="o/r")
        md = generate_markdown(d)
        assert "DevLens" in md

    def test_no_sections_minimal(self):
        d = ReportData(pr_number=1, pr_title="T", repo="o/r")
        md = generate_markdown(d)
        assert "PR #1" in md
        assert "## Code Review" not in md
        assert "## Security Scan" not in md

    def test_review_ai_summary(self):
        review = FakeReviewResult(summary="AI says code is great.")
        d = ReportData(pr_number=1, pr_title="T", repo="o/r", review=review)
        md = generate_markdown(d)
        assert "AI says code is great." in md


# ── generate_html ────────────────────────────────────────────

class TestGenerateHTML:
    """generate_html produces valid HTML dashboard."""

    def test_html_structure(self):
        d = ReportData(pr_number=5, pr_title="Fix bug", repo="o/r")
        html_out = generate_html(d)
        assert "<!DOCTYPE html>" in html_out
        assert "PR #5" in html_out
        assert "Fix bug" in html_out

    def test_review_section_html(self):
        d = ReportData(
            pr_number=1, pr_title="T", repo="o/r",
            review=FakeReviewResult(),
        )
        html_out = generate_html(d)
        assert "Code Review" in html_out
        assert "grade-badge" in html_out
        assert "score-bar" in html_out

    def test_security_section_html(self):
        finding = FakeSecurityFinding()
        scan = FakeScanResult(findings=[finding])
        d = ReportData(pr_number=1, pr_title="T", repo="o/r", scan_result=scan)
        html_out = generate_html(d)
        assert "Security Scan" in html_out
        assert "stat-card" in html_out
        assert "SEC-001" in html_out

    def test_summary_section_html(self):
        d = ReportData(
            pr_number=1, pr_title="T", repo="o/r",
            summary=FakePRSummary(),
        )
        html_out = generate_html(d)
        assert "PR Summary" in html_out
        assert "refactor" in html_out

    def test_html_escaping(self):
        d = ReportData(pr_number=1, pr_title="Fix <script>alert(1)</script>", repo="o/r")
        html_out = generate_html(d)
        assert "<script>" not in html_out
        assert "&lt;script&gt;" in html_out


# ── export_report ────────────────────────────────────────────

class TestExportReport:
    """export_report writes files to disk in correct format."""

    def test_export_markdown(self, tmp_path):
        d = ReportData(pr_number=1, pr_title="T", repo="o/r")
        out = tmp_path / "report.md"
        result = export_report(d, str(out))
        assert Path(result).exists()
        content = Path(result).read_text()
        assert "PR #1" in content

    def test_export_html(self, tmp_path):
        d = ReportData(pr_number=1, pr_title="T", repo="o/r")
        out = tmp_path / "report.html"
        result = export_report(d, str(out))
        content = Path(result).read_text()
        assert "<!DOCTYPE html>" in content

    def test_auto_detect_md(self, tmp_path):
        d = ReportData(pr_number=1, pr_title="T", repo="o/r")
        out = tmp_path / "out.md"
        export_report(d, str(out), fmt="auto")
        content = out.read_text()
        assert "<!DOCTYPE html>" not in content

    def test_auto_detect_html(self, tmp_path):
        d = ReportData(pr_number=1, pr_title="T", repo="o/r")
        out = tmp_path / "out.html"
        export_report(d, str(out), fmt="auto")
        content = out.read_text()
        assert "<!DOCTYPE html>" in content

    def test_creates_parent_dirs(self, tmp_path):
        d = ReportData(pr_number=1, pr_title="T", repo="o/r")
        out = tmp_path / "deep" / "nested" / "report.md"
        result = export_report(d, str(out))
        assert Path(result).exists()

    def test_explicit_fmt_overrides_extension(self, tmp_path):
        d = ReportData(pr_number=1, pr_title="T", repo="o/r")
        out = tmp_path / "report.txt"
        export_report(d, str(out), fmt="html")
        content = out.read_text()
        assert "<!DOCTYPE html>" in content
