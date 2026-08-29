"""Tests for the source-reading and file-count enforcement added to analyzer.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import analyzer


# --------------------------------------------------------------------------
# read_source
# --------------------------------------------------------------------------

def test_read_source_returns_full_text_for_small_file(tmp_path: Path) -> None:
    file_path = tmp_path / "small.py"
    file_path.write_bytes(b"class Batch:\n    pass\n")

    source, truncated, error = analyzer.read_source(file_path)

    assert error is None
    assert truncated is False
    assert source == "class Batch:\n    pass\n"


def test_read_source_truncates_large_file_and_sets_flag(tmp_path: Path) -> None:
    file_path = tmp_path / "big.py"
    # One byte over the limit so truncation is guaranteed.
    file_path.write_text("x" * (analyzer.MAX_SOURCE_BYTES + 1), encoding="utf-8")

    source, truncated, error = analyzer.read_source(file_path)

    assert error is None
    assert truncated is True
    assert source is not None
    assert len(source.encode("utf-8")) == analyzer.MAX_SOURCE_BYTES


def test_read_source_truncation_does_not_break_multibyte_characters(tmp_path: Path) -> None:
    file_path = tmp_path / "unicode_heavy.py"
    # Multi-byte characters (e.g. "é" = 2 bytes) so a naive byte-slice could
    # cut through the middle of one right at the boundary.
    file_path.write_text("é" * (analyzer.MAX_SOURCE_BYTES), encoding="utf-8")

    source, truncated, error = analyzer.read_source(file_path)

    assert error is None
    assert truncated is True
    # Should decode cleanly with no replacement/garbage characters.
    assert source is not None
    assert all(ch == "é" for ch in source)


def test_read_source_handles_undecodable_file_without_crashing(tmp_path: Path) -> None:
    file_path = tmp_path / "binary_like.py"
    file_path.write_bytes(b"\xff\xfe\x00\x01invalid utf-8 \xc0\xc1")

    source, truncated, error = analyzer.read_source(file_path)

    assert source is None
    assert truncated is False
    assert error is not None
    assert "utf-8" in error.lower()


def test_read_source_handles_unreadable_file_without_crashing(tmp_path: Path) -> None:
    missing_path = tmp_path / "does_not_exist.py"

    source, truncated, error = analyzer.read_source(missing_path)

    assert source is None
    assert truncated is False
    assert error is not None


# --------------------------------------------------------------------------
# build_file_nodes
# --------------------------------------------------------------------------

def test_build_file_nodes_includes_source_fields(tmp_path: Path) -> None:
    file_path = tmp_path / "model.py"
    file_path.write_bytes(b"class Batch:\n    ...\n")

    nodes = analyzer.build_file_nodes(tmp_path, [file_path])

    assert len(nodes) == 1
    node = nodes[0]
    assert node["id"] == "file:model.py"
    assert node["kind"] == "file"
    assert node["path"] == "model.py"
    assert node["source"] == "class Batch:\n    ...\n"
    assert node["source_truncated"] is False


def test_build_file_nodes_omits_source_and_records_error_for_undecodable_file(tmp_path: Path) -> None:
    file_path = tmp_path / "bad.py"
    file_path.write_bytes(b"\xff\xfe garbage \xc0\xc1")

    nodes = analyzer.build_file_nodes(tmp_path, [file_path])

    assert len(nodes) == 1
    node = nodes[0]
    assert "source" not in node
    assert "source_truncated" not in node
    assert "source_error" in node


# --------------------------------------------------------------------------
# File-count enforcement
# --------------------------------------------------------------------------

def _make_python_files(root: Path, count: int) -> None:
    for i in range(count):
        (root / f"module_{i}.py").write_text(f"x = {i}\n", encoding="utf-8")


def test_analyze_repository_enforces_file_count_limit(tmp_path: Path) -> None:
    _make_python_files(tmp_path, 5)

    with patch.object(analyzer, "MAX_PYTHON_FILES", 3):
        graph = analyzer.analyze_repository(tmp_path)

    assert graph["python_files_total_found"] == 5
    assert graph["python_files_analyzed"] == 3
    assert graph["python_files_truncated"] is True
    assert len(graph["nodes"]) == 3


def test_analyze_repository_does_not_truncate_when_under_limit(tmp_path: Path) -> None:
    _make_python_files(tmp_path, 3)

    with patch.object(analyzer, "MAX_PYTHON_FILES", 2000):
        graph = analyzer.analyze_repository(tmp_path)

    assert graph["python_files_total_found"] == 3
    assert graph["python_files_analyzed"] == 3
    assert graph["python_files_truncated"] is False
    assert len(graph["nodes"]) == 3


def test_analyze_repository_file_selection_is_deterministic(tmp_path: Path) -> None:
    """When truncating, the same (sorted) subset of files should be picked every run."""
    _make_python_files(tmp_path, 5)

    with patch.object(analyzer, "MAX_PYTHON_FILES", 3):
        graph_a = analyzer.analyze_repository(tmp_path)
        graph_b = analyzer.analyze_repository(tmp_path)

    paths_a = sorted(node["path"] for node in graph_a["nodes"])
    paths_b = sorted(node["path"] for node in graph_b["nodes"])
    assert paths_a == paths_b
    assert paths_a == ["module_0.py", "module_1.py", "module_2.py"]

# --------------------------------------------------------------------------
# Full graph contract and error tolerance
# --------------------------------------------------------------------------

def test_analyze_repository_uses_frontend_edge_contract(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("import target\n", encoding="utf-8")
    (tmp_path / "target.py").write_text("VALUE = 1\n", encoding="utf-8")

    graph = analyzer.analyze_repository(tmp_path)

    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert edge["id"] == "import:file:source.py->file:target.py"
    assert edge["source"] == "file:source.py"
    assert edge["target"] == "file:target.py"
    assert "source_id" not in edge
    assert "target_id" not in edge


def test_analyze_repository_skips_imports_for_undecodable_file(tmp_path: Path) -> None:
    file_path = tmp_path / "binary_like.py"
    file_path.write_bytes(b"\xff\xfe\x00\x01invalid utf-8 \xc0\xc1")

    graph = analyzer.analyze_repository(tmp_path)

    assert len(graph["nodes"]) == 1
    assert "source_error" in graph["nodes"][0]
    assert graph["edges"] == []
