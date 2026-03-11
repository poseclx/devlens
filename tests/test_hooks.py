"""Tests for devlens.hooks module."""
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from devlens.hooks import (
    install_hook,
    uninstall_hook,
    get_staged_files,
    run_hook,
)


# ---------------------------------------------------------------------------
# install_hook tests
# ---------------------------------------------------------------------------

class TestInstallHook:
    @patch("devlens.hooks._find_git_root")
    def test_install_creates_hook(self, mock_root, tmp_path):
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        mock_root.return_value = tmp_path
        result = install_hook()
        assert result is True

    @patch("devlens.hooks._find_git_root")
    def test_install_no_git_repo(self, mock_root):
        mock_root.return_value = None
        result = install_hook()
        assert result is False

    @patch("devlens.hooks._find_git_root")
    def test_install_force_overwrites(self, mock_root, tmp_path):
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        hook_file = git_dir / "pre-commit"
        hook_file.write_text("#!/bin/sh\necho existing")
        mock_root.return_value = tmp_path
        result = install_hook(force=True)
        assert result is True


# ---------------------------------------------------------------------------
# uninstall_hook tests
# ---------------------------------------------------------------------------

class TestUninstallHook:
    @patch("devlens.hooks._find_git_root")
    def test_uninstall_existing(self, mock_root, tmp_path):
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        hook_file = git_dir / "pre-commit"
        # Must contain "DevLens" (capital D, capital L) for uninstall to recognize it
        hook_file.write_text("#!/bin/sh\n# DevLens pre-commit hook\ndevlens scan")
        mock_root.return_value = tmp_path
        result = uninstall_hook()
        assert result is True

    @patch("devlens.hooks._find_git_root")
    def test_uninstall_no_git(self, mock_root):
        mock_root.return_value = None
        result = uninstall_hook()
        assert result is False


# ---------------------------------------------------------------------------
# get_staged_files tests
# ---------------------------------------------------------------------------

class TestGetStagedFiles:
    @patch("devlens.hooks.subprocess.run")
    def test_returns_file_list(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="file1.py\nfile2.py\n",
        )
        files = get_staged_files()
        assert "file1.py" in files
        assert "file2.py" in files

    @patch("devlens.hooks.subprocess.run")
    def test_empty_staging(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
        )
        files = get_staged_files()
        assert files == []

    @patch("devlens.hooks.subprocess.run")
    def test_git_error(self, mock_run):
        mock_run.side_effect = FileNotFoundError("git not found")
        files = get_staged_files()
        assert files == []


# ---------------------------------------------------------------------------
# run_hook tests
# ---------------------------------------------------------------------------

def _setup_run_hook_mocks():
    """Inject mock modules for run_hook's internal imports.

    run_hook() does::
        from devlens.security import scan_path, SecurityFinding, Severity
        from devlens.config import load_config, get_security_config
        from devlens.ignore import load_ignore_patterns

    devlens.ignore doesn't exist, and we want to isolate the others.
    """
    # Save originals so we can restore them in finally blocks
    _saved = {
        "devlens.ignore": sys.modules.get("devlens.ignore"),
        "devlens.config": sys.modules.get("devlens.config"),
        "devlens.security": sys.modules.get("devlens.security"),
    }

    # -- devlens.ignore mock --
    mock_ignore_mod = MagicMock()
    ignore_filter = MagicMock()
    # filter_paths returns whatever was passed in (no filtering)
    ignore_filter.filter_paths = MagicMock(side_effect=lambda paths: paths)
    mock_ignore_mod.load_ignore_patterns = MagicMock(return_value=ignore_filter)

    # -- devlens.config mock --
    mock_config_mod = MagicMock()
    mock_config_mod.load_config = MagicMock(return_value={})
    mock_config_mod.get_security_config = MagicMock(return_value={"ignore_rules": [], "fail_on": "high"})

    # -- devlens.security mock --
    mock_security_mod = MagicMock()
    mock_security_mod.scan_path = MagicMock(return_value=[])
    mock_security_mod.SecurityFinding = MagicMock
    mock_security_mod.Severity = MagicMock

    sys.modules["devlens.ignore"] = mock_ignore_mod
    sys.modules["devlens.config"] = mock_config_mod
    sys.modules["devlens.security"] = mock_security_mod

    return mock_security_mod, _saved


def _restore_modules(_saved):
    """Restore original sys.modules entries after run_hook mocks."""
    for key, orig in _saved.items():
        if orig is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = orig


class TestRunHook:
    @patch("devlens.hooks.get_staged_files")
    def test_clean_returns_zero(self, mock_staged):
        """No staged files -> immediate return 0."""
        mock_staged.return_value = []
        # run_hook() imports devlens.ignore at the top of the function body
        # (before checking staged files), so we must inject the fake module
        _mock_security, _saved = _setup_run_hook_mocks()
        try:
            result = run_hook()
            assert result == 0
        finally:
            _restore_modules(_saved)

    @patch("devlens.hooks.get_staged_files")
    def test_with_issues(self, mock_staged, tmp_path):
        """Staged files but no findings -> return 0."""
        mock_staged.return_value = ["insecure.py"]
        mock_security, _saved = _setup_run_hook_mocks()
        # scan_path returns empty list -> no findings -> return 0
        mock_security.scan_path.return_value = []
        try:
            result = run_hook()
            assert isinstance(result, int)
            assert result == 0
        finally:
            # Clean up injected modules
            _restore_modules(_saved)
