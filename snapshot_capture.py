"""Internal offline capture of commit-verified Python source for evidence storage.

Only use with a server-owned checkout created by the repository loader. This is
not an HTTP endpoint or an arbitrary-directory upload facility. Repository code
is parsed in a fresh temporary source tree, never imported or executed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Any

from analyzer import IGNORED_DIR_NAMES, MAX_PYTHON_FILES, analyze_repository
from evidence_store import EvidenceSnapshotStore, SnapshotReference
from interpretation_evidence import MAX_SOURCE_FILE_BYTES
from repository_loader import PUBLIC_GIT_OPTIONS, public_git_environment

MAX_CAPTURE_BYTES = 8 * 1024 * 1024
MAX_TREE_OUTPUT_BYTES = 8 * 1024 * 1024


class SnapshotCaptureError(ValueError):
    """Source could not be tied to the selected commit within capture limits."""


def _git(root: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", *PUBLIC_GIT_OPTIONS, "-c", f"safe.directory={root.as_posix()}",
             "-c", "core.fsmonitor=false", "--no-replace-objects", "-C", str(root), *arguments],
            env=public_git_environment(), capture_output=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SnapshotCaptureError("Could not inspect the selected Git snapshot.") from exc
    if result.returncode or len(result.stdout) > MAX_TREE_OUTPUT_BYTES:
        raise SnapshotCaptureError("Git snapshot inspection failed or exceeded the output limit.")
    return result.stdout


def _python_entries(tree: bytes) -> list[tuple[str, str, int]]:
    entries = []
    seen = set()
    prefixes: dict[str, str] = {}
    total_bytes = 0
    for record in tree.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, oid, size = metadata.split()
            path = raw_path.decode("utf-8")
            if (not path.endswith(".py") and path != "pyproject.toml") or any(part in IGNORED_DIR_NAMES for part in path.split("/")[:-1]):
                continue
            # Never read symlinks, submodules, or special files from the checkout.
            if kind != b"blob" or mode not in {b"100644", b"100755"}:
                continue
            parts = path.split("/")
            reserved = {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
            if (len(path) > 500 or any(char in path for char in '\\:<>|"?*')
                    or any(ord(char) < 32 for char in path)
                    or any(part in {"", ".", ".."} or part.endswith((".", " "))
                           or part.split(".")[0].upper() in reserved for part in parts)
                    or path.casefold() in seen):
                raise ValueError("unsafe or colliding path")
            # Prevent case-insensitive directory aliases from changing staged
            # paths on Windows, even when the full file names differ.
            for index in range(1, len(parts) + 1):
                prefix = "/".join(parts[:index])
                if prefixes.setdefault(prefix.casefold(), prefix) != prefix:
                    raise ValueError("case-colliding directory")
            object_id = oid.decode("ascii")
            source_size = int(size)
            if not re.fullmatch(r"[0-9a-f]{40}", object_id) or not 0 <= source_size <= MAX_SOURCE_FILE_BYTES:
                raise ValueError("invalid blob or oversized file")
        except (ValueError, UnicodeError) as exc:
            raise SnapshotCaptureError("Git source entry is invalid or exceeds capture limits.") from exc
        entries.append((path, object_id, source_size))
        seen.add(path.casefold())
        total_bytes += source_size
        if len(entries) > MAX_PYTHON_FILES or total_bytes > MAX_CAPTURE_BYTES:
            raise SnapshotCaptureError("Python snapshot exceeds capture limits.")
    return entries


def analyze_verified_snapshot(checkout: Path) -> tuple[dict[str, Any], dict[str, bytes], str]:
    """Analyze exactly the captured bytes after verifying their Git blob IDs.

Untracked files are intentionally excluded. Modified/missing tracked Python
files fail closed, including checkout filters or newline transformations that
change the blob bytes. Caller controls job concurrency and process resource
limits; Git output limits here are checked after subprocess capture, not streamed.
"""
    root = checkout.resolve(strict=True)
    commit_sha = _git(root, "rev-parse", "--verify", "HEAD^{commit}").strip().decode("ascii")
    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise SnapshotCaptureError("Expected a full SHA-1 Git commit.")
    entries = _python_entries(_git(root, "ls-tree", "-r", "-z", "--long", "--full-tree", commit_sha))
    sources: dict[str, bytes] = {}
    for relative, object_id, size in entries:
        path = root / relative
        try:
            if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root and root in parent.parents):
                raise SnapshotCaptureError("Captured source cannot traverse a symlink.")
            if not path.resolve(strict=True).is_relative_to(root) or not stat.S_ISREG(path.lstat().st_mode):
                raise SnapshotCaptureError("Captured source must be a regular repository file.")
            with path.open("rb") as source_file:
                raw = source_file.read(MAX_SOURCE_FILE_BYTES + 1)
        except OSError as exc:
            raise SnapshotCaptureError("Could not read committed Python source.") from exc
        digest = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        if len(raw) != size or digest != object_id:
            raise SnapshotCaptureError("Checkout source does not match the selected Git commit.")
        sources[relative] = raw

    # Stage only verified bytes. Subsequent changes in the original checkout
    # cannot affect repeated parser reads or the returned source mapping.
    with tempfile.TemporaryDirectory(prefix="archaeologist-evidence-") as directory:
        stage = Path(directory).resolve()
        for relative, raw in sources.items():
            target = stage / relative
            if not target.resolve().is_relative_to(stage):
                raise SnapshotCaptureError("Source staging path escaped its private directory.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        graph = analyze_repository(stage)
    graph.pop("repo_root", None)
    graph["snapshot"] = {"commit_sha": commit_sha}
    graph["analysis"]["limitations"].append(
        "Evidence snapshot includes committed regular Python files and the root pyproject.toml; ignored trees, symlinks, submodules and untracked files are excluded."
    )
    # The symbol evidence store retains Python excerpts only. Manifest declarations
    # and their content hash are already included in the detached graph.
    return graph, {path: raw for path, raw in sources.items() if path.endswith(".py")}, commit_sha


def analyze_and_store_snapshot(checkout: Path, store: EvidenceSnapshotStore, *, owner_key: str
                               ) -> tuple[dict[str, Any], SnapshotReference]:
    """Internal trusted-job bridge. owner_key must be server-authenticated."""
    graph, sources, commit_sha = analyze_verified_snapshot(checkout)
    reference = store.register_trusted_snapshot(owner_key=owner_key, graph=graph,
                                                source_files=sources, commit_sha=commit_sha)
    return graph, reference
