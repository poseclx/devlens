"""Tests for devlens.config module."""
import pytest
from pathlib import Path
import tempfile
import yaml

from devlens.config import load_config, DEFAULT_CONFIG, CONFIG_FILENAMES


class TestLoadConfig:
    def test_returns_defaults_when_no_config_file(self, tmp_path):
        cfg = load_config(start=tmp_path)
        assert cfg == DEFAULT_CONFIG

    def test_loads_devlens_yml(self, tmp_path):
        config_file = tmp_path / ".devlens.yml"
        config_file.write_text(yaml.dump({"model": "claude-3-5-sonnet-20241022", "detail": "high"}))
        cfg = load_config(start=tmp_path)
        assert cfg["model"] == "claude-3-5-sonnet-20241022"
        assert cfg["detail"] == "high"

    def test_user_config_merged_with_defaults(self, tmp_path):
        config_file = tmp_path / ".devlens.yml"
        config_file.write_text(yaml.dump({"model": "gemini-1.5-pro"}))
        cfg = load_config(start=tmp_path)
        assert cfg["model"] == "gemini-1.5-pro"
        assert "detail" in cfg
        assert "ignore_paths" in cfg

    def test_walks_up_to_parent(self, tmp_path):
        config_file = tmp_path / ".devlens.yml"
        config_file.write_text(yaml.dump({"model": "gpt-4-turbo"}))
        subdir = tmp_path / "src" / "module"
        subdir.mkdir(parents=True)
        cfg = load_config(start=subdir)
        assert cfg["model"] == "gpt-4-turbo"

    def test_empty_config_file_returns_defaults(self, tmp_path):
        config_file = tmp_path / ".devlens.yml"
        config_file.write_text("")
        cfg = load_config(start=tmp_path)
        assert cfg == DEFAULT_CONFIG

    def test_default_config_has_required_keys(self):
        for key in ("model", "detail", "risk_focus", "ignore_paths"):
            assert key in DEFAULT_CONFIG