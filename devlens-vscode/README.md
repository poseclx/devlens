# DevLens for Visual Studio Code

AI-powered code review, security scanning, complexity analysis, and custom rules -- all integrated directly into your editor.

## Features

- **Real-time Diagnostics**: Security vulnerabilities, complexity warnings, rule violations, and dependency issues appear as you code
- **Quick Fixes**: Auto-fix suggestions available via the lightbulb menu
- **Hover Info**: Detailed explanations and suggestions when hovering over flagged code
- **CodeLens**: Quality score (A-F) displayed at the top of each file
- **AI Review**: Optional LLM-powered intelligent code review (OpenAI / Anthropic)
- **Dashboard**: Generate interactive HTML dashboards with analysis results
- **Multi-language**: Python, JavaScript, TypeScript, Java, Go, Rust, and more

## Requirements

- **Python 3.10+** with DevLens installed:
  ```bash
  pip install devlens
  ```
- For AI-powered review:
  ```bash
  pip install devlens[ai]
  ```

## Quick Start

1. Install the DevLens Python package: `pip install devlens`
2. Install this extension from the VS Code Marketplace
3. Open any supported file -- DevLens starts analyzing automatically
4. Configure via `.devlens.yml` in your project root (optional)

## Extension Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `devlens.enabled` | `true` | Enable/disable DevLens |
| `devlens.pythonPath` | `"python"` | Python interpreter path |
| `devlens.lintOnSave` | `true` | Analyze on file save |
| `devlens.lintOnOpen` | `true` | Analyze on file open |
| `devlens.lintOnChange` | `false` | Analyze on every keystroke (debounced) |
| `devlens.debounceMs` | `500` | Debounce delay for lint-on-change |
| `devlens.severityFilter` | `"low"` | Minimum severity to display |
| `devlens.showCodeLens` | `true` | Show quality score CodeLens |
| `devlens.configPath` | `""` | Path to .devlens.yml (auto-detect) |
| `devlens.aiReview.enabled` | `false` | Enable AI code review |
| `devlens.aiReview.provider` | `"openai"` | AI provider (openai/anthropic) |
| `devlens.logLevel` | `"info"` | Server log level |

## Commands

Open the Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`) and type "DevLens":

| Command | Description |
|---------|-------------|
| **DevLens: Analyze Current File** | Run full analysis on the active file |
| **DevLens: Run AI Review** | Get AI-powered code review |
| **DevLens: Show Dashboard** | Generate and open HTML dashboard |
| **DevLens: Restart Language Server** | Restart the DevLens LSP server |
| **DevLens: Clear Analysis Cache** | Clear cached analysis results |
| **DevLens: Configure AI Provider** | Set up AI review provider |

## Severity Levels

DevLens maps its findings to VS Code diagnostic severities:

| DevLens Severity | VS Code | Icon |
|------------------|---------|------|
| Critical | Error | Red squiggle |
| High | Warning | Yellow squiggle |
| Medium | Information | Blue squiggle |
| Low | Hint | Dots |

## Configuration File

Create a `.devlens.yml` in your project root for project-specific settings:

```yaml
model: gpt-4o
detail: high

security:
  enabled: true
  fail_on: high

rules:
  enabled: true
  builtin_ast:
    - no-eval
    - no-exec
    - no-star-import

ai_review:
  enabled: true
  provider: openai
  model: gpt-4o
  temperature: 0.3

plugins:
  auto_discover: true

lsp:
  lint_on_save: true
  lint_on_change: false
  debounce_ms: 500
  show_code_lens: true
```

## Troubleshooting

### Language server not starting
1. Verify DevLens is installed: `devlens --version`
2. Check the Python path in settings: `devlens.pythonPath`
3. Check the output panel: View > Output > DevLens

### No diagnostics appearing
1. Ensure `devlens.enabled` is `true`
2. Check the severity filter: `devlens.severityFilter`
3. Verify the file type is supported
4. Check the DevLens output panel for errors

### AI Review not working
1. Enable AI review: `devlens.aiReview.enabled = true`
2. Install AI dependencies: `pip install devlens[ai]`
3. Configure API key in `.devlens.yml` or environment variable

## Development

```bash
cd devlens-vscode
npm install
npm run compile
# Press F5 in VS Code to launch Extension Development Host
```

## License

MIT
