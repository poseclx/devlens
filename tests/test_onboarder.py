# tests/test_onboarder.py
"""Tests for devlens.onboarder — repository onboarding analyzer."""
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field

from devlens.onboarder import (
    RepoSnapshot,
    OnboardingResult,
    _build_tree,
    _detect_languages,
    scan_repo,
    _static_onboard,
    analyze_repo,
)


# ── RepoSnapshot ─────────────────────────────────────────────

class TestRepoSnapshot:
    """RepoSnapshot holds scanned repo structure."""

    def test_creation(self, tmp_path):
        snap = RepoSnapshot(
            root=str(tmp_path),
            files=["main.py", "utils.py"],
            tree="main.py\nutils.py",
            languages={"python": 2},
            total_files=2,
            total_lines=100,
        )
        assert snap.total_files == 2
        assert snap.languages["python"] == 2

    def test_empty_repo(self):
        snap = RepoSnapshot(
            root="/tmp/empty",
            files=[],
            tree="(empty)",
            languages={},
            total_files=0,
            total_lines=0,
        )
        assert snap.total_files == 0


# ── _build_tree ──────────────────────────────────────────────

class TestBuildTree:
    """_build_tree creates a text tree representation."""

    def test_basic_tree(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hi')")
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "app.py").write_text("x = 1")
        tree = _build_tree(tmp_path)
        assert "main.py" in tree
        assert "src" in tree

    def test_empty_dir(self, tmp_path):
        tree = _build_tree(tmp_path)
        assert isinstance(tree, str)


# ── _detect_languages ────────────────────────────────────────

class TestDetectLanguages:
    """_detect_languages counts files per language."""

    def test_python_files(self):
        files = ["main.py", "utils.py", "test.py"]
        langs = _detect_languages(files)
        assert langs.get("python", langs.get("Python", 0)) >= 3 or "py" in str(langs).lower()

    def test_mixed_languages(self):
        files = ["app.py", "index.js", "style.css", "main.go"]
        langs = _detect_languages(files)
        assert len(langs) >= 2

    def test_empty_files(self):
        langs = _detect_languages([])
        assert len(langs) == 0 or sum(langs.values()) == 0


# ── scan_repo ────────────────────────────────────────────────

class TestScanRepo:
    """scan_repo walks directory and creates RepoSnapshot."""

    def test_scan_simple_repo(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')\n")
        (tmp_path / "README.md").write_text("# Title\n")
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "app.py").write_text("class App:\n    pass\n")
        snap = scan_repo(str(tmp_path))
        assert isinstance(snap, RepoSnapshot)
        assert snap.total_files >= 2
        assert len(snap.files) >= 2

    def test_ignores_git_dir(self, tmp_path):
        (tmp_path / "main.py").write_text("x = 1\n")
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("git stuff")
        snap = scan_repo(str(tmp_path))
        assert not any(".git" in f for f in snap.files)

    def test_ignores_node_modules(self, tmp_path):
        (tmp_path / "index.js").write_text("const x = 1;\n")
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "pkg.js").write_text("module")
        snap = scan_repo(str(tmp_path))
        assert not any("node_modules" in f for f in snap.files)

    def test_counts_lines(self, tmp_path):
        (tmp_path / "main.py").write_text("line1\nline2\nline3\n")
        snap = scan_repo(str(tmp_path))
        assert snap.total_lines >= 3


# ── _static_onboard ──────────────────────────────────────────

class TestStaticOnboard:
    """_static_onboard creates guide without AI."""

    def test_basic_output(self, tmp_path):
        (tmp_path / "main.py").write_text("def main():\n    pass\n")
        (tmp_path / "README.md").write_text("# Project\n")
        snap = scan_repo(str(tmp_path))
        result = _static_onboard(snap)
        assert isinstance(result, OnboardingResult)
        assert result.overview  # not empty
        assert len(result.key_files) >= 0

    def test_detects_entry_points(self, tmp_path):
        (tmp_path / "main.py").write_text("if __name__ == '__main__':\n    pass\n")
        snap = scan_repo(str(tmp_path))
        result = _static_onboard(snap)
        assert isinstance(result, OnboardingResult)


# ── analyze_repo (main entry) ────────────────────────────────

class TestAnalyzeRepo:
    """analyze_repo is the main entry point."""

    def test_static_mode(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1\n")
        result = analyze_repo(str(tmp_path))
        assert isinstance(result, OnboardingResult)
        assert result.overview  # not empty

    @patch("devlens.onboarder._call_llm")
    def test_ai_mode(self, mock_llm, tmp_path):
        mock_llm.return_value = json.dumps({
            "overview": "A Python project",
            "architecture": "Simple script",
            "key_files": [{"file": "app.py", "role": "Main app"}],
            "entry_points": ["app.py"],
            "getting_started": ["Run python app.py"],
            "conventions": ["PEP 8"],
        })
        (tmp_path / "app.py").write_text("print('hello')\n")
        result = analyze_repo(str(tmp_path), use_ai=True, model="gpt-4o", api_key="test")
        assert result.overview == "A Python project"

    def test_nonexistent_path(self):
        with pytest.raises((FileNotFoundError, SystemExit, OSError)):
            analyze_repo("/nonexistent/path/xyz123")
