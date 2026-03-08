"""Multi-language complexity analysis — regex+heuristic adapters.

Provides complexity analysis for non-Python languages without external
dependencies (no tree-sitter needed). Each language adapter uses regex
patterns to detect function boundaries and decision points.

Supported languages:
  - JavaScript / TypeScript (.js, .jsx, .ts, .tsx, .mjs, .cjs, .mts)
  - Java (.java)
  - Go (.go)
  - Rust (.rs)

Usage:
    from devlens.languages import get_adapter, SUPPORTED_EXTENSIONS

    adapter = get_adapter("app.ts")
    if adapter:
        result = adapter.analyze(source_code, "app.ts")
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from devlens.complexity import FunctionMetrics, FileComplexity


# -- Extension to language mapping --

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
}

ALL_EXTENSIONS = tuple(SUPPORTED_EXTENSIONS.keys()) + (".py",)


# -- Base adapter --

class LanguageAdapter(ABC):
    """Base class for language-specific complexity analyzers."""

    name: str = "unknown"
    extensions: tuple[str, ...] = ()

    @abstractmethod
    def find_functions(self, source: str, filepath: str) -> list[FunctionMetrics]:
        """Extract function metrics from source code."""
        ...

    def analyze(self, source: str, filepath: str) -> FileComplexity:
        """Analyze a file and return FileComplexity."""
        lines = source.splitlines()
        total_lines = len(lines)
        code_lines = sum(
            1 for line in lines
            if line.strip() and not _is_comment(line, self.name)
        )
        functions = self.find_functions(source, filepath)
        return FileComplexity(
            file=filepath,
            total_lines=total_lines,
            code_lines=code_lines,
            functions=functions,
        )


def _is_comment(line: str, lang: str) -> bool:
    """Check if a line is a single-line comment (simplified)."""
    stripped = line.strip()
    if stripped.startswith("//"):
        return True
    if stripped.startswith("/*") or stripped.startswith("*"):
        return True
    if stripped.startswith("#") and lang == "python":
        return True
    return False


# -- Shared helpers --

def _count_decision_points(body: str, lang: str) -> int:
    """Count decision points for cyclomatic complexity."""
    cc = 1
    decision_patterns = [
        r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b",
        r"\bcase\b", r"\bcatch\b", r"\?\?", r"\?[^?]", r"&&", r"\|\|",
    ]
    if lang == "go":
        decision_patterns.extend([r"\bselect\b", r"\bdefer\b.*\bfunc\b"])
    elif lang == "rust":
        decision_patterns.extend([r"\bmatch\b", r"\bif\s+let\b", r"\bwhile\s+let\b", r"\bloop\b"])
    elif lang == "java":
        decision_patterns.extend([r"\binstanceof\b"])
    for pat in decision_patterns:
        cc += len(re.findall(pat, body))
    return cc


def _compute_max_nesting(body: str) -> int:
    """Compute maximum nesting depth by tracking brace levels."""
    max_depth = 0
    current = 0
    in_string = False
    string_char = None
    prev_char = None
    for ch in body:
        if in_string:
            if ch == string_char and prev_char != "\\":
                in_string = False
        else:
            if ch in ('"', "'", "`"):
                in_string = True
                string_char = ch
            elif ch == "{":
                current += 1
                max_depth = max(max_depth, current)
            elif ch == "}":
                current = max(0, current - 1)
        prev_char = ch
    return max(0, max_depth - 1)


def _compute_cognitive(body: str, lang: str) -> int:
    """Compute cognitive complexity (simplified heuristic)."""
    score = 0
    nesting = 0
    nesting_keywords = {"if", "for", "while", "switch", "catch"}
    if lang == "go":
        nesting_keywords.add("select")
    elif lang == "rust":
        nesting_keywords.update({"match", "loop"})
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
            continue
        for kw in nesting_keywords:
            matches = re.findall(rf"\b{kw}\b", stripped)
            for _ in matches:
                score += 1 + nesting
        for ch in stripped:
            if ch == "{":
                nesting += 1
            elif ch == "}":
                nesting = max(0, nesting - 1)
        score += stripped.count("&&")
        score += stripped.count("||")
    return score


def _extract_function_body(source: str, start_pos: int) -> tuple[str, int]:
    """Extract function body from opening { to matching }."""
    brace_pos = source.find("{", start_pos)
    if brace_pos == -1:
        return "", start_pos
    depth = 0
    i = brace_pos
    in_string = False
    string_char = None
    prev = None
    while i < len(source):
        ch = source[i]
        if in_string:
            if ch == string_char and prev != "\\":
                in_string = False
        else:
            if ch in ('"', "'", "`"):
                in_string = True
                string_char = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return source[brace_pos : i + 1], i
        prev = ch
        i += 1
    return source[brace_pos:], len(source)


def _line_number(source: str, pos: int) -> int:
    """Convert character position to 1-based line number."""
    return source[:pos].count("\n") + 1


# -- JavaScript / TypeScript adapter --

class JavaScriptAdapter(LanguageAdapter):
    """Adapter for JavaScript and TypeScript complexity analysis."""
    name = "javascript"
    extensions = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts")

    _FUNCTION_PATTERNS = [
        re.compile(
            r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)",
            re.MULTILINE,
        ),
        re.compile(
            r"(?:(?:public|private|protected|static|async|get|set|readonly|override)\s+)*"
            r"(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)\s*(?::\s*[^{]+?)?\s*\{",
            re.MULTILINE,
        ),
        re.compile(
            r"(?:const|let|var)\s+(\w+)\s*(?::\s*[^=]+?)?\s*=\s*"
            r"(?:async\s+)?(?:\(([^)]*)\)|\w+)\s*(?::\s*[^=]+?)?\s*=>\s*\{",
            re.MULTILINE,
        ),
    ]

    _SKIP_NAMES = frozenset({
        "if", "for", "while", "switch", "catch", "return",
        "import", "from", "class", "new", "throw", "else",
    })

    def find_functions(self, source: str, filepath: str) -> list[FunctionMetrics]:
        functions: list[FunctionMetrics] = []
        seen_lines: set[int] = set()
        for pattern in self._FUNCTION_PATTERNS:
            for match in pattern.finditer(source):
                pos = match.start()
                line = _line_number(source, pos)
                if line in seen_lines:
                    continue
                seen_lines.add(line)
                name = match.group(1)
                if name in self._SKIP_NAMES:
                    continue
                params_str = match.group(2) if match.lastindex >= 2 else ""
                params = len([p for p in (params_str or "").split(",") if p.strip()])
                body, end_pos = _extract_function_body(source, pos)
                if not body:
                    continue
                end_line = _line_number(source, end_pos)
                length = end_line - line + 1
                if length < 2:
                    continue
                functions.append(FunctionMetrics(
                    name=name, file=filepath, line=line, end_line=end_line,
                    length=length,
                    cyclomatic=_count_decision_points(body, "javascript"),
                    max_nesting=_compute_max_nesting(body),
                    cognitive=_compute_cognitive(body, "javascript"),
                    params=params,
                ))
        return functions


# -- Java adapter --

class JavaAdapter(LanguageAdapter):
    """Adapter for Java complexity analysis."""
    name = "java"
    extensions = (".java",)

    _METHOD_PATTERN = re.compile(
        r"(?:(?:public|private|protected|static|final|abstract|synchronized|native|default)\s+)*"
        r"(?:<[^>]+>\s+)?(?:\w+(?:\[\])?(?:<[^>]+>)?)\s+(\w+)\s*\(([^)]*)\)\s*"
        r"(?:throws\s+[\w\s,]+?)?\s*\{",
        re.MULTILINE,
    )

    _SKIP_NAMES = frozenset({
        "if", "for", "while", "switch", "catch", "class",
        "new", "return", "throw", "import", "package",
    })

    def find_functions(self, source: str, filepath: str) -> list[FunctionMetrics]:
        functions: list[FunctionMetrics] = []
        for match in self._METHOD_PATTERN.finditer(source):
            name = match.group(1)
            if name in self._SKIP_NAMES:
                continue
            pos = match.start()
            line = _line_number(source, pos)
            params_str = match.group(2)
            params = len([p for p in params_str.split(",") if p.strip()]) if params_str.strip() else 0
            body, end_pos = _extract_function_body(source, pos)
            if not body:
                continue
            end_line = _line_number(source, end_pos)
            length = end_line - line + 1
            if length < 2:
                continue
            functions.append(FunctionMetrics(
                name=name, file=filepath, line=line, end_line=end_line,
                length=length,
                cyclomatic=_count_decision_points(body, "java"),
                max_nesting=_compute_max_nesting(body),
                cognitive=_compute_cognitive(body, "java"),
                params=params,
            ))
        return functions


# -- Go adapter --

class GoAdapter(LanguageAdapter):
    """Adapter for Go complexity analysis."""
    name = "go"
    extensions = (".go",)

    _FUNC_PATTERN = re.compile(
        r"func\s+(?:\(\s*\w+\s+\*?\w+(?:\.\w+)?\s*\)\s+)?(\w+)\s*\(([^)]*)\)",
        re.MULTILINE,
    )

    def find_functions(self, source: str, filepath: str) -> list[FunctionMetrics]:
        functions: list[FunctionMetrics] = []
        for match in self._FUNC_PATTERN.finditer(source):
            name = match.group(1)
            pos = match.start()
            line = _line_number(source, pos)
            params_str = match.group(2)
            params = len([p for p in params_str.split(",") if p.strip()]) if params_str.strip() else 0
            body, end_pos = _extract_function_body(source, pos)
            if not body:
                continue
            end_line = _line_number(source, end_pos)
            length = end_line - line + 1
            if length < 2:
                continue
            functions.append(FunctionMetrics(
                name=name, file=filepath, line=line, end_line=end_line,
                length=length,
                cyclomatic=_count_decision_points(body, "go"),
                max_nesting=_compute_max_nesting(body),
                cognitive=_compute_cognitive(body, "go"),
                params=params,
            ))
        return functions


# -- Rust adapter --

class RustAdapter(LanguageAdapter):
    """Adapter for Rust complexity analysis."""
    name = "rust"
    extensions = (".rs",)

    _FN_PATTERN = re.compile(
        r"(?:pub(?:\([\w:]+\))?\s+)?(?:async\s+)?"
        r"fn\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)",
        re.MULTILINE,
    )

    def find_functions(self, source: str, filepath: str) -> list[FunctionMetrics]:
        functions: list[FunctionMetrics] = []
        for match in self._FN_PATTERN.finditer(source):
            name = match.group(1)
            pos = match.start()
            line = _line_number(source, pos)
            params_str = match.group(2)
            parts = [p.strip() for p in params_str.split(",") if p.strip()]
            params = len([p for p in parts if p not in ("self", "&self", "&mut self")])
            body, end_pos = _extract_function_body(source, pos)
            if not body:
                continue
            end_line = _line_number(source, end_pos)
            length = end_line - line + 1
            if length < 2:
                continue
            functions.append(FunctionMetrics(
                name=name, file=filepath, line=line, end_line=end_line,
                length=length,
                cyclomatic=_count_decision_points(body, "rust"),
                max_nesting=_compute_max_nesting(body),
                cognitive=_compute_cognitive(body, "rust"),
                params=params,
            ))
        return functions


# -- Adapter registry --

_ADAPTERS: dict[str, LanguageAdapter] = {
    "javascript": JavaScriptAdapter(),
    "typescript": JavaScriptAdapter(),
    "java": JavaAdapter(),
    "go": GoAdapter(),
    "rust": RustAdapter(),
}


def get_adapter(filepath: str) -> LanguageAdapter | None:
    """Return the appropriate adapter for a file, or None."""
    from pathlib import Path
    ext = Path(filepath).suffix.lower()
    lang = SUPPORTED_EXTENSIONS.get(ext)
    if lang:
        return _ADAPTERS.get(lang)
    return None


def detect_language(filepath: str) -> str | None:
    """Detect language from file extension."""
    from pathlib import Path
    ext = Path(filepath).suffix.lower()
    return SUPPORTED_EXTENSIONS.get(ext)


def analyze_file_multilang(filepath: str, content: str | None = None) -> FileComplexity:
    """Analyze a file using the appropriate language adapter.

    Falls back to Python AST for .py, or basic line counting for unknown.
    """
    from pathlib import Path as P
    if content is None:
        content = P(filepath).read_text(errors="ignore")
    if filepath.endswith(".py"):
        from devlens.complexity import analyze_file
        return analyze_file(filepath, content)
    adapter = get_adapter(filepath)
    if adapter:
        return adapter.analyze(content, filepath)
    lines = content.splitlines()
    total = len(lines)
    code = sum(1 for ln in lines if ln.strip() and not ln.strip().startswith(("//", "#", "/*", "*")))
    return FileComplexity(file=filepath, total_lines=total, code_lines=code, functions=[])
