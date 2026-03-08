"""DevLens CLI — entry point."""

from __future__ import annotations
import sys
import os
import json
import subprocess
import platform
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich import box

from devlens.github import fetch_pr
from devlens.analyzer import analyze_pr, ReviewResult
from devlens.config import load_config, get_cache_config, get_rules_config, get_dashboard_config, get_scoreboard_config, get_plugin_config, get_ai_review_config
from devlens.plugins import (
    PluginManager,
    PluginType,
    install_plugin_from_pip,
    uninstall_plugin_from_pip,
    create_plugin_template,
)
from devlens.ai_review import run_ai_review_sync, configure_api_key, ReviewMode
from devlens.language_server import start_server as _start_lsp
from devlens.ignore import load_ignore_patterns

console = Console()

# ── Provider / model catalogue ────────────────────────────────

PROVIDERS = {
    "openai": {
        "label": "OpenAI (GPT-4o)",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o3-mini"],
        "default": "gpt-4o",
        "env": "OPENAI_API_KEY",
        "install": "openai",
        "key_prefix": "sk-",
        "key_hint": "Starts with sk-...",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "models": [
            "claude-opus-4-5",
            "claude-sonnet-4-5",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ],
        "default": "claude-3-5-sonnet-20241022",
        "env": "ANTHROPIC_API_KEY",
        "install": "anthropic",
        "key_prefix": "sk-ant-",
        "key_hint": "Starts with sk-ant-...",
    },
    "gemini": {
        "label": "Google Gemini",
        "models": ["gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash", "gemini-2.5-pro"],
        "default": "gemini-1.5-pro",
        "env": "GEMINI_API_KEY",
        "install": "google-generativeai",
        "key_prefix": "AI",
        "key_hint": "Get from https://aistudio.google.com/apikey",
    },
    "groq": {
        "label": "Groq (FREE — ultra-fast)",
        "models": [
            "groq/llama-3.3-70b-versatile",
            "groq/llama-3.1-8b-instant",
            "groq/mixtral-8x7b-32768",
            "groq/gemma2-9b-it",
        ],
        "default": "groq/llama-3.3-70b-versatile",
        "env": "GROQ_API_KEY",
        "install": "groq",
        "key_prefix": "gsk_",
        "key_hint": "FREE — get from https://console.groq.com/keys",
    },
    "ollama": {
        "label": "Ollama (FREE — runs locally)",
        "models": [
            "ollama/llama3.1",
            "ollama/llama3.1:70b",
            "ollama/codellama",
            "ollama/mistral",
            "ollama/deepseek-coder-v2",
        ],
        "default": "ollama/llama3.1",
        "env": "",
        "install": "",
        "key_prefix": "",
        "key_hint": "No API key needed! Install Ollama from https://ollama.com",
        "no_key": True,
    },
    "openrouter": {
        "label": "OpenRouter (100+ models, free tier)",
        "models": [
            "openrouter/meta-llama/llama-3.1-8b-instruct:free",
            "openrouter/google/gemma-2-9b-it:free",
            "openrouter/mistralai/mistral-7b-instruct:free",
            "openrouter/meta-llama/llama-3.1-70b-instruct",
            "openrouter/anthropic/claude-3.5-sonnet",
        ],
        "default": "openrouter/meta-llama/llama-3.1-8b-instruct:free",
        "env": "OPENROUTER_API_KEY",
        "install": "openai",
        "key_prefix": "sk-or-",
        "key_hint": "Get from https://openrouter.ai/keys (free models available!)",
    },
}

_CONFIG_PATH = Path.home() / ".devlens" / "config.json"


def _resolve_model(model_flag: str | None, cfg: dict) -> tuple[str, str]:
    """Return (model, provider) from flag > config > saved setup."""
    if model_flag:
        for pname, pinfo in PROVIDERS.items():
            if any(model_flag.startswith(prefix) for prefix in _model_prefixes(pname)):
                return model_flag, pname
        raise click.BadParameter(
            f"Cannot detect provider for model '{model_flag}'. "
            "Supported prefixes: gpt-*, claude-*, gemini-*, groq/*, ollama/*, openrouter/*"
        )

    saved = _load_setup()
    # Saved setup (from `devlens init`) takes priority over project defaults
    # because project config has a hardcoded default model ("gpt-4o") that
    # would always shadow the user's chosen provider.
    model = saved.get("model") or cfg.get("model")
    provider = saved.get("provider") or cfg.get("provider")

    if model and provider:
        pinfo = PROVIDERS.get(provider, {})
        # Ollama needs no API key
        if pinfo.get("no_key"):
            return model, provider
        # Check if API key is available
        env_var = pinfo.get("env", "")
        api_key = saved.get("api_key") or os.environ.get(env_var, "")
        if not api_key:
            console.print()
            console.print(Panel(
                f"[bold yellow]API key missing for {pinfo.get('label', provider)}.[/]\n\n"
                "Run [bold cyan]devlens init[/] to configure your API key.",
                title="[bold]API Key Required[/]",
                border_style="yellow",
            ))
            sys.exit(1)
        # Set env var from saved config if not already set
        if env_var and not os.environ.get(env_var) and saved.get("api_key"):
            os.environ[env_var] = saved["api_key"]
        return model, provider

    # Nothing configured — run init automatically
    console.print()
    console.print(Panel(
        "[bold yellow]DevLens is not configured yet.[/]\n\n"
        "Starting first-time setup...",
        title="[bold]Welcome to DevLens![/]",
        border_style="cyan",
    ))
    console.print()
    _run_init_flow()
    # Reload after init
    saved = _load_setup()
    model = saved.get("model")
    provider = saved.get("provider")
    if model and provider:
        pinfo = PROVIDERS.get(provider, {})
        env_var = pinfo.get("env", "")
        if not os.environ.get(env_var) and saved.get("api_key"):
            os.environ[env_var] = saved["api_key"]
        return model, provider
    sys.exit(1)


def _model_prefixes(provider: str) -> list[str]:
    if provider == "openai":
        return ["gpt", "o1", "o3"]
    if provider == "anthropic":
        return ["claude"]
    if provider == "gemini":
        return ["gemini"]
    if provider == "groq":
        return ["groq/"]
    if provider == "ollama":
        return ["ollama/"]
    if provider == "openrouter":
        return ["openrouter/"]
    return []


