"""Configuration loader for DevLens.

Supports .devlens.yml in the project root with sections for:
  - model / detail / repo (general)
  - security (scanner rules and settings)
  - comment (PR comment bot settings)
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
