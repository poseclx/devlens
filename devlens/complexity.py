"""Complexity analyzer — measures code complexity metrics using Python AST.

Analyzes:
  - Cyclomatic complexity (decision points per function)
  - Function length (lines of code)
  - Nesting depth (max indentation level)
  - Cognitive complexity (weighted by nesting)
  - Module-level summary with grade

Supports Python files. Other languages use heuristic line-counting.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FunctionMetrics:
    """Metrics for a single function or method."""
    name: str
    file: str
    line: int
    end_line: int
    length: int                    # lines of code
    cyclomatic: int = 1            # cyclomatic complexity (starts at 1)
    max_nesting: int = 0           # deepest nesting level
    cognitive: int = 0             # cognitive complexity score
    params: int = 0                # number of parameters

    @property
    def risk(self) -> str:
        if self.cyclomatic > 20 or self.length > 100 or self.max_nesting > 6:
            return "high"
        if self.cyclomatic > 10 or self.length > 50 or self.max_nesting > 4:
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
        return max((f.cyclomatic for f in self.functions), default=0)

    @property
    def high_risk_functions(self) -> list[FunctionMetrics]:
        return [f for f in self.functions if f.risk == "high"]

    @property
    def medium_risk_functions(self) -> list[FunctionMetrics]:
        return [f for f in self.functions if f.risk == "medium"]


@dataclass 
class ComplexityReport:
    """Aggregate complexity report for a project or PR."""
    files: list[FileComplexity] = field(default_factory=list)

    @property
    def total_functions(self) -> int:
        return sum(len(f.functions) for f in self.files)

    @property
    def high_risk_count(self) -> int:
        return sum(len(f.high_risk_functions) for f in self.files)

    @property
    def medium_risk_count(self) -> int:
        return sum(len(f.medium_risk_functions) for f in self.files)

    @property
    def avg_cyclomatic(self) -> float:
        all_funcs = [fn for f in self.files for fn in f.functions]
        if not all_funcs:
            return 0.0
        return sum(fn.cyclomatic for fn in all_funcs) / len(all_funcs)

    @property
    def grade(self) -> str:
        avg = self.avg_cyclomatic
        high = self.high_risk_count
        if avg <= 5 and high == 0:
            return "A"
        if avg <= 10 and high <= 2:
            return "B"
        if avg <= 20 and high <= 5:
            return "C"
        if avg <= 25:
            return "D"
        return "F"

    @property
    def score(self) -> int:
        """0-100 complexity score (higher = simpler code)."""
        avg = self.avg_cyclomatic
        high = self.high_risk_count
        base = max(0, 100 - avg * 4)
        penalty = high * 5
        return max(0, min(100, int(base - penalty)))

    def to_dict(self) -> dict:
        return {
            "grade": self.grade,
            "score": self.score,
            "total_functions": self.total_functions,
            "high_risk": self.high_risk_count,
            "medium_risk": self.medium_risk_count,
            "avg_cyclomatic": round(self.avg_cyclomatic, 1),
            "files": [
                {
                    "file": f.file,
                    "total_lines": f.total_lines,
                    "code_lines": f.code_lines,
                    "functions": [fn.to_dict() for fn in f.functions],
                }
                for f in self.files
            ],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Complexity Report — Grade: {self.grade} ({self.score}/100)\n",
            f"**Functions:** {self.total_functions} | "
            f"**Avg Complexity:** {self.avg_cyclomatic:.1f} | "
            f"**High Risk:** {self.high_risk_count} | "
            f"**Medium Risk:** {self.medium_risk_count}\n",
        ]

        # High risk functions table
        high_risk = [(fn, f.file) for f in self.files for fn in f.high_risk_functions]
        if high_risk:
            lines.append("## High Risk Functions\n")
            lines.append("| Function | File | Cyclomatic | Length | Nesting |")
            lines.append("|----------|------|-----------|--------|---------|")
            for fn, _ in sorted(high_risk, key=lambda x: x[0].cyclomatic, reverse=True):
                lines.append(
                    f"| `{fn.name}` | `{fn.file}:{fn.line}` | "
                    f"{fn.cyclomatic} | {fn.length} | {fn.max_nesting} |"
                )
            lines.append("")

        # Medium risk
        med_risk = [(fn, f.file) for f in self.files for fn in f.medium_risk_functions]
        if med_risk:
            lines.append("## Medium Risk Functions\n")
            lines.append("| Function | File | Cyclomatic | Length | Nesting |")
            lines.append("|----------|------|-----------|--------|---------|")
            for fn, _ in sorted(med_risk, key=lambda x: x[0].cyclomatic, reverse=True)[:15]:
                lines.append(
                    f"| `{fn.name}` | `{fn.file}:{fn.line}` | "
                    f"{fn.cyclomatic} | {fn.length} | {fn.max_nesting} |"
                )
            if len(med_risk) > 15:
                lines.append(f"| ... | +{len(med_risk) - 15} more | | | |")
            lines.append("")

        return "\n".join(lines)


# ── AST-based Python analysis ────────────────────────────────

# Nodes that add a decision point (cyclomatic complexity)
_DECISION_NODES = (
    ast.If, ast.IfExp,                    # if / ternary
    ast.For, ast.AsyncFor,                # for loops
    ast.While,                            # while loops
    ast.ExceptHandler,                    # except clauses
    ast.With, ast.AsyncWith,              # context managers
    ast.Assert,                           # assertions
    ast.BoolOp,                           # and/or chains
)


class _ComplexityVisitor(ast.NodeVisitor):
    """AST visitor that computes complexity metrics for each function."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.functions: list[FunctionMetrics] = []
        self._current_nesting = 0

    def _analyze_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionMetrics:
        name = node.name
        line = node.lineno
        end_line = node.end_lineno or line
        length = end_line - line + 1

        # Count parameters
        args = node.args
        params = (
            len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
            + (1 if args.vararg else 0) + (1 if args.kwarg else 0)
        )
        # Subtract 'self' / 'cls'
        if args.args and args.args[0].arg in ("self", "cls"):
            params -= 1

        # Cyclomatic complexity
        cyclomatic = 1  # base complexity
        for child in ast.walk(node):
            if isinstance(child, _DECISION_NODES):
                cyclomatic += 1
            # Count boolean operators (each 'and'/'or' adds a path)
            if isinstance(child, ast.BoolOp):
                cyclomatic += len(child.values) - 1

        # Max nesting depth
        max_nesting = self._compute_nesting(node)

        # Cognitive complexity (simplified: cyclomatic * nesting weight)
        cognitive = self._compute_cognitive(node)

        return FunctionMetrics(
            name=name, file=self.filepath, line=line, end_line=end_line,
            length=length, cyclomatic=cyclomatic, max_nesting=max_nesting,
            cognitive=cognitive, params=params,
        )

    def _compute_nesting(self, node: ast.AST, depth: int = 0) -> int:
        """Compute maximum nesting depth inside a function."""
        max_depth = depth
        nesting_nodes = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With,
                         ast.AsyncWith, ast.Try, ast.ExceptHandler)

        for child in ast.iter_child_nodes(node):
            if isinstance(child, nesting_nodes):
                child_depth = self._compute_nesting(child, depth + 1)
                max_depth = max(max_depth, child_depth)
            else:
                child_depth = self._compute_nesting(child, depth)
                max_depth = max(max_depth, child_depth)

        return max_depth

    def _compute_cognitive(self, node: ast.AST, nesting: int = 0) -> int:
        """Compute cognitive complexity (increments weighted by nesting)."""
        total = 0
        incrementing = (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While,
                        ast.ExceptHandler)

        for child in ast.iter_child_nodes(node):
            if isinstance(child, incrementing):
                total += 1 + nesting  # base increment + nesting penalty
                total += self._compute_cognitive(child, nesting + 1)
            elif isinstance(child, ast.BoolOp):
                total += 1  # each boolean sequence
                total += self._compute_cognitive(child, nesting)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Nested function — adds structural complexity
                total += 1 + nesting
                total += self._compute_cognitive(child, nesting + 1)
            else:
                total += self._compute_cognitive(child, nesting)

        return total

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(self._analyze_function(node))
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.functions.append(self._analyze_function(node))
        self.generic_visit(node)


