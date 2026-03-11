<div align="center">

# DevLens

**Comprehensive Code Quality Analysis Toolkit**

Security scanning, complexity analysis, custom rules, AI-powered code review, and more -- all in one tool.

[![CI](https://github.com/poseclx/devlens/actions/workflows/ci.yml/badge.svg)](https://github.com/poseclx/devlens/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/devlens.svg)](https://pypi.org/project/devlens/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[Features](#-features) | [Installation](#-installation) | [Quick Start](#-quick-start) | [Documentation](#-documentation) | [Contributing](#-contributing)

</div>

---

## What is DevLens?

DevLens is a Python-powered code quality toolkit that brings security scanning, complexity analysis, custom rule enforcement, AI-assisted code review, and team scoreboarding into a single CLI. It works across **Python, JavaScript/TypeScript, Java, Go, Ruby, PHP, Rust, C/C++, and C#** with deep AST-based analysis for Python and regex-based pattern matching for other languages.

Whether you're a solo developer catching security issues before commit, a team lead tracking code quality trends, or an open-source maintainer onboarding new contributors -- DevLens has you covered.

## Key Highlights

- **Multi-language security scanner** with 20+ built-in detection rules (secrets, injections, crypto issues)
- **Cyclomatic & cognitive complexity** analysis with A-F grading
- **AI-powered code review** supporting 6 LLM providers (OpenAI, Anthropic, Gemini, Groq, Ollama, OpenRouter)
- **VS Code extension** with real-time diagnostics via Language Server Protocol
- **Plugin architecture** for custom checkers, fixers, and reporters
- **Team scoreboard** with HTML dashboards and trend tracking
- **Pre-commit hooks** for automated scanning in your Git workflow
- **GitHub Actions** for CI/CD integration with automated PR reviews
- **Dependency auditing** via OSV.dev vulnerability database
- **Auto-fix generation** with diff patches for security findings

---

## Features

### Security Scanning

AST-based analysis for Python and regex-based pattern matching for other languages. Catches real vulnerabilities, not just style issues.

| Category | Examples | Severity |
|----------|----------|----------|
| Hardcoded Secrets | AWS keys, GitHub tokens, Stripe keys, private keys, passwords, database URLs | Critical |
| Injection Attacks | SQL injection, command injection, XSS, SSRF | High |
| Dangerous Functions | `eval()`, `exec()`, unsafe deserialization (`pickle.loads`) | High |
| Crypto Issues | Weak algorithms (MD5/SHA1 for security), insecure random | Medium |
| Path Traversal | Unsanitized file path construction | Medium |
| Misconfigurations | Debug mode in production, disabled SSL verification | Low |

**Rule IDs**: `SEC001`-`SEC010` (secrets), `VLN001`-`VLN010` (vulnerabilities)

```bash
# Scan a file or directory
devlens security ./src

# JSON output for CI integration
devlens security ./src --format json
```

### Complexity Analysis

AST-based metrics that help you find functions that are too complex, too long, or too deeply nested.

**Metrics tracked:**
- **Cyclomatic complexity** -- number of independent paths through code
- **Cognitive complexity** -- how hard code is for a human to understand
- **Function length** -- lines of code per function
- **Nesting depth** -- maximum indentation level
- **Parameter count** -- function argument count

**Grading scale:** A (90-100) through F (0-59), with configurable thresholds.

```bash
# Analyze complexity
devlens complexity ./src

# Markdown report
devlens complexity ./src --format markdown --output report.md
```

### Custom Rule Engine

Define your own rules using three flexible rule types:

| Type | Description | Use Case |
|------|-------------|----------|
| **Pattern** | Regex-based text matching | Enforce naming conventions, ban specific imports |
| **Threshold** | Metric limit checks | Max function length, max complexity score |
| **AST** | Python AST node inspection | Ban `eval`, catch mutable defaults |

**Built-in AST rules:** `no-eval`, `no-exec`, `no-star-import`, `no-mutable-default`, `no-bare-except`, `no-global`

Custom rules are defined in `.devlens.yml` or a separate `.devlens-rules.yml`:

```yaml
rules:
  custom:
    - id: no-print-statements
      type: pattern
      pattern: "\\bprint\\("
      message: "Use logging instead of print()"
      severity: warning
      include: ["*.py"]
```

### AI-Powered Code Review

Get intelligent code feedback from your preferred LLM provider. DevLens supports **6 providers** with language-aware prompts optimized for Python, JavaScript, TypeScript, Java, Go, and Rust.

**Review modes:**

| Mode | Command | Description |
|------|---------|-------------|
| Full Review | `devlens ai review <file>` | Comprehensive quality analysis |
| Bug Detection | `devlens ai bugs <file>` | Find potential bugs and edge cases |
| Refactoring | `devlens ai refactor <file>` | Suggest improvements and patterns |
| Explanation | `devlens ai explain <file>` | Explain what the code does |
| Commit Message | `devlens ai commit-msg` | Generate commit message from staged changes |
| Fix Suggestions | `devlens ai review <file>` | Auto-generate fix patches |

**Supported LLM providers:**

| Provider | Models | Environment Variable |
|----------|--------|---------------------|
| OpenAI | gpt-4o, o1, o3 | `OPENAI_API_KEY` |
| Anthropic | Claude 3.5/4 | `ANTHROPIC_API_KEY` |
| Google Gemini | gemini-2.0-flash, gemini-pro | `GEMINI_API_KEY` |
| Groq | LLaMA, Mixtral (free tier) | `GROQ_API_KEY` |
| Ollama | Any local model | No key needed |
| OpenRouter | 100+ models | `OPENROUTER_API_KEY` |

```bash
# AI review with default provider
devlens ai review app.py

# Use specific model
devlens ai review app.py --model gpt-4o

# Configure provider
devlens ai configure
```

### GitHub Integration

Review PRs and files directly from GitHub without cloning:

```bash
# Review a pull request
devlens github pr-review https://github.com/owner/repo/pull/42

# Review a specific file
devlens github file-review owner/repo src/main.py
```

DevLens can also post review comments directly on PRs via the GitHub API.

### Dependency Auditing

Scan your dependency files against the [OSV.dev](https://osv.dev) vulnerability database:

```bash
# Audit dependencies
devlens dep-audit .
```

**Supported formats:** `requirements.txt`, `package.json`, `go.mod`

Returns severity-classified findings (critical, high, medium, low) with CVE references.

### Documentation Health Check

Validate your Markdown documentation for quality and accuracy:

```bash
devlens docs-check README.md
```

- Extracts and optionally **executes** code blocks to verify they work
- AI-powered content analysis (checks clarity, completeness, accuracy)
- Static checks: missing language tags, short examples, broken structure
- Health score from 0-100 with actionable recommendations

### Repository Onboarding

Automatically generate an onboarding guide for new contributors:

```bash
devlens onboard .
```

Produces a comprehensive `ONBOARDING.md` covering:
- Project overview and architecture
- Key files and entry points
- Technology stack
- Getting started instructions
- Where to start contributing

Works with or without AI -- falls back to static analysis when no LLM is configured.

### Team Scoreboard

Track code quality across your team with gamified scoring:

```bash
# View scoreboard
devlens scoreboard
```

- Per-developer and per-project score tracking
- Composite scoring formula across all metrics
- Trend tracking over time
- **Static HTML dashboard** with Chart.js graphs
- Dark/light theme toggle
- Podium display for top 3 contributors

Scores are stored in `.devlens-scores/` as JSON history.

### Auto-Fix Generation

DevLens can generate fix patches for detected security issues:

- Rule-based fix templates for all `SEC`/`VLN` rules
- AI-powered fix generation via LLM for complex issues
- Outputs unified diff patches
- Confidence levels for each suggestion

### Incremental Caching

Smart caching to avoid re-analyzing unchanged files:

- SHA-256 file hashing for change detection
- Configurable TTL (default: 7 days)
- Cache invalidation on file change, analyzer version change, or config change
- Cache stored in `.devlens-cache/`

---

## Installation

### Basic Install

```bash
pip install devlens
```

### With AI Support

```bash
# OpenAI + Anthropic + Gemini
pip install devlens[ai]

# All AI providers including Groq
pip install devlens[all-ai]
```

### With IDE/LSP Support

```bash
pip install devlens[ide]
```

### Full Install (Everything)

```bash
pip install devlens[all-ai,ide]
```

### Development Install

```bash
git clone https://github.com/poseclx/devlens.git
cd devlens
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

**Requirements:** Python 3.10 or higher

---

## Quick Start

### 1. Initialize Configuration

```bash
devlens init
```

This creates a `.devlens.yml` file with sensible defaults.

### 2. Run Full Analysis

```bash
devlens analyze ./src
```

This runs security scanning, complexity analysis, and rule checking in one pass.

### 3. Export Results

```bash
# JSON for CI pipelines
devlens analyze ./src --format json --output results.json

# Markdown for documentation
devlens analyze ./src --format markdown --output report.md
```

### 4. Enable AI Review

```bash
export OPENAI_API_KEY="sk-..."
devlens ai review ./src/main.py
```

---

## Configuration

DevLens looks for configuration in this order:

1. `.devlens.yml` / `.devlens.yaml`
2. `.devlens.toml`
3. `pyproject.toml` under `[tool.devlens]`

### Example `.devlens.yml`

```yaml
security:
  enabled: true
  severity_threshold: medium

complexity:
  enabled: true
  max_cyclomatic: 10
  max_cognitive: 15
  max_function_length: 50
  max_nesting_depth: 4
  max_parameters: 5

rules:
  builtin:
    - no-eval
    - no-exec
    - no-star-import
    - no-mutable-default
    - no-bare-except
  custom: []

ignore:
  paths:
    - "**/test_*"
    - "**/migrations/**"
    - "**/.venv/**"

ai:
  provider: openai
  model: gpt-4o

cache:
  enabled: true
  ttl_days: 7
```

---

## IDE Integration

### VS Code Extension

The `devlens-vscode` extension provides real-time code quality feedback directly in your editor.

**Features:**
- Real-time diagnostics (underlines and problems panel)
- Quick fix code actions for detected issues
- Hover information with rule explanations
- CodeLens showing quality scores above functions
- Integrated AI review commands
- File analysis dashboard

**Supported languages:** Python, JavaScript, TypeScript, Java, Go, Rust, Ruby, PHP, C, C++, C#

**Extension settings:**

| Setting | Description | Default |
|---------|-------------|---------|
| `devlens.enabled` | Enable/disable the extension | `true` |
| `devlens.pythonPath` | Path to Python interpreter | `python3` |
| `devlens.aiProvider` | AI provider for reviews | `openai` |
| `devlens.aiModel` | AI model to use | `gpt-4o` |
| `devlens.trace.server` | LSP trace level | `off` |

### Language Server Protocol (LSP)

DevLens includes a full LSP server for integration with any editor:

```bash
# Start LSP server (STDIO mode)
devlens lsp

# TCP mode for network editors
devlens lsp --tcp --port 2087
```

**LSP capabilities:**
- `textDocument/diagnostic` -- real-time issue detection
- `textDocument/codeAction` -- quick fixes
- `textDocument/hover` -- rule explanations
- `textDocument/codeLens` -- quality score overlays
- Custom commands: `analyzeFile`, `showDashboard`, `analyzeWorkspace`, `clearCache`

---

## Plugin System

Extend DevLens with custom plugins. Plugins can be checkers, fixers, reporters, formatters, analyzers, or custom types.

### Creating a Plugin

```bash
# Scaffold a new plugin
devlens plugins create my-custom-checker
```

### Plugin Types

| Type | Description |
|------|-------------|
| `CHECKER` | Adds new analysis checks |
| `FIXER` | Provides auto-fix capabilities |
| `REPORTER` | Custom output formats |
| `FORMATTER` | Code formatting integration |
| `ANALYZER` | Full analysis pipeline |
| `CUSTOM` | Anything else |

### Plugin Discovery

Plugins are discovered via:
1. **Entry points** -- `[project.entry-points."devlens.plugins"]` in `pyproject.toml`
2. **Local directories** -- plugins in your project
3. **Plugin registry** -- centralized plugin index

### Managing Plugins

```bash
devlens plugins list              # List installed plugins
devlens plugins info <name>       # Plugin details
devlens plugins install <package> # Install from PyPI
devlens plugins uninstall <name>  # Remove a plugin
```

### Plugin Lifecycle Hooks

```python
from devlens.plugins import PluginBase, PluginType

class MyPlugin(PluginBase):
    name = "my-plugin"
    version = "1.0.0"
    plugin_type = PluginType.CHECKER

    def on_start(self, config):
        """Called before analysis begins."""

    def on_file(self, filepath, content):
        """Called for each file being analyzed."""

    def on_complete(self, results):
        """Called after all files are processed."""
```

---

## CI/CD Integration

### GitHub Actions

DevLens ships with ready-to-use workflow templates:

#### Automated PR Review (`devlens.yml`)

Automatically reviews pull requests with AI and posts comments:

```yaml
name: DevLens PR Review
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install devlens[ai]
      - run: devlens analyze . --format json --output results.json
      - run: devlens github pr-review ${{ github.event.pull_request.html_url }}
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

#### CI Pipeline (`ci.yml`)

Lint, type check, and test across Python versions:

- **Linting**: ruff
- **Type checking**: mypy
- **Testing**: pytest with coverage (Python 3.10, 3.11, 3.12)

#### Documentation Check (`docs-check.yml`)

Scheduled weekly (Monday 09:00 UTC) documentation health validation.

#### Auto-Onboarding (`onboarding.yml`)

Regenerates `ONBOARDING.md` when dependency files change.

#### PyPI Publishing (`publish.yml`)

Automated publishing on GitHub release using OIDC trusted publishing.

### Pre-commit Hooks

Add DevLens to your `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/poseclx/devlens
    rev: v0.8.0
    hooks:
      - id: devlens-scan
        # Security scan on staged files (per-commit)
        types_or: [python, javascript, ts, go, rust, yaml, json, toml]

      - id: devlens-scan-all
        # Full scan on push
        stages: [push]
```

---

## CLI Reference

```
Usage: devlens [OPTIONS] COMMAND [ARGS]...

Commands:
  analyze      Run full analysis (security + complexity + rules)
  security     Security-only scan
  complexity   Complexity analysis
  ai           AI-powered code review commands
    review       Full code review
    explain      Explain code
    bugs         Detect potential bugs
    refactor     Suggest refactoring
    commit-msg   Generate commit message
    configure    Set up AI provider
  github       GitHub integration
    pr-review    Review a pull request
    file-review  Review a file from a repo
  plugins      Plugin management
    list         List installed plugins
    info         Plugin details
    install      Install a plugin
    uninstall    Remove a plugin
    create       Scaffold a new plugin
  onboard      Generate onboarding guide
  docs-check   Check documentation health
  dep-audit    Audit dependency vulnerabilities
  scoreboard   Team quality scoreboard
  lsp          Start Language Server
  init         Initialize configuration
  version      Show version

Global Options:
  --config PATH       Config file path
  --format TEXT       Output format (table|json|markdown)
  --output PATH       Write output to file
  --verbose / -v      Verbose output
  --ai / --no-ai      Enable/disable AI features
  --model TEXT        Override AI model
```

---

## Architecture

```
devlens/
|-- __init__.py          # Package exports (DevLensAnalyzer, load_config, etc.)
|-- __main__.py          # python -m devlens entry point
|-- cli.py               # Typer CLI with all commands and subcommands
|-- config.py            # YAML/TOML config loading and defaults
|-- analyzer.py          # Core orchestrator -- coordinates all analysis
|-- security.py          # AST + regex security scanner (20+ rules)
|-- complexity.py        # Cyclomatic/cognitive complexity with A-F grading
|-- rules.py             # Custom rule engine (pattern, threshold, AST)
|-- ai_review.py         # Multi-provider AI code review
|-- language_server.py   # LSP server (pygls 2.0)
|-- plugins.py           # Plugin framework with entry-point discovery
|-- github.py            # GitHub PR/file review integration
|-- onboarder.py         # Repository onboarding guide generator
|-- docs_checker.py      # Markdown documentation health checker
|-- depaudit.py          # Dependency vulnerability auditor (OSV.dev)
|-- scoreboard.py        # Team scoreboard with HTML dashboard
|-- cache.py             # SHA-256 incremental analysis cache
|-- fixer.py             # Auto-fix patch generator
|-- summarizer.py        # Human-readable report summarizer
|-- reporter.py          # Multi-format report output

devlens-vscode/          # VS Code extension (TypeScript)
|-- src/                 # Extension source code
|-- package.json         # Extension manifest and settings

tests/                   # Comprehensive test suite (13 files)
|-- conftest.py          # Shared pytest fixtures
|-- test_analyzer.py     # Core analyzer tests
|-- test_cli.py          # CLI command tests
|-- test_complexity.py   # Complexity metrics tests
|-- test_config.py       # Configuration loading tests
|-- test_security.py     # Security scanner tests
|-- test_plugins.py      # Plugin system tests
|-- test_github.py       # GitHub integration tests
|-- test_reporter.py     # Report generation tests
|-- test_onboarder.py    # Onboarding tests
|-- test_docs_checker.py # Docs checker tests
|-- test_depaudit.py     # Dependency audit tests
|-- test_language_server.py # LSP server tests

.github/workflows/       # CI/CD
|-- ci.yml               # Lint + type check + test matrix
|-- devlens.yml          # Automated PR review
|-- docs-check.yml       # Weekly docs validation
|-- onboarding.yml       # Auto-generate ONBOARDING.md
|-- publish.yml          # PyPI release pipeline
```

---

## Programmatic Usage

DevLens can be used as a Python library:

```python
from devlens import DevLensAnalyzer, load_config

# Load configuration
config = load_config()

# Create analyzer
analyzer = DevLensAnalyzer(config)

# Analyze a file or directory
results = analyzer.analyze("./src")

# Access results
for finding in results.security_findings:
    print(f"{finding.severity}: {finding.message} at {finding.file}:{finding.line}")

for metric in results.complexity_metrics:
    print(f"{metric.function_name}: complexity={metric.cyclomatic}, grade={metric.grade}")
```

### AI Review Programmatic Access

```python
from devlens import AIReviewer

reviewer = AIReviewer(provider="openai", model="gpt-4o")
result = reviewer.review(code, mode="review")
print(result.feedback)
```

### Plugin Manager

```python
from devlens import PluginManager

pm = PluginManager()
pm.discover()  # Find all plugins
pm.load_all()  # Load and validate

for plugin in pm.plugins:
    print(f"{plugin.name} v{plugin.version} ({plugin.plugin_type})")
```

---

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

```bash
# Setup
git clone https://github.com/poseclx/devlens.git
cd devlens
pip install -e ".[dev]"

# Run tests
pytest

# Lint and format
ruff check .
ruff format .
```

**Areas where we'd love help:**
- New LLM provider integrations
- GitHub Actions workflow improvements
- Additional security detection rules
- Test coverage improvements
- Documentation and examples

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

<div align="center">

**DevLens** -- See your code clearly.

[Report Bug](https://github.com/poseclx/devlens/issues) | [Request Feature](https://github.com/poseclx/devlens/issues) | [Discussions](https://github.com/poseclx/devlens/discussions)

</div>