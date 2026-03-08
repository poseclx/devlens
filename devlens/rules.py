"""Custom rule engine for DevLens.

Allows users to define their own rules via YAML configuration:
  - pattern rules: regex-based pattern matching
  - threshold rules: metric threshold enforcement
  - ast rules: Python AST visitor checks

Built-in AST rules:
  - no-eval: Disallow eval() calls
  - no-exec: Disallow exec() calls
  - no-star-import: Disallow 'from x import *'
  - no-mutable-default: Disallow mutable default arguments
  - no-bare-except: Disallow bare except clauses
  - no-global: Disallow global statements

Usage:
    from devlens.rules import RuleEngine

    engine = RuleEngine.from_config(config)
    violations = engine.evaluate_file("src/app.py", content)
    threshold_violations = engine.evaluate_metrics(function_metrics)
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class RuleType(str, Enum):
    PATTERN = "pattern"
    THRESHOLD = "threshold"
    AST = "ast"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class Rule:
    """A single rule definition."""
    id: str
    title: str
    type: RuleType
    severity: Severity = Severity.MEDIUM
    description: str = ""
    suggestion: str = ""
    enabled: bool = True

    # Pattern rule fields
    pattern: str | None = None
    pattern_flags: int = 0

    # Threshold rule fields
    metric: str | None = None          # cyclomatic, nesting, length, cognitive, params
    max_value: int | None = None

    # AST rule fields
    ast_check: str | None = None       # built-in check name

    # File filtering
    include_patterns: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)

    def matches_file(self, filepath: str) -> bool:
        """Check if this rule applies to the given file."""
        if self.include_patterns:
            if not any(re.search(p, filepath) for p in self.include_patterns):
                return False
        if self.exclude_patterns:
            if any(re.search(p, filepath) for p in self.exclude_patterns):
                return False
        return True

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "title": self.title,
            "type": self.type.value,
            "severity": self.severity.value,
            "description": self.description,
            "enabled": self.enabled,
        }
        if self.pattern:
            d["pattern"] = self.pattern
        if self.metric:
            d["metric"] = self.metric
            d["max_value"] = self.max_value
        if self.ast_check:
            d["ast_check"] = self.ast_check
        return d


@dataclass
class RuleViolation:
    """A rule violation found during evaluation."""
    rule_id: str
    title: str
    severity: Severity
    file: str
    line: int | None = None
    message: str = ""
    suggestion: str = ""
    match: str = ""

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "severity": self.severity.value,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "suggestion": self.suggestion,
            "match": self.match[:80] + "..." if len(self.match) > 80 else self.match,
        }


@dataclass
class RuleValidationError:
    """Error found during rule validation."""
    rule_id: str
    field: str
    message: str


# -- Built-in AST checks --

class _ASTChecker(ast.NodeVisitor):
    """AST visitor that collects violations for built-in checks."""

    def __init__(self, filepath: str, checks: set[str]):
        self.filepath = filepath
        self.checks = checks
        self.violations: list[tuple[str, int, str]] = []  # (check_name, line, detail)

    def visit_Call(self, node: ast.Call) -> None:
        # no-eval
        if "no-eval" in self.checks:
            if isinstance(node.func, ast.Name) and node.func.id == "eval":
                self.violations.append(("no-eval", node.lineno, "eval() call detected"))
        # no-exec
        if "no-exec" in self.checks:
            if isinstance(node.func, ast.Name) and node.func.id == "exec":
                self.violations.append(("no-exec", node.lineno, "exec() call detected"))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # no-star-import
        if "no-star-import" in self.checks:
            if node.names and node.names[0].name == "*":
                module = node.module or "unknown"
                self.violations.append(
                    ("no-star-import", node.lineno, f"Star import from {module}")
                )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_mutable_defaults(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_mutable_defaults(node)
        self.generic_visit(node)

    def _check_mutable_defaults(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Check for mutable default arguments."""
        if "no-mutable-default" not in self.checks:
            return
        for default in node.args.defaults + node.args.kw_defaults:
            if default is None:
                continue
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self.violations.append(
                    ("no-mutable-default", node.lineno,
                     f"Mutable default argument in {node.name}()")
                )
                break

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        # no-bare-except
        if "no-bare-except" in self.checks:
            if node.type is None:
                self.violations.append(
                    ("no-bare-except", node.lineno, "Bare except clause")
                )
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        # no-global
        if "no-global" in self.checks:
            names = ", ".join(node.names)
            self.violations.append(
                ("no-global", node.lineno, f"Global statement: {names}")
            )
        self.generic_visit(node)


