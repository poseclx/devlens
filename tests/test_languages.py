# tests/test_languages.py
"""Tests for devlens.languages — multi-language complexity adapters."""
import pytest
from devlens.languages import (
    LanguageAdapter,
    JavaScriptAdapter,
    JavaAdapter,
    GoAdapter,
    RustAdapter,
    get_adapter,
    detect_language,
    analyze_file_multilang,
    SUPPORTED_EXTENSIONS,
    ALL_EXTENSIONS,
    _is_comment,
    _count_decision_points,
    _compute_max_nesting,
    _compute_cognitive,
    _extract_function_body,
    _line_number,
)
from devlens.complexity import FunctionMetrics, FileComplexity


# ── SUPPORTED_EXTENSIONS & ALL_EXTENSIONS ────────────────────

class TestConstants:
    """Module-level constants are correct."""

    def test_supported_extensions_keys(self):
        expected = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".java", ".go", ".rs"}
        assert set(SUPPORTED_EXTENSIONS.keys()) == expected

    def test_all_extensions_includes_python(self):
        assert ".py" in ALL_EXTENSIONS

    def test_all_extensions_includes_supported(self):
        for ext in SUPPORTED_EXTENSIONS:
            assert ext in ALL_EXTENSIONS


# ── _is_comment ──────────────────────────────────────────────

class TestIsComment:
    """_is_comment detects single-line comments per language."""

    def test_double_slash(self):
        assert _is_comment("  // comment", "javascript") is True

    def test_block_comment_start(self):
        assert _is_comment("/* block */", "java") is True

    def test_star_continuation(self):
        assert _is_comment(" * middle line", "go") is True

    def test_hash_python(self):
        assert _is_comment("# comment", "python") is True

    def test_hash_non_python(self):
        assert _is_comment("# not a comment", "javascript") is False

    def test_normal_code(self):
        assert _is_comment("let x = 1;", "javascript") is False

    def test_empty_line(self):
        assert _is_comment("", "javascript") is False


# ── _count_decision_points ───────────────────────────────────

class TestCountDecisionPoints:
    """_count_decision_points calculates cyclomatic complexity."""

    def test_simple_function(self):
        body = "{ return x; }"
        assert _count_decision_points(body, "javascript") == 1

    def test_if_statement(self):
        body = "{ if (x > 0) { return x; } }"
        cc = _count_decision_points(body, "javascript")
        assert cc >= 2

    def test_multiple_branches(self):
        body = "{ if (a) {} else if (b) {} for (;;) {} while (c) {} }"
        cc = _count_decision_points(body, "javascript")
        assert cc >= 5

    def test_logical_operators(self):
        body = "{ if (a && b || c) {} }"
        cc = _count_decision_points(body, "javascript")
        assert cc >= 3

    def test_go_select(self):
        body = "{ select { case <-ch: } }"
        cc = _count_decision_points(body, "go")
        assert cc >= 2

    def test_rust_match(self):
        body = "{ match x { 1 => {}, 2 => {} } }"
        cc = _count_decision_points(body, "rust")
        assert cc >= 2


# ── _compute_max_nesting ─────────────────────────────────────

class TestComputeMaxNesting:
    """_compute_max_nesting tracks brace depth."""

    def test_no_nesting(self):
        assert _compute_max_nesting("{ return x; }") == 0

    def test_single_nesting(self):
        assert _compute_max_nesting("{ if (x) { y; } }") == 1

    def test_deep_nesting(self):
        body = "{ if (a) { if (b) { if (c) { x; } } } }"
        assert _compute_max_nesting(body) >= 2

    def test_ignores_strings(self):
        body = '{ let s = "{ { { }"; }'
        # Braces inside strings should not count
        depth = _compute_max_nesting(body)
        assert depth <= 1


# ── _extract_function_body ───────────────────────────────────

class TestExtractFunctionBody:
    """_extract_function_body finds matching braces."""

    def test_simple_body(self):
        source = "function foo() { return 1; }"
        body, end = _extract_function_body(source, 0)
        assert "return 1" in body
        assert end > 0

    def test_nested_braces(self):
        source = "function foo() { if (x) { return 1; } }"
        body, end = _extract_function_body(source, 0)
        assert body.count("{") == body.count("}")

    def test_no_opening_brace(self):
        body, end = _extract_function_body("no brace here", 0)
        assert body == ""


# ── _line_number ─────────────────────────────────────────────

class TestLineNumber:
    """_line_number converts char position to 1-based line."""

    def test_first_line(self):
        assert _line_number("hello\nworld", 0) == 1

    def test_second_line(self):
        assert _line_number("hello\nworld", 6) == 2

    def test_third_line(self):
        assert _line_number("a\nb\nc", 4) == 3


# ── get_adapter & detect_language ────────────────────────────

class TestAdapterRegistry:
    """get_adapter and detect_language resolve file extensions."""

    @pytest.mark.parametrize("ext,expected_type", [
        ("app.js", JavaScriptAdapter),
        ("app.ts", JavaScriptAdapter),
        ("App.java", JavaAdapter),
        ("main.go", GoAdapter),
        ("lib.rs", RustAdapter),
    ])
    def test_get_adapter(self, ext, expected_type):
        adapter = get_adapter(ext)
        assert isinstance(adapter, expected_type)

    def test_get_adapter_python_returns_none(self):
        assert get_adapter("main.py") is None

    def test_get_adapter_unknown_returns_none(self):
        assert get_adapter("data.csv") is None

    def test_detect_language_js(self):
        assert detect_language("app.js") == "javascript"

    def test_detect_language_ts(self):
        assert detect_language("app.ts") == "typescript"

    def test_detect_language_unknown(self):
        assert detect_language("file.txt") is None


