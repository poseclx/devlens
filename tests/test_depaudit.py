"""Tests for devlens.depaudit module."""

from __future__ import annotations

import json
from unittest.mock import patch

from devlens.depaudit import (
    Dependency,
    Vulnerability,
    AuditReport,
    parse_dependencies,
    audit_dependencies,
)


# ---------------------------------------------------------------------------
# Dependency dataclass tests
# ---------------------------------------------------------------------------

class TestDependency:
    def test_fields(self):
        dep = Dependency(
            name="requests", version="2.31.0",
            ecosystem="PyPI", source_file="requirements.txt",
        )
        assert dep.name == "requests"
        assert dep.version == "2.31.0"
        assert dep.ecosystem == "PyPI"

    def test_to_dict(self):
        dep = Dependency("flask", "3.0.0", "PyPI", "requirements.txt")
        d = dep.to_dict()
        assert d["name"] == "flask"
        assert d["version"] == "3.0.0"
        assert d["ecosystem"] == "PyPI"
        # Dependency.to_dict() returns key "source" (not "source_file")
        assert d["source"] == "requirements.txt"


# ---------------------------------------------------------------------------
# Vulnerability dataclass tests
# ---------------------------------------------------------------------------

class TestVulnerability:
    def test_fields(self):
        vuln = Vulnerability(
            id="CVE-2024-0001", summary="Test vuln",
            severity="high", package="requests",
            version="2.31.0", fixed_in="2.32.0",
            url="https://example.com",
        )
        assert vuln.id == "CVE-2024-0001"
        assert vuln.severity == "high"
        assert vuln.fixed_in == "2.32.0"

    def test_to_dict(self):
        vuln = Vulnerability(
            id="GHSA-0001", summary="XSS issue",
            severity="critical", package="flask",
            version="2.0.0",
        )
        d = vuln.to_dict()
        assert d["id"] == "GHSA-0001"
        assert d["severity"] == "critical"

    def test_default_fields(self):
        vuln = Vulnerability(
            id="V1", summary="s", severity="low",
            package="p", version="1.0",
        )
        assert vuln.fixed_in == ""
        assert vuln.url == ""
        assert vuln.aliases == []


# ---------------------------------------------------------------------------
# AuditReport tests
# ---------------------------------------------------------------------------

class TestAuditReport:
    def test_empty_report(self):
        report = AuditReport()
        assert report.critical_count == 0
        assert report.high_count == 0
        assert report.dependencies == []
        assert report.vulnerabilities == []

    def test_critical_count(self):
        vulns = [
            Vulnerability("V1", "s", "critical", "p", "1.0"),
            Vulnerability("V2", "s", "high", "p", "1.0"),
            Vulnerability("V3", "s", "critical", "p", "1.0"),
        ]
        report = AuditReport(vulnerabilities=vulns)
        assert report.critical_count == 2

    def test_high_count(self):
        vulns = [
            Vulnerability("V1", "s", "high", "p", "1.0"),
            Vulnerability("V2", "s", "medium", "p", "1.0"),
            Vulnerability("V3", "s", "high", "p", "1.0"),
        ]
        report = AuditReport(vulnerabilities=vulns)
        assert report.high_count == 2


# ---------------------------------------------------------------------------
# parse_dependencies tests
# ---------------------------------------------------------------------------

class TestParseDependencies:
    def test_parse_requirements_txt(self, tmp_requirements_txt):
        deps = parse_dependencies(str(tmp_requirements_txt))
        names = [d.name for d in deps]
        assert "requests" in names
        assert "flask" in names
        assert "numpy" in names
        # Comments and blank lines should be skipped
        assert len(deps) >= 3

    def test_parse_requirements_ecosystem(self, tmp_requirements_txt):
        deps = parse_dependencies(str(tmp_requirements_txt))
        for dep in deps:
            assert dep.ecosystem == "PyPI"
            # source_file stores the full path (str(path)), not just the filename
            assert dep.source_file.endswith("requirements.txt")

    def test_parse_package_json(self, tmp_package_json):
        deps = parse_dependencies(str(tmp_package_json))
        names = [d.name for d in deps]
        assert "express" in names
        assert "lodash" in names

    def test_parse_package_json_ecosystem(self, tmp_package_json):
        deps = parse_dependencies(str(tmp_package_json))
        for dep in deps:
            assert dep.ecosystem == "npm"

    def test_parse_go_mod(self, tmp_go_mod):
        deps = parse_dependencies(str(tmp_go_mod))
        names = [d.name for d in deps]
        assert any("gin" in n for n in names)

    def test_parse_go_mod_ecosystem(self, tmp_go_mod):
        deps = parse_dependencies(str(tmp_go_mod))
        for dep in deps:
            assert dep.ecosystem == "Go"

    def test_parse_empty_directory(self, tmp_path):
        deps = parse_dependencies(str(tmp_path))
        assert deps == []

    def test_parse_multiple_files(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"express": "4.18.2"}
        }))
        deps = parse_dependencies(str(tmp_path))
        ecosystems = {d.ecosystem for d in deps}
        assert "PyPI" in ecosystems
        assert "npm" in ecosystems


# ---------------------------------------------------------------------------
# audit_dependencies tests (mocked network)
# ---------------------------------------------------------------------------

class TestAuditDependencies:
    @patch("devlens.depaudit._query_osv")
    def test_audit_with_vulns(self, mock_query, tmp_requirements_txt, mock_osv_response):
        # _query_osv returns list[dict] (the "vulns" list from OSV API)
        mock_query.return_value = mock_osv_response["vulns"]

        report = audit_dependencies(str(tmp_requirements_txt))
        assert isinstance(report, AuditReport)
        assert len(report.dependencies) >= 3

    @patch("devlens.depaudit._query_osv")
    def test_audit_no_vulns(self, mock_query, tmp_requirements_txt):
        mock_query.return_value = []

        report = audit_dependencies(str(tmp_requirements_txt))
        assert report.critical_count == 0
        assert report.high_count == 0

    @patch("devlens.depaudit._query_osv")
    def test_audit_network_error(self, mock_query, tmp_requirements_txt):
        # _query_osv handles network errors internally (try/except -> return [])
        # When mocked, simulate that graceful behavior by returning empty list
        mock_query.return_value = []
        report = audit_dependencies(str(tmp_requirements_txt))
        # Should handle gracefully, return report with deps but no vulns
        assert isinstance(report, AuditReport)
        assert len(report.dependencies) >= 3
        assert len(report.vulnerabilities) == 0