def _load_setup() -> dict:
    if _CONFIG_PATH.exists():
        try:
            return json.loads(_CONFIG_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_setup(data: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(data, indent=2))


def _install_package(package: str) -> bool:
    """Install a Python package, returns True on success."""
    console.print(f"\n[bold cyan]Installing {package}...[/]")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", package, "--quiet"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        console.print(f"[green]OK[/] {package} installed.")
        return True
    except subprocess.CalledProcessError:
        console.print(f"[red]Failed to install {package}.[/] Run manually: pip install {package}")
        return False


def _fix_windows_path() -> bool:
    """Add Python Scripts dir to user PATH on Windows. Returns True if changed."""
    if platform.system() != "Windows":
        return False

    scripts_dir = Path(sys.executable).parent / "Scripts"
    if not scripts_dir.exists():
        # Microsoft Store Python layout
        scripts_dir = Path(sys.executable).parent.parent / "Scripts"
    if not scripts_dir.exists():
        # Try site packages scripts
        import site
        user_scripts = Path(site.getusersitepackages()).parent / "Scripts"
        if user_scripts.exists():
            scripts_dir = user_scripts

    scripts_str = str(scripts_dir)
    current_path = os.environ.get("PATH", "")

    if scripts_str.lower() in current_path.lower():
        return False  # Already in PATH

    console.print(f"\n[yellow]Adding to PATH:[/] {scripts_str}")
    try:
        # Use setx to persist for user
        subprocess.check_call(
            ["setx", "PATH", f"{scripts_str};%PATH%"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Also update current session
        os.environ["PATH"] = f"{scripts_str};{current_path}"
        console.print("[green]OK[/] PATH updated. New terminals will have 'devlens' available.")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.print(
            f"[yellow]Could not auto-update PATH.[/]\n"
            f"Add this directory to your PATH manually:\n"
            f"  [bold]{scripts_str}[/]"
        )
        return False


def _run_init_flow() -> None:
    """Interactive setup flow — called by `devlens init` or auto on first use."""
    console.print(Panel(
        "[bold cyan]DevLens Setup[/]\n\n"
        "Let's configure your AI provider. This takes 30 seconds.\n"
        "You can change this any time by running [bold]devlens init[/] again.",
        border_style="cyan",
    ))
    console.print()

    # --- 1. Provider choice ---
    provider_choices = list(PROVIDERS.keys())
    num_providers = len(provider_choices)
    console.print("  [dim]── Paid ──[/]")
    for i, (key, info) in enumerate(PROVIDERS.items(), 1):
        tag = " [bold green]FREE[/]" if info.get("no_key") or "FREE" in info["label"] else ""
        if i == 4 and num_providers > 3:
            console.print()
            console.print("  [dim]── Free / Local ──[/]")
        console.print(f"  [bold cyan]{i}.[/] {info['label']}{tag}")
    console.print()

    valid_choices = [str(i) for i in range(1, num_providers + 1)]
    raw = click.prompt(
        f"Select provider (1-{num_providers})",
        type=click.Choice(valid_choices),
        show_choices=False,
    )
    provider = provider_choices[int(raw) - 1]
    pinfo = PROVIDERS[provider]

    # --- 2. Model choice ---
    console.print()
    console.print(f"[bold]Models for {pinfo['label']}:[/]")
    for i, m in enumerate(pinfo["models"], 1):
        default_tag = " [dim](default)[/]" if m == pinfo["default"] else ""
        free_tag = " [green]FREE[/]" if ":free" in m else ""
        console.print(f"  [bold cyan]{i}.[/] {m}{default_tag}{free_tag}")
    console.print()

    model_raw = click.prompt(
        "Select model (Enter for default)",
        default="1",
        type=click.Choice([str(i) for i in range(1, len(pinfo["models"]) + 1)]),
        show_choices=False,
    )
    model = pinfo["models"][int(model_raw) - 1]

    # --- 3. API Key (skip for Ollama) ---
    api_key = ""
    env_var = pinfo.get("env", "")

    if pinfo.get("no_key"):
        # Ollama: no key needed, just check connectivity
        console.print()
        console.print("[bold green]No API key needed![/] Ollama runs locally.")
        console.print("[dim]Make sure Ollama is running: ollama serve[/]")
        console.print(f"[dim]And pull the model: ollama pull {model.removeprefix('ollama/')}[/]")
    else:
        existing_key = os.environ.get(env_var, "") if env_var else ""
        console.print()
        if existing_key:
            masked = existing_key[:8] + "..." + existing_key[-4:]
            console.print(f"[green]Found existing key:[/] {masked}")
            use_existing = click.confirm("Use this key?", default=True)
            if use_existing:
                api_key = existing_key
            else:
                api_key = click.prompt(
                    f"Enter your {pinfo['label']} API key",
                    hide_input=True,
                ).strip()
        else:
            console.print(f"[bold]Enter your {pinfo['label']} API key[/]")
            console.print(f"[dim]{pinfo['key_hint']}[/]")
            api_key = click.prompt(
                "API key",
                hide_input=True,
            ).strip()

        if not api_key:
            console.print("[red]No API key provided. Setup cancelled.[/]")
            return

        # Set for current session
        if env_var:
            os.environ[env_var] = api_key

    # --- 4. Install AI package (skip if empty) ---
    if pinfo.get("install"):
        _install_package(pinfo["install"])

    # --- 5. Fix PATH on Windows ---
    if platform.system() == "Windows":
        _fix_windows_path()

    # --- 6. Save config ---
    setup_data = {
        "provider": provider,
        "model": model,
    }
    if api_key:
        setup_data["api_key"] = api_key
    _save_setup(setup_data)

    console.print()
    console.print(Panel(
        f"[bold green]Setup complete![/]\n\n"
        f"  Provider : [cyan]{pinfo['label']}[/]\n"
        f"  Model    : [cyan]{model}[/]\n"
        f"  API Key  : [dim]{api_key[:8]}...{api_key[-4:]}[/]\n"
        f"  Config   : [dim]{_CONFIG_PATH}[/]\n\n"
        f"You're ready to go! Try:\n"
        f"  [bold]devlens review 42 --repo owner/repo --ai[/]\n"
        f"  [bold]devlens onboard . --ai[/]",
        border_style="green",
    ))
    console.print()


# ── main group ────────────────────────────────────────────────

@click.group()
@click.version_option(version="0.8.0", prog_name="devlens")
def main() -> None:
    """DevLens -- AI-powered developer assistant.

    Commands:\n
      init     Set up your AI provider (run once)\n
      review   Analyze a GitHub Pull Request\n
      scan     Security scanning (secrets, vulnerabilities)\n
      onboard  Generate an onboarding guide for a repository\n
      docs     Check documentation health\n
      doctor   Diagnose and fix common setup issues\n
      plugin   Manage DevLens plugins\n
      ai-review  AI-powered code review\n
      lsp      Language Server Protocol commands\n
    """


# ── devlens init ──────────────────────────────────────────────

@main.command()
def init() -> None:
    """Interactive setup: choose AI provider, model, and enter API key.

    Saves everything to ~/.devlens/config.json so you never
    have to pass --model or set env vars again.

    Examples:\n
      devlens init\n
    """
    console.print()
    _run_init_flow()


# ── devlens doctor ────────────────────────────────────────────

@main.command()
def doctor() -> None:
    """Diagnose and fix common setup issues.

    Checks:\n
      - Python and pip availability\n
      - PATH configuration (auto-fixes on Windows)\n
      - AI provider configuration\n
      - API key validity\n

    Examples:\n
      devlens doctor\n
    """
    console.print()
    console.print(Panel("[bold cyan]DevLens Doctor[/]", border_style="cyan"))
    console.print()

    all_ok = True

    # 1. Python
    console.print(f"[bold]Python:[/] {sys.version.split()[0]} at {sys.executable}")
    console.print("[green]  OK[/]")
    console.print()

    # 2. PATH check
    console.print("[bold]PATH check:[/]")
    import shutil
    devlens_path = shutil.which("devlens")
    if devlens_path:
        console.print(f"[green]  OK[/] devlens found at {devlens_path}")
    else:
        console.print("[yellow]  WARNING[/] 'devlens' not found in PATH")
        if platform.system() == "Windows":
            console.print("  Attempting auto-fix...")
            _fix_windows_path()
        else:
            console.print("  Add the pip scripts directory to your PATH.")
        all_ok = False
    console.print()

    # 3. Config
    console.print("[bold]Configuration:[/]")
    saved = _load_setup()
    if saved.get("provider") and saved.get("model"):
        pinfo = PROVIDERS.get(saved["provider"], {})
        console.print(f"[green]  OK[/] Provider: {pinfo.get('label', saved['provider'])}")
        console.print(f"[green]  OK[/] Model: {saved['model']}")
    else:
        console.print("[yellow]  WARNING[/] Not configured. Run: devlens init")
        all_ok = False
    console.print()

    # 4. API Key
    console.print("[bold]API Key:[/]")
    if saved.get("api_key"):
        key = saved["api_key"]
        console.print(f"[green]  OK[/] Key saved: {key[:8]}...{key[-4:]}")
    elif saved.get("provider"):
        env_var = PROVIDERS.get(saved["provider"], {}).get("env", "")
        if os.environ.get(env_var):
            console.print(f"[green]  OK[/] Found in environment: {env_var}")
        else:
            console.print(f"[yellow]  WARNING[/] No API key found. Run: devlens init")
            all_ok = False
    else:
        console.print("[yellow]  WARNING[/] No provider configured.")
        all_ok = False
    console.print()

    # 5. AI package
    console.print("[bold]AI Package:[/]")
    if saved.get("provider"):
        pkg = PROVIDERS.get(saved["provider"], {}).get("install", "")
        try:
            __import__(pkg.replace("-", "_").split(">=")[0])
            console.print(f"[green]  OK[/] {pkg} is installed")
        except ImportError:
            console.print(f"[yellow]  WARNING[/] {pkg} not installed. Installing...")
            _install_package(pkg)
            all_ok = False
    else:
        console.print("[dim]  Skipped (no provider configured)[/]")
    console.print()

    if all_ok:
        console.print(Panel("[bold green]All checks passed![/]", border_style="green"))
    else:
        console.print(Panel(
            "[bold yellow]Some issues found.[/]\n"
            "Run [bold]devlens init[/] to fix configuration issues.\n"
            "Restart your terminal after PATH changes.",
            border_style="yellow",
        ))
    console.print()


def _static_review(pr) -> ReviewResult:
    """Quick heuristic review without AI — classifies files by risk based on patterns."""
    RISKY_PATTERNS = {
        "security": ["auth", "login", "password", "token", "secret", "crypt", "jwt", "oauth", "session"],
        "config": ["config", "env", ".yml", ".yaml", ".toml", "settings", "dockerfile", "docker-compose"],
        "database": ["migration", "schema", "model", "sql", "db"],
        "api": ["route", "endpoint", "controller", "handler", "middleware", "api"],
    }
    SAFE_PATTERNS = [".md", ".txt", ".rst", "readme", "changelog", "license", "docs/",
                     ".lock", "package-lock", ".gitignore", ".editorconfig"]

    risk_items = []
    safe_items = []

    for f in pr.files:
        name = f["filename"].lower()
        additions = f.get("additions", 0)
        deletions = f.get("deletions", 0)

        # Check safe patterns first
        if any(pat in name for pat in SAFE_PATTERNS):
            safe_items.append({"file": f["filename"], "reason": "Documentation or config — safe to skim"})
            continue

        # Check risky patterns
        flagged = False
        for category, patterns in RISKY_PATTERNS.items():
            if any(pat in name for pat in patterns):
                risk_items.append({
                    "file": f["filename"],
                    "reason": f"Touches {category}-related code (+{additions} -{deletions})",
                    "severity": "high" if additions + deletions > 100 else "medium",
                })
                flagged = True
                break

        if not flagged:
            if additions + deletions > 200:
                risk_items.append({
                    "file": f["filename"],
                    "reason": f"Large change (+{additions} -{deletions})",
                    "severity": "medium",
                })
            else:
                safe_items.append({"file": f["filename"], "reason": f"Standard change (+{additions} -{deletions})"})

    total_changes = pr.additions + pr.deletions
    if risk_items:
        verdict = f"Needs careful review — {len(risk_items)} file(s) flagged across {total_changes} line changes."
    else:
        verdict = f"Looks straightforward — {total_changes} line changes, no risky patterns detected."

    return ReviewResult(
        pr_number=pr.number,
        title=pr.title,
        summary=f"PR changes {pr.changed_files} file(s) with +{pr.additions} -{pr.deletions} lines. (Static analysis — use --ai for deeper review)",
        risk_items=risk_items,
        safe_items=safe_items,
        verdict=verdict,
    )


# ── devlens review ────────────────────────────────────────────

@main.command()
@click.argument("pr_number", type=int)
@click.option("--repo", "-r", default=None, help="owner/repo (auto-detects from git remote if omitted)")
@click.option(
    "--format", "-f", "output_format",
    type=click.Choice(["text", "markdown", "json", "html"]),
    default="text", show_default=True,
    help="Output format.",
)
@click.option(
    "--detail", "-d",
    type=click.Choice(["low", "medium", "high"]),
    default=None,
    help="Analysis detail level (overrides config).",
)
@click.option("--output", "-o", default=None, help="Save output to a file.")
@click.option("--ai", is_flag=True, default=False, help="Enable AI-powered analysis.")
@click.option("--comment", is_flag=True, default=False, help="Post result as a GitHub PR comment (requires GITHUB_TOKEN).")
@click.option("--summary", is_flag=True, default=False, help="Show a concise 3-5 sentence summary of the PR before the full review.")
@click.option(
    "--model", "-m", default=None,
    help="LLM model to use: gpt-4o, claude-3-5-sonnet-20241022, gemini-1.5-pro, etc. "
         "Overrides saved provider. Requires the matching API key env var.",
)
def review(
    pr_number: int,
    repo: str | None,
    output_format: str,
    detail: str | None,
    output: str | None,
    ai: bool,
    comment: bool,
    summary: bool,
    model: str | None,
) -> None:
    """Analyze a Pull Request and surface what actually matters.

    PR_NUMBER is the GitHub PR number to review.

    Use --ai to enable AI analysis. The provider is picked from your saved
    setup (devlens init) or from the --model flag.\n
    Use --comment to post the result directly as a GitHub PR comment.\n

    Examples:\n
      devlens review 42\n
      devlens review 42 --ai\n
      devlens review 42 --ai --comment\n
      devlens review 42 --ai --model claude-3-5-sonnet-20241022\n
      devlens review 42 --ai --format html --output report.html\n
    """
    cfg = load_config()
    detail_level = detail or cfg.get("detail", "medium")
    resolved_repo = repo or cfg.get("repo") or _detect_repo()
    if not resolved_repo:
        console.print("[bold red]Error:[/] Could not detect repo. Run inside a git repo or pass --repo owner/name.")
        sys.exit(1)

    resolved_model: str | None = None
    if ai:
        resolved_model, _ = _resolve_model(model, cfg)

    with console.status(f"[bold cyan]Fetching PR #{pr_number} from {resolved_repo}..."):
        pr_data = fetch_pr(resolved_repo, pr_number)

    if ai and resolved_model:
        cfg["model"] = resolved_model

    status_msg = f"[bold cyan]Analyzing with {resolved_model}..." if ai else "[bold cyan]Running static analysis..."
    with console.status(status_msg):
        if ai:
            try:
                result = analyze_pr(pr_data, detail=detail_level, config=cfg)
            except EnvironmentError as exc:
                console.print(f"[bold yellow]Warning:[/] {exc}")
                console.print("[dim]Falling back to static analysis...[/]")
                result = _static_review(pr_data)
        else:
            result = _static_review(pr_data)

    # Show PR summary if requested
    if summary:
        from devlens.summarizer import summarize_pr
        with console.status("[bold cyan]Generating PR summary..."):
            pr_summary = summarize_pr(pr_data, use_ai=ai, model=resolved_model or "gpt-4o")
        console.print()
        console.print(Panel(
            Markdown(pr_summary.to_markdown()),
            title="[bold]PR Summary[/]",
            border_style="blue",
        ))
        console.print()

    if output_format == "html":
        from devlens.reporter import ReportData, export_report
        report_data = ReportData(
            pr_number=pr_number, pr_title=pr_data.title,
            repo=resolved_repo, review=result,
        )
        out = output or f"devlens-pr-{pr_number}.html"
        export_report(report_data, out, fmt="html")
        console.print(f"[bold green]HTML report saved:[/] {out}")
    elif output_format == "json":
        text = json.dumps(result.to_dict(), indent=2)
        _emit(text, output, console)
    elif output_format == "markdown":
        text = result.to_markdown()
        console.print(Markdown(text))
        if output:
            Path(output).write_text(text)
            console.print(f"\n[dim]Saved to {output}[/]")
    else:
        result.print_rich(console)

    # Post as PR comment if requested
    if comment:
        from devlens.commenter import post_review_comment
        try:
            comment_url = post_review_comment(result, resolved_repo, pr_number)
            console.print(f"\n[bold green]Comment posted:[/] {comment_url}")
        except EnvironmentError as exc:
            console.print(f"\n[bold red]Error:[/] {exc}")
        except Exception as exc:
            console.print(f"\n[bold red]Failed to post comment:[/] {exc}")


# ── devlens onboard ───────────────────────────────────────────

@main.command()
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--format", "-f", "output_format",
    type=click.Choice(["text", "markdown", "json", "html"]),
    default="text", show_default=True,
    help="Output format.",
)
@click.option("--output", "-o", default=None, help="Save output to a file.")
@click.option(
    "--model", "-m", default=None,
    help="LLM model to use: gpt-4o, claude-3-5-sonnet-20241022, gemini-1.5-pro, etc.",
)
@click.option("--ai", is_flag=True, default=False, help="Enable AI-powered analysis.")
def onboard(path: str, output_format: str, output: str | None, model: str | None, ai: bool) -> None:
    """Generate an onboarding guide for a repository.

    PATH defaults to the current directory.

    Use --ai to enable AI analysis. The provider is picked from your saved
    setup (devlens init) or from the --model flag.\n

    Examples:\n
      devlens onboard .\n
      devlens onboard . --ai\n
      devlens onboard ~/projects/my-app --ai --model gemini-1.5-pro\n
      devlens onboard . --ai --format html --output onboarding.html\n
    """
    from devlens.onboarder import scan_repo, analyze_repo

    cfg = load_config()

    resolved_model: str | None = None
    if ai:
        resolved_model, _ = _resolve_model(model, cfg)

    with console.status("[bold cyan]Scanning repository..."):
        snapshot = scan_repo(path)

    lang_str = ", ".join(snapshot.languages[:5]) or "unknown"
    console.print(
        Panel(
            f"[bold]{snapshot.root.name}[/]  |  Languages: [cyan]{lang_str}[/]  |  Files scanned: [cyan]{len(snapshot.file_contents)}[/]",
            title="[bold green]Repo Snapshot[/]",
            border_style="green",
        )
    )

    status_msg = f"[bold cyan]Generating onboarding guide with {resolved_model}..." if ai else "[bold cyan]Running static onboarding analysis..."
    with console.status(status_msg):
        try:
            result = analyze_repo(snapshot, use_ai=ai, model=resolved_model or "gpt-4o")
        except EnvironmentError as exc:
            console.print(f"[bold yellow]Warning:[/] {exc}")
            console.print("[dim]Falling back to static analysis...[/]")
            result = analyze_repo(snapshot, use_ai=False)

    if output_format == "html":
        from devlens.reporter import render_onboard_html, save_html
        html = render_onboard_html(result, repo_name=snapshot.root.name)
        out = output or "devlens-onboarding.html"
        save_html(html, out)
        console.print(f"[bold green]HTML report saved:[/] {out}")
    elif output_format == "json":
        text = json.dumps(result.to_dict(), indent=2)
        _emit(text, output, console)
    elif output_format == "markdown":
        text = result.to_markdown()
        console.print(Markdown(text))
        if output:
            Path(output).write_text(text)
            console.print(f"\n[dim]Saved to {output}[/]")
    else:
        _print_onboarding_rich(result, console)


# ── devlens docs ──────────────────────────────────────────────

@main.group()
def docs() -> None:
    """Documentation health commands."""


@docs.command("check")
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--format", "-f", "output_format",
    type=click.Choice(["text", "markdown", "json", "html"]),
    default="text", show_default=True,
    help="Output format.",
)
@click.option("--output", "-o", default=None, help="Save output to a file.")
@click.option("--run-code", is_flag=True, default=False, help="Actually execute Python code blocks to test them.")
@click.option(
    "--model", "-m", default=None,
    help="LLM model to use: gpt-4o, claude-3-5-sonnet-20241022, gemini-1.5-pro, etc.",
)
@click.option("--ai", is_flag=True, default=False, help="Enable AI-powered analysis.")
def docs_check(
    file_path: str,
    output_format: str,
    output: str | None,
    run_code: bool,
    model: str | None,
    ai: bool,
) -> None:
    """Check a documentation file for stale or broken code examples.

    FILE_PATH is the Markdown file to check (e.g. README.md).

    Use --ai to enable AI analysis. The provider is picked from your saved
    setup (devlens init) or from the --model flag.\n

    Examples:\n
      devlens docs check README.md\n
      devlens docs check README.md --ai\n
      devlens docs check README.md --ai --model gemini-1.5-pro\n
      devlens docs check docs/quickstart.md --ai --format html --output report.html\n
      devlens docs check README.md --run-code\n
    """
    from devlens.docs_checker import check_docs

    cfg = load_config()

    resolved_model: str | None = None
    if ai:
        resolved_model, _ = _resolve_model(model, cfg)

    status_msg = f"[bold cyan]Checking {file_path} with AI ({resolved_model})..." if ai else f"[bold cyan]Checking {file_path}..."
    with console.status(status_msg):
        try:
            result = check_docs(file_path, run_code=run_code, use_ai=ai, model=resolved_model or "gpt-4o")
        except EnvironmentError as exc:
            console.print(f"[bold yellow]Warning:[/] {exc}")
            console.print("[dim]Falling back to static analysis...[/]")
            result = check_docs(file_path, run_code=run_code, use_ai=False)

    if output_format == "html":
        from devlens.reporter import render_docs_html, save_html
        html = render_docs_html(result)
        out = output or "devlens-docs-health.html"
        save_html(html, out)
        console.print(f"[bold green]HTML report saved:[/] {out}")
    elif output_format == "json":
        text = json.dumps(result.to_dict(), indent=2)
        _emit(text, output, console)
    elif output_format == "markdown":
        text = result.to_markdown()
        console.print(Markdown(text))
        if output:
            Path(output).write_text(text)
    else:
        _print_docs_rich(result, console)


# ── devlens scan ─────────────────────────────────────────────────────

@main.group()
def scan() -> None:
    """Security scanning commands."""


@scan.command("pr")
@click.argument("pr_number", type=int)
@click.option("--repo", "-r", default=None, help="owner/repo (auto-detects from git remote if omitted)")
@click.option(
    "--format", "-f", "output_format",
    type=click.Choice(["text", "markdown", "json", "html"]),
    default="text", show_default=True,
    help="Output format.",
)
@click.option("--output", "-o", default=None, help="Save output to a file.")
@click.option("--ai", is_flag=True, default=False, help="Enable AI-powered deep analysis.")
@click.option("--comment", is_flag=True, default=False, help="Post result as a GitHub PR comment.")
@click.option("--fix", is_flag=True, default=False, help="Generate fix suggestions for findings.")
@click.option(
    "--model", "-m", default=None,
    help="LLM model for AI analysis.",
)
@click.option("--rules", is_flag=True, default=False, help="Run custom rules against PR files.")
def scan_pr_cmd(
    pr_number: int,
    repo: str | None,
    output_format: str,
    output: str | None,
    ai: bool,
    comment: bool,
    fix: bool,
    model: str | None,
    rules: bool,
) -> None:
    """Scan a Pull Request for security vulnerabilities.

    Detects hardcoded secrets, SQL injection, command injection,
    unsafe deserialization, and 20+ other security patterns.

    Use --ai for deeper AI-powered analysis beyond regex patterns.
    Use --comment to post the security report as a PR comment.\n

    Examples:\n
      devlens scan pr 42\n
      devlens scan pr 42 --ai\n
      devlens scan pr 42 --ai --comment\n
      devlens scan pr 42 --format json --output security.json\n
    """
    from devlens.security import scan_pr, Severity
    from devlens.config import get_security_config

    cfg = load_config()
    sec_cfg = get_security_config(cfg)
    resolved_repo = repo or cfg.get("repo") or _detect_repo()
    if not resolved_repo:
        console.print("[bold red]Error:[/] Could not detect repo. Run inside a git repo or pass --repo owner/name.")
        sys.exit(1)

    resolved_model: str | None = None
    if ai:
        resolved_model, _ = _resolve_model(model, cfg)

    with console.status(f"[bold cyan]Fetching PR #{pr_number} from {resolved_repo}..."):
        pr_data = fetch_pr(resolved_repo, pr_number)

    status_msg = f"[bold cyan]Security scanning with AI ({resolved_model})..." if ai else "[bold cyan]Running security scan..."
    with console.status(status_msg):
        custom_rules = sec_cfg.get("custom_rules", [])
        # Convert severity strings to Severity enum for custom rules
        for rule in custom_rules:
            if isinstance(rule.get("severity"), str):
                rule["severity"] = Severity(rule["severity"])

        result = scan_pr(
            pr_data,
            use_ai=ai,
            model=resolved_model or "gpt-4o",
            custom_rules=custom_rules if custom_rules else None,
        )

    # Display results
    score = result.score
    grade = result.grade
    color = "green" if score >= 90 else "yellow" if score >= 60 else "red"

    if output_format == "json":
        text = json.dumps(result.to_dict(), indent=2)
        _emit(text, output, console)
    elif output_format == "markdown":
        text = result.to_markdown()
        console.print(Markdown(text))
        if output:
            Path(output).write_text(text)
            console.print(f"\n[dim]Saved to {output}[/]")
    elif output_format == "html":
        from devlens.reporter import ReportData, export_report
        report_data = ReportData(
            pr_number=pr_number, pr_title=pr_data.title,
            repo=resolved_repo, scan_result=result,
        )
        out = output or f"devlens-scan-{pr_number}.html"
        export_report(report_data, out, fmt="html")
        console.print(f"[bold green]HTML report saved:[/] {out}")
    else:
        # Rich text output
        console.print()
        console.print(
            Panel(
                f"[bold {color}]{score}/100[/] (Grade: [bold]{grade}[/])",
                title=f"[bold]Security Score — PR #{pr_number}[/]",
                border_style=color,
            )
        )
        console.print(f"\n[dim]Files scanned: {result.files_scanned}/{result.total_files}[/]\n")

        if result.findings:
            from rich.table import Table
            table = Table(title=f"Findings ({len(result.findings)})", box=box.SIMPLE_HEAVY, show_lines=True)
            table.add_column("Severity", justify="center", no_wrap=True)
            table.add_column("Rule", style="dim", no_wrap=True)
            table.add_column("File", style="cyan", no_wrap=True)
            table.add_column("Description")

            for f in sorted(result.findings, key=lambda x: ["critical", "high", "medium", "low", "info"].index(x.severity.value)):
                sev = f.severity.value
                sev_color = {"critical": "red bold", "high": "red", "medium": "yellow", "low": "blue"}.get(sev, "white")
                loc = f.file + (f":{f.line}" if f.line else "")
                table.add_row(f"[{sev_color}]{sev.upper()}[/{sev_color}]", f.rule_id, loc, f.title)

            console.print(table)
        else:
            console.print("[bold green]No security issues found![/]")

        if result.ai_summary:
            console.print()
            console.print(Panel(result.ai_summary, title="[bold]AI Security Assessment[/]", border_style="blue"))

        console.print()

    # Generate fix suggestions if requested
    if fix:
        from devlens.fixer import suggest_fixes, format_fixes_markdown
        with console.status("[bold cyan]Generating fix suggestions..."):
            file_contents = {}
            for f in pr_data.files:
                if f.get("patch"):
                    # Reconstruct content from patch (added lines only)
                    added = [l[1:] for l in f["patch"].split("\n") if l.startswith("+") and not l.startswith("+++")]
                    file_contents[f["filename"]] = "\n".join(added)
            fixes = suggest_fixes(result.findings, file_contents, use_ai=ai, model=resolved_model or "gpt-4o")
        if fixes:
            console.print()
            console.print(Panel(
                Markdown(format_fixes_markdown(fixes)),
                title=f"[bold]Fix Suggestions ({len(fixes)})[/]",
                border_style="green",
            ))
        else:
            console.print("\n[dim]No automatic fix suggestions available for these findings.[/]")

    # Post as PR comment if requested
    if comment:
        from devlens.commenter import post_security_comment
        try:
            comment_url = post_security_comment(result, resolved_repo, pr_number)
            console.print(f"[bold green]Comment posted:[/] {comment_url}")
        except EnvironmentError as exc:
            console.print(f"[bold red]Error:[/] {exc}")
        except Exception as exc:
            console.print(f"[bold red]Failed to post comment:[/] {exc}")

    # Check fail_on threshold
    fail_on = sec_cfg.get("fail_on", "high")
    threshold_order = ["low", "medium", "high", "critical"]
    if fail_on in threshold_order:
        threshold_idx = threshold_order.index(fail_on)
        for f in result.findings:
            if f.severity.value in threshold_order[threshold_idx:]:
                console.print(f"\n[bold red]FAILED:[/] Found {f.severity.value} severity issue (threshold: {fail_on})")
                sys.exit(1)


@scan.command("path")
@click.argument("target", default=".", type=click.Path(exists=True))
@click.option(
    "--format", "-f", "output_format",
    type=click.Choice(["text", "json"]),
    default="text", show_default=True,
)
@click.option("--output", "-o", default=None, help="Save output to a file.")
@click.option("--staged", is_flag=True, default=False, help="Only scan git staged files (for pre-commit hooks).")
def scan_path_cmd(target: str, output_format: str, output: str | None, staged: bool) -> None:
    """Scan local files or directories for secrets and vulnerabilities.

    TARGET defaults to the current directory.

    Examples:\n
      devlens scan path\n
      devlens scan path ./src\n
      devlens scan path . --format json --output secrets.json\n
    """
    from devlens.security import scan_path
    from devlens.config import get_security_config

    cfg = load_config()
    sec_cfg = get_security_config(cfg)
    ignore_filter = load_ignore_patterns()

    if staged:
        from devlens.hooks import get_staged_files
        staged_files = get_staged_files()
        if not staged_files:
            console.print("[bold green]No staged files to scan.[/]")
            return
        staged_files = ignore_filter.filter_paths(staged_files)
        console.print(f"[dim]Scanning {len(staged_files)} staged file(s)...[/]")

    with console.status(f"[bold cyan]Scanning {target}..."):
        findings = scan_path(target, ignore_patterns=sec_cfg.get("ignore_rules") or None)

    # If --staged, filter findings to only staged files
    if staged:
        staged_set = set(staged_files)
        findings = [f for f in findings if f.file in staged_set]

    if output_format == "json":
        data = [f.to_dict() for f in findings]
        text = json.dumps(data, indent=2)
        _emit(text, output, console)
    else:
        console.print()
        if not findings:
            console.print(Panel("[bold green]No security issues found![/]", border_style="green"))
        else:
            console.print(f"[bold red]Found {len(findings)} issue(s):[/]\n")
            for f in findings:
                sev = f.severity.value.upper()
                sev_color = {"CRITICAL": "red bold", "HIGH": "red", "MEDIUM": "yellow", "LOW": "blue"}.get(sev, "white")
                loc = f.file + (f":{f.line}" if f.line else "")
                console.print(f"  [{sev_color}]{sev}[/{sev_color}] {f.title}")
                console.print(f"    [cyan]{loc}[/] — {f.description}")
                if f.suggestion:
                    console.print(f"    [dim]Fix: {f.suggestion}[/]")
                console.print()
    console.print()


# ── Helpers ───────────────────────────────────────────────────

def _emit(text: str, output: str | None, console: Console) -> None:
    if output:
        Path(output).write_text(text)
        console.print(f"[dim]Saved to {output}[/]")
    else:
        click.echo(text)


# ── devlens hook ─────────────────────────────────────────────────────

@main.group()
def hook() -> None:
    """Manage git pre-commit hooks."""


@hook.command("install")
@click.option("--force", is_flag=True, default=False, help="Overwrite existing pre-commit hook.")
def hook_install(force: bool) -> None:
    """Install DevLens as a git pre-commit hook.

    This creates a .git/hooks/pre-commit script that runs
    'devlens scan path --staged' before every commit.

    Examples:\n
      devlens hook install\n
      devlens hook install --force\n
    """
    from devlens.hooks import install_hook
    success = install_hook(force=force)
    if not success:
        sys.exit(1)


@hook.command("uninstall")
def hook_uninstall() -> None:
    """Remove the DevLens pre-commit hook."""
    from devlens.hooks import uninstall_hook
    success = uninstall_hook()
    if not success:
        sys.exit(1)


@hook.command("run")
def hook_run() -> None:
    """Manually run the pre-commit hook check.

    This is the same check that runs automatically before commits.
    """
    from devlens.hooks import run_hook
    exit_code = run_hook()
    sys.exit(exit_code)



# ── devlens complexity ───────────────────────────────────────────────

@main.command()
@click.argument("target", default=".", type=click.Path(exists=True))
@click.option(
    "--format", "-f", "output_format",
    type=click.Choice(["text", "markdown", "json"]),
    default="text", show_default=True,
)
@click.option("--output", "-o", default=None, help="Save output to a file.")
@click.option("--threshold", "-t", type=int, default=10, help="Cyclomatic complexity warning threshold.")
@click.option("--lang", default=None, type=click.Choice(["python", "javascript", "typescript", "java", "go", "rust", "auto"]), help="Language filter (default: auto-detect).")
@click.option("--no-cache", is_flag=True, default=False, help="Skip cache, force fresh analysis.")
def complexity(target: str, output_format: str, output: str | None, threshold: int, lang: str | None, no_cache: bool) -> None:
    """Analyze code complexity metrics.

    Measures cyclomatic complexity, function length, nesting depth,
    and cognitive complexity for Python files.

    TARGET defaults to the current directory.

    Examples:\n
      devlens complexity\n
      devlens complexity ./src\n
      devlens complexity --threshold 15 --format json\n
    """
    from devlens.complexity import analyze_path
    from devlens.languages import analyze_file_multilang, ALL_EXTENSIONS, SUPPORTED_EXTENSIONS
    from devlens.cache import CacheManager

    cfg = load_config()
    cache_cfg = get_cache_config(cfg)

    cache = None
    if cache_cfg.get("enabled", True) and not no_cache:
        cache = CacheManager(
            root=target if Path(target).is_dir() else ".",
            cache_dir=cache_cfg.get("dir", ".devlens-cache"),
            ttl_days=cache_cfg.get("ttl_days", 7),
        )

    # Determine extensions based on --lang
    if lang and lang != "auto":
        ext_map = {"python": (".py",), "javascript": (".js", ".jsx", ".mjs", ".cjs"),
                   "typescript": (".ts", ".tsx", ".mts"), "java": (".java",),
                   "go": (".go",), "rust": (".rs",)}
        extensions = ext_map.get(lang, (".py",))
    else:
        extensions = ALL_EXTENSIONS

    with console.status(f"[bold cyan]Analyzing complexity in {target}..."):
        report = analyze_path(target, extensions=extensions)

    if cache:
        cache.save()

    if output_format == "json":
        text = json.dumps(report.to_dict(), indent=2)
        _emit(text, output, console)
    elif output_format == "markdown":
        text = report.to_markdown()
        console.print(Markdown(text))
        if output:
            Path(output).write_text(text)
            console.print(f"\n[dim]Saved to {output}[/]")
    else:
        # Rich text output
        score = report.score
        grade = report.grade
        color = "green" if score >= 80 else "yellow" if score >= 50 else "red"

        console.print()
        console.print(
            Panel(
                f"[bold {color}]{score}/100[/] (Grade: [bold]{grade}[/])",
                title="[bold]Complexity Score[/]",
                border_style=color,
            )
        )
        console.print(
            f"\n[dim]Functions: {report.total_functions} | "
            f"Avg Complexity: {report.avg_cyclomatic:.1f} | "
            f"High Risk: {report.high_risk_count} | "
            f"Medium Risk: {report.medium_risk_count}[/]\n"
        )

        # Show problematic functions
        all_funcs = [fn for f in report.files for fn in f.functions]
        risky = [fn for fn in all_funcs if fn.cyclomatic >= threshold]

        if risky:
            table = Table(title=f"Functions Above Threshold ({threshold})", box=box.SIMPLE_HEAVY, show_lines=True)
            table.add_column("Risk", justify="center", no_wrap=True)
            table.add_column("Function", style="cyan")
            table.add_column("File", style="dim")
            table.add_column("Cyclomatic", justify="right")
            table.add_column("Length", justify="right")
            table.add_column("Nesting", justify="right")
            table.add_column("Cognitive", justify="right")

            for fn in sorted(risky, key=lambda x: x.cyclomatic, reverse=True)[:20]:
                risk_color = {"high": "red bold", "medium": "yellow"}.get(fn.risk, "green")
                table.add_row(
                    f"[{risk_color}]{fn.risk.upper()}[/{risk_color}]",
                    fn.name,
                    f"{fn.file}:{fn.line}",
                    str(fn.cyclomatic),
                    str(fn.length),
                    str(fn.max_nesting),
                    str(fn.cognitive),
                )
            console.print(table)
        else:
            console.print("[bold green]All functions are below the complexity threshold![/]")
        console.print()


# ── devlens audit ────────────────────────────────────────────────────

@main.command()
@click.argument("target", default=".", type=click.Path(exists=True))
@click.option(
    "--format", "-f", "output_format",
    type=click.Choice(["text", "markdown", "json"]),
    default="text", show_default=True,
)
@click.option("--output", "-o", default=None, help="Save output to a file.")
@click.option("--no-cache", is_flag=True, default=False, help="Skip cache, force fresh analysis.")
def audit(target: str, output_format: str, output: str | None, no_cache: bool) -> None:
    """Audit project dependencies for known vulnerabilities.

    Scans requirements.txt, pyproject.toml, package.json, and go.mod
    against the OSV.dev vulnerability database.

    TARGET defaults to the current directory.

    Examples:\n
      devlens audit\n
      devlens audit ./my-project\n
      devlens audit --format json --output vulns.json\n
    """
    from devlens.depaudit import audit_dependencies

    with console.status(f"[bold cyan]Auditing dependencies in {target}..."):
        report = audit_dependencies(target)

    if not report.dependencies:
        console.print("\n[yellow]No dependency files found (requirements.txt, pyproject.toml, package.json, go.mod).[/]\n")
        return

    if output_format == "json":
        text = json.dumps(report.to_dict(), indent=2)
        _emit(text, output, console)
    elif output_format == "markdown":
        text = report.to_markdown()
        console.print(Markdown(text))
        if output:
            Path(output).write_text(text)
            console.print(f"\n[dim]Saved to {output}[/]")
    else:
        # Rich text output
        score = report.score
        grade = report.grade
        color = "green" if score >= 90 else "yellow" if score >= 60 else "red"

        console.print()
        console.print(
            Panel(
                f"[bold {color}]{score}/100[/] (Grade: [bold]{grade}[/])",
                title="[bold]Dependency Audit[/]",
                border_style=color,
            )
        )
        console.print(
            f"\n[dim]Dependencies: {len(report.dependencies)} | "
            f"Vulnerabilities: {len(report.vulnerabilities)} "
            f"(Critical: {report.critical_count}, High: {report.high_count}, "
            f"Medium: {report.medium_count}, Low: {report.low_count})[/]\n"
        )

        if report.vulnerabilities:
            table = Table(title=f"Vulnerabilities ({len(report.vulnerabilities)})", box=box.SIMPLE_HEAVY, show_lines=True)
            table.add_column("Severity", justify="center", no_wrap=True)
            table.add_column("Package", style="cyan", no_wrap=True)
            table.add_column("Version", style="dim")
            table.add_column("ID", no_wrap=True)
            table.add_column("Summary")
            table.add_column("Fix", style="green")

            for v in sorted(report.vulnerabilities,
                          key=lambda x: ["critical", "high", "medium", "low"].index(x.severity)):
                sev_color = {"critical": "red bold", "high": "red", "medium": "yellow", "low": "blue"}.get(v.severity, "white")
                fix = v.fixed_in or "—"
                table.add_row(
                    f"[{sev_color}]{v.severity.upper()}[/{sev_color}]",
                    v.package,
                    v.version,
                    v.id,
                    v.summary[:60],
                    fix,
                )
            console.print(table)
        else:
            console.print("[bold green]No known vulnerabilities found![/]")
        console.print()


# ── devlens fix ──────────────────────────────────────────────────────

@main.command("fix")
@click.argument("target", default=".", type=click.Path(exists=True))
@click.option("--ai", is_flag=True, default=False, help="Use AI for intelligent fix generation.")
@click.option(
    "--model", "-m", default=None,
    help="LLM model for AI-powered fixes.",
)
@click.option(
    "--format", "-f", "output_format",
    type=click.Choice(["text", "markdown", "json"]),
    default="text", show_default=True,
)
@click.option("--output", "-o", default=None, help="Save fixes to a file.")
@click.option("--no-cache", is_flag=True, default=False, help="Skip cache, force fresh analysis.")
def fix_cmd(target: str, ai: bool, model: str | None, output_format: str, output: str | None, no_cache: bool) -> None:
    """Scan for security issues and generate fix suggestions.

    Combines security scanning with automated fix generation.
    Use --ai for LLM-powered intelligent fixes.

    Examples:\n
      devlens fix\n
      devlens fix ./src --ai\n
      devlens fix . --format json --output fixes.json\n
    """
    from devlens.security import scan_path
    from devlens.fixer import suggest_fixes, format_fixes_markdown, format_fixes_json

    cfg = load_config()
    resolved_model: str | None = None
    if ai:
        resolved_model, _ = _resolve_model(model, cfg)

    with console.status(f"[bold cyan]Scanning {target} for issues..."):
        findings = scan_path(target)

    if not findings:
        console.print("\n[bold green]No security issues found — nothing to fix![/]\n")
        return

    console.print(f"\n[dim]Found {len(findings)} issue(s). Generating fixes...[/]")

    # Read file contents for context
    from pathlib import Path as P
    root = P(target)
    file_contents = {}
    for f in findings:
        fp = root / f.file if not f.file.startswith("/") else P(f.file)
        if fp.exists() and fp.stat().st_size < 500_000:
            try:
                file_contents[f.file] = fp.read_text(errors="ignore")
            except Exception:
                pass

    with console.status("[bold cyan]Generating fix suggestions..."):
        fixes = suggest_fixes(findings, file_contents, use_ai=ai, model=resolved_model or "gpt-4o")

    if output_format == "json":
        text = format_fixes_json(fixes)
        _emit(text, output, console)
    elif output_format == "markdown":
        text = format_fixes_markdown(fixes)
        console.print(Markdown(text))
        if output:
            Path(output).write_text(text)
            console.print(f"\n[dim]Saved to {output}[/]")
    else:
        if not fixes:
            console.print("[yellow]No automatic fix suggestions available for these findings.[/]\n")
            return

        for i, fix in enumerate(fixes, 1):
            conf_color = {"high": "green", "medium": "yellow", "low": "red"}.get(fix.confidence, "white")
            console.print(f"\n[bold]{i}. {fix.title}[/] [{conf_color}]({fix.confidence} confidence)[/{conf_color}]")
            console.print(f"   [dim]File: {fix.file} | Rule: {fix.finding_id}[/]")
            console.print(f"   {fix.explanation}")
            console.print(f"\n   ```diff\n{fix.diff}\n   ```")
        console.print()


def _print_docs_rich(result, console: Console) -> None:
    score = result.health_score
    color = "green" if score >= 80 else "yellow" if score >= 50 else "red"

    console.print()
    console.print(
        Panel(
            f"[bold {color}]{score}/100[/] — {result.summary}",
            title=f"[bold]Docs Health:[/] {result.file_path}",
            border_style=color,
        )
    )

    if result.issues:
        console.print(f"\n[bold]Issues ({len(result.issues)})[/]\n")
        for issue in result.issues:
            sev_color = {"error": "red", "warning": "yellow", "info": "blue"}.get(issue.severity, "white")
            console.print(f"  [{sev_color}][{issue.severity.upper()}][/{sev_color}] [bold]{issue.title}[/] (block #{issue.block_index})")
            console.print(f"    {issue.description}")
            console.print(f"    [dim]Fix: {issue.suggestion}[/]\n")

    if result.recommendations:
        console.print("[bold]Recommendations[/]")
        for rec in result.recommendations:
            console.print(f"  * {rec}")
    console.print()


def _print_onboarding_rich(result, console: Console) -> None:
    console.print()
    console.print(Panel(result.overview, title="[bold]Overview[/]", border_style="cyan"))
    console.print()
    console.print(Panel(result.architecture, title="[bold]Architecture[/]", border_style="blue"))
    console.print()

    if result.tech_stack:
        console.print("[bold]Tech Stack:[/] " + "  ".join(f"[cyan]{t}[/]" for t in result.tech_stack))
        console.print()

    if result.entry_points:
        console.print("[bold]Entry Points:[/]")
        for ep in result.entry_points:
            console.print(f"  * [green]{ep}[/]")
        console.print()

    if result.key_files:
        table = Table(title="Key Files", box=box.SIMPLE_HEAVY, show_lines=True)
        table.add_column("File", style="cyan", no_wrap=True)
        table.add_column("Role")
        for kf in result.key_files:
            table.add_row(kf["file"], kf["role"])
        console.print(table)
        console.print()

    if result.getting_started:
        console.print("[bold]Getting Started:[/]")
        for i, step in enumerate(result.getting_started, 1):
            console.print(f"  [bold cyan]{i}.[/] {step}")
        console.print()

    console.print(Panel(result.where_to_start, title="[bold green]Where to Start Reading[/]", border_style="green"))
    console.print()


def _detect_repo() -> str | None:
    try:
        remote = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True).strip()
        if "github.com" in remote:
            return remote.split("github.com")[-1].lstrip(":/").removesuffix(".git")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return None



