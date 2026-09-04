"""One trusted static-analysis job. Invoked by deep_service, never by repo code."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# -I excludes cwd and user Python paths. Only this installed application's code
# directory is restored, never the downloaded repository directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

MAX_OUTPUT_BYTES = 10 * 1024 * 1024
MAX_INPUT_FILES = 500
MAX_FILE_BYTES = 1024 * 1024
MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_EVIDENCE_FILES = 500


class InputLimitError(ValueError):
    pass


def set_resource_limits() -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("The hosted worker requires Linux resource limits.")
    import resource
    for limit, value in [
        (resource.RLIMIT_AS, 768 * 1024 * 1024),
        (resource.RLIMIT_CPU, 40),
        (resource.RLIMIT_FSIZE, 64 * 1024 * 1024),
        (resource.RLIMIT_NOFILE, 128),
        (resource.RLIMIT_CORE, 0),
    ]:
        _, hard = resource.getrlimit(limit)
        bound = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(limit, (bound, bound))


def analyze_checkout(checkout: Path) -> dict:
    from analyzer import analyze_repository, find_python_files
    files = find_python_files(checkout)
    if len(files) > MAX_INPUT_FILES:
        raise InputLimitError("Repository exceeds hosted deep-analysis limits.")
    total = 0
    for path in files:
        size = path.stat().st_size
        total += size
        if size > MAX_FILE_BYTES or total > MAX_INPUT_BYTES:
            raise InputLimitError("Repository exceeds hosted deep-analysis limits.")
    graph = analyze_repository(checkout)
    if len(graph["nodes"]) > 10_000 or len(graph["edges"]) > 30_000:
        raise InputLimitError("Graph exceeds hosted deep-analysis limits.")
    graph.pop("repo_root", None)
    return graph


def analyze_checkout_with_sources(checkout: Path) -> tuple[dict, dict[str, bytes], str]:
    """Capture and analyze the exact committed bytes retained for interpretation."""
    from analyzer import find_python_files
    from snapshot_capture import analyze_verified_snapshot

    files = find_python_files(checkout)
    if len(files) > MAX_INPUT_FILES:
        raise InputLimitError("Repository exceeds hosted deep-analysis limits.")
    total = 0
    for path in files:
        size = path.stat().st_size
        total += size
        if size > MAX_FILE_BYTES or total > MAX_INPUT_BYTES:
            raise InputLimitError("Repository exceeds hosted deep-analysis limits.")
    graph, sources, sha = analyze_verified_snapshot(checkout)
    if len(graph["nodes"]) > 10_000 or len(graph["edges"]) > 30_000:
        raise InputLimitError("Graph exceeds hosted deep-analysis limits.")
    return graph, sources, sha


def analyze_public_repository(url: str) -> dict:
    from repository_loader import load_repository, resolve_commit_sha, validate_github_url, cleanup_repository
    owner, repository = validate_github_url(url)
    canonical = f"https://github.com/{owner}/{repository}"
    checkout = load_repository(canonical, timeout_seconds=30)
    try:
        sha = resolve_commit_sha(checkout)
        graph = analyze_checkout(checkout)
        graph.update(repository={"name": f"{owner}/{repository}", "url": canonical,
                                 "pinned_url": f"{canonical}/tree/{sha}", "source": "github"},
                     snapshot={"commit_sha": sha}, source_url=canonical)
        return graph
    finally:
        cleanup_repository(checkout)


def analyze_public_repository_with_sources(url: str) -> tuple[dict, dict[str, bytes], str]:
    from repository_loader import load_repository, resolve_commit_sha, validate_github_url, cleanup_repository
    owner, repository = validate_github_url(url)
    canonical = f"https://github.com/{owner}/{repository}"
    checkout = load_repository(canonical, timeout_seconds=30)
    try:
        sha = resolve_commit_sha(checkout)
        graph, sources, captured_sha = analyze_checkout_with_sources(checkout)
        if captured_sha != sha:
            raise InputLimitError("Repository snapshot changed during analysis.")
        graph.update(repository={"name": f"{owner}/{repository}", "url": canonical,
                                 "pinned_url": f"{canonical}/tree/{sha}", "source": "github"},
                     snapshot={"commit_sha": sha}, source_url=canonical)
        return graph, sources, sha
    finally:
        cleanup_repository(checkout)


def write_result(graph: dict, output: Path) -> None:
    # Check the serialized byte cap incrementally, without making a second large
    # in-memory JSON copy. A partial file is never consumed after a failed job.
    total = 0
    with output.open("xb") as target:
        for chunk in json.JSONEncoder(ensure_ascii=False, separators=(",", ":")).iterencode(graph):
            encoded = chunk.encode("utf-8")
            total += len(encoded)
            if total > MAX_OUTPUT_BYTES:
                raise InputLimitError("Report exceeds the portable output limit.")
            target.write(encoded)


def write_evidence(sources: dict[str, bytes], commit_sha: str, output: Path) -> None:
    """Write a private manifest plus exact bytes for the parent service only."""
    if len(sources) > MAX_EVIDENCE_FILES:
        raise InputLimitError("Evidence snapshot exceeds hosted limits.")
    output.mkdir(mode=0o700)
    manifest = {"commit_sha": commit_sha, "files": []}
    for index, (path, raw) in enumerate(sorted(sources.items())):
        if not isinstance(path, str) or not isinstance(raw, bytes):
            raise InputLimitError("Evidence snapshot is invalid.")
        name = f"{index:04d}.source"
        target = output / name
        with target.open("xb") as stream:
            stream.write(raw)
        os.chmod(target, 0o600)
        manifest["files"].append({"path": path, "name": name, "size": len(raw)})
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


def main() -> int:
    try:
        set_resource_limits()
        payload = sys.stdin.buffer.read(2049)
        if len(payload) > 2048:
            return 2
        request = json.loads(payload)
        if not isinstance(request, dict) or not isinstance(request.get("repositoryUrl"), str):
            return 2
        graph, sources, commit_sha = analyze_public_repository_with_sources(request["repositoryUrl"])
        write_result(graph, Path.cwd() / "result.json")
        write_evidence(sources, commit_sha, Path.cwd() / "evidence")
        return 0
    except InputLimitError:
        return 3
    except Exception:
        # Only fixed exit categories cross the boundary; no Git stderr, source,
        # user URL, stack trace or environment values appear in logs/responses.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
