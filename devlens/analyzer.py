"""LLM-powered PR analyzer."""

from __future__ import annotations
import os
import json
from dataclasses import dataclass, field
from rich.console import Console
from rich.table import Table
from rich import box

from devlens.github import PRData


SYSTEM_PROMPT = """You are an expert code reviewer. Analyze the given Pull Request and return a JSON object with this exact shape:

{
  "summary": "<1-3 sentence plain-English description of what the PR does>",
  "risk_items": [
    {"file": "<filename>", "reason": "<why this needs careful review>", "severity": "high|medium|low"}
  ],
  "safe_items": [
    {"file": "<filename>", "reason": "<why this is safe to skim>"}
  ],
  "verdict": "<1 sentence overall recommendation: ready to merge / needs changes / needs discussion>"
}

Focus on: security issues, breaking changes, missing error handling, performance concerns, missing tests.
Ignore: formatting, whitespace, lock files, generated files, documentation-only changes (mark those as safe).
Be concise. Do not invent issues that are not visible in the diff."""


@dataclass
class ReviewResult:
    pr_number: int
    title: str
    summary: str
    risk_items: list[dict] = field(default_factory=list)
    safe_items: list[dict] = field(default_factory=list)
    verdict: str = ""

    def to_dict(self) -> dict:
        return {
            "pr_number": self.pr_number,
            "title": self.title,
            "summary": self.summary,
            "risk_items": self.risk_items,
            "safe_items": self.safe_items,
            "verdict": self.verdict,
        }

    def to_markdown(self) -> str:
        lines = [
            f"## PR #{self.pr_number} — {self.title}",
            "",
            f"**Summary:** {self.summary}",
            "",
            "### Risk Areas (review carefully)",
        ]
        for item in self.risk_items:
            sev = item.get("severity", "medium").upper()
            lines.append(f"- `{item['file']}` [{sev}] — {item['reason']}")
        lines += ["", "### Safe to Skim"]
        for item in self.safe_items:
            lines.append(f"- `{item['file']}` — {item['reason']}")
        lines += ["", f"**Verdict:** {self.verdict}"]
        return "\n".join(lines)

    def print_rich(self, console: Console) -> None:
        console.print(f"\n[bold]PR #{self.pr_number}[/] — {self.title}\n")
        console.print(f"[dim]Summary:[/] {self.summary}\n")

        if self.risk_items:
            risk_table = Table(title="Risk Areas (review carefully)", box=box.SIMPLE_HEAVY, show_lines=True)
            risk_table.add_column("File", style="cyan", no_wrap=True)
            risk_table.add_column("Severity", justify="center")
            risk_table.add_column("Reason")
            for item in self.risk_items:
                sev = item.get("severity", "medium")
                color = {"high": "red", "medium": "yellow", "low": "blue"}.get(sev, "white")
                risk_table.add_row(item["file"], f"[{color}]{sev.upper()}[/{color}]", item["reason"])
            console.print(risk_table)

        if self.safe_items:
            safe_table = Table(title="Safe to Skim", box=box.SIMPLE, show_lines=False)
            safe_table.add_column("File", style="dim cyan", no_wrap=True)
            safe_table.add_column("Reason", style="dim")
            for item in self.safe_items:
                safe_table.add_row(item["file"], item["reason"])
            console.print(safe_table)

        console.print(f"\n[bold green]Verdict:[/] {self.verdict}\n")


def _build_prompt(pr: PRData, detail: str) -> str:
    max_patch_lines = {"low": 30, "medium": 80, "high": 200}.get(detail, 80)

    files_section = []
    for f in pr.files:
        patch = f.get("patch", "") or ""
        patch_lines = patch.splitlines()[:max_patch_lines]
        files_section.append(
            f"### {f['filename']} ({f['status']}, +{f['additions']} -{f['deletions']})\n"
            + "\n".join(patch_lines)
        )

    return f"""PR #{pr.number}: {pr.title}
Author: {pr.author}
Base: {pr.base_branch} <- {pr.head_branch}
Changes: +{pr.additions} -{pr.deletions} across {pr.changed_files} files
Labels: {", ".join(pr.labels) or "none"}

Description:
{pr.body or "(no description)"}

--- FILES ---
{"".join(files_section)}
"""


