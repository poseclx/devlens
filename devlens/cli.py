"""DevLens CLI — entry point."""

from __future__ import annotations
import sys
import json
from pathlib import Path

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich import box

from devlens.github import fetch_pr
from devlens.analyzer import analyze_pr
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
    },
    "gemini": {
        "label": "Google Gemini",
        "models": ["gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash", "gemini-2.5-pro"],
        "default": "gemini-1.5-pro",
        "env": "GEMINI_API_KEY",
        "install": "google-generativeai",
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
            "Use gpt-*, claude-*, or gemini-* prefixed model names."
        )

    saved = _load_setup()
    model = cfg.get("model") or saved.get("model")
    provider = cfg.get("provider") or saved.get("provider")

    if model and provider:
        return model, provider

    # Nothing configured — prompt
    console.print()
    console.print(Panel(
        "[bold yellow]No AI provider configured.[/]\n\n"
        "Run [bold cyan]devlens init[/] to set up once, "
        "or pass [bold cyan]--model[/] to specify a model inline.",
        title="[bold]Setup required[/]",
        border_style="yellow",
    ))
    sys.exit(1)


def _model_prefixes(provider: str) -> list[str]:
    if provider == "openai":
        return ["gpt", "o1", "o3"]
    if provider == "anthropic":
        return ["claude"]
    if provider == "gemini":
        return ["gemini"]
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


# ── main group ────────────────────────────────────────────────

@click.group()
@click.version_option(version="0.1.0", prog_name="devlens")
def main() -> None:
    """DevLens — AI-powered developer assistant.

    Commands:\n
      init     Choose your AI provider (run once)\n
      review   Analyze a GitHub Pull Request\n
      onboard  Generate an onboarding guide for a repository\n
      docs     Check documentation health\n
    """


# ── devlens init ──────────────────────────────────────────────

@main.command()
def init() -> None:
    """Interactive setup: choose your AI provider and default model.

    Saves your preference to ~/.devlens/config.json so you never
    have to pass --model again.

    Examples:\n
      devlens init\n
    """
    console.print()
    console.print(Panel(
        "[bold cyan]DevLens AI Setup[/]\n\n"
        "Choose which AI provider you want to use.\n"
        "You can change this any time by running [bold]devlens init[/] again.",
        border_style="cyan",
    ))
    console.print()

    # --- Provider choice ---
    provider_choices = list(PROVIDERS.keys())
    for i, (key, info) in enumerate(PROVIDERS.items(), 1):
        console.print(f"  [bold cyan]{i}.[/] {info['label']}  ([dim]{info['env']}[/])")
    console.print()

    raw = click.prompt(
        "Select provider",
        type=click.Choice(["1", "2", "3"]),
        show_choices=False,
    )
    provider = provider_choices[int(raw) - 1]
    pinfo = PROVIDERS[provider]

    console.print()
    console.print(f"[bold]Available models for {pinfo['label']}:[/]")
    for i, m in enumerate(pinfo["models"], 1):
        default_tag = " [dim](default)[/]" if m == pinfo["default"] else ""
        console.print(f"  [bold cyan]{i}.[/] {m}{default_tag}")
    console.print()

    model_raw = click.prompt(
        "Select model (or press Enter for default)",
        default="1",
        type=click.Choice([str(i) for i in range(1, len(pinfo["models"]) + 1)]),
        show_choices=False,
    )
    model = pinfo["models"][int(model_raw) - 1]

    # --- Confirm env var ---
    env_var = pinfo["env"]
    import os
    has_key = bool(os.environ.get(env_var))
    console.print()
    if has_key:
        console.print(f"[green]✓[/] [dim]{env_var}[/] is already set in your environment.")
    else:
        console.print(
            f"[yellow]![/] [dim]{env_var}[/] is not set.\n"
            f"  Add it to your shell profile:\n\n"
            f"  [bold]export {env_var}=your-key-here[/]\n"
        )

    # --- Save ---
    _save_setup({"provider": provider, "model": model})

    console.print(Panel(
        f"[bold green]Setup saved![/]\n\n"
        f"Provider : [cyan]{pinfo['label']}[/]\n"
        f"Model    : [cyan]{model}[/]\n"
        f"Config   : [dim]{_CONFIG_PATH}[/]\n\n"
        f"Install the required package if you haven't:\n"
        f"  [bold]pip install {pinfo['install']}[/]",
        border_style="green",
    ))
    console.print()


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

    status_msg = f"[bold cyan]Analyzing with {resolved_model}..." if ai else "[bold cyan]Running static analysis..."
    with console.status(status_msg):
        try:
            result = analyze_pr(pr_data, detail=detail_level, config=cfg, use_ai=ai, model=resolved_model or "gpt-4o")
        except EnvironmentError as exc:
            console.print(f"[bold yellow]Warning:[/] {exc}")
            console.print("[dim]Falling back to static analysis...[/]")
            result = analyze_pr(pr_data, detail=detail_level, config=cfg, use_ai=False)

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
            console.print(f"  • {rec}")
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
            console.print(f"  • [green]{ep}[/]")
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
    import subprocess
    try:
        remote = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True).strip()
        if "github.com" in remote:
            return remote.split("github.com")[-1].lstrip(":/").removesuffix(".git")
    except subprocess.CalledProcessError:
        pass
    return None


if __name__ == "__main__":
    main()
