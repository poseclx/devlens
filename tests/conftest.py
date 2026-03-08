# tests/conftest.py
"""Shared fixtures for DevLens test suite."""
import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# PR Data fixture (preserved from original)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_pr_data():
    """Minimal PRData-like dict for testing without hitting GitHub API."""
    from devlens.github import PRData
    return PRData(
        number=42,
        title="Add user authentication",
        body="Implements JWT-based auth flow.",
        author="dev",
        base_branch="main",
        head_branch="feature/auth",
        additions=120,
        deletions=15,
        changed_files=5,
        labels=["feature"],
        files=[
            {
                "filename": "auth/jwt.py",
                "status": "added",
                "additions": 80,
                "deletions": 0,
                "patch": "+def verify_token(token):\n+    pass",
            },
            {
                "filename": "requirements.txt",
                "status": "modified",
                "additions": 2,
                "deletions": 0,
                "patch": "+PyJWT==2.8.0\n+cryptography==42.0.0",
            },
        ],
    )


# ---------------------------------------------------------------------------
# Python source code fixtures
# ---------------------------------------------------------------------------

SIMPLE_PYTHON = '''\
def hello(name):
    """Greet someone."""
    return f"Hello, {name}!"


def add(a, b):
    return a + b
'''

COMPLEX_PYTHON = '''\
def process_data(data, mode, strict=False):
    """A deliberately complex function for testing."""
    result = []
    for item in data:
        if mode == "filter":
            if item.get("active"):
                if strict:
                    if item.get("verified"):
                        if item.get("score", 0) > 50:
                            result.append(item)
                        else:
                            if item.get("override"):
                                result.append(item)
                else:
                    result.append(item)
        elif mode == "transform":
            try:
                val = int(item.get("value", 0))
                if val > 0:
                    result.append({"id": item["id"], "value": val * 2})
                elif val == 0:
                    result.append({"id": item["id"], "value": 1})
                else:
                    raise ValueError("Negative")
            except (ValueError, KeyError):
                if strict:
                    raise
                continue
        elif mode == "aggregate":
            pass
    return result


def simple_func():
    return 42
'''

SECURITY_ISSUES_PYTHON = '''\
import subprocess
import os

def run_command(cmd):
    result = subprocess.call(cmd, shell=True)
    return result

def get_password():
    password = "admin123"
    return password

def read_file(path):
    eval(path)
    return open(path).read()

API_KEY = "sk-1234567890abcdef"
'''


@pytest.fixture
def tmp_python_file(tmp_path):
    """Create a temporary Python file with simple code."""
    p = tmp_path / "sample.py"
    p.write_text(SIMPLE_PYTHON)
    return p


@pytest.fixture
def tmp_complex_python_file(tmp_path):
    """Create a temporary Python file with complex code."""
    p = tmp_path / "complex.py"
    p.write_text(COMPLEX_PYTHON)
    return p


@pytest.fixture
def tmp_security_python_file(tmp_path):
    """Create a temporary Python file with security issues."""
    p = tmp_path / "insecure.py"
    p.write_text(SECURITY_ISSUES_PYTHON)
    return p


@pytest.fixture
def tmp_python_project(tmp_path):
    """Create a minimal Python project directory for testing."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "main.py").write_text(SIMPLE_PYTHON)
    (src / "utils.py").write_text(COMPLEX_PYTHON)
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\nflask==3.0.0\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Sample findings / reports
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_findings():
    """Sample findings from various analyzers."""
    return {
        "security": [
            {
                "id": "SEC001",
                "title": "Shell injection risk",
                "severity": "high",
                "file": "app.py",
                "line": 10,
                "message": "subprocess.call with shell=True",
                "suggestion": "Use subprocess.run with shell=False",
                "category": "security",
            },
            {
                "id": "SEC003",
                "title": "Hardcoded credential",
                "severity": "critical",
                "file": "config.py",
                "line": 5,
                "message": "Hardcoded password detected",
                "suggestion": "Use environment variables",
                "category": "security",
            },
        ],
        "complexity": [
            {
                "id": "CX001",
                "title": "High cyclomatic complexity",
                "severity": "medium",
                "file": "utils.py",
                "line": 1,
                "message": "Function process_data has complexity 12",
                "suggestion": "Refactor into smaller functions",
                "category": "complexity",
            },
        ],
        "rules": [
            {
                "id": "RULE001",
                "title": "Missing docstring",
                "severity": "low",
                "file": "utils.py",
                "line": 30,
                "message": "Function simple_func missing docstring",
                "suggestion": "Add a docstring",
                "category": "rules",
            },
        ],
        "dependencies": [
            {
                "id": "DEP001",
                "title": "Known vulnerability",
                "severity": "high",
                "file": "requirements.txt",
                "line": 1,
                "message": "requests 2.31.0 has CVE-2024-XXXX",
                "suggestion": "Upgrade to requests>=2.32.0",
                "category": "dependencies",
            },
        ],
    }


@pytest.fixture
def sample_complexity_report():
    """Pre-built ComplexityReport for tests that don't need real analysis."""
    from devlens.complexity import FunctionMetrics, FileComplexity, ComplexityReport

    simple_fn = FunctionMetrics(
        name="hello", file="simple.py", line=1, end_line=3,
        cyclomatic=1, length=3, max_nesting=0,
    )
    complex_fn = FunctionMetrics(
        name="process_data", file="complex.py", line=1, end_line=30,
        cyclomatic=12, length=30, max_nesting=5,
    )
    medium_fn = FunctionMetrics(
        name="validate", file="complex.py", line=35, end_line=50,
        cyclomatic=6, length=16, max_nesting=3,
    )

    file1 = FileComplexity(
        file="simple.py", total_lines=10, code_lines=7,
        functions=[simple_fn],
    )
    file2 = FileComplexity(
        file="complex.py", total_lines=55, code_lines=45,
        functions=[complex_fn, medium_fn],
    )

    return ComplexityReport(files=[file1, file2])


