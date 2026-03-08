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
from devlens.config import load_config

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
    model = cfg.get("model") or saved.get("model")
    provider = cfg.get("provider") or saved.get("provider")

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
@click.version_option(version="0.1.0", prog_name="devlens")
def main() -> None:
    """DevLens — AI-powered developer assistant.

    Commands:\n
      init     Set up your AI provider (run once)\n
      review   Analyze a GitHub Pull Request\n
      onboard  Generate an onboarding guide for a repository\n
      docs     Check documentation health\n
      doctor   Diagnose and fix common setup issues\n
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
    model: str | None,
) -> None:
    """Analyze a Pull Request and surface what actually matters.

    PR_NUMBER is the GitHub PR number to review.

    Use --ai to enable AI analysis. The provider is picked from your saved
    setup (devlens init) or from the --model flag.\n

    Examples:\n
      devlens review 42\n
      devlens review 42 --ai\n
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

    if output_format == "html":
        from devlens.reporter import render_pr_html, save_html
        html = render_pr_html(result, repo=resolved_repo, pr_number=pr_number)
        out = output or f"devlens-pr-{pr_number}.html"
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
        result.print_rich(console)


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


# ── Helpers ───────────────────────────────────────────────────

def _emit(text: str, output: str | None, console: Console) -> None:
    if output:
        Path(output).write_text(text)
        console.print(f"[dim]Saved to {output}[/]")
    else:
        click.echo(text)


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


if __name__ == "__main__":
    main()
