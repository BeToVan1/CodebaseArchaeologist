"""Validate and clone a public GitHub repository into a local temporary directory."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# Only accept https://github.com/<owner>/<repository>[.git][/]
GITHUB_URL_PATTERN = re.compile(
    r"^https://github\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]+?)"
    r"(?:\.git)?/?$"
)

DEFAULT_CLONE_TIMEOUT_SECONDS = 120
DEFAULT_MAX_REPO_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB


class RepositoryLoadError(ValueError):
    """Raised when a repository URL is invalid, inaccessible, or too large to load."""


def validate_github_url(github_url: str) -> tuple[str, str]:
    """Validate that github_url is a public https://github.com/<owner>/<repository> URL.

    Returns (owner, repo) on success. Raises RepositoryLoadError otherwise.
    """
    if not isinstance(github_url, str) or not github_url.strip():
        raise RepositoryLoadError("Repository URL must be a non-empty string.")

    match = GITHUB_URL_PATTERN.match(github_url.strip())
    if not match:
        raise RepositoryLoadError(
            f"'{github_url}' is not a valid https://github.com/<owner>/<repository> URL."
        )
    return match.group("owner"), match.group("repo")


def _directory_size_bytes(path: Path) -> int:
    return sum(entry.stat().st_size for entry in path.rglob("*") if entry.is_file())


def load_repository(
    github_url: str,
    *,
    timeout_seconds: int = DEFAULT_CLONE_TIMEOUT_SECONDS,
    max_size_bytes: int = DEFAULT_MAX_REPO_SIZE_BYTES,
) -> Path:
    """Validate a public GitHub URL and shallow-clone it into a fresh temporary directory.

    Raises RepositoryLoadError for malformed/non-GitHub URLs, private or missing
    repositories, clones that exceed the time limit, or repositories that exceed
    the size limit. The caller owns the returned directory and should remove it
    with cleanup_repository() once done (analyzer.py does this automatically).
    """
    validate_github_url(github_url)

    destination = Path(tempfile.mkdtemp(prefix="codebase-archaeologist-"))

    # Disable interactive credential prompts so a private/inaccessible repo fails
    # fast and clearly instead of hanging until the timeout.
    clone_env = dict(os.environ, GIT_TERMINAL_PROMPT="0")

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", github_url, str(destination)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=clone_env,
        )
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise RepositoryLoadError(
            f"Cloning '{github_url}' exceeded the {timeout_seconds}s time limit."
        ) from exc

    if result.returncode != 0:
        shutil.rmtree(destination, ignore_errors=True)
        stderr_snippet = " ".join((result.stderr or "").split())[:300]
        raise RepositoryLoadError(
            f"Could not clone '{github_url}'. It may be private, deleted, or "
            f"misspelled. (git said: {stderr_snippet or 'no output'})"
        )

    size_bytes = _directory_size_bytes(destination)
    if size_bytes > max_size_bytes:
        shutil.rmtree(destination, ignore_errors=True)
        raise RepositoryLoadError(
            f"Repository size ({size_bytes:,} bytes) exceeds the "
            f"{max_size_bytes:,} byte limit."
        )

    return destination


def cleanup_repository(path: Path) -> None:
    """Remove a temporary directory created by load_repository."""
    shutil.rmtree(path, ignore_errors=True)