# ── devlens cache ────────────────────────────────────────────────────

@main.group("cache")
def cache_group() -> None:
    """Manage the analysis cache."""


@cache_group.command("clear")
def cache_clear_cmd() -> None:
    """Clear all cached analysis results.

    Removes the .devlens-cache directory and all cached data.

    Examples:\n
      devlens cache clear\n
    """
    from devlens.cache import CacheManager

    cfg = load_config()
    cache_cfg = get_cache_config(cfg)
    cache = CacheManager(
        cache_dir=cache_cfg.get("dir", ".devlens-cache"),
        ttl_days=cache_cfg.get("ttl_days", 7),
    )
    count = cache.clear()
    console.print(f"\n[bold green]Cache cleared![/] Removed {count} cached entries.\n")


@cache_group.command("stats")
def cache_stats_cmd() -> None:
    """Show cache statistics.

    Displays cached entries, size, and breakdown by analyzer.

    Examples:\n
      devlens cache stats\n
    """
    from devlens.cache import CacheManager

    cfg = load_config()
    cache_cfg = get_cache_config(cfg)
    cache = CacheManager(
        cache_dir=cache_cfg.get("dir", ".devlens-cache"),
        ttl_days=cache_cfg.get("ttl_days", 7),
    )
    stats = cache.stats()

    console.print()
    if stats.total_entries == 0:
        console.print("[dim]Cache is empty. Run a scan to populate it.[/]\n")
        return

    table = Table(title="Cache Statistics", box=box.SIMPLE_HEAVY)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Total entries", str(stats.total_entries))
    table.add_row("Valid entries", f"[green]{stats.valid_entries}[/]")
    table.add_row("Expired entries", f"[yellow]{stats.expired_entries}[/]")
    table.add_row("Size on disk", stats.to_dict()["size_human"])

    console.print(table)

    if stats.analyzers:
        console.print("\n[bold]By Analyzer:[/]")
        for analyzer, count in sorted(stats.analyzers.items()):
            console.print(f"  {analyzer}: {count} entries")
    console.print()


