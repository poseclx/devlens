# tests/test_ignore.py
"""Tests for devlens.ignore — .devlensignore pattern matching."""
import pytest
from pathlib import Path
from devlens.ignore import IgnoreFilter, load_ignore_patterns


# ── Pattern parsing ──────────────────────────────────────────────────

class TestIgnoreFilterParsing:
    """IgnoreFilter._parse correctly categorizes patterns."""

    def test_empty_patterns(self):
        f = IgnoreFilter([])
        assert f._excludes == []
        assert f._includes == []

    def test_none_patterns(self):
        f = IgnoreFilter(None)
        assert f._excludes == []
        assert f._includes == []

    def test_comments_ignored(self):
        f = IgnoreFilter(["# this is a comment", "*.pyc"])
        assert f._excludes == ["*.pyc"]
        assert f._includes == []

    def test_blank_lines_ignored(self):
        f = IgnoreFilter(["", "  ", "*.log", ""])
        assert f._excludes == ["*.log"]

    def test_negation_patterns(self):
        f = IgnoreFilter(["*.env", "!important.env"])
        assert f._excludes == ["*.env"]
        assert f._includes == ["important.env"]

    def test_mixed_patterns(self):
        f = IgnoreFilter([
            "# comment",
            "dist/*",
            "*.lock",
            "!yarn.lock",
            "",
            "**/*.min.js",
        ])
        assert f._excludes == ["dist/*", "*.lock", "**/*.min.js"]
        assert f._includes == ["yarn.lock"]


# ── should_ignore matching ───────────────────────────────────────────

class TestShouldIgnore:
    """IgnoreFilter.should_ignore glob and regex matching."""

    def test_simple_glob(self):
        f = IgnoreFilter(["*.pyc"])
        assert f.should_ignore("module.pyc") is True
        assert f.should_ignore("module.py") is False

    def test_directory_pattern(self):
        f = IgnoreFilter(["dist/*"])
        assert f.should_ignore("dist/bundle.js") is True
        assert f.should_ignore("src/main.py") is False

    def test_doublestar_pattern(self):
        f = IgnoreFilter(["**/*.min.js"])
        assert f.should_ignore("assets/js/app.min.js") is True
        assert f.should_ignore("app.min.js") is True
        assert f.should_ignore("app.js") is False

    def test_negation_overrides_exclude(self):
        f = IgnoreFilter(["*.env", "!important.env"])
        assert f.should_ignore("secrets.env") is True
        assert f.should_ignore("important.env") is False

    def test_no_patterns_nothing_ignored(self):
        f = IgnoreFilter()
        assert f.should_ignore("anything.py") is False

    def test_trailing_slash_directory_marker(self):
        """Trailing / in pattern is stripped; still matches paths."""
        f = IgnoreFilter(["build/"])
        assert f.should_ignore("build/output.js") is True

    def test_backslash_normalized(self):
        f = IgnoreFilter(["dist/*"])
        assert f.should_ignore("dist\\bundle.js") is True

    def test_basename_match_no_slash(self):
        """Pattern without / matches against basename."""
        f = IgnoreFilter(["*.log"])
        assert f.should_ignore("logs/server.log") is True
        assert f.should_ignore("server.log") is True

    def test_nested_path_with_slash_pattern(self):
        f = IgnoreFilter(["src/generated/*"])
        assert f.should_ignore("src/generated/types.ts") is True
        assert f.should_ignore("src/main.ts") is False


# ── filter_paths ─────────────────────────────────────────────────

class TestFilterPaths:
    """IgnoreFilter.filter_paths returns only non-ignored paths."""

    def test_filters_matching_paths(self):
        f = IgnoreFilter(["*.pyc", "*.log"])
        paths = ["main.py", "main.pyc", "app.log", "readme.md"]
        assert f.filter_paths(paths) == ["main.py", "readme.md"]

    def test_empty_input(self):
        f = IgnoreFilter(["*.pyc"])
        assert f.filter_paths([]) == []

    def test_negation_preserved(self):
        f = IgnoreFilter(["*.env", "!keep.env"])
        paths = ["db.env", "keep.env", "main.py"]
        result = f.filter_paths(paths)
        assert "keep.env" in result
        assert "db.env" not in result


# ── from_file / find_and_load ────────────────────────────────────────

class TestFromFile:
    """IgnoreFilter.from_file and find_and_load filesystem loading."""

    def test_from_file_reads_patterns(self, tmp_path):
        ignore_file = tmp_path / ".devlensignore"
        ignore_file.write_text("*.pyc\n# comment\n!keep.pyc\ndist/*\n")
        f = IgnoreFilter.from_file(ignore_file)
        assert f._excludes == ["*.pyc", "dist/*"]
        assert f._includes == ["keep.pyc"]

    def test_from_file_missing_returns_empty(self, tmp_path):
        f = IgnoreFilter.from_file(tmp_path / "nonexistent")
        assert f._excludes == []
        assert f._includes == []

    def test_find_and_load_walks_up(self, tmp_path):
        # Create .devlensignore in parent
        ignore_file = tmp_path / ".devlensignore"
        ignore_file.write_text("*.log\n")
        # Start search from a subdirectory
        sub = tmp_path / "src" / "deep"
        sub.mkdir(parents=True)
        f = IgnoreFilter.find_and_load(sub)
        assert f.should_ignore("server.log") is True

    def test_find_and_load_no_file_returns_empty(self, tmp_path):
        isolated = tmp_path / "isolated_dir"
        isolated.mkdir()
        f = IgnoreFilter.find_and_load(isolated)
        assert f._excludes == []


# ── load_ignore_patterns convenience ─────────────────────────────────

class TestLoadIgnorePatterns:
    """Convenience function delegates to find_and_load."""

    def test_returns_ignore_filter(self, tmp_path):
        ignore_file = tmp_path / ".devlensignore"
        ignore_file.write_text("*.tmp\n")
        result = load_ignore_patterns(tmp_path)
        assert isinstance(result, IgnoreFilter)
        assert result.should_ignore("data.tmp") is True

    def test_none_start_uses_cwd(self):
        result = load_ignore_patterns(None)
        assert isinstance(result, IgnoreFilter)
