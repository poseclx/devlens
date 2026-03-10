"""Auto-fix suggestion generator — produces unified diff patches for security findings.

Uses LLM to generate fix suggestions in unified diff format.
Falls back to rule-based suggestions when AI is unavailable.

Usage:
    from devlens.fixer import suggest_fixes
    fixes = suggest_fixes(findings, file_contents, model="gpt-4o")
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field


@dataclass
class FixSuggestion:
    """A suggested fix for a security or code quality finding."""
    finding_id: str          # Rule ID (e.g., SEC001, VLN003)
    file: str                # File path
    title: str               # Short description
    original: str            # Original code snippet
    suggested: str           # Fixed code snippet
    diff: str                # Unified diff format
    explanation: str         # Why this fix is recommended
    confidence: str = "medium"  # low | medium | high
    auto_applicable: bool = False  # Can be applied without review?

    def to_dict(self) -> dict:
        return {
            "finding_id": self.finding_id,
            "file": self.file,
            "title": self.title,
            "original": self.original[:200],
            "suggested": self.suggested[:200],
            "diff": self.diff,
            "explanation": self.explanation,
            "confidence": self.confidence,
            "auto_applicable": self.auto_applicable,
        }

    def to_markdown(self) -> str:
        return (
            f"### Fix: {self.title}\n"
            f"**File:** `{self.file}` | **Confidence:** {self.confidence}\n\n"
            f"{self.explanation}\n\n"
            f"```diff\n{self.diff}\n```\n"
        )


# ── Rule-based fix templates ─────────────────────────────────

RULE_FIXES: dict[str, dict] = {
    "SEC001": {
        "title": "Remove hardcoded AWS key",
        "template": 'Replace with: `os.environ.get("AWS_ACCESS_KEY_ID")`',
        "pattern_hint": "AWS key assignment",
    },
    "SEC002": {
        "title": "Remove hardcoded AWS secret",
        "template": 'Replace with: `os.environ.get("AWS_SECRET_ACCESS_KEY")`',
        "pattern_hint": "AWS secret assignment",
    },
    "SEC003": {
        "title": "Remove hardcoded GitHub token",
        "template": 'Replace with: `os.environ.get("GITHUB_TOKEN")`',
        "pattern_hint": "GitHub token assignment",
    },
    "SEC005": {
        "title": "Remove hardcoded Stripe key",
        "template": 'Replace with: `os.environ.get("STRIPE_SECRET_KEY")`',
        "pattern_hint": "Stripe key assignment",
    },
    "SEC007": {
        "title": "Remove hardcoded private key",
        "template": "Move private key to a secure vault or environment variable",
        "pattern_hint": "Inline private key",
    },
    "SEC009": {
        "title": "Remove hardcoded password",
        "template": 'Replace with: `os.environ.get("DB_PASSWORD")`',
        "pattern_hint": "Password in code",
    },
    "SEC010": {
        "title": "Remove hardcoded database URL",
        "template": 'Replace with: `os.environ.get("DATABASE_URL")`',
        "pattern_hint": "Database connection string",
    },
    "VLN001": {
        "title": "Fix SQL injection",
        "template": "Use parameterized queries: `cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))`",
        "pattern_hint": "String formatting in SQL",
    },
    "VLN002": {
        "title": "Fix command injection",
        "template": "Use `subprocess.run()` with a list of arguments instead of shell=True",
        "pattern_hint": "os.system or shell=True",
    },
    "VLN003": {
        "title": "Fix unsafe deserialization",
        "template": "Use `json.loads()` or `yaml.safe_load()` instead of pickle/yaml.load",
        "pattern_hint": "pickle.loads or yaml.load",
    },
    "VLN004": {
        "title": "Remove eval/exec usage",
        "template": "Replace with `ast.literal_eval()` for data parsing, or use a safe expression evaluator",
        "pattern_hint": "eval() or exec()",
    },
    "VLN005": {
        "title": "Fix disabled SSL verification",
        "template": "Remove `verify=False` — use proper CA certificates instead",
        "pattern_hint": "verify=False",
    },
    "VLN007": {
        "title": "Fix path traversal vulnerability",
        "template": "Use `pathlib.Path.resolve()` and validate the path is within the expected directory",
        "pattern_hint": "Unsanitized path join",
    },
    "VLN010": {
        "title": "Disable debug mode in production",
        "template": "Use `DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'`",
        "pattern_hint": "DEBUG = True",
    },
}


FIX_PROMPT = """\
You are an expert security engineer. Generate a fix for the following code issue.

Finding: {title} ({rule_id})
Severity: {severity}
File: {file}
Line: {line}
Description: {description}

Code context (surrounding lines):
```
{context}
```

Matched line:
```
{match}
```