# ── devlens rules ────────────────────────────────────────────────────

@main.group("rules")
def rules_group() -> None:
    """Manage custom analysis rules."""


@rules_group.command("list")
def rules_list_cmd() -> None:
    """List all active rules (built-in and custom).

    Shows rule IDs, types, severity, and enabled status.

    Examples:\n
      devlens rules list\n
    """
    from devlens.rules import RuleEngine

    cfg = load_config()
    rules_cfg = get_rules_config(cfg)
    engine = RuleEngine.from_config(cfg)
    rules = engine.list_rules()

    console.print()
    if not rules:
        console.print("[dim]No rules configured. Add rules in .devlens.yml or .devlens-rules.yml[/]\n")
        return

    table = Table(title=f"Active Rules ({len(rules)})", box=box.SIMPLE_HEAVY, show_lines=True)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title")
    table.add_column("Type", justify="center")
    table.add_column("Severity", justify="center")
    table.add_column("Enabled", justify="center")

    for rule in rules:
        sev = rule["severity"]
        sev_color = {"critical": "red bold", "high": "red", "medium": "yellow", "low": "blue", "info": "dim"}.get(sev, "white")
        enabled = "[green]Yes[/]" if rule["enabled"] else "[red]No[/]"
        table.add_row(
            rule["id"],
            rule["title"],
            rule["type"],
            f"[{sev_color}]{sev.upper()}[/{sev_color}]",
            enabled,
        )
    console.print(table)
    console.print()


