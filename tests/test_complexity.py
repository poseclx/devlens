# tests/test_complexity.py
"""Tests for devlens.complexity — AST-based complexity analysis."""

from __future__ import annotations

import textwrap
from devlens.complexity import (
    FunctionMetrics,
    FileComplexity,
    ComplexityReport,
    analyze_file,
    analyze_path,
    analyze_pr_complexity,
)


# ── FunctionMetrics ──────────────────────────────────────────

class TestFunctionMetrics:
    """FunctionMetrics dataclass and risk property."""
    def _make(self, **kw):
        defaults = dict(
            name="fn", file="test.py", line=1, end_line=5,
            length=5, cyclomatic=1, max_nesting=0, cognitive=0, params=0,
        )
        defaults.update(kw)
        return FunctionMetrics(**defaults)

    def test_low_risk_defaults(self):
        m = self._make()
        assert m.risk == "low"

    def test_medium_risk_cyclomatic(self):
        m = self._make(cyclomatic=11)
        assert m.risk == "medium"

    def test_medium_risk_length(self):
        m = self._make(length=51)
        assert m.risk == "medium"

    def test_medium_risk_nesting(self):
        m = self._make(max_nesting=5)
        assert m.risk == "medium"

    def test_high_risk_cyclomatic(self):
        m = self._make(cyclomatic=21)
        assert m.risk == "high"

    def test_high_risk_length(self):
        m = self._make(length=101)
        assert m.risk == "high"

    def test_high_risk_nesting(self):
        m = self._make(max_nesting=7)
        assert m.risk == "high"

    def test_boundary_medium_cyclomatic_10_is_low(self):
        m = self._make(cyclomatic=10)
        assert m.risk == "low"

    def test_boundary_high_cyclomatic_20_is_medium(self):
        m = self._make(cyclomatic=20)
        assert m.risk == "medium"

    def test_to_dict_contains_all_fields(self):
        m = self._make(name="calc", cyclomatic=5, params=3)
        d = m.to_dict()
        assert d["name"] == "calc"
        assert d["cyclomatic"] == 5
        assert d["params"] == 3
        assert d["risk"] == "low"
        assert set(d.keys()) == {
            "name", "file", "line", "length",
            "cyclomatic", "max_nesting", "cognitive", "params", "risk",
        }


# ── FileComplexity ───────────────────────────────────────────

class TestFileComplexity:
    """FileComplexity aggregate properties."""

    def _make_fn(self, **kw):
        defaults = dict(
            name="fn", file="f.py", line=1, end_line=5,
            length=5, cyclomatic=1, max_nesting=0, cognitive=0, params=0,
        )
        defaults.update(kw)
        return FunctionMetrics(**defaults)

    def test_avg_cyclomatic_no_functions(self):
        fc = FileComplexity(file="empty.py", total_lines=0, code_lines=0)
        assert fc.avg_cyclomatic == 0.0

    def test_avg_cyclomatic_single(self):
        fc = FileComplexity(
            file="a.py", total_lines=10, code_lines=8,
            functions=[self._make_fn(cyclomatic=6)],
        )
        assert fc.avg_cyclomatic == 6.0

    def test_avg_cyclomatic_multiple(self):
        fc = FileComplexity(
            file="a.py", total_lines=20, code_lines=15,
            functions=[
                self._make_fn(cyclomatic=4),
                self._make_fn(cyclomatic=8),
            ],
        )
        assert fc.avg_cyclomatic == 6.0

    def test_max_cyclomatic(self):
        fc = FileComplexity(
            file="a.py", total_lines=20, code_lines=15,
            functions=[
                self._make_fn(cyclomatic=3),
                self._make_fn(cyclomatic=15),
                self._make_fn(cyclomatic=7),
            ],
        )
        assert fc.max_cyclomatic == 15

    def test_max_cyclomatic_empty(self):
        fc = FileComplexity(file="a.py", total_lines=0, code_lines=0)
        assert fc.max_cyclomatic == 0

    def test_high_risk_functions(self):
        fc = FileComplexity(
            file="a.py", total_lines=100, code_lines=80,
            functions=[
                self._make_fn(name="safe", cyclomatic=2),
                self._make_fn(name="danger", cyclomatic=25),
                self._make_fn(name="mid", cyclomatic=12),
            ],
        )
        high = fc.high_risk_functions
        assert len(high) == 1
        assert high[0].name == "danger"

    def test_medium_risk_functions(self):
        fc = FileComplexity(
            file="a.py", total_lines=100, code_lines=80,
            functions=[
                self._make_fn(name="safe", cyclomatic=2),
                self._make_fn(name="danger", cyclomatic=25),
                self._make_fn(name="mid", cyclomatic=12),
            ],
        )
        med = fc.medium_risk_functions
        assert len(med) == 1
        assert med[0].name == "mid"


