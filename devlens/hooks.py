"""Pre-commit hook integration for DevLens.

Provides:
  - install_hook(): Installs DevLens as a git pre-commit hook
  - run_hook(): Entry point called by the pre-commit framework
  - Generates .pre-commit-hooks.yaml for the pre-commit framework
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from rich.console import Console

console = Console()

HOOK_SCRIPT = """\
#!/bin/sh
# DevLens pre-commit hook — scans staged files for secrets & vulnerabilities
# Installed by: devlens hook install

echo "🔍 DevLens: scanning staged files..."
devlens scan path --staged
EXIT_CODE=$?
if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "❌ DevLens found security issues. Fix them before committing."
    echo "   Run 'devlens scan path' for details."
    echo "   Use 'git commit --no-verify' to bypass (not recommended)."
    exit 1
fi
echo "✅ DevLens: no issues found."
exit 0
"""


def _find_git_root(start: Path | None = None) -> Path | None:
    """Walk up to find the .git directory."""
    search = start or Path.cwd()
    for directory in [search, *search.parents]:
        if (directory / ".git").is_dir():
            return directory
    return None


def install_hook(force: bool = False) -> bool:
    """Install DevLens as a git pre-commit hook.
    
    Returns True on success, False on failure.
    """
    git_root = _find_git_root()
    if not git_root:
        console.print("[red]Error:[/red] Not inside a git repository.")
        return False

    hooks_dir = git_root / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-commit"

    if hook_path.exists() and not force:
        console.print(
            f"[yellow]Warning:[/yellow] Pre-commit hook already exists at {hook_path}\n"
            f"Use [bold]devlens hook install --force[/bold] to overwrite."
        )
        return False

    hook_path.write_text(HOOK_SCRIPT)
    # Make executable
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    console.print(f"[green]✓[/green] Pre-commit hook installed at [bold]{hook_path}[/bold]")
    console.print("  DevLens will scan for secrets before every commit.")
    console.print("  Bypass with: [dim]git commit --no-verify[/dim]")
    return True


def uninstall_hook() -> bool:
    """Remove the DevLens pre-commit hook."""
    git_root = _find_git_root()
    if not git_root:
        console.print("[red]Error:[/red] Not inside a git repository.")
        return False

    hook_path = git_root / ".git" / "hooks" / "pre-commit"
    if not hook_path.exists():
        console.print("[yellow]No pre-commit hook found.[/yellow]")
        return False

    content = hook_path.read_text()
    if "DevLens" not in content:
        console.print(
            "[yellow]Warning:[/yellow] Existing hook was not installed by DevLens. "
            "Remove manually if needed."
        )
        return False

    hook_path.unlink()
    console.print("[green]✓[/green] DevLens pre-commit hook removed.")
    return True


def get_staged_files() -> list[str]:
    """Get list of staged files from git."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            capture_output=True, text=True, check=True,
        )
        return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def run_hook() -> int:
    """Run security scan on staged files. Returns exit code (0=clean, 1=issues found)."""
    from devlens.security import scan_path, SecurityFinding, Severity
    from devlens.config import load_config, get_security_config
    from devlens.ignore import load_ignore_patterns

    staged = get_staged_files()
    if not staged:
        return 0

    cfg = load_config()
    sec_cfg = get_security_config(cfg)
    ignore_filter = load_ignore_patterns()

    # Filter out ignored files
    files_to_scan = ignore_filter.filter_paths(staged)
    if not files_to_scan:
        return 0

    # Scan the current directory but we'll filter findings to staged files only
    findings = scan_path(".", ignore_patterns=sec_cfg.get("ignore_rules"))

    # Keep only findings in staged files
    staged_set = set(files_to_scan)
    relevant: list[SecurityFinding] = [f for f in findings if f.file in staged_set]

    if not relevant:
        return 0

    # Check against fail threshold
    fail_on = sec_cfg.get("fail_on", "high")
    severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    threshold = severity_rank.get(fail_on, 3)

    blocking = [f for f in relevant if severity_rank.get(f.severity.value, 0) >= threshold]

    if blocking:
        console.print(f"\n[red]DevLens found {len(blocking)} blocking issue(s):[/red]\n")
        for finding in blocking[:10]:
            console.print(
                f"  [{finding.severity.value.upper()}] {finding.title}\n"
                f"    {finding.file}:{finding.line or '?'} — {finding.match[:60]}"
            )
        if len(blocking) > 10:
            console.print(f"  ... and {len(blocking) - 10} more")
        return 1

    return 0