Generate a fix in this JSON format:
{{
  "original": "the exact original code that needs to change (can be multiple lines)",
  "suggested": "the fixed code",
  "diff": "unified diff format showing the change",
  "explanation": "1-2 sentence explanation of why this fix works",
  "confidence": "low|medium|high",
  "auto_applicable": true/false
}}

Rules:
- The fix must be minimal — change only what's necessary
- Maintain the same code style and indentation
- The diff must be valid unified diff format
- Set auto_applicable=true only for simple, unambiguous fixes (like replacing verify=False)
- Set confidence=high only when the fix is clearly correct
"""


def _get_file_context(file_contents: dict[str, str], filepath: str, line: int | None, window: int = 5) -> str:
    """Extract lines around the finding for context."""
    content = file_contents.get(filepath, "")
    if not content or not line:
        return "(file content not available)"
    
    lines = content.splitlines()
    start = max(0, line - window - 1)
    end = min(len(lines), line + window)
    
    context_lines = []
    for i in range(start, end):
        marker = ">>>" if i == line - 1 else "   "
        context_lines.append(f"{marker} {i + 1:4d} | {lines[i]}")
    
    return "\n".join(context_lines)


def _rule_based_fix(finding, file_contents: dict[str, str]) -> FixSuggestion | None:
    """Generate a fix suggestion using predefined templates."""
    rule = RULE_FIXES.get(finding.rule_id)
    if not rule:
        return None
    
    context = _get_file_context(file_contents, finding.file, finding.line)
    
    return FixSuggestion(
        finding_id=finding.rule_id,
        file=finding.file,
        title=rule["title"],
        original=finding.match,
        suggested=rule["template"],
        diff=f"--- a/{finding.file}\n+++ b/{finding.file}\n@@ -{finding.line or 1},1 +{finding.line or 1},1 @@\n-{finding.match}\n+# TODO: {rule['template']}",
        explanation=rule["template"],
        confidence="medium",
        auto_applicable=False,
    )


def _ai_fix(finding, file_contents: dict[str, str], model: str) -> FixSuggestion | None:
    """Generate fix using LLM."""
    context = _get_file_context(file_contents, finding.file, finding.line)
    
    prompt = FIX_PROMPT.format(
        title=finding.title,
        rule_id=finding.rule_id,
        severity=getattr(finding.severity, 'value', finding.severity),
        file=finding.file,
        line=finding.line or "?",
        description=finding.description,
        context=context,
        match=finding.match,
    )
    
    try:
        from devlens.analyzer import _call_llm
        raw = _call_llm(model, prompt)
        
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(text)
        
        return FixSuggestion(
            finding_id=finding.rule_id,
            file=finding.file,
            title=finding.title,
            original=data.get("original", finding.match),
            suggested=data.get("suggested", ""),
            diff=data.get("diff", ""),
            explanation=data.get("explanation", ""),
            confidence=data.get("confidence", "medium"),
            auto_applicable=data.get("auto_applicable", False),
        )
    except Exception:
        return _rule_based_fix(finding, file_contents)


def suggest_fixes(
    findings: list,
    file_contents: dict[str, str] | None = None,
    *,
    use_ai: bool = False,
    model: str = "gpt-4o",
    max_fixes: int = 20,
) -> list[FixSuggestion]:
    """Generate fix suggestions for security findings.
    
    Args:
        findings: List of SecurityFinding objects
        file_contents: Dict mapping filepath -> file content (for context)
        use_ai: Use LLM for intelligent fix generation
        model: LLM model to use
        max_fixes: Maximum number of fixes to generate
    
    Returns:
        List of FixSuggestion objects
    """
    contents = file_contents or {}
    fixes: list[FixSuggestion] = []
    
    for finding in findings[:max_fixes]:
        if use_ai:
            fix = _ai_fix(finding, contents, model)
        else:
            fix = _rule_based_fix(finding, contents)
        
        if fix:
            fixes.append(fix)
    
    return fixes


def format_fixes_markdown(fixes: list[FixSuggestion]) -> str:
    """Format all fixes as a single Markdown document."""
    if not fixes:
        return "No fix suggestions available."
    
    parts = [f"# Fix Suggestions ({len(fixes)} total)\n"]
    for i, fix in enumerate(fixes, 1):
        parts.append(f"## {i}. {fix.title}")
        parts.append(f"**File:** `{fix.file}` | **Rule:** {fix.finding_id} | **Confidence:** {fix.confidence}\n")
        parts.append(fix.explanation + "\n")
        parts.append(f"```diff\n{fix.diff}\n```\n")
    
    return "\n".join(parts)


def format_fixes_json(fixes: list[FixSuggestion]) -> str:
    """Format fixes as JSON."""
    return json.dumps([f.to_dict() for f in fixes], indent=2)