# Built-in AST rule definitions
BUILTIN_AST_RULES: dict[str, dict] = {
    "no-eval": {
        "title": "No eval() calls",
        "severity": "high",
        "description": "eval() executes arbitrary code and is a security risk.",
        "suggestion": "Use ast.literal_eval() for safe parsing, or refactor to avoid eval.",
    },
    "no-exec": {
        "title": "No exec() calls",
        "severity": "high",
        "description": "exec() executes arbitrary code and is a security risk.",
        "suggestion": "Refactor to use functions or importlib instead.",
    },
    "no-star-import": {
        "title": "No wildcard imports",
        "severity": "medium",
        "description": "Star imports pollute the namespace and make code harder to understand.",
        "suggestion": "Import specific names: from module import name1, name2",
    },
    "no-mutable-default": {
        "title": "No mutable default arguments",
        "severity": "medium",
        "description": "Mutable defaults are shared across calls and cause subtle bugs.",
        "suggestion": "Use None as default and create the mutable object inside the function.",
    },
    "no-bare-except": {
        "title": "No bare except clauses",
        "severity": "medium",
        "description": "Bare except catches everything including KeyboardInterrupt and SystemExit.",
        "suggestion": "Catch specific exceptions: except ValueError: or except Exception:",
    },
    "no-global": {
        "title": "No global statements",
        "severity": "low",
        "description": "Global variables make code harder to test and reason about.",
        "suggestion": "Pass values as function parameters or use a class/module pattern.",
    },
}


# -- Rule Engine --

class RuleEngine:
    """Custom rule engine that evaluates pattern, threshold, and AST rules."""

    def __init__(self, rules: list[Rule] | None = None):
        self.rules: list[Rule] = rules or []

    @classmethod
    def from_config(cls, config: dict) -> "RuleEngine":
        """Create engine from DevLens config dict.

        Reads rules from:
          1. config["rules"]["custom_rules"] (inline YAML rules)
          2. config["rules"]["files"] (external .devlens-rules.yml files)
          3. config["rules"]["builtin_ast"] (list of built-in AST check names)
        """
        rules: list[Rule] = []
        rules_cfg = config.get("rules", {})

        if not rules_cfg.get("enabled", True):
            return cls(rules=[])

        # Load built-in AST rules
        builtin_names = rules_cfg.get("builtin_ast", list(BUILTIN_AST_RULES.keys()))
        for name in builtin_names:
            if name in BUILTIN_AST_RULES:
                info = BUILTIN_AST_RULES[name]
                rules.append(Rule(
                    id=name,
                    title=info["title"],
                    type=RuleType.AST,
                    severity=Severity(info["severity"]),
                    description=info["description"],
                    suggestion=info["suggestion"],
                    ast_check=name,
                ))

        # Load inline custom rules
        for rule_def in rules_cfg.get("custom_rules", []):
            rule = _parse_rule(rule_def)
            if rule:
                rules.append(rule)

        # Load from external rule files
        for rule_file in rules_cfg.get("files", []):
            p = Path(rule_file)
            if p.exists():
                loaded = _load_rules_file(p)
                rules.extend(loaded)

        return cls(rules=rules)

    @classmethod
    def from_file(cls, filepath: str | Path) -> "RuleEngine":
        """Load rules from a YAML file."""
        rules = _load_rules_file(Path(filepath))
        return cls(rules=rules)

    def evaluate_file(self, filepath: str, content: str) -> list[RuleViolation]:
        """Evaluate all applicable rules against a file's content.

        Returns list of violations found.
        """
        violations: list[RuleViolation] = []

        # Separate rules by type
        pattern_rules = [r for r in self.rules if r.type == RuleType.PATTERN and r.enabled and r.matches_file(filepath)]
        ast_rules = [r for r in self.rules if r.type == RuleType.AST and r.enabled and r.matches_file(filepath)]

        # Pattern rules
        for rule in pattern_rules:
            if not rule.pattern:
                continue
            try:
                compiled = re.compile(rule.pattern, rule.pattern_flags)
            except re.error:
                continue

            for i, line in enumerate(content.splitlines(), 1):
                for match in compiled.finditer(line):
                    violations.append(RuleViolation(
                        rule_id=rule.id,
                        title=rule.title,
                        severity=rule.severity,
                        file=filepath,
                        line=i,
                        message=rule.description,
                        suggestion=rule.suggestion,
                        match=match.group(0),
                    ))

        # AST rules (Python only)
        if filepath.endswith(".py") and ast_rules:
            checks = {r.ast_check for r in ast_rules if r.ast_check}
            rule_map = {r.ast_check: r for r in ast_rules if r.ast_check}

            try:
                tree = ast.parse(content)
                checker = _ASTChecker(filepath, checks)
                checker.visit(tree)

                for check_name, line, detail in checker.violations:
                    rule = rule_map.get(check_name)
                    if rule:
                        violations.append(RuleViolation(
                            rule_id=rule.id,
                            title=rule.title,
                            severity=rule.severity,
                            file=filepath,
                            line=line,
                            message=detail,
                            suggestion=rule.suggestion,
                        ))
            except SyntaxError:
                pass

        return violations

    def evaluate_metrics(self, functions: list[dict]) -> list[RuleViolation]:
        """Evaluate threshold rules against function metrics.

        Args:
            functions: list of FunctionMetrics.to_dict() results

        Returns list of threshold violations.
        """
        violations: list[RuleViolation] = []
        threshold_rules = [r for r in self.rules if r.type == RuleType.THRESHOLD and r.enabled]

        for rule in threshold_rules:
            if not rule.metric or rule.max_value is None:
                continue

            for fn in functions:
                filepath = fn.get("file", "")
                if not rule.matches_file(filepath):
                    continue

                value = fn.get(rule.metric)
                if value is not None and value > rule.max_value:
                    violations.append(RuleViolation(
                        rule_id=rule.id,
                        title=rule.title,
                        severity=rule.severity,
                        file=filepath,
                        line=fn.get("line"),
                        message=f"{fn.get('name', '?')}(): {rule.metric}={value} exceeds max={rule.max_value}",
                        suggestion=rule.suggestion,
                    ))

        return violations

    def list_rules(self) -> list[dict]:
        """List all rules with their status."""
        return [r.to_dict() for r in self.rules]

    def validate(self) -> list[RuleValidationError]:
        """Validate all rules and return any errors found."""
        errors: list[RuleValidationError] = []

        seen_ids: set[str] = set()
        for rule in self.rules:
            # Duplicate ID check
            if rule.id in seen_ids:
                errors.append(RuleValidationError(rule.id, "id", "Duplicate rule ID"))
            seen_ids.add(rule.id)

            # Type-specific validation
            if rule.type == RuleType.PATTERN:
                if not rule.pattern:
                    errors.append(RuleValidationError(rule.id, "pattern", "Pattern rule missing 'pattern' field"))
                else:
                    try:
                        re.compile(rule.pattern)
                    except re.error as e:
                        errors.append(RuleValidationError(rule.id, "pattern", f"Invalid regex: {e}"))

            elif rule.type == RuleType.THRESHOLD:
                if not rule.metric:
                    errors.append(RuleValidationError(rule.id, "metric", "Threshold rule missing 'metric' field"))
                elif rule.metric not in ("cyclomatic", "nesting", "length", "cognitive", "params", "max_nesting"):
                    errors.append(RuleValidationError(rule.id, "metric", f"Unknown metric: {rule.metric}"))
                if rule.max_value is None:
                    errors.append(RuleValidationError(rule.id, "max_value", "Threshold rule missing 'max_value' field"))

            elif rule.type == RuleType.AST:
                if not rule.ast_check:
                    errors.append(RuleValidationError(rule.id, "ast_check", "AST rule missing 'ast_check' field"))
                elif rule.ast_check not in BUILTIN_AST_RULES:
                    errors.append(RuleValidationError(rule.id, "ast_check", f"Unknown AST check: {rule.ast_check}"))

            # Severity validation
            valid_severities = {s.value for s in Severity}
            if rule.severity.value not in valid_severities:
                errors.append(RuleValidationError(rule.id, "severity", f"Invalid severity: {rule.severity}"))

        return errors


