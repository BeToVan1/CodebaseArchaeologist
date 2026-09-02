"""Offline tests for the static-analysis worker and public clone boundary."""
import io
import json
import os
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import analyzer
import deep_analysis_worker as worker
import repository_loader as loader


@pytest.mark.parametrize("url", ["https://github.com/a/.", "https://github.com/a/..", "https://github.com/a/b/extra", "https://github.com@evil.test/a/b"])
def test_reject_unsafe_repository_urls(url):
    with pytest.raises(loader.RepositoryLoadError):
        loader.validate_github_url(url)


def test_public_git_environment_drops_secrets_and_overrides():
    with patch.dict(os.environ, {"OPENAI_API_KEY": "secret", "ARCHAEOLOGIST_SERVICE_TOKEN": "secret",
                                "GIT_CONFIG_COUNT": "1", "GIT_ASKPASS": "bad", "PYTHONPATH": "bad",
                                "HTTPS_PROXY": "bad", "GITHUB_TOKEN": "secret", "PATH": "trusted"}, clear=True):
        env = loader.public_git_environment()
    assert env["PATH"] == "trusted"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert not any(value in {"secret", "bad"} for value in env.values())


def test_missing_git_removes_its_temporary_checkout(tmp_path):
    target = tmp_path / "checkout"
    target.mkdir()
    with patch.object(loader.tempfile, "mkdtemp", return_value=str(target)), patch.object(loader.subprocess, "run", side_effect=FileNotFoundError):
        with pytest.raises(loader.RepositoryLoadError, match="could not be started"):
            loader.load_repository("https://github.com/example/project")
    assert not target.exists()


def test_scanner_ignores_vendor_trees_and_symlinks(tmp_path):
    (tmp_path / "valid.py").write_text("pass")
    ignored = tmp_path / ".git"
    ignored.mkdir()
    (ignored / "hidden.py").write_text("raise RuntimeError()")
    outside = tmp_path.parent / "outside.py"
    outside.write_text("SECRET = 'must not appear'")
    try:
        (tmp_path / "linked.py").symlink_to(outside)
        (tmp_path / "linked-dir").symlink_to(ignored, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires OS support/permission; run in Linux container.")
    assert analyzer.find_python_files(tmp_path) == [tmp_path / "valid.py"]


def test_analysis_never_executes_repository_code(tmp_path):
    marker = tmp_path / "executed"
    (tmp_path / "danger.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    graph = worker.analyze_checkout(tmp_path)
    assert graph["analysis"]["tier"] == "deep"
    assert "repo_root" not in graph
    assert not marker.exists()


def test_framework_fixture_produces_deep_graph():
    graph = worker.analyze_checkout(Path(__file__).parent / "tests/fixtures/portable-report")
    assert graph["flows"]
    assert any(node.get("sqlalchemy", {}).get("kind") == "model" for node in graph["nodes"])


@pytest.mark.parametrize("limit", ["MAX_FILE_BYTES", "MAX_INPUT_BYTES", "MAX_INPUT_FILES"])
def test_input_limits_fail_before_analysis(tmp_path, limit):
    (tmp_path / "a.py").write_text("pass\n")
    with patch.object(worker, limit, 0), patch.object(analyzer, "analyze_repository") as analyze:
        with pytest.raises(worker.InputLimitError):
            worker.analyze_checkout(tmp_path)
        analyze.assert_not_called()


def test_output_limit_counts_utf8_bytes(tmp_path):
    with patch.object(worker, "MAX_OUTPUT_BYTES", 20):
        with pytest.raises(worker.InputLimitError):
            worker.write_result({"source": "é" * 20}, tmp_path / "result.json")
    assert (tmp_path / "result.json").stat().st_size <= 20


def test_snapshot_is_pinned_and_checkout_cleaned_on_error(tmp_path):
    with patch.object(loader, "load_repository", return_value=tmp_path), patch.object(loader, "resolve_commit_sha", return_value="a" * 40), patch.object(loader, "cleanup_repository") as cleanup:
        with patch.object(worker, "analyze_checkout", return_value={"schema_version": "1.1"}):
            graph = worker.analyze_public_repository("https://github.com/example/project.git")
        assert graph["repository"]["pinned_url"].endswith("/tree/" + "a" * 40)
        cleanup.assert_called_once_with(tmp_path)
        cleanup.reset_mock()
        with patch.object(worker, "analyze_checkout", side_effect=worker.InputLimitError):
            with pytest.raises(worker.InputLimitError):
                worker.analyze_public_repository("https://github.com/example/project")
        cleanup.assert_called_once_with(tmp_path)


def test_worker_main_reports_fixed_error_codes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(worker, "set_resource_limits", lambda: None)
    for failure, expected in [(worker.InputLimitError("secret"), 3), (RuntimeError("secret"), 2)]:
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"repositoryUrl":"https://github.com/a/b"}')))
        with patch.object(worker, "analyze_public_repository", side_effect=failure):
            assert worker.main() == expected


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="non-Linux guard")
def test_worker_fails_closed_without_linux_limits():
    with pytest.raises(RuntimeError, match="requires Linux"):
        worker.set_resource_limits()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux limits; run Docker validation")
def test_actual_worker_resource_limits_in_separate_process():
    code = (
        f"import sys; sys.path.insert(0, {str(Path(__file__).parent)!r}); "
        "import deep_analysis_worker as w, resource, json; w.set_resource_limits(); "
        "print(json.dumps([resource.getrlimit(k) for k in [resource.RLIMIT_AS, resource.RLIMIT_CPU, resource.RLIMIT_FSIZE, resource.RLIMIT_NOFILE, resource.RLIMIT_CORE]]))"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    limits = json.loads(result.stdout)
    assert limits == [[768 * 1024 * 1024] * 2, [40, 40], [64 * 1024 * 1024] * 2, [128, 128], [0, 0]]