# ── ComplexityReport ─────────────────────────────────────────

class TestComplexityReport:
    """ComplexityReport aggregate metrics and grading."""

    def _make_fn(self, **kw):
        defaults = dict(
            name="fn", file="f.py", line=1, end_line=5,
            length=5, cyclomatic=1, max_nesting=0, cognitive=0, params=0,
        )
        defaults.update(kw)
        return FunctionMetrics(**defaults)

    def _make_report(self, func_specs):
        """Build report from list of (name, cyclomatic, length, nesting) tuples."""
        funcs = []
        for name, cyc, length, nesting in func_specs:
            funcs.append(self._make_fn(
                name=name, cyclomatic=cyc, length=length, max_nesting=nesting,
            ))
        fc = FileComplexity(
            file="test.py", total_lines=100, code_lines=80, functions=funcs,
        )
        return ComplexityReport(files=[fc])

    def test_empty_report(self):
        r = ComplexityReport()
        assert r.total_functions == 0
        assert r.high_risk_count == 0
        assert r.medium_risk_count == 0
        assert r.avg_cyclomatic == 0.0

    def test_grade_a(self):
        r = self._make_report([
            ("fn1", 3, 10, 1),
            ("fn2", 2, 8, 0),
        ])
        assert r.grade == "A"

    def test_grade_b(self):
        r = self._make_report([
            ("fn1", 8, 20, 2),
            ("fn2", 7, 15, 1),
        ])
        assert r.grade == "B"

    def test_grade_c(self):
        r = self._make_report([
            ("fn1", 14, 30, 3),
            ("fn2", 12, 25, 2),
            ("fn3", 22, 110, 7),
        ])
        assert r.grade == "C"

    def test_grade_d(self):
        r = self._make_report([
            ("fn1", 18, 40, 3),
            ("fn2", 22, 110, 7),
            ("fn3", 22, 110, 7),
            ("fn4", 22, 110, 7),
            ("fn5", 22, 110, 7),
            ("fn6", 22, 110, 7),
            ("fn7", 22, 110, 7),
        ])
        assert r.grade == "D"

    def test_grade_f(self):
        r = self._make_report([
            ("fn1", 25, 110, 7),
            ("fn2", 30, 120, 8),
        ])
        assert r.grade == "F"

    def test_score_simple_code(self):
        r = self._make_report([
            ("fn1", 2, 5, 0),
            ("fn2", 1, 3, 0),
        ])
        assert r.score >= 90

    def test_score_complex_code(self):
        r = self._make_report([
            ("fn1", 25, 110, 7),
            ("fn2", 30, 120, 8),
        ])
        assert r.score <= 20

    def test_score_clamped_0_100(self):
        r = self._make_report([
            (f"fn{i}", 50, 200, 10) for i in range(20)
        ])
        assert 0 <= r.score <= 100

    def test_total_functions(self):
        r = self._make_report([
            ("fn1", 1, 5, 0),
            ("fn2", 2, 8, 0),
            ("fn3", 3, 10, 1),
        ])
        assert r.total_functions == 3

    def test_to_dict_structure(self):
        r = self._make_report([("fn1", 5, 10, 1)])
        d = r.to_dict()
        assert "grade" in d
        assert "score" in d
        assert "total_functions" in d
        assert "files" in d
        assert len(d["files"]) == 1
        assert len(d["files"][0]["functions"]) == 1

    def test_to_markdown_contains_grade(self):
        r = self._make_report([("fn1", 3, 10, 1)])
        md = r.to_markdown()
        assert "Grade:" in md
        assert "A" in md

    def test_to_markdown_high_risk_table(self):
        r = self._make_report([
            ("dangerous", 25, 110, 7),
        ])
        md = r.to_markdown()
        assert "High Risk Functions" in md
        assert "dangerous" in md

    def test_to_markdown_medium_risk_table(self):
        r = self._make_report([
            ("moderate", 12, 40, 3),
        ])
        md = r.to_markdown()
        assert "Medium Risk Functions" in md
        assert "moderate" in md


# ── analyze_file ─────────────────────────────────────────────