# -- YAML parsing helpers --

def _parse_rule(rule_def: dict) -> Rule | None:
    """Parse a rule definition dict into a Rule object."""
    try:
        rule_id = rule_def.get("id", "")
        if not rule_id:
            return None

        rule_type = RuleType(rule_def.get("type", "pattern"))
        severity = Severity(rule_def.get("severity", "medium"))

        # Parse pattern flags
        flags = 0
        flag_str = rule_def.get("flags", "")
        if "i" in flag_str:
            flags |= re.IGNORECASE
        if "m" in flag_str:
            flags |= re.MULTILINE

        return Rule(
            id=rule_id,
            title=rule_def.get("title", rule_id),
            type=rule_type,
            severity=severity,
            description=rule_def.get("description", ""),
            suggestion=rule_def.get("suggestion", ""),
            enabled=rule_def.get("enabled", True),
            pattern=rule_def.get("pattern"),
            pattern_flags=flags,
            metric=rule_def.get("metric"),
            max_value=rule_def.get("max_value"),
            ast_check=rule_def.get("ast_check"),
            include_patterns=rule_def.get("include", []),
            exclude_patterns=rule_def.get("exclude", []),
        )
    except (ValueError, KeyError):
        return None


def _load_rules_file(path: Path) -> list[Rule]:
    """Load rules from a YAML file."""
    rules: list[Rule] = []
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return rules

        for rule_def in data.get("rules", []):
            rule = _parse_rule(rule_def)
            if rule:
                rules.append(rule)

    except (yaml.YAMLError, OSError):
        pass

    return rules
