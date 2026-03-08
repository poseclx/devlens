# tests/test_security.py
"""Tests for devlens.security — secret/vulnerability scanning."""
import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field

from devlens.security import (
    Severity,
    SecurityFinding,
    ScanResult,
    SECRET_RULES,
    VULN_RULES,
    RISKY_FILES,
    DEFAULT_IGNORE,
    _should_skip,
    _extract_added_lines,
    scan_pr,
    scan_path,
)


# ── Fake PRData ──────────────────────────────────────────────

@dataclass
class FakePRData:
    number: int = 42
    title: str = "Add feature"
    body: str = "Some description"
    changed_files: int = 2
    files: list = field(default_factory=list)


# ── Severity enum ────────────────────────────────────────────

class TestSeverity:
    """Severity enum has correct values and is a str mixin."""

    def test_values(self):
        assert Severity.CRITICAL.value == "critical"
        assert Severity.HIGH.value == "high"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.LOW.value == "low"
        assert Severity.INFO.value == "info"

    def test_string_behavior(self):
        assert str(Severity.CRITICAL) == "Severity.CRITICAL" or "critical" in str(Severity.CRITICAL).lower()

    def test_comparison(self):
        # Enum members are comparable
        assert Severity.CRITICAL == Severity.CRITICAL
        assert Severity.HIGH != Severity.LOW


# ── SecurityFinding ──────────────────────────────────────────

class TestSecurityFinding:
    """SecurityFinding dataclass and to_dict."""

    def test_creation(self):
        f = SecurityFinding(
            rule_id="SEC001", title="AWS Key",
            severity=Severity.CRITICAL, file="config.py",
            line=10, match="AKIA1234567890123456",
        )
        assert f.rule_id == "SEC001"
        assert f.severity == Severity.CRITICAL

    def test_to_dict(self):
        f = SecurityFinding(
            rule_id="SEC001", title="AWS Key",
            severity=Severity.CRITICAL, file="config.py",
            line=10, match="short",
            description="AWS key found", suggestion="Use env vars",
        )
        d = f.to_dict()
        assert d["rule_id"] == "SEC001"
        assert d["severity"] == "critical"
        assert d["file"] == "config.py"
        assert d["line"] == 10

    def test_to_dict_truncates_long_match(self):
        long_match = "A" * 100
        f = SecurityFinding(
            rule_id="T", title="T",
            severity=Severity.LOW, file="f.py",
            match=long_match,
        )
        d = f.to_dict()
        assert len(d["match"]) <= 84  # 80 + "..."

    def test_defaults(self):
        f = SecurityFinding(
            rule_id="T", title="T",
            severity=Severity.LOW, file="f.py",
        )
        assert f.line is None
        assert f.match == ""
        assert f.description == ""
        assert f.suggestion == ""


# ── ScanResult properties ────────────────────────────────────

