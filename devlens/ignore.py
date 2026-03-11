"""DevLens ignore-file support — .devlensignore parser.

Reads .devlensignore (gitignore-style patterns) and applies them to file paths.
Supports:
  - Glob patterns: *.lock, dist/*, **/*.min.js
  - Negation: !important.env
  - Comments: lines starting with #
  - Directory markers: trailing / means directory only
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path


class IgnoreFilter:
    """Gitignore-style file filter using .devlensignore patterns."""

    def __init__(self, patterns: list[str] | None = None):
        self._includes: list[str] = []
        self._excludes: list[str] = []
        if patterns:
            self._parse(patterns)

    def _parse(self, lines: list[str]) -> None:
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("!"):
                self._includes.append(line[1:].strip())
            else:
                self._excludes.append(line)

    @classmethod
    def from_file(cls, path: Path) -> "IgnoreFilter":
        """Load patterns from a .devlensignore file."""
        if not path.exists():
            return cls()
        lines = path.read_text().splitlines()
        return cls(lines)

    @classmethod
    def find_and_load(cls, start: Path | None = None) -> "IgnoreFilter":
        """Walk up from start dir looking for .devlensignore."""
        search = start or Path.cwd()
        for directory in [search, *search.parents]:
            candidate = directory / ".devlensignore"
            if candidate.exists():
                return cls.from_file(candidate)
        return cls()

    def should_ignore(self, filepath: str) -> bool:
        """Check if a file path should be ignored.
        
        Returns True if the path matches an exclude pattern
        AND does not match any include (negation) pattern.
        """
        # Normalize path separators
        normalized = filepath.replace("\\", "/")
        
        # Check negation first — if it matches an include, never ignore
        for pattern in self._includes:
            if self._match(normalized, pattern):
                return False

        # Check exclude patterns
        for pattern in self._excludes:
            if self._match(normalized, pattern):
                return True

        return False

    def filter_paths(self, paths: list[str]) -> list[str]:
        """Return only paths that should NOT be ignored."""
        return [p for p in paths if not self.should_ignore(p)]

    @staticmethod
    def _match(filepath: str, pattern: str) -> bool:
        """Match a filepath against a gitignore-style pattern."""
        # Remove trailing slash (directory marker — we treat all as paths)
        pattern = pattern.rstrip("/")
        
        # If pattern has no slash, match against basename only
        if "/" not in pattern:
            basename = filepath.rsplit("/", 1)[-1]
            if fnmatch.fnmatch(basename, pattern):
                return True
            # Also try against full path for ** patterns
            if fnmatch.fnmatch(filepath, f"**/{pattern}"):
                return True

        # Pattern with slash: match against full path
        # Convert ** to regex-friendly pattern
        regex_pattern = pattern
        regex_pattern = regex_pattern.replace(".", r"\.")
        regex_pattern = regex_pattern.replace("?", "§QUESTION§")
        regex_pattern = regex_pattern.replace("**/", "§DOUBLESTAR_SLASH§")
        regex_pattern = regex_pattern.replace("**", "§DOUBLESTAR§")
        regex_pattern = regex_pattern.replace("*", "[^/]*")
        regex_pattern = regex_pattern.replace("§DOUBLESTAR_SLASH§", "(.*/)?")
        regex_pattern = regex_pattern.replace("§DOUBLESTAR§", ".*")
        regex_pattern = regex_pattern.replace("§QUESTION§", "[^/]")

        # Match from start or as a suffix
        if re.search(regex_pattern, filepath):
            return True

        return False


def load_ignore_patterns(start: Path | None = None) -> IgnoreFilter:
    """Convenience function: find and load .devlensignore from project root."""
    return IgnoreFilter.find_and_load(start)
