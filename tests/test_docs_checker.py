# tests/test_docs_checker.py
"""Tests for devlens.docs_checker — documentation health analysis."""
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import field

from devlens.docs_checker import (
    CodeBlock,
    DocsIssue,
    DocsHealthResult,
    extract_code_blocks,
    run_python_block,
    _static_check,
    _call_llm,
    check_docs,
)


# ── CodeBlock dataclass ──────────────────────────────────────

class TestCodeBlock:
    """CodeBlock stores extracted code block metadata."""

    def test_defaults(self):
        b = CodeBlock(index=0, language="python", code="x = 1", line_number=5)
        assert b.runnable is False
        assert b.run_result is None
        assert b.run_passed is None

    def test_runnable_flag(self):
        b = CodeBlock(index=0, language="python", code="x=1", line_number=1, runnable=True)
        assert b.runnable is True


# ── DocsIssue dataclass ──────────────────────────────────────

class TestDocsIssue:
    """DocsIssue stores a single documentation problem."""

    def test_fields(self):
        issue = DocsIssue(
            block_index=0, language="python", severity="error",
            title="Syntax Error", description="Invalid syntax",
            suggestion="Fix the code", code="def f("
        )
        assert issue.severity == "error"
        assert issue.block_index == 0


# ── DocsHealthResult ─────────────────────────────────────────

class TestDocsHealthResult:
    """DocsHealthResult aggregates analysis results."""

    def _make_result(self, **kwargs):
        defaults = dict(
            file_path="README.md", health_score=85,
            summary="Looks good", ai_powered=False,
        )
        defaults.update(kwargs)
        return DocsHealthResult(**defaults)

    def test_to_dict_basic(self):
        r = self._make_result()
        d = r.to_dict()
        assert d["file_path"] == "README.md"
        assert d["health_score"] == 85
        assert d["total_blocks"] == 0

    def test_to_dict_with_issues(self):
        issue = DocsIssue(0, "python", "error", "Bad", "Desc", "Fix", "code")
        r = self._make_result(issues=[issue])
        d = r.to_dict()
        assert len(d["issues"]) == 1
        assert d["issues"][0]["title"] == "Bad"

    def test_to_markdown_header(self):
        r = self._make_result()
        md = r.to_markdown()
        assert "README.md" in md
        assert "85/100" in md

    def test_to_markdown_static_note(self):
        r = self._make_result(ai_powered=False)
        md = r.to_markdown()
        assert "static analysis" in md

    def test_to_markdown_ai_note(self):
        r = self._make_result(ai_powered=True)
        md = r.to_markdown()
        assert "AI-powered" in md

    def test_to_markdown_with_issues(self):
        issue = DocsIssue(0, "python", "error", "Syntax Error", "Bad code", "Fix it", "x =")
        r = self._make_result(issues=[issue])
        md = r.to_markdown()
        assert "Syntax Error" in md
        assert "## Issues" in md

    def test_to_markdown_with_recommendations(self):
        r = self._make_result(recommendations=["Add examples", "Fix typos"])
        md = r.to_markdown()
        assert "## Recommendations" in md
        assert "Add examples" in md


# ── extract_code_blocks ──────────────────────────────────────

class TestExtractCodeBlocks:
    """extract_code_blocks parses fenced code from Markdown."""

    def test_single_python_block(self):
        content = "Some text\n\n```python\nprint('hello')\n```\n\nMore text"
        blocks = extract_code_blocks(content)
        assert len(blocks) == 1
        assert blocks[0].language == "python"
        assert blocks[0].code == "print('hello')"
        assert blocks[0].runnable is True

    def test_multiple_blocks(self):
        content = "```python\nx = 1\n```\n\n```bash\necho hi\n```\n\n```json\n{}\n```"
        blocks = extract_code_blocks(content)
        assert len(blocks) == 3
        assert blocks[0].language == "python"
        assert blocks[1].language == "bash"
        assert blocks[2].language == "json"

    def test_no_language_tag(self):
        content = "```\nsome code\n```"
        blocks = extract_code_blocks(content)
        assert len(blocks) == 1
        assert blocks[0].language == "text"
        assert blocks[0].runnable is False

    def test_empty_content(self):
        assert extract_code_blocks("No code here") == []

    def test_line_numbers(self):
        content = "Line 1\nLine 2\n\n```python\ncode\n```"
        blocks = extract_code_blocks(content)
        assert blocks[0].line_number >= 3

    def test_runnable_languages(self):
        for lang in ("python", "py", "bash", "sh", "shell"):
            content = f"```{lang}\ncommand\n```"
            blocks = extract_code_blocks(content)
            assert blocks[0].runnable is True, f"{lang} should be runnable"

    def test_non_runnable_languages(self):
        for lang in ("json", "yaml", "text", "sql"):
            content = f"```{lang}\ndata\n```"
            blocks = extract_code_blocks(content)
            assert blocks[0].runnable is False, f"{lang} should not be runnable"

    def test_block_indices(self):
        content = "```python\na\n```\n```bash\nb\n```\n```json\nc\n```"
        blocks = extract_code_blocks(content)
        assert [b.index for b in blocks] == [0, 1, 2]


