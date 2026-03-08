"""Tests for devlens.dashboard module."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from devlens.dashboard import (
    MetricCard,
    SectionData,
    DashboardData,
    collect_project_metrics,
    generate_dashboard_html,
    export_dashboard,
)


# ---------------------------------------------------------------------------
# MetricCard tests
# ---------------------------------------------------------------------------

class TestMetricCard:
    def test_fields(self):
        card = MetricCard(label="Functions", value=42)
        assert card.label == "Functions"
        assert card.value == 42
        assert card.icon == ""
        assert card.trend == ""
        assert card.color == ""

    def test_with_all_fields(self):
        card = MetricCard(
            label="Security Issues",
            value=3,
            icon="shield",
            trend="down",
            color="red",
        )
        assert card.icon == "shield"
        assert card.trend == "down"
        assert card.color == "red"

    def test_string_value(self):
        card = MetricCard(label="Grade", value="A")
        assert card.value == "A"


# ---------------------------------------------------------------------------
# SectionData tests
# ---------------------------------------------------------------------------

class TestSectionData:
    def test_fields(self):
        section = SectionData(id="complexity", title="Complexity Analysis")
        assert section.id == "complexity"
        assert section.title == "Complexity Analysis"
        assert section.rows == []
        assert section.chart_data == {}

    def test_with_rows(self):
        rows = [
            {"function": "process_data", "complexity": "12", "risk": "high"},
            {"function": "validate", "complexity": "6", "risk": "medium"},
        ]
        section = SectionData(
            id="cx", title="Complexity",
            rows=rows, summary="2 functions analyzed",
        )
        assert len(section.rows) == 2
        assert section.summary == "2 functions analyzed"


# ---------------------------------------------------------------------------
# DashboardData tests
# ---------------------------------------------------------------------------

class TestDashboardData:
    def test_defaults(self):
        data = DashboardData()
        assert data.project_name == ""
        assert data.cards == []
        assert data.sections == []

    def test_with_cards_and_sections(self):
        cards = [MetricCard(label="Score", value=85)]
        sections = [SectionData(id="s1", title="Section 1")]
        data = DashboardData(
            project_name="devlens",
            cards=cards,
            sections=sections,
        )
        assert data.project_name == "devlens"
        assert len(data.cards) == 1
        assert len(data.sections) == 1


# ---------------------------------------------------------------------------
# collect_project_metrics tests (mocked)
# ---------------------------------------------------------------------------

class TestCollectProjectMetrics:
    @patch("devlens.dashboard._collect_complexity")
    @patch("devlens.dashboard._collect_security")
    def test_returns_dashboard_data(self, mock_sec, mock_cx, tmp_python_project):
        mock_cx.return_value = SectionData(
            id="complexity", title="Complexity",
            summary="Analyzed", rows=[],
        )
        mock_sec.return_value = SectionData(
            id="security", title="Security",
            summary="Scanned", rows=[],
        )
        data = collect_project_metrics(str(tmp_python_project))
        assert isinstance(data, DashboardData)

    def test_with_skip(self, tmp_python_project):
        data = collect_project_metrics(
            str(tmp_python_project),
            skip={"security", "dependencies", "rules"},
        )
        assert isinstance(data, DashboardData)
        section_ids = [s.id for s in data.sections]
        assert "security" not in section_ids


# ---------------------------------------------------------------------------
# generate_dashboard_html tests
# ---------------------------------------------------------------------------

class TestGenerateDashboardHtml:
    def test_basic_html(self):
        data = DashboardData(
            project_name="test-project",
            cards=[MetricCard(label="Grade", value="A")],
            sections=[],
        )
        html = generate_dashboard_html(data)
        assert "<html" in html.lower() or "<!doctype" in html.lower()
        assert "test-project" in html
        assert "Grade" in html

    def test_contains_cards(self):
        data = DashboardData(
            project_name="proj",
            cards=[
                MetricCard(label="Functions", value=10),
                MetricCard(label="Issues", value=2),
            ],
        )
        html = generate_dashboard_html(data)
        assert "Functions" in html
        assert "Issues" in html

    def test_contains_sections(self):
        data = DashboardData(
            project_name="proj",
            sections=[
                SectionData(
                    id="cx", title="Complexity",
                    rows=[{"name": "func1", "score": "5"}],
                ),
            ],
        )
        html = generate_dashboard_html(data)
        assert "Complexity" in html

    def test_empty_dashboard(self):
        data = DashboardData(project_name="empty")
        html = generate_dashboard_html(data)
        assert isinstance(html, str)
        assert len(html) > 0


# ---------------------------------------------------------------------------
# export_dashboard tests
# ---------------------------------------------------------------------------

class TestExportDashboard:
    def test_creates_file(self, tmp_python_project):
        output = str(tmp_python_project / "dashboard.html")
        result = export_dashboard(
            str(tmp_python_project),
            output=output,
            skip={"dependencies"},
        )
        assert Path(result).exists()
        content = Path(result).read_text()
        assert "<html" in content.lower() or "<!doctype" in content.lower()

    def test_default_output_name(self, tmp_python_project):
        result = export_dashboard(str(tmp_python_project), skip={"dependencies"})
        assert Path(result).exists()
        assert "devlens-dashboard" in Path(result).name