def analyze_pr(pr: PRData, detail: str = "medium", config: dict | None = None) -> ReviewResult:
    """Send PR data to LLM and parse structured review result."""
    cfg = config or {}
    model = cfg.get("model", "gpt-4o")

    # Inject API key from config into environment so backend functions can find it
    api_key = cfg.get("api_key")
    if api_key:
        _ENV_MAP = {
            "gpt": "OPENAI_API_KEY", "o1": "OPENAI_API_KEY", "o3": "OPENAI_API_KEY",
            "claude": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "groq/": "GROQ_API_KEY",
            "openrouter/": "OPENROUTER_API_KEY",
        }
        for prefix, env_var in _ENV_MAP.items():
            if model.startswith(prefix) and not os.environ.get(env_var):
                os.environ[env_var] = api_key
                break

    prompt = _build_prompt(pr, detail)

    raw = _call_llm(model, prompt)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: try to extract JSON block from response
        import re
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group()) if match else {}

    return ReviewResult(
        pr_number=pr.number,
        title=pr.title,
        summary=data.get("summary", ""),
        risk_items=data.get("risk_items", []),
        safe_items=data.get("safe_items", []),
        verdict=data.get("verdict", ""),
    )


def _call_llm(model: str, prompt: str) -> str:
    """Route to the appropriate LLM provider based on model name prefix."""
    if model.startswith("gpt") or model.startswith("o1") or model.startswith("o3"):
        return _openai(model, prompt)
    elif model.startswith("claude"):
        return _anthropic(model, prompt)
    elif model.startswith("gemini"):
        return _gemini(model, prompt)
    elif model.startswith("groq/"):
        return _groq(model.removeprefix("groq/"), prompt)
    elif model.startswith("ollama/"):
        return _ollama(model.removeprefix("ollama/"), prompt)
    elif model.startswith("openrouter/"):
        return _openrouter(model.removeprefix("openrouter/"), prompt)
    else:
        raise ValueError(
            f"Unsupported model: {model!r}. Supported prefixes: "
            "gpt-*, o1-*, o3-* (OpenAI), claude-* (Anthropic), gemini-* (Google), "
            "groq/* (Groq), ollama/* (Ollama), openrouter/* (OpenRouter)."
        )


def _openai(model: str, prompt: str) -> str:
    from openai import OpenAI
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")
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


def _anthropic(model: str, prompt: str) -> str:
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise EnvironmentError("ANTHROPIC_API_KEY environment variable is not set.")
    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def _gemini(model: str, prompt: str) -> str:
    import google.generativeai as genai
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise EnvironmentError(
            "GEMINI_API_KEY (or GOOGLE_API_KEY) environment variable is not set."
        )
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


# ── Free / alternative providers ──────────────────────────────


def _groq(model: str, prompt: str) -> str:
    """Groq Cloud — free tier, extremely fast inference.

    Uses the groq Python SDK (OpenAI-compatible).
    Get a free key at https://console.groq.com/keys
    """
    from groq import Groq

    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise EnvironmentError(
            "GROQ_API_KEY environment variable is not set. "
            "Get a free key at https://console.groq.com/keys"
        )
    client = Groq(api_key=key)
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


def _ollama(model: str, prompt: str) -> str:
    """Ollama — 100% local, completely free, no API key needed.

    Requires Ollama running locally: https://ollama.com
    Start with: ollama serve && ollama pull llama3.1
    """
    import httpx

    base_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    try:
        resp = httpx.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "format": "json",
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=120.0,
        )
        resp.raise_for_status()
    except httpx.ConnectError:
        raise EnvironmentError(
            f"Cannot connect to Ollama at {base_url}. "
            "Make sure Ollama is running: ollama serve"
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise EnvironmentError(
                f"Model '{model}' not found in Ollama. "
                f"Pull it first: ollama pull {model}"
            )
        raise

    data = resp.json()
    return data.get("message", {}).get("content", "{}")


def _openrouter(model: str, prompt: str) -> str:
    """OpenRouter — unified gateway to 100+ models, many free.

    Uses OpenAI-compatible API. Get a key at https://openrouter.ai/keys
    Free models include: meta-llama/llama-3.1-8b-instruct:free,
    google/gemma-2-9b-it:free, mistralai/mistral-7b-instruct:free
    """
    from openai import OpenAI

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise EnvironmentError(
            "OPENROUTER_API_KEY environment variable is not set. "
            "Get a key at https://openrouter.ai/keys"
        )
    client = OpenAI(
        api_key=key,
        base_url="https://openrouter.ai/api/v1",
    )
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