class TestAnalyzeFile:
    """analyze_file() function tests."""

    def test_simple_python(self, tmp_python_file):
        fc = analyze_file(str(tmp_python_file))
        assert fc.file == str(tmp_python_file)
        assert fc.total_lines > 0
        assert fc.code_lines > 0
        assert len(fc.functions) >= 2

    def test_complex_python(self, tmp_complex_python_file):
        fc = analyze_file(str(tmp_complex_python_file))
        names = [fn.name for fn in fc.functions]
        assert "process_data" in names
        assert "simple_func" in names

    def test_complex_function_high_cyclomatic(self, tmp_complex_python_file):
        fc = analyze_file(str(tmp_complex_python_file))
        process = next(fn for fn in fc.functions if fn.name == "process_data")
        assert process.cyclomatic > 5

    def test_complex_function_nesting(self, tmp_complex_python_file):
        fc = analyze_file(str(tmp_complex_python_file))
        process = next(fn for fn in fc.functions if fn.name == "process_data")
        assert process.max_nesting >= 3

    def test_simple_function_low_complexity(self, tmp_python_file):
        fc = analyze_file(str(tmp_python_file))
        for fn in fc.functions:
            assert fn.cyclomatic <= 3
            assert fn.risk == "low"

    def test_content_parameter(self):
        code = textwrap.dedent("""\
            def greet(name):
                return f"Hi, {name}"
        """)
        fc = analyze_file("virtual.py", content=code)
        assert len(fc.functions) == 1
        assert fc.functions[0].name == "greet"

    def test_syntax_error_graceful(self):
        bad_code = "def broken(:\n    pass"
        fc = analyze_file("bad.py", content=bad_code)
        assert fc.total_lines > 0
        assert fc.functions == []

    def test_non_python_file(self, tmp_path):
        js = tmp_path / "app.js"
        js.write_text("function hello() { return 42; }\nconsole.log(hello());\n")
        fc = analyze_file(str(js))
        assert fc.total_lines == 2
        assert fc.functions == []

    def test_param_count_excludes_self(self):
        code = textwrap.dedent("""\
            class Foo:
                def method(self, x, y):
                    return x + y
        """)
        fc = analyze_file("cls.py", content=code)
        method = next(fn for fn in fc.functions if fn.name == "method")
        assert method.params == 2

    def test_async_function_detected(self):
        code = textwrap.dedent("""\
            import asyncio
            async def fetch(url):
                await asyncio.sleep(1)
                return url
        """)
        fc = analyze_file("async.py", content=code)
        assert len(fc.functions) == 1
        assert fc.functions[0].name == "fetch"

    def test_empty_file(self):
        fc = analyze_file("empty.py", content="")
        assert fc.total_lines == 0
        assert fc.code_lines == 0
        assert fc.functions == []

    def test_comments_not_counted_as_code(self):
        code = "# comment\n# another\npass\n"
        fc = analyze_file("comments.py", content=code)
        assert fc.total_lines == 3
        assert fc.code_lines == 1


# ── analyze_path ─────────────────────────────────────────────

class TestAnalyzePath:
    """analyze_path() directory scanning."""

    def test_single_file(self, tmp_python_file):
        report = analyze_path(str(tmp_python_file))
        assert len(report.files) == 1

    def test_project_directory(self, tmp_python_project):
        report = analyze_path(str(tmp_python_project))
        py_files = [f.file for f in report.files]
        assert len(py_files) >= 2

    def test_skips_pycache(self, tmp_python_project):
        cache_dir = tmp_python_project / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "cached.py").write_text("x = 1\n")
        report = analyze_path(str(tmp_python_project))
        files = [f.file for f in report.files]
        assert not any("__pycache__" in f for f in files)

    def test_extensions_filter(self, tmp_python_project):
        (tmp_python_project / "src" / "app.js").write_text("var x = 1;\n")
        report = analyze_path(str(tmp_python_project), extensions=(".py",))
        files = [f.file for f in report.files]
        assert not any(f.endswith(".js") for f in files)

    def test_ignore_patterns(self, tmp_python_project):
        report = analyze_path(
            str(tmp_python_project),
            ignore_patterns=[r"utils\.py$"],
        )
        files = [f.file for f in report.files]
        assert not any("utils.py" in f for f in files)

    def test_report_grade(self, tmp_python_project):
        report = analyze_path(str(tmp_python_project))
        assert report.grade in ("A", "B", "C", "D", "F")


# ── analyze_pr_complexity ────────────────────────────────────

class TestAnalyzePrComplexity:
    """analyze_pr_complexity() from PR patch data."""

    def test_basic_pr(self, sample_pr_data):
        report = analyze_pr_complexity(sample_pr_data)
        assert isinstance(report, ComplexityReport)

    def test_only_python_files(self, sample_pr_data):
        report = analyze_pr_complexity(sample_pr_data)
        files = [f.file for f in report.files]
        assert not any(f.endswith(".txt") for f in files)

    def test_extracts_added_lines(self, sample_pr_data):
        report = analyze_pr_complexity(sample_pr_data)
        py_files = [f for f in report.files if f.file.endswith(".py")]
        assert len(py_files) >= 1

    def test_empty_patch_skipped(self, sample_pr_data):
        sample_pr_data.files.append({
            "filename": "empty.py",
            "status": "modified",
            "additions": 0,
            "deletions": 0,
            "patch": "",
        })
        report = analyze_pr_complexity(sample_pr_data)
        files = [f.file for f in report.files]
        assert "empty.py" not in files
