from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

import pytest

import snapshot_capture
from evidence_store import EvidenceSnapshotStore
from repository_loader import public_git_environment
from snapshot_capture import SnapshotCaptureError, analyze_and_store_snapshot, analyze_verified_snapshot


@pytest.fixture
def checkout(tmp_path):
    if not shutil.which("git"):
        pytest.skip("Git required for offline capture tests")
    root = tmp_path / "checkout"
    root.mkdir()
    def git(*args):
        return subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}",
            "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", "-C", str(root), *args],
            env=public_git_environment(), check=True, capture_output=True, timeout=10)
    git("init", "--template=")
    (root / "example.py").write_bytes(b"raise RuntimeError('must not execute')\n\ndef run():\n    return 1\n")
    git("add", "example.py")
    git("commit", "-m", "Offline fixture")
    return root


def test_root_manifest_is_commit_verified_but_not_a_symbol_source(checkout):
    manifest = checkout / "pyproject.toml"
    manifest.write_bytes(b'[project]\nname="committed-project"\n')
    base = ["git", "-c", f"safe.directory={checkout.as_posix()}", "-c", "core.autocrlf=false",
            "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "-C", str(checkout)]
    for args in (["add", "pyproject.toml"], ["commit", "-m", "Add manifest fixture"]):
        subprocess.run([*base, *args], env=public_git_environment(), capture_output=True, check=True, timeout=10)
    graph, sources, _ = analyze_verified_snapshot(checkout)
    assert graph["project_discovery"]["declarations"][0]["value"] == "committed-project"
    assert "pyproject.toml" not in sources
    # The detached metadata does not require retaining non-symbol source files.
    graph, ref = analyze_and_store_snapshot(checkout, EvidenceSnapshotStore(), owner_key="owner")
    assert ref.report_id and graph["project_discovery"]["status"] == "parsed"
    manifest.write_bytes(b'[project]\nname="tampered-project"\n')
    with pytest.raises(SnapshotCaptureError): analyze_verified_snapshot(checkout)


def test_untracked_manifest_is_not_claimed_as_snapshot_metadata(checkout):
    (checkout / "pyproject.toml").write_bytes(b'[project]\nname="untracked-project"\n')
    graph, _, _ = analyze_verified_snapshot(checkout)
    assert graph["project_discovery"]["status"] == "missing"


def test_capture_store_and_resolve_real_git_source_without_execution(checkout):
    store = EvidenceSnapshotStore()
    graph, ref = analyze_and_store_snapshot(checkout, store, owner_key="fixture-owner")
    node = next(n for n in graph["nodes"] if n.get("name") == "run")
    result = store.prepare(owner_key="fixture-owner", report_id=ref.report_id, node_id=node["id"])
    assert result.source_excerpt == "def run():\n    return 1"
    assert result.commit_sha == graph["snapshot"]["commit_sha"]
    assert "repo_root" not in graph


@pytest.mark.parametrize("change", ["modified", "deleted", "newline-filter"])
def test_changed_committed_source_cannot_register(checkout, change):
    path = checkout / "example.py"
    if change == "modified": path.write_bytes(b"def run():\n    return 2\n")
    if change == "deleted": path.unlink()
    if change == "newline-filter": path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    store = EvidenceSnapshotStore()
    with pytest.raises(SnapshotCaptureError):
        analyze_and_store_snapshot(checkout, store, owner_key="fixture-owner")
    assert store.usage()["snapshots"] == 0


def test_untracked_source_is_not_included(checkout):
    (checkout / "untracked.py").write_bytes(b"def extra(): pass\n")
    graph, sources, _ = analyze_verified_snapshot(checkout)
    assert set(sources) == {"example.py"}
    assert not any(n.get("name") == "extra" for n in graph["nodes"])


def test_private_stage_is_removed_and_original_changes_cannot_affect_parser(checkout):
    original = snapshot_capture.analyze_repository
    stages = []
    def analyze(stage):
        stages.append(stage)
        (checkout / "example.py").write_bytes(b"def changed(): pass\n")
        return original(stage)
    with patch.object(snapshot_capture, "analyze_repository", side_effect=analyze):
        graph, sources, _ = analyze_verified_snapshot(checkout)
    assert any(n.get("name") == "run" for n in graph["nodes"])
    assert b"return 1" in sources["example.py"]
    assert all(not stage.exists() for stage in stages)


def test_private_stage_is_removed_on_analysis_failure(checkout):
    stages = []
    def fail(stage):
        stages.append(stage)
        raise ValueError("synthetic parser failure")
    with patch.object(snapshot_capture, "analyze_repository", side_effect=fail):
        with pytest.raises(ValueError, match="synthetic"):
            analyze_verified_snapshot(checkout)
    assert stages and all(not stage.exists() for stage in stages)


@pytest.mark.parametrize("path", ["../outside.py", "/absolute.py", "C:/file.py", "a\\b.py", "a//b.py",
                                 "CON.py", "folder./file.py", "folder /file.py", "a\n.py"])
def test_tree_paths_cannot_escape_staging(path):
    record = b"100644 blob " + b"a" * 40 + b" 1\t" + path.encode() + b"\0"
    with pytest.raises(SnapshotCaptureError): snapshot_capture._python_entries(record)


def test_symlink_and_ignored_tree_entries_are_not_captured():
    records = b"120000 blob " + b"a" * 40 + b" 1\tlink.py\0"
    records += b"100644 blob " + b"a" * 40 + b" 1\tnode_modules/code.py\0"
    assert snapshot_capture._python_entries(records) == []


def test_capture_limits_fail_closed(checkout):
    with patch.object(snapshot_capture, "MAX_CAPTURE_BYTES", 1):
        with pytest.raises(SnapshotCaptureError, match="limits"):
            analyze_verified_snapshot(checkout)


def test_case_colliding_directories_are_rejected_before_staging():
    header = b"100644 blob " + b"a" * 40 + b" 1\t"
    with pytest.raises(SnapshotCaptureError):
        snapshot_capture._python_entries(header + b"A/one.py\0" + header + b"a/two.py\0")
