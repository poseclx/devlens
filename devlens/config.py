"""Configuration loader for DevLens.

Supports .devlens.yml in the project root with sections for:
  - model / detail / repo (general)
  - security (scanner rules and settings)
  - comment (PR comment bot settings)
  - cache (incremental analysis caching)
  - rules (custom rule engine)
  - dashboard (web dashboard settings)
  - scoreboard (team scoreboard settings)
  - ignore_paths (files to skip)

Example .devlens.yml:

    model: groq/llama-3.3-70b-versatile
    detail: high
    repo: owner/repo

    security:
      enabled: true
      fail_on: high          # critical | high | medium | low
      ignore_rules: []       # e.g. ["VLN007", "VLN010"]
      custom_rules:
        - id: CUSTOM001
          title: Internal API Token
          pattern: "itk_[A-Za-z0-9]{32}"
          severity: critical
          description: Internal API token detected.
          suggestion: Use vault injection instead.

    comment:
      enabled: false         # auto-comment on every review?
      template: default      # default | minimal | full
      include_security: true

    cache:
      enabled: true
      dir: .devlens-cache    # cache directory (relative to project root)
      ttl_days: 7            # time-to-live in days

    rules:
      enabled: true
      files:                 # external rule files
        - .devlens-rules.yml
      builtin_ast:           # built-in AST checks to enable
        - no-eval
        - no-exec
        - no-star-import
        - no-mutable-default
        - no-bare-except
        - no-global
      custom_rules: []       # inline custom rules (same format as rule files)

    dashboard:
      theme: dark            # dark | light
      output: devlens-dashboard.html
      sections:              # which sections to include
        - complexity
        - security
        - dependencies
        - docs
        - rules
      auto_open: true

    scoreboard:
      enabled: true
      dir: .devlens-scores   # score history directory
      auto_record: false     # auto-record after each review?
      output: devlens-scoreboard.html

    ignore_paths:
      - "*.lock"
      - "dist/*"
      - "*.generated.*"
      - "migrations/*"
"""

from __future__ import annotations

from pathlib import Path
import yaml


DEFAULT_CONFIG: dict = {
    "model": "gpt-4o",
    "detail": "medium",
    "risk_focus": ["security", "breaking-changes", "performance"],
    "ignore_paths": ["*.lock", "dist/*", "*.generated.*"],
    "security": {
        "enabled": True,
        "fail_on": "high",
        "ignore_rules": [],
        "custom_rules": [],
    },
    "comment": {
        "enabled": False,
        "template": "default",
        "include_security": True,
    },
    "cache": {
        "enabled": True,
        "dir": ".devlens-cache",
        "ttl_days": 7,
    },
    "rules": {
        "enabled": True,
        "files": [".devlens-rules.yml"],
        "builtin_ast": [
            "no-eval",
            "no-exec",
            "no-star-import",
            "no-mutable-default",
            "no-bare-except",
            "no-global",
        ],
        "custom_rules": [],
    },
    "dashboard": {
        "theme": "dark",
        "output": "devlens-dashboard.html",
        "sections": ["complexity", "security", "dependencies", "docs", "rules"],
        "auto_open": True,
    },
    "scoreboard": {
        "enabled": True,
        "dir": ".devlens-scores",
        "auto_record": False,
        "output": "devlens-scoreboard.html",
    },
    "plugins": {
        "enabled": True,
        "enabled_plugins": [],
        "plugin_dir": "",
        "auto_discover": True,
        "fail_on_error": False,
    },
    "lsp": {
        "enabled": True,
        "mode": "stdio",
        "host": "127.0.0.1",
        "port": 2087,
        "lint_on_save": True,
        "lint_on_open": True,
        "lint_on_change": False,
        "debounce_ms": 500,
        "show_code_lens": True,
        "max_file_size": 500000,
        "severity_filter": "low",
        "log_level": "info",
    },
    "ai_review": {
        "enabled": True,
        "provider": "openai",
        "model": "",
        "api_key": "",
        "api_base_url": "",
        "max_tokens": 4096,
        "temperature": 0.3,
        "timeout": 60.0,
        "max_retries": 3,
        "rate_limit_rpm": 60,
        "rate_limit_tpm": 100000,
        "cache_enabled": True,
        "cache_dir": ".devlens-cache/ai",
        "cache_ttl": 86400,
        "max_file_size": 50000,
        "context_lines": 5,
        "languages": [],
    },
}

CONFIG_FILENAMES = [".devlens.yml", ".devlens.yaml", "devlens.yml"]


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base (override wins for scalars)."""
    merged = base.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(start: Path | None = None) -> dict:
    """Walk up from start (default: cwd) looking for a config file."""
    search = start or Path.cwd()
    for directory in [search, *search.parents]:
        for name in CONFIG_FILENAMES:
            candidate = directory / name
            if candidate.exists():
                with candidate.open() as f:
                    user_cfg = yaml.safe_load(f) or {}
                return _deep_merge(DEFAULT_CONFIG, user_cfg)
    return DEFAULT_CONFIG.copy()


def get_security_config(cfg: dict) -> dict:
    """Extract security section with defaults applied."""
    return _deep_merge(DEFAULT_CONFIG["security"], cfg.get("security", {}))


def get_comment_config(cfg: dict) -> dict:
    """Extract comment section with defaults applied."""
    return _deep_merge(DEFAULT_CONFIG["comment"], cfg.get("comment", {}))


def get_cache_config(cfg: dict) -> dict:
    """Extract cache section with defaults applied."""
    return _deep_merge(DEFAULT_CONFIG["cache"], cfg.get("cache", {}))


def get_rules_config(cfg: dict) -> dict:
    """Extract rules section with defaults applied."""
    return _deep_merge(DEFAULT_CONFIG["rules"], cfg.get("rules", {}))


def get_dashboard_config(cfg: dict) -> dict:
    """Extract dashboard section with defaults applied."""
    return _deep_merge(DEFAULT_CONFIG["dashboard"], cfg.get("dashboard", {}))


def get_scoreboard_config(cfg: dict) -> dict:
    """Extract scoreboard section with defaults applied."""
    return _deep_merge(DEFAULT_CONFIG["scoreboard"], cfg.get("scoreboard", {}))


def get_plugin_config(cfg: dict) -> dict:
    """Extract plugins section with defaults applied."""
    return _deep_merge(DEFAULT_CONFIG["plugins"], cfg.get("plugins", {}))


def get_lsp_config(cfg: dict) -> dict:
    """Extract lsp section with defaults applied."""
    return _deep_merge(DEFAULT_CONFIG["lsp"], cfg.get("lsp", {}))


def get_ai_review_config(cfg: dict) -> dict:
    """Extract ai_review section with defaults applied."""
    return _deep_merge(DEFAULT_CONFIG["ai_review"], cfg.get("ai_review", {}))