class TestScanResult:
    """ScanResult computed properties and methods."""

    def _make_finding(self, severity):
        return SecurityFinding(
            rule_id="T", title="T", severity=severity, file="f.py"
        )

    def test_empty_findings_score_100(self):
        r = ScanResult(pr_number=1, title="T", total_files=5, files_scanned=5)
        assert r.score == 100
        assert r.grade == "A"

    def test_severity_counts(self):
        r = ScanResult(
            pr_number=1, title="T", total_files=5, files_scanned=5,
            findings=[
                self._make_finding(Severity.CRITICAL),
                self._make_finding(Severity.CRITICAL),
                self._make_finding(Severity.HIGH),
                self._make_finding(Severity.MEDIUM),
                self._make_finding(Severity.LOW),
                self._make_finding(Severity.LOW),
            ],
        )
        assert r.critical_count == 2
        assert r.high_count == 1
        assert r.medium_count == 1
        assert r.low_count == 2

    def test_score_calculation(self):
        r = ScanResult(
            pr_number=1, title="T", total_files=1, files_scanned=1,
            findings=[self._make_finding(Severity.CRITICAL)],
        )
        # 100 - 25 = 75
        assert r.score == 75
        assert r.grade == "B"

    def test_score_floor_zero(self):
        findings = [self._make_finding(Severity.CRITICAL)] * 10
        r = ScanResult(pr_number=1, title="T", total_files=1, files_scanned=1, findings=findings)
        assert r.score == 0
        assert r.grade == "F"

    def test_grade_boundaries(self):
        # A: >= 90
        r = ScanResult(pr_number=1, title="T", total_files=1, files_scanned=1, findings=[])
        assert r.grade == "A"

        # B: >= 75 (one critical = 75)
        r.findings = [self._make_finding(Severity.CRITICAL)]
        assert r.grade == "B"

        # C: >= 60 (two high = 70... need to adjust)
        r.findings = [self._make_finding(Severity.HIGH), self._make_finding(Severity.HIGH)]
        assert r.score == 70  # 100 - 30
        assert r.grade == "C"

        # D: >= 40
        r.findings = [self._make_finding(Severity.CRITICAL)] * 2 + [self._make_finding(Severity.HIGH)]
        assert r.score == 35  # 100 - 50 - 15
        assert r.grade == "F"  # below 40

    def test_to_dict(self):
        r = ScanResult(
            pr_number=42, title="PR Title", total_files=10, files_scanned=8,
            findings=[self._make_finding(Severity.HIGH)],
        )
        d = r.to_dict()
        assert d["pr_number"] == 42
        assert d["score"] == 85
        assert d["grade"] == "B"
        assert d["summary"]["high"] == 1
        assert len(d["findings"]) == 1

    def test_to_markdown_clean(self):
        r = ScanResult(pr_number=1, title="Clean PR", total_files=5, files_scanned=5)
        md = r.to_markdown()
        assert "PR #1" in md
        assert "100/100" in md
        assert "No security issues found" in md

    def test_to_markdown_with_findings(self):
        f = SecurityFinding(
            rule_id="SEC001", title="AWS Key",
            severity=Severity.CRITICAL, file="config.py",
            line=10, match="AKIA...",
            description="Key found", suggestion="Use env vars",
        )
        r = ScanResult(pr_number=5, title="T", total_files=3, files_scanned=3, findings=[f])
        md = r.to_markdown()
        assert "[CRITICAL]" in md
        assert "SEC001" in md
        assert "config.py" in md
        assert "line 10" in md
        assert "Use env vars" in md

    def test_to_markdown_with_ai_summary(self):
        r = ScanResult(
            pr_number=1, title="T", total_files=1, files_scanned=1,
            ai_summary="This PR has moderate security risk.",
        )
        md = r.to_markdown()
        assert "AI Analysis" in md
        assert "moderate security risk" in md


# ── _should_skip ─────────────────────────────────────────────

class TestShouldSkip:
    """_should_skip filters files by ignore patterns."""

    def test_lock_files_skipped(self):
        assert _should_skip("package-lock.json") is True
        assert _should_skip("yarn.lock") is True
        assert _should_skip("go.sum") is True

    def test_minified_files_skipped(self):
        assert _should_skip("bundle.min.js") is True
        assert _should_skip("styles.min.css") is True

    def test_normal_files_not_skipped(self):
        assert _should_skip("main.py") is False
        assert _should_skip("src/app.js") is False

    def test_vendor_skipped(self):
        assert _should_skip("vendor/lib.go") is True

    def test_custom_patterns(self):
        assert _should_skip("test.py", [r"\.py$"]) is True
        assert _should_skip("test.js", [r"\.py$"]) is False


# ── _extract_added_lines ─────────────────────────────────────

class TestExtractAddedLines:
    """_extract_added_lines parses unified diff hunks."""

    def test_simple_patch(self):
        patch = "@@ -0,0 +1,3 @@\n+line1\n+line2\n+line3"
        lines = _extract_added_lines(patch)
        assert len(lines) == 3
        assert lines[0] == (1, "line1")
        assert lines[2] == (3, "line3")

    def test_mixed_patch(self):
        patch = "@@ -1,3 +1,4 @@\n context\n-removed\n+added1\n+added2\n context"
        lines = _extract_added_lines(patch)
        assert len(lines) == 2
        assert all(l[1].startswith("added") for l in lines)

    def test_empty_patch(self):
        assert _extract_added_lines("") == []
        assert _extract_added_lines(None) == []

    def test_multiple_hunks(self):
        patch = "@@ -1,2 +1,2 @@\n-old\n+new1\n@@ -10,2 +10,2 @@\n-old2\n+new2"
        lines = _extract_added_lines(patch)
        assert len(lines) == 2


# ── scan_pr ──────────────────────────────────────────────────

