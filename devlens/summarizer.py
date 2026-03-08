"""PR diff summarizer — generates concise 3-5 sentence summaries of what a PR does.

Uses the LLM to produce a human-readable overview without reading every line.
Falls back to a heuristic summary if no AI model is configured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from devlens.github import PRData


@dataclass
class PRSummary:
    """Structured summary of a PR."""
    overview: str          # 3-5 sentence overview
    key_changes: list[str] # bullet points of major changes
    impact: str            # low / medium / high
    categories: list[str]  # e.g. ["feature", "refactor", "bugfix", "docs"]

    def to_markdown(self) -> str:
        cats = ", ".join(f"`{c}`" for c in self.categories) if self.categories else "general"
        bullets = "\n".join(f"  - {c}" for c in self.key_changes) if self.key_changes else "  - Minor changes"
        return (
            f"**Summary** ({cats} — impact: {self.impact})\n\n"
            f"{self.overview}\n\n"
            f"**Key changes:**\n{bullets}"
        )


SUMMARY_PROMPT = """\
You are a senior developer reviewing a Pull Request. Provide a concise summary.

PR Title: {title}
PR Description: {body}

Files changed ({file_count}):
{file_list}

Diff (first 15000 chars):
{diff}

Return a JSON object:
{{
  "overview": "3-5 sentence overview of what this PR does and why",
  "key_changes": ["up to 5 bullet points of the most important changes"],
  "impact": "low|medium|high",
  "categories": ["feature|bugfix|refactor|docs|test|config|deps"]
}}

Be specific about what changed. Don't just say "updates code" — say what the update does."""


def _heuristic_summary(pr: PRData) -> PRSummary:
    """Generate a basic summary without AI."""
    files = pr.files
    extensions = set()
    dirs = set()
    total_add = 0
    total_del = 0

    for f in files:
        name = f["filename"]
        if "." in name:
            extensions.add(name.rsplit(".", 1)[1])
        if "/" in name:
            dirs.add(name.split("/")[0])
        total_add += f.get("additions", 0)
        total_del += f.get("deletions", 0)

    # Guess categories
    categories = []
    if any(e in extensions for e in ("md", "rst", "txt")):
        categories.append("docs")
    if any(e in extensions for e in ("test", "spec")) or any("test" in f["filename"].lower() for f in files):
        categories.append("test")
    if any(f["filename"] in ("pyproject.toml", "package.json", "requirements.txt", "go.mod") for f in files):
        categories.append("deps")
    if not categories:
        categories.append("feature")

    # Impact
    impact = "low" if total_add + total_del < 100 else "medium" if total_add + total_del < 500 else "high"

    overview = (
        f"This PR modifies {len(files)} file(s) across {len(dirs) or 1} director{'ies' if len(dirs) != 1 else 'y'}, "
        f"with +{total_add}/-{total_del} line changes. "
        f"Primary file types: {', '.join(sorted(extensions)[:5]) or 'various'}."
    )

    key_changes = [f["filename"] for f in sorted(files, key=lambda x: x.get("changes", 0), reverse=True)[:5]]

    return PRSummary(
        overview=overview,
        key_changes=key_changes,
        impact=impact,
        categories=categories,
    )


def summarize_pr(
    pr: PRData,
    *,
    use_ai: bool = True,
    model: str = "gpt-4o",
) -> PRSummary:
    """Generate a concise summary of a PR.
    
    With use_ai=True, uses LLM for intelligent summarization.
    Falls back to heuristic summary on failure or when AI is disabled.
    """
    if not use_ai:
        return _heuristic_summary(pr)

    # Build diff text
    diff_parts = []
    for f in pr.files:
        patch = f.get("patch", "")
        if patch:
            diff_parts.append(f"--- {f['filename']} ---\n{patch}")
    diff_text = "\n\n".join(diff_parts)
    if len(diff_text) > 15000:
        diff_text = diff_text[:15000] + "\n\n... (truncated)"

    file_list = "\n".join(
        f"  {f['filename']} (+{f.get('additions', 0)}/-{f.get('deletions', 0)})"
        for f in pr.files[:30]
    )

    prompt = SUMMARY_PROMPT.format(
        title=pr.title,
        body=(pr.body[:2000] if pr.body else "(no description)"),
        file_count=len(pr.files),
        file_list=file_list,
        diff=diff_text,
    )

    try:
        from devlens.analyzer import _call_llm
        raw = _call_llm(model, prompt)
        
        # Parse JSON from response
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(text)

        return PRSummary(
            overview=data.get("overview", ""),
            key_changes=data.get("key_changes", []),
            impact=data.get("impact", "medium"),
            categories=data.get("categories", ["feature"]),
        )
    except Exception:
        return _heuristic_summary(pr)
