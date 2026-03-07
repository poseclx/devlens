# Contributing to DevLens

Thanks for your interest! Here's how to get started.

## Setup

```bash
git clone https://github.com/poseclx/devlens.git
cd devlens
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest
```

## Code Style

We use `ruff` for linting and formatting:

```bash
ruff check .
ruff format .
```

## Submitting a PR

1. Fork the repo and create a branch: `git checkout -b feat/your-feature`
2. Make your changes with tests
3. Run `ruff` and `pytest` before pushing
4. Open a PR with a clear description of what and why

## Areas to Contribute

- New LLM provider integrations (Gemini, Mistral, local Ollama)
- GitHub Actions workflow file
- More risk detection heuristics
- Test coverage improvements
- Docs and examples