# ── _static_check ────────────────────────────────────────────

class TestStaticCheck:
    """_static_check performs basic analysis without AI."""

    def test_no_blocks_score(self):
        result = _static_check([], "# Empty doc")
        assert result.health_score == 70
        assert "No code blocks" in result.summary

    def test_clean_blocks(self):
        blocks = [
            CodeBlock(0, "python", "print('hi')\nx = 1", 1, True),
            CodeBlock(1, "bash", "echo hello\necho world", 5, True),
        ]
        result = _static_check(blocks, "content")
        assert result.health_score >= 60

    def test_missing_language_tag_issue(self):
        blocks = [CodeBlock(0, "text", "some code", 1)]
        result = _static_check(blocks, "content")
        assert any(i.title == "Missing language tag" for i in result.issues)

    def test_single_line_example_info(self):
        blocks = [CodeBlock(0, "python", "x = 1", 1, True)]
        result = _static_check(blocks, "content")
        assert any("Single-line" in i.title for i in result.issues)

    def test_recommendations_present(self):
        result = _static_check([], "empty")
        assert len(result.recommendations) > 0


# ── check_docs (main entry point) ────────────────────────────

class TestCheckDocs:
    """check_docs orchestrates the full documentation check."""

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            check_docs("/nonexistent/file.md")

    def test_basic_check(self, tmp_path):
        md = tmp_path / "README.md"
        md.write_text("# Title\n\n```python\nprint('hi')\n```\n")
        result = check_docs(str(md))
        assert result.file_path == str(md)
        assert result.health_score > 0
        assert result.ai_powered is False

    def test_with_no_code_blocks(self, tmp_path):
        md = tmp_path / "README.md"
        md.write_text("# Title\n\nJust text, no code.\n")
        result = check_docs(str(md))
        assert len(result.blocks) == 0

    @patch("devlens.docs_checker._call_llm")
    def test_ai_mode(self, mock_llm, tmp_path):
        mock_llm.return_value = json.dumps({
            "summary": "AI analysis complete",
            "health_score": 90,
            "issues": [],
            "good_examples": [],
            "recommendations": ["Keep it up"],
        })
        md = tmp_path / "README.md"
        md.write_text("# Title\n\n```python\nprint('hi')\n```\n")
        result = check_docs(str(md), use_ai=True, model="gpt-4o", api_key="test-key")
        assert result.ai_powered is True
        assert result.health_score == 90

    def test_sets_file_path(self, tmp_path):
        md = tmp_path / "docs.md"
        md.write_text("# Doc\n")
        result = check_docs(str(md))
        assert "docs.md" in result.file_path


# ── _call_llm routing ────────────────────────────────────────

class TestCallLLM:
    """_call_llm routes to correct provider based on model prefix."""

    @patch("devlens.docs_checker._openai", return_value='{"result": "ok"}')
    def test_openai_routing(self, mock):
        _call_llm("gpt-4o", "prompt")
        mock.assert_called_once()

    @patch("devlens.docs_checker._anthropic", return_value='{"result": "ok"}')
    def test_anthropic_routing(self, mock):
        _call_llm("claude-3-sonnet", "prompt")
        mock.assert_called_once()

    @patch("devlens.docs_checker._gemini", return_value='{"result": "ok"}')
    def test_gemini_routing(self, mock):
        _call_llm("gemini-pro", "prompt")
        mock.assert_called_once()

    @patch("devlens.docs_checker._groq", return_value='{"result": "ok"}')
    def test_groq_routing(self, mock):
        _call_llm("groq/llama3", "prompt")
        mock.assert_called_once()

    @patch("devlens.docs_checker._ollama", return_value='{"result": "ok"}')
    def test_ollama_routing(self, mock):
        _call_llm("ollama/mistral", "prompt")
        mock.assert_called_once()

    @patch("devlens.docs_checker._openrouter", return_value='{"result": "ok"}')
    def test_openrouter_routing(self, mock):
        _call_llm("openrouter/meta-llama", "prompt")
        mock.assert_called_once()

    def test_unsupported_model_raises(self):
        with pytest.raises(ValueError, match="Unsupported model"):
            _call_llm("unknown-model", "prompt")
