"""AST-based complexity analysis for Python source files."""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devlens.github import PRData


@dataclass
class FunctionMetrics:
    """Metrics for a single function or method."""
    name: str
    file: str
    line: int
    end_line: int
    length: int
    cyclomatic: int
    max_nesting: int
    cognitive: int = 0
    params: int = 0

    @property
    def risk(self) -> str:
        if self.cyclomatic > 20 or self.length > 100 or self.max_nesting >= 7:
            return "high"
        if self.cyclomatic > 10 or self.length > 50 or self.max_nesting >= 5:
            return "medium"
        return "low"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "file": self.file,
            "line": self.line,
            "length": self.length,
            "cyclomatic": self.cyclomatic,
            "max_nesting": self.max_nesting,
            "cognitive": self.cognitive,
            "params": self.params,
            "risk": self.risk,
        }


@dataclass
class FileComplexity:
    """Complexity metrics for a single file."""
    file: str
    total_lines: int
    code_lines: int
    functions: list[FunctionMetrics] = field(default_factory=list)

    @property
    def avg_cyclomatic(self) -> float:
        if not self.functions:
            return 0.0
        return sum(f.cyclomatic for f in self.functions) / len(self.functions)

    @property
    def max_cyclomatic(self) -> int:
        if not self.functions:
            return 0
        return max(f.cyclomatic for f in self.functions)

    @property
    def high_risk_functions(self) -> list[FunctionMetrics]:
        return [f for f in self.functions if f.risk == "high"]

    @property
    def medium_risk_functions(self) -> list[FunctionMetrics]:
        return [f for f in self.functions if f.risk == "medium"]


@dataclass
class ComplexityReport:
    """Aggregated complexity report across multiple files."""
    files: list[FileComplexity] = field(default_factory=list)

    @property
    def _all_functions(self) -> list[FunctionMetrics]:
        funcs: list[FunctionMetrics] = []
        for fc in self.files:
            funcs.extend(fc.functions)
        return funcs

    @property
    def total_functions(self) -> int:
        return len(self._all_functions)

    @property
    def high_risk_count(self) -> int:
        return sum(1 for f in self._all_functions if f.risk == "high")

    @property
    def medium_risk_count(self) -> int:
        return sum(1 for f in self._all_functions if f.risk == "medium")

    @property
    def avg_cyclomatic(self) -> float:
        funcs = self._all_functions
        if not funcs:
            return 0.0
        return sum(f.cyclomatic for f in funcs) / len(funcs)

    @property
    def score(self) -> int:
        """Compute a 0-100 quality score."""
        if not self._all_functions:
            return 100
        funcs = self._all_functions
        avg_cc = self.avg_cyclomatic
        high_pct = self.high_risk_count / len(funcs)
        med_pct = self.medium_risk_count / len(funcs)

        s = 100.0
        s -= avg_cc * 1.5
        s -= high_pct * 5
        s -= med_pct * 2
        s -= max(0, avg_cc - 20) ** 2 * 0.6
        return max(0, min(100, int(s)))

    @property
    def grade(self) -> str:
        s = self.score
        if s >= 90:
            return "A"
        if s >= 80:
            return "B"
        if s >= 70:
            return "C"
        if s >= 60:
            return "D"
        return "F"

    def to_dict(self) -> dict:
        return {
            "grade": self.grade,
            "score": self.score,
            "total_functions": self.total_functions,
            "files": [
                {
                    "file": fc.file,
                    "total_lines": fc.total_lines,
                    "code_lines": fc.code_lines,
                    "functions": [fn.to_dict() for fn in fc.functions],
                }
                for fc in self.files
            ],
        }

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# Complexity Report")
        lines.append("")
        lines.append(f"**Grade:** {self.grade} | **Score:** {self.score}/100")
        lines.append(f"**Total functions:** {self.total_functions}")
        lines.append("")

        high = [f for f in self._all_functions if f.risk == "high"]
        if high:
            lines.append("## High Risk Functions")
            lines.append("")
            lines.append("| Function | File | Cyclomatic | Length | Nesting |")
            lines.append("|----------|------|-----------|--------|---------|")
            for fn in high:
                lines.append(
                    f"| {fn.name} | {fn.file} | {fn.cyclomatic} | {fn.length} | {fn.max_nesting} |"
                )
            lines.append("")

        medium = [f for f in self._all_functions if f.risk == "medium"]
        if medium:
            lines.append("## Medium Risk Functions")
            lines.append("")
            lines.append("| Function | File | Cyclomatic | Length | Nesting |")
            lines.append("|----------|------|-----------|--------|---------|")
            for fn in medium:
                lines.append(
                    f"| {fn.name} | {fn.file} | {fn.cyclomatic} | {fn.length} | {fn.max_nesting} |"
                )
            lines.append("")

        return "\n".join(lines)