@rules_group.command("validate")
@click.argument("rule_file", default=".devlens-rules.yml", type=click.Path())
def rules_validate_cmd(rule_file: str) -> None:
    """Validate a rules file for errors.

    Checks rule definitions for missing fields, invalid regex patterns,
    unknown metrics, and duplicate IDs.

    Examples:\n
      devlens rules validate\n
      devlens rules validate my-rules.yml\n
    """
    from devlens.rules import RuleEngine

    cfg = load_config()
    engine = RuleEngine.from_config(cfg)

    # Also load from specified file if it exists
    rule_path = Path(rule_file)
    if rule_path.exists():
        file_engine = RuleEngine.from_file(rule_file)
        engine.rules.extend(file_engine.rules)
        console.print(f"\n[dim]Loaded rules from {rule_file}[/]")

    errors = engine.validate()

    console.print()
    if not errors:
        console.print(f"[bold green]All {len(engine.rules)} rules are valid![/]\n")
    else:
        console.print(f"[bold red]Found {len(errors)} validation error(s):[/]\n")
        for err in errors:
            console.print(f"  [red]Rule '{err.rule_id}'[/] -> field '{err.field}': {err.message}")
        console.print()
        sys.exit(1)


# ── devlens dashboard ─────────────────────────────────────────

@main.command("dashboard")
@click.argument("target", default=".", type=click.Path(exists=True))
@click.option("--output", "-o", default="devlens-dashboard.html", help="Output HTML file path.")
@click.option("--skip", multiple=True, help="Sections to skip (complexity, security, dependencies, docs, rules).")
@click.option("--port", type=int, default=None, help="Start a local HTTP server on this port after generating.")
@click.option("--open/--no-open", "auto_open", default=True, help="Auto-open in browser.")
def dashboard_cmd(target: str, output: str, skip: tuple[str, ...], port: int | None, auto_open: bool) -> None:
    """Generate an interactive HTML dashboard for the project.

    Runs all DevLens analyses (complexity, security, dependency audit,
    docs check, custom rules) and produces a single self-contained HTML
    file with charts, tables, and dark/light theme toggle.

    Examples:\n
      devlens dashboard\n
      devlens dashboard ./my-project -o report.html\n
      devlens dashboard --skip docs --skip rules\n
      devlens dashboard --port 8080\n
    """
    from devlens.dashboard import collect_project_metrics, generate_dashboard_html

    cfg = load_config(Path(target))
    skip_set = set(skip) if skip else set()

    console.print()
    console.print("[bold]Collecting project metrics...[/]")

    with console.status("[dim]Running analyses...[/]"):
        data = collect_project_metrics(target, config=cfg, skip=skip_set)

    console.print(f"  [green]Found {len(data.cards)} metrics across {len(data.sections)} sections[/]")

    html_content = generate_dashboard_html(data)
    out_path = Path(output)
    out_path.write_text(html_content, encoding="utf-8")
    console.print(f"  [green]Dashboard written to[/] [bold]{out_path}[/]")

    if port:
        import http.server
        import threading
        import webbrowser

        os.chdir(out_path.parent)
        handler = http.server.SimpleHTTPRequestHandler
        server = http.server.HTTPServer(("127.0.0.1", port), handler)
        url = f"http://127.0.0.1:{port}/{out_path.name}"
        console.print(f"\n  [bold]Serving at[/] [link={url}]{url}[/link]")
        console.print("  [dim]Press Ctrl+C to stop[/]\n")
        if auto_open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
            console.print("\n[dim]Server stopped.[/]")
    elif auto_open:
        import webbrowser
        webbrowser.open(str(out_path.resolve()))

    console.print()


