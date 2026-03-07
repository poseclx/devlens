<div align="center">

# DevLens

**A PR review assistant that cuts through the noise and tells you what actually matters.**

[![PyPI version](https://img.shields.io/pypi/v/devlens?color=blue&label=PyPI)](https://pypi.org/project/devlens/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![GitHub Actions](https://img.shields.io/badge/CI%2FCD-GitHub%20Actions-2088FF?logo=github-actions&logoColor=white)](https://github.com/poseclx/devlens/actions)

DevLens analyzes GitHub Pull Requests and gives you a concise, risk-aware summary — so you spend less time reading diffs and more time writing code.

[Getting Started](#installation) | [Usage](#usage) | [Configuration](#configuration) | [GitHub Actions](#github-actions-setup) | [Contributing](#contributing)

</div>

---

## Why DevLens?

Code reviews are critical but time-consuming. DevLens surfaces what actually matters in a PR:

- **Risk Detection** — Flags security concerns, breaking changes, and logic-heavy areas
- **Smart Summarization** — Skips trivial changes (formatting, imports), highlights what matters
- **Context-Aware** — Understands the purpose of the PR from title, description, and linked issues
- **CI/CD Ready** — Works as a GitHub Action or standalone CLI tool
- **Multi-Provider** — Supports OpenAI, Anthropic, and Google Gemini out of the box

## Installation

```bash
pip install devlens
```

Or install directly from GitHub:

```bash
pip install git+https://github.com/poseclx/devlens.git
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

### Example Output

```
PR #42 — Add user authentication middleware

SUMMARY
  Adds JWT-based auth middleware to the Express app.
  Touches 8 files, 320 lines changed.

RISK AREAS (review carefully)
  - src/middleware/auth.js       Token expiry logic may have edge case on refresh
  - src/routes/user.js           New endpoint /api/user/me has no rate limiting
  - config/env.example           JWT_SECRET added — make sure .env is in .gitignore

SAFE TO SKIM
  - tests/                       Unit tests for new middleware (looks correct)
  - package.json                 Dependency additions only (jsonwebtoken, express-jwt)
  - README.md                    Docs update

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

Add DevLens to your CI pipeline by copying the workflow files from [`.github/workflows/`](.github/workflows/).

Then go to **Settings > Secrets and variables > Actions** and add the secret for your AI provider:

| Provider | Secret Name | Where to Get It |
|:---|:---|:---|
| OpenAI | `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| Anthropic | `ANTHROPIC_API_KEY` | [console.anthropic.com/keys](https://console.anthropic.com/settings/keys) |
| Google Gemini | `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/app/apikey) |

> Add only the one you use — DevLens auto-detects which provider is configured. `GITHUB_TOKEN` is provided automatically by GitHub Actions.

## Environment Variables

| Variable | Description |
|:---|:---|
| `GITHUB_TOKEN` | Provided automatically by GitHub Actions |
| `OPENAI_API_KEY` | OpenAI API key (if using GPT models) |
| `ANTHROPIC_API_KEY` | Anthropic API key (if using Claude) |
| `GEMINI_API_KEY` | Google Gemini API key (if using Gemini models) |

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT — see [LICENSE](LICENSE.txt)

---

<div align="center">

Built by [@theonurrs](https://x.com/theonurrs)

</div>
