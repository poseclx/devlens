"""Repo onboarding analyzer — scans a local codebase and produces a guide (AI optional)."""

from __future__ import annotations
import os
import json
import re
from pathlib import Path
from dataclasses import dataclass, field


SYSTEM_PROMPT = """You are an expert software architect. Analyze the given repository structure and source files, then return a JSON object with this exact shape:

{
  "overview": "<2-4 sentence plain-English description of what this project does>",
  "architecture": "<paragraph describing the high-level architecture, key layers, and how they connect>",
  "key_files": [
    {"file": "<relative path>", "role": "<what this file does and why it matters>"}
  ],
  "entry_points": ["<list of main entry point files or commands>"],
  "tech_stack": ["<list of key technologies, frameworks, languages detected>"],
  "getting_started": [
    "<step 1>",
    "<step 2>",
    "<step 3>"
  ],
  "where_to_start": "<1-2 sentences telling a new developer exactly where to begin reading the code>"
}

Be concise and practical. Focus on helping a new developer get productive fast."""


IGNORED_DIRS = {
    ".git", ".github", "node_modules", "__pycache__", ".venv", "venv",
    "env", "dist", "build", ".next", ".nuxt", "coverage", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "target", "vendor",
}

IGNORED_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll", ".exe",
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".ico", ".webp",
    ".mp4", ".mp3", ".wav", ".pdf", ".zip", ".tar", ".gz",
    ".lock", ".sum",
}

MAX_FILE_CHARS = 3000
MAX_FILES_CONTENT = 20


@dataclass
class RepoSnapshot:
    root: Path
    structure: str = ""
    languages: list[str] = field(default_factory=list)
    file_contents: dict[str, str] = field(default_factory=dict)
    dependency_files: dict[str, str] = field(default_factory=dict)


@dataclass
class OnboardingResult:
    overview: str
    architecture: str
    key_files: list[dict]
    entry_points: list[str]
    tech_stack: list[str]
    getting_started: list[str]
    where_to_start: str
    ai_powered: bool = False

    def to_dict(self) -> dict:
        return {**self.__dict__}

    def to_markdown(self) -> str:
        ai_note = " *(AI-powered)*" if self.ai_powered else " *(static analysis)*"
        lines = [
            "# DevLens Onboarding Guide" + ai_note,
            "",
            "## Overview",
            self.overview,
            "",
            "## Architecture",
            self.architecture,
            "",
            "## Tech Stack",
            *[f"- {t}" for t in self.tech_stack],
            "",
            "## Entry Points",
            *[f"- `{e}`" for e in self.entry_points],
            "",
            "## Key Files",
        ]
        for kf in self.key_files:
            lines.append(f"- `{kf['file']}` — {kf['role']}")
        lines += [
            "",
            "## Getting Started",
            *[f"{i+1}. {step}" for i, step in enumerate(self.getting_started)],
            "",
            "## Where to Start Reading",
            self.where_to_start,
        ]
        return "\n".join(lines)


def _build_tree(root: Path, prefix: str = "", depth: int = 0, max_depth: int = 4) -> str:
    if depth > max_depth:
        return ""
    lines = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
    except PermissionError:
        return ""
    for entry in entries:
        if entry.name in IGNORED_DIRS or entry.name.startswith("."):
            continue
        connector = "├── " if entry != entries[-1] else "└── "
        lines.append(prefix + connector + entry.name)
        if entry.is_dir():
            extension = "│   " if entry != entries[-1] else "    "
            lines.append(_build_tree(entry, prefix + extension, depth + 1, max_depth))
    return "\n".join(filter(None, lines))


def _detect_languages(root: Path) -> list[str]:
    ext_map = {
        ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
        ".js": "JavaScript", ".jsx": "JavaScript", ".go": "Go",
        ".rs": "Rust", ".java": "Java", ".rb": "Ruby", ".php": "PHP",
        ".cs": "C#", ".cpp": "C++", ".c": "C", ".swift": "Swift",
        ".kt": "Kotlin", ".ex": "Elixir", ".exs": "Elixir",
    }
    found: dict[str, int] = {}
    for path in root.rglob("*"):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        lang = ext_map.get(path.suffix)
        if lang:
            found[lang] = found.get(lang, 0) + 1
    return sorted(found, key=found.get, reverse=True)


def _read_dep_files(root: Path) -> dict[str, str]:
    dep_filenames = {
        "package.json", "pyproject.toml", "requirements.txt",
        "Cargo.toml", "go.mod", "Gemfile", "pom.xml",
        "build.gradle", "composer.json", "mix.exs",
    }
    result = {}
    for name in dep_filenames:
        p = root / name
        if p.exists():
            try:
                result[name] = p.read_text(errors="ignore")[:2000]
            except Exception:
                pass
    return result