@pytest.fixture
def sample_rule_definitions():
    """Sample rule definition dicts for RuleEngine tests."""
    return [
        {
            "id": "NO_EVAL",
            "title": "No eval() usage",
            "type": "pattern",
            "severity": "critical",
            "pattern": r"eval\s*\(",
            "description": "eval() is dangerous",
            "suggestion": "Use ast.literal_eval() instead",
        },
        {
            "id": "MAX_COMPLEXITY",
            "title": "Max cyclomatic complexity",
            "type": "threshold",
            "severity": "medium",
            "metric": "cyclomatic",
            "max_value": 10,
            "description": "Functions should not exceed complexity 10",
        },
        {
            "id": "NO_STAR_IMPORT",
            "title": "No wildcard imports",
            "type": "pattern",
            "severity": "low",
            "pattern": r"from\s+\S+\s+import\s+\*",
            "description": "Wildcard imports pollute namespace",
            "include_patterns": ["*.py"],
            "exclude_patterns": ["__init__.py"],
        },
    ]


@pytest.fixture
def mock_cache_manager(tmp_path):
    """Create a CacheManager instance using a temp directory."""
    from devlens.cache import CacheManager
    return CacheManager(root=str(tmp_path), ttl_days=7, enabled=True)


# ---------------------------------------------------------------------------
# Dependency / audit fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_requirements_txt(tmp_path):
    """Create a requirements.txt for dependency audit tests."""
    p = tmp_path / "requirements.txt"
    p.write_text(
        "requests==2.31.0\n"
        "flask==3.0.0\n"
        "pyyaml>=6.0\n"
        "numpy==1.26.4\n"
        "# comment line\n"
        "  \n"
    )
    return tmp_path


@pytest.fixture
def tmp_package_json(tmp_path):
    """Create a package.json for dependency audit tests."""
    p = tmp_path / "package.json"
    p.write_text(json.dumps({
        "name": "test-app",
        "version": "1.0.0",
        "dependencies": {
            "express": "4.18.2",
            "lodash": "4.17.21",
        },
        "devDependencies": {
            "jest": "29.7.0",
        },
    }, indent=2))
    return tmp_path


@pytest.fixture
def tmp_go_mod(tmp_path):
    """Create a go.mod for dependency audit tests."""
    p = tmp_path / "go.mod"
    p.write_text(
        "module example.com/myapp\n\n"
        "go 1.21\n\n"
        "require (\n"
        "\tgithub.com/gin-gonic/gin v1.9.1\n"
        "\tgithub.com/go-sql-driver/mysql v1.7.1\n"
        ")\n"
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Mock OSV API responses
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_osv_response():
    """Fake OSV API response for testing audit without network."""
    return {
        "vulns": [
            {
                "id": "GHSA-test-0001",
                "summary": "Test vulnerability in requests",
                "severity": [{"type": "CVSS_V3", "score": "7.5"}],
                "affected": [
                    {
                        "package": {"name": "requests", "ecosystem": "PyPI"},
                        "ranges": [
                            {
                                "type": "ECOSYSTEM",
                                "events": [
                                    {"introduced": "0"},
                                    {"fixed": "2.32.0"},
                                ],
                            }
                        ],
                    }
                ],
                "aliases": ["CVE-2024-00001"],
                "references": [{"type": "ADVISORY", "url": "https://example.com"}],
            }
        ]
    }
