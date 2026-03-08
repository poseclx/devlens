"""Tests for devlens.rules module."""
import pytest
import yaml
from pathlib import Path
from unittest.mock import patch

from devlens.rules import (
    RuleType,
    Severity,
    Rule,
    RuleViolation,
    RuleValidationError,
    RuleEngine,
)


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------

class TestEnums:
    def test_rule_type_values(self):
        assert RuleType.PATTERN == "pattern"
        assert RuleType.THRESHOLD == "threshold"
        assert RuleType.AST == "ast"

    def test_severity_ordering(self):
        levels = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
        assert len(levels) == 5

    def test_severity_values(self):
        assert Severity.CRITICAL == "critical"
        assert Severity.HIGH == "high"
        assert Severity.MEDIUM == "medium"
        assert Severity.LOW == "low"
        assert Severity.INFO == "info"


# ---------------------------------------------------------------------------
# Rule tests
# ---------------------------------------------------------------------------

class TestRule:
    def test_matches_file_no_patterns(self):
        rule = Rule(id="R1", title="Test", type=RuleType.PATTERN, pattern="test")
        assert rule.matches_file("anything.py") is True

    def test_matches_file_include_pattern(self):
        rule = Rule(
            id="R1", title="Test", type=RuleType.PATTERN,
            pattern="test", include_patterns=["*.py"],
        )
        assert rule.matches_file("app.py") is True
        assert rule.matches_file("style.css") is False

    def test_matches_file_exclude_pattern(self):
        rule = Rule(
            id="R1", title="Test", type=RuleType.PATTERN,
            pattern="test", exclude_patterns=["__init__.py"],
        )
        assert rule.matches_file("main.py") is True
        assert rule.matches_file("__init__.py") is False

    def test_matches_file_include_and_exclude(self):
        rule = Rule(
            id="R1", title="Test", type=RuleType.PATTERN,
            pattern="test",
            include_patterns=["*.py"],
            exclude_patterns=["test_*.py"],
        )
        assert rule.matches_file("app.py") is True
        assert rule.matches_file("test_app.py") is False
        assert rule.matches_file("data.json") is False

    def test_to_dict(self):
        rule = Rule(
            id="R1", title="No eval", type=RuleType.PATTERN,
            severity=Severity.CRITICAL, pattern=r"eval\(",
        )
        d = rule.to_dict()
        assert d["id"] == "R1"
        assert d["title"] == "No eval"
        assert d["type"] == "pattern"
        assert d["severity"] == "critical"

    def test_disabled_rule(self):
        rule = Rule(id="R1", title="Off", type=RuleType.PATTERN, enabled=False)
        assert rule.enabled is False


# ---------------------------------------------------------------------------
# RuleViolation tests
# ---------------------------------------------------------------------------

class TestRuleViolation:
    def test_to_dict(self):
        v = RuleViolation(
            rule_id="SEC001", title="Shell injection",
            severity=Severity.HIGH, file="app.py", line=10,
            message="Dangerous call", suggestion="Fix it",
        )
        d = v.to_dict()
        assert d["rule_id"] == "SEC001"
        assert d["severity"] == "high"
        assert d["file"] == "app.py"
        assert d["line"] == 10

    def test_to_dict_without_line(self):
        v = RuleViolation(
            rule_id="R1", title="Test", severity=Severity.LOW,
            file="test.py",
        )
        d = v.to_dict()
        assert d["line"] is None


# ---------------------------------------------------------------------------
# RuleEngine tests
# ---------------------------------------------------------------------------

class TestRuleEngine:
    def test_init_empty(self):
        engine = RuleEngine()
        assert engine.list_rules() == []

    def test_init_with_rules(self, sample_rule_definitions):
        engine = RuleEngine.from_config({"rules": sample_rule_definitions})
        rules = engine.list_rules()
        assert len(rules) == 3

    def test_from_file(self, tmp_path, sample_rule_definitions):
        rules_file = tmp_path / "rules.yml"
        rules_file.write_text(yaml.dump({"rules": sample_rule_definitions}))
        engine = RuleEngine.from_file(str(rules_file))
        assert len(engine.list_rules()) == 3

    def test_evaluate_file_pattern_match(self, sample_rule_definitions):
        engine = RuleEngine.from_config({"rules": sample_rule_definitions})
        code = 'result = eval("2+2")\nprint(result)\n'
        violations = engine.evaluate_file("test.py", code)
        eval_violations = [v for v in violations if v.rule_id == "NO_EVAL"]
        assert len(eval_violations) >= 1
        assert eval_violations[0].severity == Severity.CRITICAL

    def test_evaluate_file_no_match(self, sample_rule_definitions):
        engine = RuleEngine.from_config({"rules": sample_rule_definitions})
        code = "x = 1 + 2\nprint(x)\n"
        violations = engine.evaluate_file("test.py", code)
        pattern_violations = [v for v in violations if v.rule_id in ("NO_EVAL", "NO_STAR_IMPORT")]
        assert len(pattern_violations) == 0

    def test_evaluate_file_star_import(self, sample_rule_definitions):
        engine = RuleEngine.from_config({"rules": sample_rule_definitions})
        code = "from os import *\n"
        violations = engine.evaluate_file("test.py", code)
        star_violations = [v for v in violations if v.rule_id == "NO_STAR_IMPORT"]
        assert len(star_violations) >= 1

    def test_evaluate_file_excludes_init(self, sample_rule_definitions):
        engine = RuleEngine.from_config({"rules": sample_rule_definitions})
        code = "from os import *\n"
        violations = engine.evaluate_file("__init__.py", code)
        star_violations = [v for v in violations if v.rule_id == "NO_STAR_IMPORT"]
        assert len(star_violations) == 0

    def test_validate_valid_rules(self, sample_rule_definitions):
        engine = RuleEngine.from_config({"rules": sample_rule_definitions})
        errors = engine.validate()
        assert len(errors) == 0

    def test_validate_invalid_pattern(self):
        bad_rules = [{
            "id": "BAD", "title": "Bad regex", "type": "pattern",
            "severity": "high", "pattern": "[invalid(",
        }]
        engine = RuleEngine.from_config({"rules": bad_rules})
        errors = engine.validate()
        assert len(errors) >= 1

    def test_disabled_rule_not_evaluated(self):
        rules = [{
            "id": "OFF", "title": "Disabled", "type": "pattern",
            "severity": "high", "pattern": "print",
            "enabled": False,
        }]
        engine = RuleEngine.from_config({"rules": rules})
        violations = engine.evaluate_file("test.py", "print('hello')\n")
        assert all(v.rule_id != "OFF" for v in violations)
