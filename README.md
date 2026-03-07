# DevLens

> AI-powered PR review assistant that cuts through the noise and tells you what actually matters.

DevLens analyzes GitHub Pull Requests and gives you a concise, risk-aware summary — so you spend less time reading diffs and more time writing code.

## Features

- **Risk Detection** — Flags security concerns, breaking changes, and logic-heavy areas
- **Smart Summarization** — Skips trivial changes (formatting, imports), highlights what matters
- **Context-Aware** — Understands the purpose of the PR from title, description, and linked issues
- **CI/CD Ready** — Works as a GitHub Action or CLI tool

## Installation

```bash
pip install devlens
```

Or install from source:

```bash
git clone https://github.com/poseclx/devlens.git
cd devlens
pip install -e .
```

## Usage

```bash
# Analyze a PR by number (auto-detects repo from current directory)
devlens review 42

# Specify a repo explicitly
devlens review 42 --repo owner/repo-name

# Output as markdown (useful for CI/CD comments)
devlens review 42 --format markdown

# Set verbosity
devlens review 42 --detail high
```

## Configuration

Create a `.devlens.yml` in your project root:

```yaml
model: gpt-4o
detail: medium
risk_focus:
  - security
  - breaking-changes
  - performance
ignore_paths:
  - "*.lock"
  - "dist/*"
  - "*.generated.*"
```

## GitHub Actions Setup

Add DevLens to your repo by copying the workflow files from `.github/workflows/`.

Then go to **Settings → Secrets and variables → Actions** and add the secret for your AI provider:

| Provider | Secret name | Where to get it |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | platform.openai.com/api-keys |
| Anthropic | `ANTHROPIC_API_KEY` | console.anthropic.com/keys |
| Google Gemini | `GEMINI_API_KEY` | aistudio.google.com/apikey |

## Environment Variables

| Variable | Description |
|---|---|
| `GITHUB_TOKEN` | Provided automatically by GitHub Actions |
| `OPENAI_API_KEY` | OpenAI API key (if using GPT models) |
| `ANTHROPIC_API_KEY` | Anthropic API key (if using Claude) |
| `GEMINI_API_KEY` | Google Gemini API key (if using Gemini models) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to contribute.

## License

MIT — see [LICENSE](LICENSE)