class TestScanPR:
    """scan_pr integrates rules against PR diff."""

    def test_clean_pr(self):
        pr = FakePRData(files=[
            {"filename": "main.py", "patch": "@@ -0,0 +1,1 @@\n+print('hello')"},
        ])
        result = scan_pr(pr)
        assert isinstance(result, ScanResult)
        assert result.score == 100
        assert len(result.findings) == 0

    def test_detects_aws_key(self):
        pr = FakePRData(files=[
            {"filename": "config.py", "patch": "@@ -0,0 +1,1 @@\n+AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'"},
        ])
        result = scan_pr(pr)
        assert any(f.rule_id == "SEC001" for f in result.findings)

    def test_detects_github_token(self):
        pr = FakePRData(files=[
            {"filename": "ci.py", "patch": "@@ -0,0 +1,1 @@\n+token = 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij'"},
        ])
        result = scan_pr(pr)
        assert any(f.rule_id == "SEC003" for f in result.findings)

    def test_detects_eval_usage(self):
        pr = FakePRData(files=[
            {"filename": "handler.py", "patch": "@@ -0,0 +1,1 @@\n+result = eval(user_input)"},
        ])
        result = scan_pr(pr)
        assert any(f.rule_id == "VLN004" for f in result.findings)

    def test_detects_risky_file(self):
        pr = FakePRData(files=[
            {"filename": ".env.prod", "patch": ""},
        ])
        result = scan_pr(pr)
        assert any(f.rule_id == "FIL001" for f in result.findings)

    def test_skips_lock_files(self):
        pr = FakePRData(files=[
            {"filename": "package-lock.json", "patch": "@@ -0,0 +1,1 @@\n+secret = 'AKIAIOSFODNN7EXAMPLE'"},
        ])
        result = scan_pr(pr)
        assert len(result.findings) == 0

    def test_files_scanned_count(self):
        pr = FakePRData(
            changed_files=3,
            files=[
                {"filename": "a.py", "patch": "@@ -0,0 +1,1 @@\n+x = 1"},
                {"filename": "b.py", "patch": "@@ -0,0 +1,1 @@\n+y = 2"},
                {"filename": "c.py", "patch": ""},  # no patch
            ],
        )
        result = scan_pr(pr)
        assert result.files_scanned == 2
        assert result.total_files == 3

    def test_custom_rules(self):
        custom = [{
            "id": "CUSTOM1", "title": "Custom Secret",
            "pattern": r"MY_SECRET_\d+",
            "severity": Severity.HIGH,
            "description": "Custom rule", "suggestion": "Fix it",
        }]
        pr = FakePRData(files=[
            {"filename": "app.py", "patch": "@@ -0,0 +1,1 @@\n+key = MY_SECRET_12345"},
        ])
        result = scan_pr(pr, custom_rules=custom)
        assert any(f.rule_id == "CUSTOM1" for f in result.findings)

    @patch("devlens.security._ai_security_review", return_value="AI says all clear.")
    def test_ai_mode(self, mock_ai):
        pr = FakePRData(files=[
            {"filename": "app.py", "patch": "@@ -0,0 +1,1 @@\n+x = 1"},
        ])
        result = scan_pr(pr, use_ai=True, model="gpt-4o")
        assert result.ai_summary == "AI says all clear."
        mock_ai.assert_called_once()


# ── scan_path ────────────────────────────────────────────────

class TestScanPath:
    """scan_path scans local filesystem."""

    def test_clean_directory(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')\n")
        findings = scan_path(str(tmp_path))
        assert isinstance(findings, list)
        assert len(findings) == 0

    def test_detects_secret_in_file(self, tmp_path):
        (tmp_path / "config.py").write_text("AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n")
        findings = scan_path(str(tmp_path))
        assert any(f.rule_id == "SEC001" for f in findings)

    def test_detects_risky_filename(self, tmp_path):
        (tmp_path / ".env.local").write_text("SECRET=value\n")
        findings = scan_path(str(tmp_path))
        assert any(f.rule_id == "FIL001" for f in findings)

    def test_skips_ignored_dirs(self, tmp_path):
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "pkg.js").write_text("secret = 'AKIAIOSFODNN7EXAMPLE'\n")
        findings = scan_path(str(tmp_path))
        assert len(findings) == 0

    def test_skips_large_files(self, tmp_path):
        large = tmp_path / "big.py"
        large.write_text("x = 1\n" * 100_000)  # > 500KB
        findings = scan_path(str(tmp_path))
        # Large file skipped, no crash
        assert isinstance(findings, list)


# ── Rule coverage ────────────────────────────────────────────

class TestRuleCoverage:
    """Verify rule lists are well-formed."""

    def test_secret_rules_have_required_fields(self):
        for rule in SECRET_RULES:
            assert "id" in rule
            assert "title" in rule
            assert "pattern" in rule
            assert "severity" in rule
            assert isinstance(rule["severity"], Severity)

    def test_vuln_rules_have_required_fields(self):
        for rule in VULN_RULES:
            assert "id" in rule
            assert "title" in rule
            assert "pattern" in rule
            assert "severity" in rule

    def test_risky_files_have_required_fields(self):
        for rule in RISKY_FILES:
            assert "id" in rule
            assert "pattern" in rule
            assert "severity" in rule

    def test_secret_rule_count(self):
        assert len(SECRET_RULES) == 12

    def test_vuln_rule_count(self):
        assert len(VULN_RULES) == 12

    def test_default_ignore_patterns(self):
        assert len(DEFAULT_IGNORE) >= 8