def _collect_source_files(root: Path) -> dict[str, str]:
    text_extensions = {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
        ".rb", ".php", ".cs", ".cpp", ".c", ".swift", ".kt",
        ".md", ".yaml", ".yml", ".toml", ".json", ".env.example",
    }
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts)):
        if len(files) >= MAX_FILES_CONTENT:
            break
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.suffix in IGNORED_EXTENSIONS:
            continue
        if path.suffix not in text_extensions and path.name not in {"Makefile", "Dockerfile"}:
            continue
        if path.is_file():
            try:
                content = path.read_text(errors="ignore")[:MAX_FILE_CHARS]
                files[str(path.relative_to(root))] = content
            except Exception:
                pass
    return files


def scan_repo(path: str = ".") -> RepoSnapshot:
    root = Path(path).resolve()
    snapshot = RepoSnapshot(root=root)
    snapshot.structure = _build_tree(root)
    snapshot.languages = _detect_languages(root)
    snapshot.dependency_files = _read_dep_files(root)
    snapshot.file_contents = _collect_source_files(root)
    return snapshot


def _static_onboard(snapshot: RepoSnapshot) -> OnboardingResult:
    """Fallback: build a basic onboarding guide from static analysis."""
    langs = snapshot.languages or ["unknown"]
    dep_names = list(snapshot.dependency_files.keys())

    # Guess entry points
    entry_points = []
    for fname in snapshot.file_contents:
        if fname in ("main.py", "app.py", "server.py", "index.ts", "index.js", "main.go", "main.rs"):
            entry_points.append(fname)
    if not entry_points:
        entry_points = ["See README for entry points"]

    # Key files (top-level source files)
    key_files = [
        {"file": f, "role": "Source file (role unknown — run with --ai for details)"}
        for f in list(snapshot.file_contents.keys())[:6]
    ]

    # Getting started steps
    getting_started = ["Clone the repository"]
    if "requirements.txt" in dep_names:
        getting_started.append("pip install -r requirements.txt")
    elif "pyproject.toml" in dep_names:
        getting_started.append("pip install -e .")
    elif "package.json" in dep_names:
        getting_started.append("npm install")
    elif "go.mod" in dep_names:
        getting_started.append("go mod download")
    getting_started.append("See README.md for further instructions")

    return OnboardingResult(
        overview=f"A {', '.join(langs[:2])} project. Run with --ai for an AI-generated description.",
        architecture="Static analysis only. Use --ai flag for a detailed architecture overview.",
        key_files=key_files,
        entry_points=entry_points,
        tech_stack=langs + dep_names,
        getting_started=getting_started,
        where_to_start="Start with the README, then explore the entry point files listed above.",
        ai_powered=False,
    )


def _build_prompt(snapshot: RepoSnapshot) -> str:
    parts = [
        f"Repository: {snapshot.root.name}",
        f"Languages detected: {', '.join(snapshot.languages) or 'unknown'}",
        "",
        "## Directory Structure",
        snapshot.structure or "(empty)",
        "",
    ]
    if snapshot.dependency_files:
        parts.append("## Dependency Files")
        for name, content in snapshot.dependency_files.items():
            parts.append(f"### {name}\n{content}")
        parts.append("")
    if snapshot.file_contents:
        parts.append("## Source Files (truncated)")
        for fname, content in snapshot.file_contents.items():
            parts.append(f"### {fname}\n{content}\n")
    return "\n".join(parts)


def _call_llm(model: str, prompt: str) -> str:
    """Route to the correct LLM provider based on model name prefix."""
    if model.startswith("gpt") or model.startswith("o1") or model.startswith("o3"):
        return _openai(model, prompt)
    elif model.startswith("claude"):
        return _anthropic(model, prompt)
    elif model.startswith("gemini"):
        return _gemini(model, prompt)
    else:
        raise ValueError(
            f"Unsupported model: {model!r}. "
            "Use gpt-* / o1-* / o3-* (OpenAI), claude-* (Anthropic), or gemini-* (Google)."
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


def analyze_repo(
    snapshot: RepoSnapshot,
    use_ai: bool = False,
    model: str = "gpt-4o",
    api_key: str | None = None,
) -> OnboardingResult:
    """Analyze the repo snapshot. Uses AI only when use_ai=True and a key is available."""
    if not use_ai:
        return _static_onboard(snapshot)

    prompt = _build_prompt(snapshot)
    # api_key override: inject into env temporarily if provided
    if api_key:
        _inject_key(model, api_key)

    raw = _call_llm(model, prompt)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group()) if match else {}

    return OnboardingResult(
        overview=data.get("overview", ""),
        architecture=data.get("architecture", ""),
        key_files=data.get("key_files", []),
        entry_points=data.get("entry_points", []),
        tech_stack=data.get("tech_stack", []),
        getting_started=data.get("getting_started", []),
        where_to_start=data.get("where_to_start", ""),
        ai_powered=True,
    )


def _inject_key(model: str, api_key: str) -> None:
    """Temporarily set the correct env var for the given model's provider."""
    if model.startswith("claude"):
        os.environ.setdefault("ANTHROPIC_API_KEY", api_key)
    elif model.startswith("gemini"):
        os.environ.setdefault("GEMINI_API_KEY", api_key)
    else:
        os.environ.setdefault("OPENAI_API_KEY", api_key)
