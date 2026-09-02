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

DEFAULT_CLONE_TIMEOUT_SECONDS = 60
DEFAULT_MAX_REPO_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
FULL_COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class RepositoryLoadError(ValueError):
    """Raised when a repository URL is invalid, inaccessible, or too large to load."""


def validate_github_url(github_url: str) -> tuple[str, str]:
    """Validate that github_url is a public https://github.com/<owner>/<repository> URL.

    Returns (owner, repo) on success. Raises RepositoryLoadError otherwise.
    """
    if not isinstance(github_url, str) or not github_url.strip():
        raise RepositoryLoadError("Repository URL must be a non-empty string.")

    match = GITHUB_URL_PATTERN.match(github_url.strip())
    if not match or match.group("repo") in {".", ".."}:
        raise RepositoryLoadError(
            f"'{github_url}' is not a valid https://github.com/<owner>/<repository> URL."
        )
    return match.group("owner"), match.group("repo")


def _directory_size_bytes(path: Path) -> int:
    return sum(entry.lstat().st_size for entry in path.rglob("*") if not entry.is_symlink() and entry.is_file())


def public_git_environment() -> dict[str, str]:
    """Do not inherit credentials, Git overrides, hooks, proxies or user config."""
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL"}
    return {
        **{key: value for key, value in os.environ.items() if key.upper() in allowed},
        "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_LFS_SKIP_SMUDGE": "1",
    }


PUBLIC_GIT_OPTIONS = [
    "-c", "credential.helper=", "-c", "http.followRedirects=false",
    "-c", f"core.hooksPath={os.devnull}", "-c", "protocol.allow=never",
    "-c", "protocol.https.allow=always", "-c", "submodule.recurse=false",
]


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
    owner, repository = validate_github_url(github_url)
    github_url = f"https://github.com/{owner}/{repository}"

    destination = Path(tempfile.mkdtemp(prefix="codebase-archaeologist-"))

    # Disable interactive credential prompts so a private/inaccessible repo fails
    # fast and clearly instead of hanging until the timeout.
    clone_env = public_git_environment()

    try:
        result = subprocess.run(
            ["git", *PUBLIC_GIT_OPTIONS, "clone", "--depth", "1", "--single-branch",
             "--no-tags", "--template=", github_url, str(destination)],
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
    except OSError as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise RepositoryLoadError("Git could not be started.") from exc

    if result.returncode != 0:
        shutil.rmtree(destination, ignore_errors=True)
        raise RepositoryLoadError(
            f"Could not clone '{github_url}'. It may be private, deleted, or "
            "misspelled. Renamed repositories must use their current URL."
        )

    try:
        size_bytes = _directory_size_bytes(destination)
    except OSError as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise RepositoryLoadError("Could not measure the repository checkout.") from exc
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


def resolve_commit_sha(path: Path) -> str:
    """Return the immutable HEAD commit for a cloned repository."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            env=public_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepositoryLoadError("Could not resolve the repository commit SHA.") from exc

    commit_sha = result.stdout.strip().lower()
    if result.returncode != 0 or not FULL_COMMIT_SHA_PATTERN.fullmatch(commit_sha):
        raise RepositoryLoadError("Could not resolve the repository commit SHA.")
    return commit_sha