# ── devlens scoreboard ────────────────────────────────────────

@main.group("scoreboard")
def scoreboard_group() -> None:
    """Team scoreboard — track and compare developer metrics."""


@scoreboard_group.command("record")
@click.argument("target", default=".", type=click.Path(exists=True))
@click.option("--author", "-a", required=True, help="Developer name or handle.")
@click.option("--pr", "pr_number", type=int, default=None, help="Associated PR number.")
def scoreboard_record_cmd(target: str, author: str, pr_number: int | None) -> None:
    """Record current analysis metrics for a developer.

    Runs complexity + security + rules analysis on the target path and
    saves the results to the project's score history.

    Examples:\n
      devlens scoreboard record --author alice\n
      devlens scoreboard record ./src --author bob --pr 42\n
    """
    from devlens.scoreboard import record_score

    cfg = load_config(Path(target))
    metrics: dict = {}

    # Collect complexity
    console.print()
    with console.status("[dim]Analyzing complexity...[/]"):
        try:
            from devlens.complexity import analyze_path
            report = analyze_path(target)
            metrics["complexity_avg"] = round(report.avg_complexity, 1)
            metrics["complexity_grade"] = report.overall_grade
        except Exception:
            pass

    # Collect security
    with console.status("[dim]Running security scan...[/]"):
        try:
            from devlens.security import scan_path
            findings = scan_path(target)
            metrics["security_issues"] = len(findings)
        except Exception:
            pass

    # Collect rules
    with console.status("[dim]Evaluating rules...[/]"):
        try:
            from devlens.rules import RuleEngine
            engine = RuleEngine(config=cfg.get("rules", {}))
            violations = engine.evaluate_path(target)
            metrics["rule_violations"] = len(violations)
        except Exception:
            pass

    metrics["reviews"] = 1  # each record counts as one review contribution

    sb_cfg = get_scoreboard_config(cfg)
    entry = record_score(
        target,
        author=author,
        pr_number=pr_number,
        metrics=metrics,
        scores_dir=sb_cfg.get("dir"),
    )

    console.print(f"  [green]Recorded score for[/] [bold]{author}[/]")
    for k, v in metrics.items():
        console.print(f"    {k}: {v}")
    console.print(f"  [dim]Timestamp: {entry.timestamp}[/]")
    console.print()


@scoreboard_group.command("show")
@click.argument("target", default=".", type=click.Path(exists=True))
@click.option("--output", "-o", default="devlens-scoreboard.html", help="Output HTML file path.")
@click.option("--open/--no-open", "auto_open", default=True, help="Auto-open in browser.")
def scoreboard_show_cmd(target: str, output: str, auto_open: bool) -> None:
    """Generate the HTML scoreboard with leaderboard and charts.

    Examples:\n
      devlens scoreboard show\n
      devlens scoreboard show -o team-scores.html\n
    """
    from devlens.scoreboard import load_history, generate_scoreboard_html

    cfg = load_config(Path(target))
    sb_cfg = get_scoreboard_config(cfg)

    history = load_history(target, scores_dir=sb_cfg.get("dir"))

    if not history.entries:
        console.print("\n[yellow]No scores recorded yet.[/]")
        console.print("[dim]Use 'devlens scoreboard record --author <name>' first.[/]\n")
        return

    html_content = generate_scoreboard_html(history)
    out_path = Path(output)
    out_path.write_text(html_content, encoding="utf-8")

    console.print(f"\n  [green]Scoreboard written to[/] [bold]{out_path}[/]")
    console.print(f"  [dim]{len(history.entries)} entries, {len(history.authors)} authors[/]")

    if auto_open:
        import webbrowser
        webbrowser.open(str(out_path.resolve()))

    console.print()


@scoreboard_group.command("history")
@click.argument("target", default=".", type=click.Path(exists=True))
@click.option("--author", "-a", default=None, help="Filter by author.")
@click.option("--json-out", "as_json", is_flag=True, help="Output as raw JSON.")
def scoreboard_history_cmd(target: str, author: str | None, as_json: bool) -> None:
    """Show score history for the project.

    Examples:\n
      devlens scoreboard history\n
      devlens scoreboard history --author alice\n
      devlens scoreboard history --json-out\n
    """
    from devlens.scoreboard import load_history
    from dataclasses import asdict

    cfg = load_config(Path(target))
    sb_cfg = get_scoreboard_config(cfg)

    history = load_history(target, scores_dir=sb_cfg.get("dir"))

    entries = history.entries
    if author:
        entries = [e for e in entries if e.author == author]

    if not entries:
        console.print("\n[yellow]No entries found.[/]\n")
        return

    if as_json:
        console.print(json.dumps([asdict(e) for e in entries], indent=2))
        return

    console.print()
    table = Table(title="Score History", box=box.ROUNDED)
    table.add_column("Date", style="dim")
    table.add_column("Author", style="bold")
    table.add_column("PR")
    table.add_column("Complexity")
    table.add_column("Security")
    table.add_column("Violations")

    for e in entries[-20:]:  # last 20
        m = e.metrics
        table.add_row(
            e.timestamp[:10],
            e.author,
            str(e.pr_number or "-"),
            str(m.get("complexity_avg", "-")),
            str(m.get("security_issues", "-")),
            str(m.get("rule_violations", "-")),
        )

    console.print(table)
    if len(entries) > 20:
        console.print(f"  [dim]Showing last 20 of {len(entries)} entries[/]")
    console.print()