# ── JavaScriptAdapter ────────────────────────────────────────

class TestJavaScriptAdapter:
    """JavaScriptAdapter parses JS/TS function signatures."""

    def test_named_function(self):
        source = "function greet(name) {\n  return 'hello ' + name;\n}\n"
        adapter = JavaScriptAdapter()
        funcs = adapter.find_functions(source, "app.js")
        assert len(funcs) >= 1
        assert funcs[0].name == "greet"
        assert funcs[0].params == 1

    def test_arrow_function(self):
        source = "const add = (a, b) => {\n  return a + b;\n}\n"
        adapter = JavaScriptAdapter()
        funcs = adapter.find_functions(source, "app.js")
        assert len(funcs) >= 1
        assert funcs[0].name == "add"

    def test_async_function(self):
        source = "async function fetchData(url) {\n  const r = await fetch(url);\n  return r;\n}\n"
        adapter = JavaScriptAdapter()
        funcs = adapter.find_functions(source, "app.js")
        assert any(f.name == "fetchData" for f in funcs)

    def test_skips_keywords(self):
        source = "if (true) {\n  console.log('yes');\n}\n"
        adapter = JavaScriptAdapter()
        funcs = adapter.find_functions(source, "app.js")
        assert not any(f.name == "if" for f in funcs)

    def test_analyze_returns_file_complexity(self):
        source = "function foo() {\n  return 1;\n}\n\nfunction bar(x) {\n  if (x) {\n    return 2;\n  }\n  return 3;\n}\n"
        adapter = JavaScriptAdapter()
        result = adapter.analyze(source, "app.js")
        assert isinstance(result, FileComplexity)
        assert result.total_lines > 0
        assert len(result.functions) >= 1


# ── JavaAdapter ──────────────────────────────────────────────

class TestJavaAdapter:
    """JavaAdapter parses Java method signatures."""

    def test_public_method(self):
        source = "public class Foo {\n  public void doStuff(String s) {\n    System.out.println(s);\n  }\n}\n"
        adapter = JavaAdapter()
        funcs = adapter.find_functions(source, "Foo.java")
        assert any(f.name == "doStuff" for f in funcs)

    def test_skips_keywords(self):
        source = "if (x) {\n  doSomething();\n}\n"
        adapter = JavaAdapter()
        funcs = adapter.find_functions(source, "Test.java")
        assert not any(f.name == "if" for f in funcs)


# ── GoAdapter ────────────────────────────────────────────────

class TestGoAdapter:
    """GoAdapter parses Go function signatures."""

    def test_simple_function(self):
        source = "func main() {\n  fmt.Println(\"hello\")\n}\n"
        adapter = GoAdapter()
        funcs = adapter.find_functions(source, "main.go")
        assert any(f.name == "main" for f in funcs)

    def test_method_with_receiver(self):
        source = "func (s *Server) Start(port int) {\n  s.listen(port)\n}\n"
        adapter = GoAdapter()
        funcs = adapter.find_functions(source, "server.go")
        assert any(f.name == "Start" for f in funcs)

    def test_params_count(self):
        source = "func add(a int, b int) {\n  return a + b\n}\n"
        adapter = GoAdapter()
        funcs = adapter.find_functions(source, "math.go")
        assert funcs[0].params == 2


# ── RustAdapter ──────────────────────────────────────────────

class TestRustAdapter:
    """RustAdapter parses Rust function signatures."""

    def test_pub_function(self):
        source = "pub fn process(data: &str) {\n    println!(\"{}\", data);\n}\n"
        adapter = RustAdapter()
        funcs = adapter.find_functions(source, "lib.rs")
        assert any(f.name == "process" for f in funcs)

    def test_self_not_counted_as_param(self):
        source = "impl Foo {\n  pub fn bar(&self, x: i32) {\n    self.x = x;\n  }\n}\n"
        adapter = RustAdapter()
        funcs = adapter.find_functions(source, "foo.rs")
        matching = [f for f in funcs if f.name == "bar"]
        assert len(matching) >= 1
        assert matching[0].params == 1  # &self excluded

    def test_async_fn(self):
        source = "pub async fn fetch(url: &str) {\n    let r = get(url).await;\n}\n"
        adapter = RustAdapter()
        funcs = adapter.find_functions(source, "http.rs")
        assert any(f.name == "fetch" for f in funcs)


# ── analyze_file_multilang ───────────────────────────────────

class TestAnalyzeFileMultilang:
    """analyze_file_multilang dispatches to correct analyzer."""

    def test_js_file(self):
        source = "function hello() {\n  return 'world';\n}\n"
        result = analyze_file_multilang("app.js", source)
        assert isinstance(result, FileComplexity)
        assert result.file == "app.js"

    def test_unknown_extension(self):
        result = analyze_file_multilang("data.csv", "a,b,c\n1,2,3\n")
        assert isinstance(result, FileComplexity)
        assert result.functions == []
        assert result.total_lines == 2
