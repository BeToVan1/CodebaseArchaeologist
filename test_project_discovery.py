import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from analyzer import analyze_repository
from project_discovery import discover_project, MAX_MANIFEST_BYTES


def manifest(root, content):
    path = root / "pyproject.toml"
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return path


def fields(result):
    return {tuple(item["key"]): item["value"] for item in result["declarations"]}


def test_standard_metadata_without_running_backend_or_setup(tmp_path):
    path = manifest(tmp_path, '''[project]
name = "example"
version = "1.2.3"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.116", "sqlalchemy; python_version >= '3.11'"]
[project.optional-dependencies]
test = ["pytest"]
[project.scripts]
example = "example.cli:main"
[project.gui-scripts]
viewer = "example.viewer:main"
[build-system]
requires = ["setuptools"]
build-backend = "malicious_backend"
backend-path = ["../../outside"]
''')
    (tmp_path / "setup.py").write_text("raise RuntimeError('must not execute')", encoding="utf-8")
    result = discover_project(tmp_path)
    assert result["status"] == "parsed"
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    values = fields(result)
    assert values[("project", "requires-python")] == ">=3.11"
    assert values[("project", "scripts", "example")] == "example.cli:main"
    assert values[("project", "optional-dependencies", "test")] == ["pytest"]
    assert values[("build-system", "build-backend")] == "malicious_backend"
    assert not any("backend-path" in key for key in values)
    assert all(d["classification"] == "fact" and d["confidence"] == 1 for d in result["declarations"])
    assert not result["warnings"]


def test_analyzer_report_contains_metadata_without_creating_graph_edges(tmp_path):
    manifest(tmp_path, '[project]\nname="example"\n[project.scripts]\nhello="missing.module:main"')
    (tmp_path / "example.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    graph = analyze_repository(tmp_path)
    assert graph["project_discovery"]["status"] == "parsed"
    assert not graph["flows"]
    assert not any(edge["kind"] != "contains" for edge in graph["edges"])
    json.dumps(graph)


def test_missing_is_not_equivalent_to_no_dependencies(tmp_path):
    result = discover_project(tmp_path)
    assert result["status"] == "missing" and result["sha256"] is None
    assert not result["declarations"]


def test_dynamic_fields_are_unresolved_even_with_static_entries(tmp_path):
    manifest(tmp_path, '[project]\nname="example"\ndynamic=["version", "dependencies"]\nversion="1"\ndependencies=["partial"]')
    result = discover_project(tmp_path)
    assert ("project", "dependencies") not in fields(result)
    assert ("project", "version") not in fields(result)
    assert fields(result)[("project", "dynamic")] == ["version", "dependencies"]
    assert any("Dynamic" in warning for warning in result["warnings"])


@pytest.mark.parametrize("content", [b"\xff", b"[broken", b'name="a"\nname="b"'])
def test_invalid_manifest_yields_safe_diagnostics(tmp_path, content):
    manifest(tmp_path, content)
    result = discover_project(tmp_path)
    assert result["status"] == "invalid" and not result["declarations"]
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.parametrize("content", ['project="bad"', '[project]\ndependencies="bad"',
    '[project]\nscripts=["bad"]', '[project]\ndynamic="bad"\nname="hidden"',
    '[project]\nrequires-python=123', '[project]\nname="' + 'x'*513 + '"'])
def test_bad_field_shapes_do_not_become_facts(tmp_path, content):
    manifest(tmp_path, content)
    result = discover_project(tmp_path)
    assert result["status"] == "parsed" and result["warnings"] and not result["declarations"]


def test_tool_only_metadata_is_not_guessed(tmp_path):
    manifest(tmp_path, '[tool.poetry]\nname="legacy"\n[tool.poetry.dependencies]\npython=">=3.10"')
    result = discover_project(tmp_path)
    assert not result["declarations"] and any("No standard project table" in w for w in result["warnings"])


def test_direct_references_are_not_exposed_or_fetched(tmp_path):
    manifest(tmp_path, '[project]\ndependencies=["thing @ https://user:private-token@example.invalid/file"]')
    with patch("urllib.request.urlopen", side_effect=AssertionError("no network")):
        result = discover_project(tmp_path)
    assert not result["declarations"]
    assert "private-token" not in json.dumps(result)
    assert any("direct references" in w for w in result["warnings"])


def test_oversize_manifest_is_not_parsed(tmp_path):
    manifest(tmp_path, b" " * (MAX_MANIFEST_BYTES + 1))
    result = discover_project(tmp_path)
    assert result["status"] == "skipped" and result["sha256"] is None


def test_directory_and_unreadable_manifest(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.mkdir()
    assert discover_project(tmp_path)["status"] == "skipped"
    with patch.object(Path, "lstat", side_effect=PermissionError("private path")):
        result = discover_project(tmp_path)
    assert result["status"] == "unreadable" and "private path" not in json.dumps(result)


def test_symlink_is_not_followed(tmp_path):
    target = tmp_path / "other.toml"
    target.write_text('[project]\nname="outside"', encoding="utf-8")
    try: (tmp_path / "pyproject.toml").symlink_to(target)
    except OSError: pytest.skip("Symlink creation is not permitted on this host")
    assert discover_project(tmp_path)["status"] == "skipped"


def test_collection_and_output_limits_are_explicit(tmp_path):
    manifest(tmp_path, '[project]\ndependencies=[' + ','.join('"d"' for _ in range(129)) + ']')
    result = discover_project(tmp_path)
    assert not result["declarations"] and result["warnings"]
    manifest(tmp_path, '[project]\nname="example"\n[project.scripts]\n' + '\n'.join(f'c{i}="a:b"' for i in range(128)))
    result = discover_project(tmp_path)
    assert len(result["declarations"]) == 128
    assert any("128-record" in w for w in result["warnings"])


def test_metadata_output_byte_budget(tmp_path):
    manifest(tmp_path, '[project.optional-dependencies]\n' + '\n'.join(
        f'group{i}=[' + ','.join('"' + 'x'*500 + '"' for _ in range(100)) + ']' for i in range(4)))
    result = discover_project(tmp_path)
    assert result["status"] == "parsed"
    assert len(json.dumps(result).encode()) < 128 * 1024
    assert any("64 KiB" in w for w in result["warnings"])