@scoreboard_group.command("reset")
@click.argument("target", default=".", type=click.Path(exists=True))
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
def scoreboard_reset_cmd(target: str, yes: bool) -> None:
    """Reset (delete) all score history for this project.

    Examples:\n
      devlens scoreboard reset\n
      devlens scoreboard reset -y\n
    """
    from devlens.scoreboard import reset_history

    if not yes:
        if not click.confirm("Delete all score history?"):
            console.print("[dim]Cancelled.[/]")
            return

    cfg = load_config(Path(target))
    sb_cfg = get_scoreboard_config(cfg)

    if reset_history(target, scores_dir=sb_cfg.get("dir")):
        console.print("\n[green]Score history deleted.[/]\n")
    else:
        console.print("\n[yellow]No history file found.[/]\n")


# ═══════════════════════════════════════════════════════════════════
#  PLUGIN MANAGEMENT COMMANDS
# ═══════════════════════════════════════════════════════════════════


@main.group()
def plugin():
    """Manage DevLens plugins."""
    pass


@plugin.command("list")
@click.option("--target", "-t", default=".", help="Project root.")
@click.option("--plugin-dir", default=None, help="Custom plugin directory.")
def plugin_list(target: str, plugin_dir: str | None):
    """List discovered and loaded plugins."""
    cfg = load_config(Path(target))
    if plugin_dir:
        cfg.setdefault("plugins", {})["plugin_dir"] = plugin_dir

    pm = PluginManager(cfg)
    pm.discover()
    pm.load_all()

    plugins = pm.list_plugins()
    if not plugins:
        console.print("\n[yellow]No plugins found.[/]\n")
        console.print("[dim]Plugins are discovered from:[/]")
        console.print("[dim]  • entry-points (devlens.plugins group)[/]")
        console.print("[dim]  • .devlens-plugins/ directory[/]")
        console.print("[dim]  • --plugin-dir option[/]\n")
        return

    table = Table(title="DevLens Plugins", box=box.ROUNDED)
    table.add_column("Name", style="cyan")
    table.add_column("Version", style="green")
    table.add_column("Type", style="magenta")
    table.add_column("Status", style="bold")
    table.add_column("Priority", justify="right")
    table.add_column("Languages")

    for p in plugins:
        status = "[green]loaded[/]" if p["loaded"] else "[dim]discovered[/]"
        if p.get("enabled") is False:
            status = "[yellow]disabled[/]"
        langs = ", ".join(p["languages"][:3])
        if len(p["languages"]) > 3:
            langs += "..."
        table.add_row(
            p["name"], p["version"], p["type"],
            status, str(p["priority"]), langs,
        )

    console.print()
    console.print(table)
    console.print(f"\n[dim]{len(plugins)} plugin(s) total[/]\n")

    if pm.errors:
        console.print("[red]Errors during discovery/loading:[/]")
        for name, err in pm.errors:
            console.print(f"  [red]• {name}:[/] {err}")
        console.print()


@plugin.command("install")
@click.argument("package")
def plugin_install(package: str):
    """Install a plugin package via pip."""
    console.print(f"\n[cyan]Installing plugin:[/] {package}")
    if install_plugin_from_pip(package):
        console.print(f"[green]Successfully installed {package}[/]\n")
    else:
        console.print(f"[red]Failed to install {package}[/]\n")
        raise SystemExit(1)


@plugin.command("uninstall")
@click.argument("package")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
def plugin_uninstall(package: str, yes: bool):
    """Uninstall a plugin package."""
    if not yes:
        if not click.confirm(f"Uninstall {package}?"):
            console.print("[dim]Cancelled.[/]")
            return

    console.print(f"\n[cyan]Uninstalling:[/] {package}")
    if uninstall_plugin_from_pip(package):
        console.print(f"[green]Successfully uninstalled {package}[/]\n")
    else:
        console.print(f"[red]Failed to uninstall {package}[/]\n")
        raise SystemExit(1)


@plugin.command("enable")
@click.argument("name")
@click.option("--target", "-t", default=".", help="Project root.")
def plugin_enable(name: str, target: str):
    """Enable a plugin."""
    cfg = load_config(Path(target))
    pm = PluginManager(cfg)
    pm.discover()
    pm.load_all()

    if pm.enable(name):
        console.print(f"\n[green]Plugin '{name}' enabled.[/]\n")
    else:
        console.print(f"\n[red]Plugin '{name}' not found or not loaded.[/]\n")


@plugin.command("disable")
@click.argument("name")
@click.option("--target", "-t", default=".", help="Project root.")
def plugin_disable(name: str, target: str):
    """Disable a plugin (keeps it loaded but skips execution)."""
    cfg = load_config(Path(target))
    pm = PluginManager(cfg)
    pm.discover()
    pm.load_all()

    if pm.disable(name):
        console.print(f"\n[yellow]Plugin '{name}' disabled.[/]\n")
    else:
        console.print(f"\n[red]Plugin '{name}' not found or not loaded.[/]\n")


@plugin.command("info")
@click.argument("name")
@click.option("--target", "-t", default=".", help="Project root.")
def plugin_info(name: str, target: str):
    """Show detailed information about a plugin."""
    cfg = load_config(Path(target))
    pm = PluginManager(cfg)
    pm.discover()
    pm.load_all()

    info = pm.plugin_info(name)
    if not info:
        console.print(f"\n[red]Plugin '{name}' not found.[/]\n")
        return

    panel_lines = [
        f"[bold cyan]{info['name']}[/] v{info['version']}",
        f"[dim]{info['description']}[/]" if info["description"] else "",
        "",
        f"  [bold]Type:[/]       {info['type']}",
        f"  [bold]Author:[/]     {info['author'] or 'unknown'}",
        f"  [bold]Priority:[/]   {info['priority']}",
        f"  [bold]Languages:[/]  {', '.join(info['languages'])}",
        f"  [bold]Min DevLens:[/] {info['min_devlens_version']}",
        f"  [bold]Loaded:[/]     {'yes' if info['loaded'] else 'no'}",
        f"  [bold]Enabled:[/]    {'yes' if info.get('enabled') else 'no' if info.get('enabled') is not None else 'n/a'}",
    ]
    if info["dependencies"]:
        panel_lines.append(f"  [bold]Deps:[/]       {', '.join(info['dependencies'])}")
    if info["tags"]:
        panel_lines.append(f"  [bold]Tags:[/]       {', '.join(info['tags'])}")
    if info["homepage"]:
        panel_lines.append(f"  [bold]Homepage:[/]   {info['homepage']}")

    console.print()
    console.print(Panel("\n".join(panel_lines), title="Plugin Info", box=box.ROUNDED))
    console.print()


@plugin.command("new")
@click.argument("name")
@click.option(
    "--type", "-T", "ptype",
    type=click.Choice(["checker", "fixer", "reporter", "formatter", "analyzer"]),
    default="checker",
    help="Plugin type.",
)
@click.option("--dir", "-d", "directory", default=".devlens-plugins", help="Output directory.")
def plugin_new(name: str, ptype: str, directory: str):
    """Scaffold a new plugin from a template."""
    pt = PluginType(ptype)
    filepath = create_plugin_template(Path(directory), name, pt)
    console.print(f"\n[green]Created plugin template:[/] {filepath}")
    console.print(f"[dim]Edit the file to implement your {ptype} logic.[/]\n")


# ═══════════════════════════════════════════════════════════════════
#  AI REVIEW COMMANDS
# ═══════════════════════════════════════════════════════════════════


@main.group("ai-review")
def ai_review():
    """AI-powered code review using LLMs."""
    pass


@ai_review.command("analyze")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--target", "-t", default=".", help="Project root.")
@click.option("--json-output", "-j", is_flag=True, help="Output raw JSON.")
def ai_analyze(paths: tuple, target: str, json_output: bool):
    """Analyze files with AI-powered review."""
    cfg = load_config(Path(target))
    file_paths = [Path(p) for p in paths]

    console.print(f"\n[cyan]AI Review:[/] Analyzing {len(file_paths)} file(s)...\n")

    result = run_ai_review_sync(file_paths, cfg, ReviewMode.REVIEW)

    if "error" in result:
        console.print(f"[red]Error:[/] {result['error']}")
        raise SystemExit(1)

    if json_output:
        console.print_json(json.dumps(result, indent=2, default=str))
        return

    results = result.get("results", {})
    total_issues = 0
    for filepath, issues in results.items():
        if not issues:
            continue
        console.print(f"[bold]{filepath}[/]")
        for issue in issues:
            sev = issue.get("severity", "info")
            color = {"error": "red", "warning": "yellow", "info": "blue", "suggestion": "green"}.get(sev, "white")
            line = issue.get("line", "?")
            msg = issue.get("message", "")
            console.print(f"  [{color}]{sev.upper():>10}[/] L{line}: {msg}")
            if issue.get("suggestion"):
                console.print(f"             [dim]→ {issue['suggestion']}[/]")
            total_issues += 1
        console.print()

    stats = result.get("stats", {})
    console.print(f"[dim]Total: {total_issues} issue(s) | Tokens used: {stats.get('total_tokens', 0):,}[/]\n")


@ai_review.command("suggest-fixes")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--target", "-t", default=".", help="Project root.")
@click.option("--json-output", "-j", is_flag=True, help="Output raw JSON.")
def ai_suggest_fixes(paths: tuple, target: str, json_output: bool):
    """Review files and suggest concrete fixes."""
    cfg = load_config(Path(target))
    file_paths = [Path(p) for p in paths]

    console.print(f"\n[cyan]AI Review:[/] Finding fixes for {len(file_paths)} file(s)...\n")

    result = run_ai_review_sync(file_paths, cfg, ReviewMode.SUGGEST_FIXES)

    if "error" in result:
        console.print(f"[red]Error:[/] {result['error']}")
        raise SystemExit(1)

    if json_output:
        console.print_json(json.dumps(result, indent=2, default=str))
        return

    results = result.get("results", {})
    for filepath, fixes in results.items():
        if not fixes:
            continue
        console.print(f"[bold]{filepath}[/]")
        for i, fix in enumerate(fixes, 1):
            console.print(f"  [cyan]Fix #{i}[/] (L{fix.get('line_start', '?')}-{fix.get('line_end', '?')})")
            console.print(f"    [dim]Type:[/] {fix.get('fix_type', 'replace')}")
            if fix.get("explanation"):
                console.print(f"    [dim]Why:[/]  {fix['explanation']}")
            if fix.get("original"):
                console.print(f"    [red]- {fix['original']}[/]")
            if fix.get("replacement"):
                console.print(f"    [green]+ {fix['replacement']}[/]")
            console.print()

    stats = result.get("stats", {})
    console.print(f"[dim]Tokens used: {stats.get('total_tokens', 0):,}[/]\n")


