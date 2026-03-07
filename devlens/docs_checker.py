"""Docs Health checker — extracts code blocks from Markdown and optionally uses AI analysis."""

from __future__ import annotations
import re
import subprocess
import tempfile
import json
from dataclasses import dataclass, field
from pathlib import Path


SYSTEM_PROMPT = """You are a documentation quality expert. Analyze the given documentation file and its code examples.

For each code block, evaluate:
1. SYNTAX: Is the code syntactically valid?
2. CONSISTENCY: Does it match the rest of the docs (correct API, correct imports)?
3. COMPLETENESS: Is the example runnable or is it missing context?
4. STALENESS: Does it look like it might be outdated based on the surrounding text?

Return a JSON object with this structure:
{
  "summary": "2-3 sentence overall assessment",
  "health_score": 0-100,
  "issues": [
    {
      "block_index": 0,
      "language": "python",
      "severity": "error|warning|info",
      "title": "Short issue title",
      "description": "What is wrong and why",
      "suggestion": "How to fix it",
      "code": "the original code block"
    }
  ],
  "good_examples": [
    {
      "block_index": 1,
      "language": "python",
      "note": "Why this example is good"
    }
  ],
  "recommendations": ["actionable recommendation 1", "actionable recommendation 2"]
}
"""


@dataclass
class CodeBlock:
    index: int
    language: str
    code: str
    line_number: int
    runnable: bool = False
    run_result: str | None = None
    run_passed: bool | None = None


@dataclass
class DocsIssue:
    block_index: int
    language: str
    severity: str  # error | warning | info
    title: str
    description: str
    suggestion: str
    code: str


@dataclass
class DocsHealthResult:
    file_path: str
    health_score: int
    summary: str
    ai_powered: bool = False
    blocks: list[CodeBlock] = field(default_factory=list)
    issues: list[DocsIssue] = field(default_factory=list)
    good_examples: list[dict] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "health_score": self.health_score,
            "summary": self.summary,
            "ai_powered": self.ai_powered,
            "total_blocks": len(self.blocks),
            "issues": [
                {
                    "block_index": i.block_index,
                    "language": i.language,
                    "severity": i.severity,
                    "title": i.title,
                    "description": i.description,
                    "suggestion": i.suggestion,
                    "code": i.code,
                }
                for i in self.issues
            ],
            "good_examples": self.good_examples,
            "recommendations": self.recommendations,
        }

    def to_markdown(self) -> str:
        ai_note = " *(AI-powered)*" if self.ai_powered else " *(static analysis)*"
        lines = [
            f"# Docs Health Report: {self.file_path}",
            f"",
            f"**Health Score:** {self.health_score}/100{ai_note}",
            f"",
            f"## Summary",
            f"",
            self.summary,
            f"",
        ]
        if self.issues:
            lines += ["## Issues", ""]
            for issue in self.issues:
                emoji = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(issue.severity, "⚪")
                lines += [
                    f"### {emoji} {issue.title} (Block #{issue.block_index})",
                    f"",
                    f"**Severity:** {issue.severity}",
                    f"",
                    issue.description,
                    f"",
                    f"**Suggestion:** {issue.suggestion}",
                    f"",
                    f"```{issue.language}",
                    issue.code,
                    "```",
                    "",
                ]
        if self.recommendations:
            lines += ["## Recommendations", ""]
            for rec in self.recommendations:
                lines.append(f"- {rec}")
        return "\n".join(lines)


def extract_code_blocks(content: str) -> list[CodeBlock]:
    """Extract fenced code blocks from Markdown."""
    pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    blocks = []
    for i, match in enumerate(pattern.finditer(content)):
        lang = match.group(1).lower() or "text"
        code = match.group(2).strip()
        line_num = content[: match.start()].count("\n") + 1
        runnable = lang in ("python", "py", "bash", "sh", "shell")
        blocks.append(CodeBlock(index=i, language=lang, code=code, line_number=line_num, runnable=runnable))
    return blocks


