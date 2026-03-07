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
git clone https://github.com/yourusername/devlens.git
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

## Example Output

```
PR #42 — Add user authentication middleware

SUMMARY
  Adds JWT-based auth middleware to the Express app. Touches 8 files, 320 lines changed.

RISK AREAS (review carefully)
  - src/middleware/auth.js       Token expiry logic may have edge case on refresh
  - src/routes/user.js          New endpoint /api/user/me has no rate limiting
  - config/env.example          JWT_SECRET added — make sure .env is in .gitignore

SAFE TO SKIM
  - tests/                      Unit tests for new middleware (looks correct)
  - package.json                Dependency additions only (jsonwebtoken, express-jwt)
  - README.md                   Docs update

VERDICT
  Needs attention on 2 security items before merge. Rest looks good.
```

## Configuration

Create a `.devlens.yml` in your project root:

```yaml
model: gpt-4o          # LLM to use (gpt-4o, claude-3-5-sonnet)
detail: medium         # low | medium | high
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

Add only the one you use — DevLens auto-detects which provider is configured. `GITHUB_TOKEN` is provided automatically by GitHub Actions, no setup needed.

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