# ── AST analysis helpers ─────────────────────────────────────


class _CyclomaticVisitor(ast.NodeVisitor):
    def __init__(self):
        self.complexity = 1

    def visit_If(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_While(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_With(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        self.complexity += len(node.values) - 1
        self.generic_visit(node)

    def visit_IfExp(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_comprehension(self, node):
        self.complexity += 1
        self.complexity += len(node.ifs)
        self.generic_visit(node)

    def visit_Assert(self, node):
        self.complexity += 1
        self.generic_visit(node)


class _NestingVisitor(ast.NodeVisitor):
    def __init__(self):
        self.max_nesting = 0
        self._current = 0

    def _visit_nesting(self, node):
        self._current += 1
        self.max_nesting = max(self.max_nesting, self._current)
        self.generic_visit(node)
        self._current -= 1

    def visit_If(self, node):
        self._visit_nesting(node)

    def visit_For(self, node):
        self._visit_nesting(node)

    def visit_While(self, node):
        self._visit_nesting(node)

    def visit_With(self, node):
        self._visit_nesting(node)

    def visit_Try(self, node):
        self._visit_nesting(node)

    def visit_ExceptHandler(self, node):
        self._visit_nesting(node)


def _count_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    args = node.args
    all_args = list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs)
    count = len(all_args)
    if all_args and all_args[0].arg in ("self", "cls"):
        count -= 1
    if args.vararg:
        count += 1
    if args.kwarg:
        count += 1
    return count


def _analyze_function(node: ast.FunctionDef | ast.AsyncFunctionDef, filepath: str) -> FunctionMetrics:
    cc_visitor = _CyclomaticVisitor()
    cc_visitor.visit(node)
    nesting_visitor = _NestingVisitor()
    nesting_visitor.visit(node)
    end_line = node.end_lineno or node.lineno
    length = end_line - node.lineno + 1
    return FunctionMetrics(
        name=node.name,
        file=filepath,
        line=node.lineno,
        end_line=end_line,
        length=length,
        cyclomatic=cc_visitor.complexity,
        max_nesting=nesting_visitor.max_nesting,
        cognitive=0,
        params=_count_params(node),
    )


def analyze_file(path: str, content: str | None = None) -> FileComplexity:
    """Analyze a single file for complexity."""
    if content is None:
        try:
            content = Path(path).read_text(errors="ignore")
        except (OSError, IOError):
            return FileComplexity(file=path, total_lines=0, code_lines=0, functions=[])

    if not content:
        return FileComplexity(file=path, total_lines=0, code_lines=0, functions=[])

    lines = content.splitlines()
    total_lines = len(lines)
    code_lines = sum(
        1 for line in lines
        if line.strip() and not line.strip().startswith("#")
    )

    if not path.endswith(".py"):
        return FileComplexity(file=path, total_lines=total_lines, code_lines=code_lines, functions=[])

    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError:
        return FileComplexity(file=path, total_lines=total_lines, code_lines=code_lines, functions=[])

    functions: list[FunctionMetrics] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_analyze_function(node, path))

    return FileComplexity(
        file=path, total_lines=total_lines, code_lines=code_lines, functions=functions,
    )


def analyze_path(
    path: str,
    extensions: tuple[str, ...] | None = None,
    ignore_patterns: list[str] | None = None,
) -> ComplexityReport:
    """Analyze a file or directory for complexity."""
    p = Path(path)
    if extensions is None:
        extensions = (".py",)

    if p.is_file():
        fc = analyze_file(str(p))
        return ComplexityReport(files=[fc])

    files: list[FileComplexity] = []
    for root, dirs, filenames in os.walk(str(p)):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
        for fname in filenames:
            fpath = os.path.join(root, fname)
            if not any(fname.endswith(ext) for ext in extensions):
                continue
            if ignore_patterns:
                skip = False
                for pattern in ignore_patterns:
                    if re.search(pattern, fpath):
                        skip = True
                        break
                if skip:
                    continue
            fc = analyze_file(fpath)
            files.append(fc)

    return ComplexityReport(files=files)


def analyze_pr_complexity(pr_data: "PRData") -> ComplexityReport:
    """Analyze complexity of changed files in a PR."""
    file_results: list[FileComplexity] = []

    for f in pr_data.files:
        filename = f.get("filename", "") if isinstance(f, dict) else getattr(f, "filename", "")
        patch = f.get("patch", "") if isinstance(f, dict) else getattr(f, "patch", "")

        if not filename.endswith(".py"):
            continue
        if not patch or not patch.strip():
            continue

        added_lines = []
        for line in patch.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added_lines.append(line[1:])

        if not added_lines:
            continue

        content = "\n".join(added_lines)
        fc = analyze_file(filename, content=content)
        if fc.total_lines > 0:
            file_results.append(fc)

    return ComplexityReport(files=file_results)
