"""Tests for repository_loader.py. All git calls are mocked - no real network access."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repository_loader import (
    RepositoryLoadError,
    cleanup_repository,
    load_repository,
    resolve_commit_sha,
    validate_github_url,
)


# --------------------------------------------------------------------------
# validate_github_url
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/cosmicpython/code",
        "https://github.com/cosmicpython/code/",
        "https://github.com/cosmicpython/code.git",
        "https://github.com/psf/black",
    ],
)
def test_validate_github_url_accepts_valid_urls(url: str) -> None:
    owner, repo = validate_github_url(url)
    assert owner and repo


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "http://gitlab.com/owner/repo",
        "https://github.com/owner-only",
        "https://github.com/",
        "ftp://github.com/owner/repo",
        "https://github.com/owner/repo/extra/path",
        "https://notgithub.com/owner/repo",
    ],
)
def test_validate_github_url_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(RepositoryLoadError):
        validate_github_url(url)


# --------------------------------------------------------------------------
# load_repository
# --------------------------------------------------------------------------

def _successful_clone_result() -> MagicMock:
    result = MagicMock()
    result.returncode = 0
    result.stderr = ""
    return result


def test_load_repository_rejects_malformed_url_without_calling_git() -> None:
    with patch("repository_loader.subprocess.run") as mock_run:
        with pytest.raises(RepositoryLoadError):
            load_repository("https://gitlab.com/owner/repo")
        mock_run.assert_not_called()


def test_load_repository_returns_directory_on_success() -> None:
    with patch("repository_loader.subprocess.run", return_value=_successful_clone_result()) as mock_run:
        path = load_repository("https://github.com/cosmicpython/code")
        try:
            assert path.is_dir()
            called_args = mock_run.call_args.args[0]
            assert called_args[0] == "git"
            clone_index = called_args.index("clone")
            assert called_args[clone_index:clone_index + 3] == ["clone", "--depth", "1"]
            assert called_args[-2] == "https://github.com/cosmicpython/code"
            assert "--template=" in called_args
            assert "credential.helper=" in called_args
            assert "http.followRedirects=false" in called_args
        finally:
            cleanup_repository(path)


def test_load_repository_disables_terminal_prompts() -> None:
    with patch("repository_loader.subprocess.run", return_value=_successful_clone_result()) as mock_run:
        path = load_repository("https://github.com/cosmicpython/code")
        cleanup_repository(path)
        env = mock_run.call_args.kwargs["env"]
        assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_load_repository_raises_and_cleans_up_on_clone_failure() -> None:
    failed_result = MagicMock()
    failed_result.returncode = 128
    failed_result.stderr = "fatal: could not read Username for 'https://github.com': terminal prompts disabled"

    with patch("repository_loader.subprocess.run", return_value=failed_result):
        with pytest.raises(RepositoryLoadError, match="private, deleted, or misspelled"):
            load_repository("https://github.com/someone/private-repo")


def test_load_repository_raises_and_cleans_up_on_timeout() -> None:
    with patch(
        "repository_loader.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git clone", timeout=5),
    ):
        with pytest.raises(RepositoryLoadError, match="time limit"):
            load_repository("https://github.com/cosmicpython/code", timeout_seconds=5)


def test_load_repository_no_leftover_directory_on_failure(tmp_path: Path) -> None:
    """After a failed clone, the temp directory created for it should not remain on disk."""
    created_dirs: list[Path] = []
    real_mkdtemp = __import__("tempfile").mkdtemp

    def tracking_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created_dirs.append(Path(path))
        return path

    failed_result = MagicMock()
    failed_result.returncode = 1
    failed_result.stderr = "fatal: repository not found"

    with patch("repository_loader.tempfile.mkdtemp", side_effect=tracking_mkdtemp):
        with patch("repository_loader.subprocess.run", return_value=failed_result):
            with pytest.raises(RepositoryLoadError):
                load_repository("https://github.com/cosmicpython/does-not-exist")

    assert created_dirs, "expected a temp directory to have been created"
    assert not created_dirs[0].exists()


def test_load_repository_enforces_size_limit() -> None:
    with patch("repository_loader.subprocess.run", return_value=_successful_clone_result()):
        with patch("repository_loader._directory_size_bytes", return_value=10_000_000_000):
            with pytest.raises(RepositoryLoadError, match="exceeds the"):
                load_repository("https://github.com/cosmicpython/code", max_size_bytes=1_000)


def test_resolve_commit_sha_returns_full_normalized_sha(tmp_path: Path) -> None:
    result = MagicMock(returncode=0, stdout=("A" * 40) + "\n")

    with patch("repository_loader.subprocess.run", return_value=result) as mock_run:
        assert resolve_commit_sha(tmp_path) == "a" * 40

    assert mock_run.call_args.args[0] == ["git", "-C", str(tmp_path), "rev-parse", "HEAD"]


@pytest.mark.parametrize("returncode,stdout", [(1, ""), (0, "abc123\n")])
def test_resolve_commit_sha_rejects_failed_or_short_results(
    tmp_path: Path, returncode: int, stdout: str
) -> None:
    result = MagicMock(returncode=returncode, stdout=stdout)

    with patch("repository_loader.subprocess.run", return_value=result):
        with pytest.raises(RepositoryLoadError, match="commit SHA"):
            resolve_commit_sha(tmp_path)