def run_python_block(block: CodeBlock) -> CodeBlock:
    """Try to execute a Python code block in a subprocess."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(block.code)
        tmp_path = f.name
    try:
        result = subprocess.run(
            ["python", tmp_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        block.run_passed = result.returncode == 0
        block.run_result = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        block.run_passed = None
        block.run_result = "Timed out after 10 seconds"
    except Exception as e:
        block.run_passed = False
        block.run_result = str(e)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return block


def _static_check(blocks: list[CodeBlock], content: str) -> DocsHealthResult:
    """Fallback: basic static checks when no AI is available."""
    issues: list[DocsIssue] = []
    total = len(blocks)

    for b in blocks:
        # Flag blocks with no language tag
        if b.language == "text" and b.code:
            issues.append(DocsIssue(
                block_index=b.index,
                language="text",
                severity="info",
                title="Missing language tag",
                description="Code block has no language specified — syntax highlighting won't work.",
                suggestion="Add a language tag, e.g. ```python or ```bash",
                code=b.code,
            ))
        # Flag very short (likely incomplete) code blocks
        if len(b.code.strip().splitlines()) == 1 and b.language in ("python", "py", "bash", "sh"):
            issues.append(DocsIssue(
                block_index=b.index,
                language=b.language,
                severity="info",
                title="Single-line example",
                description="This example is very short. Consider expanding it for clarity.",
                suggestion="Add context or a full runnable example.",
                code=b.code,
            ))

    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")

    if total == 0:
        score = 70
        summary = "No code blocks found. Consider adding examples to improve usability."
    elif errors > 0:
        score = max(20, 80 - errors * 15 - warnings * 5)
        summary = f"Found {total} code block(s) with {errors} error(s) and {warnings} warning(s) (static analysis only)."
    else:
        score = max(60, 95 - warnings * 5 - len(issues) * 3)
        summary = f"Found {total} code block(s). No critical issues detected (static analysis only). Add --ai for deep analysis."

    recommendations = ["Run with --ai flag for AI-powered analysis and detailed suggestions."]
    if total == 0:
        recommendations.append("Add runnable code examples to your documentation.")

    return DocsHealthResult(
        file_path="",
        health_score=score,
        summary=summary,
        ai_powered=False,
        issues=issues,
        recommendations=recommendations,
    )


def _call_llm(model: str, prompt: str) -> str:
    """Route to the correct LLM provider based on model name prefix."""
    import os
    if model.startswith("gpt") or model.startswith("o1") or model.startswith("o3"):
        return _openai(model, prompt, os.environ.get("OPENAI_API_KEY"))
    elif model.startswith("claude"):
        return _anthropic(model, prompt, os.environ.get("ANTHROPIC_API_KEY"))
    elif model.startswith("gemini"):
        return _gemini(model, prompt, os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    else:
        raise ValueError(
            f"Unsupported model: {model!r}. "
            "Use gpt-* / o1-* / o3-* (OpenAI), claude-* (Anthropic), or gemini-* (Google)."
        )


def _openai(model: str, prompt: str, key: str | None) -> str:
    if not key:
        raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")
    from openai import OpenAI
    client = OpenAI(api_key=key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return resp.choices[0].message.content or "{}"


def _anthropic(model: str, prompt: str, key: str | None) -> str:
    if not key:
        raise EnvironmentError("ANTHROPIC_API_KEY environment variable is not set.")
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def _gemini(model: str, prompt: str, key: str | None) -> str:
    if not key:
        raise EnvironmentError(
            "GEMINI_API_KEY (or GOOGLE_API_KEY) environment variable is not set."
        )
    import google.generativeai as genai
    genai.configure(api_key=key)
    full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"
    gemini_model = genai.GenerativeModel(model)
    response = gemini_model.generate_content(
        full_prompt,
        generation_config=genai.GenerationConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )
    return response.text or "{}"


def _ai_check(blocks: list[CodeBlock], content: str, file_path: str, model: str, api_key: str | None) -> DocsHealthResult:
    """AI-powered docs check — supports OpenAI, Anthropic, and Google Gemini."""
    import os
    # Allow caller to override the key via argument
    if api_key:
        if model.startswith("claude"):
            os.environ.setdefault("ANTHROPIC_API_KEY", api_key)
        elif model.startswith("gemini"):
            os.environ.setdefault("GEMINI_API_KEY", api_key)
        else:
            os.environ.setdefault("OPENAI_API_KEY", api_key)

    blocks_summary = "\n\n".join(
        f"[Block #{b.index} — {b.language} — line {b.line_number}]\n{b.code}"
        for b in blocks
    )
    prompt = f"""Documentation file: {file_path}

--- FULL CONTENT ---
{content}

--- EXTRACTED CODE BLOCKS ---
{blocks_summary or "No code blocks found."}

Analyze and return JSON as specified."""

    raw = _call_llm(model, prompt)
    data = json.loads(raw)

    issues = [
        DocsIssue(
            block_index=i.get("block_index", 0),
            language=i.get("language", "text"),
            severity=i.get("severity", "info"),
            title=i.get("title", "Issue"),
            description=i.get("description", ""),
            suggestion=i.get("suggestion", ""),
            code=i.get("code", ""),
        )
        for i in data.get("issues", [])
    ]
    return DocsHealthResult(
        file_path=file_path,
        health_score=data.get("health_score", 0),
        summary=data.get("summary", ""),
        ai_powered=True,
        blocks=blocks,
        issues=issues,
        good_examples=data.get("good_examples", []),
        recommendations=data.get("recommendations", []),
    )


def check_docs(
    file_path: str,
    run_code: bool = False,
    use_ai: bool = False,
    model: str = "gpt-4o",
    api_key: str | None = None,
) -> DocsHealthResult:
    """Main entry point: check a documentation file for health issues."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    content = path.read_text(encoding="utf-8")
    blocks = extract_code_blocks(content)

    if run_code:
        blocks = [
            run_python_block(b) if b.language in ("python", "py") else b
            for b in blocks
        ]

    if use_ai:
        result = _ai_check(blocks, content, str(path), model, api_key)
    else:
        result = _static_check(blocks, content)

    result.file_path = str(path)
    result.blocks = blocks
    return result
