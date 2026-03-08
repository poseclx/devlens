"""Tests for devlens.fixer module."""
import pytest
import json
from unittest.mock import patch, MagicMock

from devlens.fixer import (
    FixSuggestion,
    suggest_fixes,
    format_fixes_markdown,
    format_fixes_json,
)


# ---------------------------------------------------------------------------
# FixSuggestion dataclass tests
# ---------------------------------------------------------------------------

class TestFixSuggestion:
    def test_fields(self):
        fix = FixSuggestion(
            finding_id="SEC001",
            file="app.py",
            title="Remove shell=True",
            original="subprocess.call(cmd, shell=True)",
            suggested="subprocess.run(cmd.split(), shell=False)",
            diff="- subprocess.call(cmd, shell=True)\n+ subprocess.run(cmd.split(), shell=False)",
            explanation="shell=True is dangerous",
        )
        assert fix.finding_id == "SEC001"
        assert fix.file == "app.py"
        assert fix.confidence == "medium"
        assert fix.auto_applicable is False

    def test_to_dict(self):
        fix = FixSuggestion(
            finding_id="R1", file="f.py", title="Fix",
            original="old", suggested="new", diff="-old\n+new",
            explanation="Because", confidence="high",
            auto_applicable=True,
        )
        d = fix.to_dict()
        assert d["finding_id"] == "R1"
        assert d["confidence"] == "high"
        assert d["auto_applicable"] is True
        assert d["file"] == "f.py"

    def test_default_confidence(self):
        fix = FixSuggestion(
            finding_id="X", file="f.py", title="T",
            original="a", suggested="b", diff="d",
            explanation="e",
        )
        assert fix.confidence == "medium"


# ---------------------------------------------------------------------------
# suggest_fixes tests (pattern-based, no AI)
# ---------------------------------------------------------------------------

class TestSuggestFixes:
    def test_returns_list(self, sample_findings):
        all_findings = []
        for category_findings in sample_findings.values():
            all_findings.extend(category_findings)
        fixes = suggest_fixes(all_findings, use_ai=False)
        assert isinstance(fixes, list)

    def test_security_findings_get_fixes(self, sample_findings):
        fixes = suggest_fixes(sample_findings["security"], use_ai=False)
        # At least some security findings should produce fix suggestions
        assert isinstance(fixes, list)

    def test_with_file_contents(self, sample_findings):
        contents = {
            "app.py": "import subprocess\nsubprocess.call(cmd, shell=True)\n",
            "config.py": 'password = "admin123"\n',
        }
        fixes = suggest_fixes(
            sample_findings["security"],
            file_contents=contents,
            use_ai=False,
        )
        assert isinstance(fixes, list)

    def test_max_fixes_limit(self, sample_findings):
        all_findings = []
        for category_findings in sample_findings.values():
            all_findings.extend(category_findings)
        fixes = suggest_fixes(all_findings, use_ai=False, max_fixes=1)
        assert len(fixes) <= 1

    def test_empty_findings(self):
        fixes = suggest_fixes([], use_ai=False)
        assert fixes == []

    @patch("devlens.fixer._ai_suggest_fix")
    def test_ai_mode_calls_llm(self, mock_ai, sample_findings):
        mock_ai.return_value = FixSuggestion(
            finding_id="SEC001", file="app.py",
            title="AI Fix", original="old", suggested="new",
            diff="-old\n+new", explanation="AI says so",
            confidence="high",
        )
        fixes = suggest_fixes(
            sample_findings["security"][:1],
            use_ai=True, model="gpt-4o",
        )
        assert isinstance(fixes, list)


# ---------------------------------------------------------------------------
# Format output tests
# ---------------------------------------------------------------------------

class TestFormatFixes:
    def _sample_fixes(self):
        return [
            FixSuggestion(
                finding_id="SEC001", file="app.py",
                title="Remove shell injection",
                original="subprocess.call(cmd, shell=True)",
                suggested="subprocess.run(shlex.split(cmd))",
                diff="- subprocess.call(cmd, shell=True)\n+ subprocess.run(shlex.split(cmd))",
                explanation="Prevents shell injection attacks",
                confidence="high",
                auto_applicable=True,
            ),
            FixSuggestion(
                finding_id="SEC003", file="config.py",
                title="Remove hardcoded password",
                original='password = "admin123"',
                suggested='password = os.environ["APP_PASSWORD"]',
                diff='- password = "admin123"\n+ password = os.environ["APP_PASSWORD"]',
                explanation="Use environment variables for secrets",
                confidence="medium",
            ),
        ]

    def test_markdown_contains_titles(self):
        md = format_fixes_markdown(self._sample_fixes())
        assert "Remove shell injection" in md
        assert "Remove hardcoded password" in md

    def test_markdown_contains_files(self):
        md = format_fixes_markdown(self._sample_fixes())
        assert "app.py" in md
        assert "config.py" in md

    def test_markdown_contains_diff(self):
        md = format_fixes_markdown(self._sample_fixes())
        assert "subprocess.call" in md or "subprocess.run" in md

    def test_json_valid(self):
        result = format_fixes_json(self._sample_fixes())
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2

    def test_json_fields(self):
        result = format_fixes_json(self._sample_fixes())
        parsed = json.loads(result)
        assert parsed[0]["finding_id"] == "SEC001"
        assert parsed[1]["finding_id"] == "SEC003"

    def test_empty_fixes_markdown(self):
        md = format_fixes_markdown([])
        assert isinstance(md, str)

    def test_empty_fixes_json(self):
        result = format_fixes_json([])
        parsed = json.loads(result)
        assert parsed == []