def analyze_file(filepath: str, content: str | None = None) -> FileComplexity:
    """Analyze complexity of a single Python file."""
    if content is None:
        content = Path(filepath).read_text(errors="ignore")

    total_lines = len(content.splitlines())
    code_lines = sum(
        1 for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )

    functions: list[FunctionMetrics] = []

    if filepath.endswith(".py"):
        try:
            tree = ast.parse(content)
            visitor = _ComplexityVisitor(filepath)
            visitor.visit(tree)
            functions = visitor.functions
        except SyntaxError:
            pass  # Can't parse — skip function analysis
    else:
        # Heuristic for non-Python files: count lines only
        pass

    return FileComplexity(
        file=filepath,
        total_lines=total_lines,
        code_lines=code_lines,
        functions=functions,
    )


def analyze_path(
    path: str,
    *,
    extensions: tuple[str, ...] = (".py",),
    ignore_patterns: list[str] | None = None,
) -> ComplexityReport:
    """Analyze complexity of all matching files in a directory."""
    import re

    root = Path(path)
    files: list[FileComplexity] = []
    ignore = ignore_patterns or []

    if root.is_file():
        return ComplexityReport(files=[analyze_file(str(root))])

    for fp in sorted(root.rglob("*")):
        if not fp.is_file():
            continue
        if not any(fp.name.endswith(ext) for ext in extensions):
            continue
        
        rel = str(fp.relative_to(root))
        
        # Skip common non-source dirs
        skip_dirs = {"__pycache__", ".git", "node_modules", ".venv", "venv", 
                     "dist", "build", ".egg-info", ".tox", ".mypy_cache"}
        if any(part in skip_dirs for part in fp.parts):
            continue

        if ignore and any(re.search(p, rel) for p in ignore):
            continue

        if fp.stat().st_size > 500_000:
            continue

        try:
            fc = analyze_file(rel, fp.read_text(errors="ignore"))
            files.append(fc)
        except Exception:
            continue

    return ComplexityReport(files=files)


def analyze_pr_complexity(pr_data, extensions: tuple[str, ...] = (".py",)) -> ComplexityReport:
    """Analyze complexity of files changed in a PR.
    
    Only looks at the final state of changed files (added lines).
    """
    files: list[FileComplexity] = []

    for f in pr_data.files:
        filename = f["filename"]
        if not any(filename.endswith(ext) for ext in extensions):
            continue

        patch = f.get("patch", "")
        if not patch:
            continue

        # Extract only added lines to form pseudo-file content
        added_lines = []
        for line in patch.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                added_lines.append(line[1:])

        if not added_lines:
            continue

        content = "\n".join(added_lines)
        
        # Try to analyze as Python
        if filename.endswith(".py"):
            try:
                fc = analyze_file(filename, content)
                files.append(fc)
            except Exception:
                continue

    return ComplexityReport(files=files)
