"""Configuration loader for DevLens."""

from pathlib import Path
import yaml


DEFAULT_CONFIG: dict = {
    "model": "gpt-4o",
    "detail": "medium",
    "risk_focus": ["security", "breaking-changes", "performance"],
    "ignore_paths": ["*.lock", "dist/*", "*.generated.*"],
}

CONFIG_FILENAMES = [".devlens.yml", ".devlens.yaml", "devlens.yml"]


def load_config(start: Path | None = None) -> dict:
    """Walk up from start (default: cwd) looking for a config file."""
    search = start or Path.cwd()
    for directory in [search, *search.parents]:
        for name in CONFIG_FILENAMES:
            candidate = directory / name
            if candidate.exists():
                with candidate.open() as f:
                    user_cfg = yaml.safe_load(f) or {}
                return {**DEFAULT_CONFIG, **user_cfg}
    return DEFAULT_CONFIG.copy()