@ai_review.command("explain")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--target", "-t", default=".", help="Project root.")
@click.option("--json-output", "-j", is_flag=True, help="Output raw JSON.")
def ai_explain(paths: tuple, target: str, json_output: bool):
    """Explain code in human-readable terms."""
    cfg = load_config(Path(target))
    file_paths = [Path(p) for p in paths]

    console.print(f"\n[cyan]AI Review:[/] Explaining {len(file_paths)} file(s)...\n")

    result = run_ai_review_sync(file_paths, cfg, ReviewMode.EXPLAIN)

    if "error" in result:
        console.print(f"[red]Error:[/] {result['error']}")
        raise SystemExit(1)

    if json_output:
        console.print_json(json.dumps(result, indent=2, default=str))
        return

    results = result.get("results", {})
    for filepath, explanation in results.items():
        console.print(Panel(
            f"[bold]{explanation.get('summary', 'No summary')}[/]\n\n"
            + ("[cyan]Components:[/]\n" + "\n".join(f"  • {c}" for c in explanation.get("components", [])) + "\n\n" if explanation.get("components") else "")
            + ("[magenta]Patterns:[/]\n" + "\n".join(f"  • {p}" for p in explanation.get("patterns", [])) + "\n\n" if explanation.get("patterns") else "")
            + ("[yellow]Notes:[/]\n" + "\n".join(f"  • {n}" for n in explanation.get("notes", [])) if explanation.get("notes") else ""),
            title=str(filepath),
            box=box.ROUNDED,
        ))
        console.print()

    stats = result.get("stats", {})
    console.print(f"[dim]Tokens used: {stats.get('total_tokens', 0):,}[/]\n")


@ai_review.command("commit-msg")
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option("--stdin", "use_stdin", is_flag=True, help="Read diff from stdin.")
@click.option("--copy", "copy_clip", is_flag=True, help="Copy to clipboard.")
def ai_commit_msg(paths: tuple, use_stdin: bool, copy_clip: bool):
    """Generate a conventional commit message."""
    cfg = load_config(Path("."))

    if use_stdin:
        import sys as _sys
        diff_text = _sys.stdin.read()
        # Write to temp file for the API
        tmp = Path("/tmp/devlens_diff.txt")
        tmp.write_text(diff_text)
        file_paths = [tmp]
    elif paths:
        file_paths = [Path(p) for p in paths]
    else:
        # Try git diff
        try:
            diff_result = subprocess.run(
                ["git", "diff", "--cached"], capture_output=True, text=True
            )
            if not diff_result.stdout.strip():
                diff_result = subprocess.run(
                    ["git", "diff"], capture_output=True, text=True
                )
            if diff_result.stdout.strip():
                tmp = Path("/tmp/devlens_diff.txt")
                tmp.write_text(diff_result.stdout)
                file_paths = [tmp]
            else:
                console.print("[yellow]No diff found. Stage changes or provide file paths.[/]")
                return
        except FileNotFoundError:
            console.print("[red]git not found. Provide file paths or use --stdin.[/]")
            return

    console.print("\n[cyan]AI Review:[/] Generating commit message...\n")

    result = run_ai_review_sync(file_paths, cfg, ReviewMode.COMMIT_MSG)

    if "error" in result:
        console.print(f"[red]Error:[/] {result['error']}")
        raise SystemExit(1)

    msg_data = result.get("result", {})
    subject = msg_data.get("subject", "chore: update")
    body = msg_data.get("body", "")

    full_msg = subject
    if body:
        full_msg += f"\n\n{body}"

    console.print(Panel(full_msg, title="Commit Message", box=box.ROUNDED, style="green"))
    console.print()

    if copy_clip:
        try:
            subprocess.run(
                ["pbcopy"] if platform.system() == "Darwin" else ["xclip", "-selection", "clipboard"],
                input=full_msg, text=True, check=True,
            )
            console.print("[green]Copied to clipboard![/]\n")
        except (FileNotFoundError, subprocess.CalledProcessError):
            console.print("[yellow]Could not copy to clipboard.[/]\n")

    stats = result.get("stats", {})
    console.print(f"[dim]Tokens used: {stats.get('total_tokens', 0):,}[/]\n")


@ai_review.command("bugs")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--target", "-t", default=".", help="Project root.")
@click.option("--json-output", "-j", is_flag=True, help="Output raw JSON.")
def ai_bugs(paths: tuple, target: str, json_output: bool):
    """Detect potential bugs using AI analysis."""
    cfg = load_config(Path(target))
    file_paths = [Path(p) for p in paths]

    console.print(f"\n[cyan]AI Review:[/] Bug detection on {len(file_paths)} file(s)...\n")

    result = run_ai_review_sync(file_paths, cfg, ReviewMode.BUG_DETECT)

    if "error" in result:
        console.print(f"[red]Error:[/] {result['error']}")
        raise SystemExit(1)

    if json_output:
        console.print_json(json.dumps(result, indent=2, default=str))
        return

    results = result.get("results", {})
    total = 0
    for filepath, bugs in results.items():
        if not bugs:
            continue
        console.print(f"[bold]{filepath}[/]")
        for bug in bugs:
            sev = bug.get("severity", "warning")
            color = {"error": "red", "warning": "yellow"}.get(sev, "yellow")
            console.print(f"  [{color}]BUG[/] L{bug.get('line', '?')}: {bug.get('description', '')}")
            if bug.get("suggested_fix"):
                console.print(f"       [dim]Fix: {bug['suggested_fix']}[/]")
            total += 1
        console.print()

    stats = result.get("stats", {})
    console.print(f"[dim]Found {total} potential bug(s) | Tokens: {stats.get('total_tokens', 0):,}[/]\n")


@ai_review.command("refactor")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--target", "-t", default=".", help="Project root.")
@click.option("--json-output", "-j", is_flag=True, help="Output raw JSON.")
def ai_refactor(paths: tuple, target: str, json_output: bool):
    """Suggest refactoring improvements."""
    cfg = load_config(Path(target))
    file_paths = [Path(p) for p in paths]

    console.print(f"\n[cyan]AI Review:[/] Refactoring analysis on {len(file_paths)} file(s)...\n")

    result = run_ai_review_sync(file_paths, cfg, ReviewMode.REFACTOR)

    if "error" in result:
        console.print(f"[red]Error:[/] {result['error']}")
        raise SystemExit(1)

    if json_output:
        console.print_json(json.dumps(result, indent=2, default=str))
        return

    results = result.get("results", {})
    for filepath, suggestions in results.items():
        if not suggestions:
            continue
        console.print(f"[bold]{filepath}[/]")
        for i, s in enumerate(suggestions, 1):
            console.print(f"  [magenta]#{i}[/] L{s.get('line_start', '?')}-{s.get('line_end', '?')}: {s.get('description', '')}")
            if s.get("refactored_code"):
                console.print(f"    [dim]Suggested code:[/]")
                for line in s["refactored_code"].split("\n")[:5]:
                    console.print(f"    [green]{line}[/]")
                if s["refactored_code"].count("\n") > 5:
                    console.print("    [dim]...[/]")
            console.print()

    stats = result.get("stats", {})
    console.print(f"[dim]Tokens used: {stats.get('total_tokens', 0):,}[/]\n")


@ai_review.command("configure")
@click.option(
    "--provider", "-p",
    type=click.Choice(["openai", "anthropic"]),
    required=True,
    help="LLM provider.",
)
@click.option("--api-key", "-k", prompt=True, hide_input=True, help="API key.")
@click.option("--config-file", "-c", default=".devlens.yml", help="Config file path.")
def ai_configure(provider: str, api_key: str, config_file: str):
    """Configure AI review API credentials."""
    config_path = Path(config_file)
    if configure_api_key(provider, api_key, config_path):
        console.print(f"\n[green]AI review configured![/]")
        console.print(f"  Provider: {provider}")
        console.print(f"  Config:   {config_path}\n")
    else:
        console.print(f"\n[red]Failed to save configuration.[/]\n")
        raise SystemExit(1)


if __name__ == "__main__":
    main()


# -- devlens lsp ---------------------------------------------------------------

@main.group()
def lsp() -> None:
    """Language Server Protocol commands.

    Start the DevLens LSP server for IDE integration.

    Examples:\n
      devlens lsp start\n
      devlens lsp start --mode tcp --port 2087\n
    """


@lsp.command()
@click.option(
    "--mode",
    type=click.Choice(["stdio", "tcp"]),
    default="stdio",
    show_default=True,
    help="Connection mode. Use 'stdio' for editor clients, 'tcp' for debugging.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="TCP host (only used in tcp mode).",
)
@click.option(
    "--port",
    type=int,
    default=2087,
    show_default=True,
    help="TCP port (only used in tcp mode).",
)
@click.option(
    "--log-level",
    type=click.Choice(["debug", "info", "warning", "error"]),
    default="info",
    show_default=True,
    help="Logging verbosity.",
)
def start(mode: str, host: str, port: int, log_level: str) -> None:
    """Start the DevLens language server.

    The language server provides real-time diagnostics, code actions,
    hover info, and CodeLens for any LSP-capable editor.

    STDIO mode (default) is used by editor extensions like VS Code.
    TCP mode is useful for development and debugging.

    Examples:\n
      devlens lsp start\n
      devlens lsp start --mode tcp --port 2087\n
      devlens lsp start --log-level debug\n
    """
    console.print(
        Panel(
            f"[bold green]DevLens LSP Server v0.8.0[/]\n\n"
            f"  Mode     : [cyan]{mode}[/]\n"
            f"  Host     : [dim]{host}[/]\n"
            f"  Port     : [dim]{port}[/]\n"
            f"  Log Level: [dim]{log_level}[/]\n\n"
            f"[dim]Press Ctrl+C to stop[/]",
            title="[bold]Language Server[/]",
            border_style="blue",
        )
    )
    try:
        _start_lsp(mode=mode, host=host, port=port, log_level=log_level)
    except KeyboardInterrupt:
        console.print("\n[yellow]Server stopped.[/]")
    except ImportError:
        console.print(
            "[red]Error:[/] pygls is not installed.\n"
            "Install with: [bold]pip install devlens\\[ide][/]"
        )
        raise SystemExit(1)


@lsp.command()
def info() -> None:
    """Show LSP server information and status."""
    console.print(
        Panel(
            "[bold]DevLens Language Server[/]\n\n"
            "  Version  : 0.8.0\n"
            "  Protocol : LSP 3.17\n"
            "  Runtime  : pygls 2.0+\n"
            "  Transport: STDIO / TCP\n\n"
            "[bold]Capabilities:[/]\n"
            "  - Diagnostics (security, complexity, rules, deps)\n"
            "  - Code Actions (quick fixes)\n"
            "  - Hover (rule explanations)\n"
            "  - CodeLens (quality score)\n"
            "  - Commands (analyze, dashboard, AI review)\n\n"
            "[bold]Supported Editors:[/]\n"
            "  - VS Code (via DevLens extension)\n"
            "  - Neovim (via nvim-lspconfig)\n"
            "  - Sublime Text (via LSP package)\n"
            "  - Emacs (via lsp-mode / eglot)\n\n"
            "[bold]Installation:[/]\n"
            "  pip install devlens[ide]",
            title="[bold]LSP Info[/]",
            border_style="blue",
        )
    )