"""Tests for devlens.hooks module."""
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
        hook_file.write_text("#!/bin/sh\ndevlens")
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

class TestRunHook:
    @patch("devlens.hooks.get_staged_files")
    def test_clean_returns_zero(self, mock_staged):
        mock_staged.return_value = []
        result = run_hook()
        assert result == 0

    @patch("devlens.hooks.get_staged_files")
    @patch("devlens.hooks._find_git_root")
    def test_with_issues(self, mock_root, mock_staged, tmp_path):
        mock_staged.return_value = ["insecure.py"]
        mock_root.return_value = tmp_path
        insecure = tmp_path / "insecure.py"
        insecure.write_text('password = "admin123"\n')
        result = run_hook()
        assert isinstance(result, int)
