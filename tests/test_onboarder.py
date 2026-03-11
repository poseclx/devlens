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


# -- RepoSnapshot --

class TestRepoSnapshot:
    """RepoSnapshot holds scanned repo structure."""

    def test_creation(self, tmp_path):
        snap = RepoSnapshot(
            root=tmp_path,
            structure="main.py\nutils.py",
            languages=["Python"],
            file_contents={"main.py": "print('hi')", "utils.py": "x = 1"},
        )
        assert len(snap.file_contents) == 2
        assert "Python" in snap.languages

    def test_empty_repo(self, tmp_path):
        snap = RepoSnapshot(root=tmp_path)
        assert len(snap.file_contents) == 0
        assert snap.languages == []


# -- _build_tree --

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


# -- _detect_languages --

class TestDetectLanguages:
    """_detect_languages scans a directory for language files."""

    def test_python_files(self, tmp_path):
        (tmp_path / "main.py").write_text("x = 1")
        (tmp_path / "utils.py").write_text("y = 2")
        (tmp_path / "test.py").write_text("z = 3")
        langs = _detect_languages(tmp_path)
        assert "Python" in langs

    def test_mixed_languages(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1")
        (tmp_path / "index.js").write_text("var x = 1;")
        (tmp_path / "style.css").write_text("body {}")
        (tmp_path / "main.go").write_text("package main")
        langs = _detect_languages(tmp_path)
        assert len(langs) >= 2

    def test_empty_dir(self, tmp_path):
        langs = _detect_languages(tmp_path)
        assert len(langs) == 0


# -- scan_repo --

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
        assert len(snap.file_contents) >= 2

    def test_ignores_git_dir(self, tmp_path):
        (tmp_path / "main.py").write_text("x = 1\n")
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("git stuff")
        snap = scan_repo(str(tmp_path))
        assert not any(".git" in f for f in snap.file_contents)

    def test_ignores_node_modules(self, tmp_path):
        (tmp_path / "index.js").write_text("const x = 1;\n")
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "pkg.js").write_text("module")
        snap = scan_repo(str(tmp_path))
        assert not any("node_modules" in f for f in snap.file_contents)

    def test_reads_content(self, tmp_path):
        (tmp_path / "main.py").write_text("line1\nline2\nline3\n")
        snap = scan_repo(str(tmp_path))
        total_lines = sum(c.count("\n") for c in snap.file_contents.values())
        assert total_lines >= 3


# -- _static_onboard --

class TestStaticOnboard:
    """_static_onboard creates guide without AI."""

    def test_basic_output(self, tmp_path):
        (tmp_path / "main.py").write_text("def main():\n    pass\n")
        (tmp_path / "README.md").write_text("# Project\n")
        snap = scan_repo(str(tmp_path))
        result = _static_onboard(snap)
        assert isinstance(result, OnboardingResult)
        assert result.overview

    def test_detects_entry_points(self, tmp_path):
        (tmp_path / "main.py").write_text("if __name__ == '__main__':\n    pass\n")
        snap = scan_repo(str(tmp_path))
        result = _static_onboard(snap)
        assert isinstance(result, OnboardingResult)


# -- analyze_repo (main entry) --

class TestAnalyzeRepo:
    """analyze_repo is the main entry point."""

    def test_static_mode(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1\n")
        snap = scan_repo(str(tmp_path))
        result = analyze_repo(snap)
        assert isinstance(result, OnboardingResult)
        assert result.overview

    @patch("devlens.onboarder._call_llm")
    def test_ai_mode(self, mock_llm, tmp_path):
        mock_llm.return_value = json.dumps({
            "overview": "A Python project",
            "architecture": "Simple script",
            "key_files": [{"file": "app.py", "role": "Main app"}],
            "entry_points": ["app.py"],
            "tech_stack": ["Python"],
            "getting_started": ["Run python app.py"],
            "where_to_start": "Start with app.py",
        })
        (tmp_path / "app.py").write_text("print('hello')\n")
        snap = scan_repo(str(tmp_path))
        result = analyze_repo(snap, use_ai=True, model="gpt-4o", api_key="test")
        assert result.overview == "A Python project"

    def test_nonexistent_path(self):
        with pytest.raises((FileNotFoundError, SystemExit, OSError)):
            scan_repo("/nonexistent/path/xyz123")
