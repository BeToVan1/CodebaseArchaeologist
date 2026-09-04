from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from interpretation_evidence import EvidencePreparationError, prepare_symbol_evidence
from interpretation import generate_interpretation
from test_interpretation import FakeResponses, generated, packet

PIN = "a" * 40
NODE = "symbol:example.py:run"


def report():
    return {
        "snapshot": {"commit_sha": PIN}, "analysis": {"tier": "deep"},
        "nodes": [{"id": NODE, "kind": "function", "path": "example.py",
                   "start_line": 1, "end_line": 2, "evidence_packet": packet().model_dump()}],
        "edges": [{"id": "edge:1"}], "flows": [{"id": "flow:1"}], "findings": [],
        "patterns": [{"id": "pattern:layered-architecture"}],
    }


def prepare(graph=None, sources=None, node_id=NODE, commit=PIN):
    return prepare_symbol_evidence(
        report() if graph is None else graph,
        {"example.py": b"def run():\n    pass\nSECRET_OUTSIDE_RANGE = 1\n"} if sources is None else sources,
        node_id, commit_sha=commit,
    )


def test_prepares_only_selected_source_and_copies_packet():
    graph = report()
    original = deepcopy(graph)
    prepared = prepare(graph)
    assert prepared.commit_sha == PIN
    assert prepared.source_excerpt == "def run():\n    pass"
    assert graph == original
    graph["nodes"][0]["evidence_packet"]["summary"]["text"] = "tampered"
    prepared.packet.summary.text = "also tampered"
    assert prepared.packet.summary.text == "Defines run."


@pytest.mark.parametrize("commit", ["b" * 40, "main", "a" * 39, "A" * 40])
def test_rejects_mismatched_or_unpinned_commit(commit):
    with pytest.raises(EvidencePreparationError, match="commit"):
        prepare(commit=commit)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "file", "basic", "packet_node", "packet_range"])
def test_rejects_inconsistent_selection(mutation):
    graph = report()
    node = graph["nodes"][0]
    if mutation == "missing": graph["nodes"] = []
    if mutation == "duplicate": graph["nodes"].append(deepcopy(node))
    if mutation == "file": node["kind"] = "file"
    if mutation == "basic": graph["analysis"]["tier"] = "basic"
    if mutation == "packet_node": node["evidence_packet"]["node_id"] = "other"
    if mutation == "packet_range": node["evidence_packet"]["source_range"]["end_line"] = 3
    with pytest.raises(EvidencePreparationError): prepare(graph)


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "C:/secret", "x\\secret", "./example.py", "a//b"])
def test_rejects_noncanonical_paths_without_reading_files(path):
    graph = report()
    node = graph["nodes"][0]
    node["path"] = node["evidence_packet"]["source_range"]["path"] = path
    with pytest.raises(EvidencePreparationError, match="path"):
        prepare(graph, {path: b"def run():\n    pass"})


@pytest.mark.parametrize("group", ["edges", "flows", "patterns"])
def test_rejects_dangling_packet_references(group):
    graph = report()
    graph[group] = []
    with pytest.raises(EvidencePreparationError, match="references"):
        prepare(graph)


def test_rejects_invented_claim_reference():
    graph = report()
    graph["nodes"][0]["evidence_packet"]["claims"][0]["evidence_refs"] = ["invented"]
    with pytest.raises(EvidencePreparationError, match="Claim references"):
        prepare(graph)


@pytest.mark.parametrize("source", [None, b"", b"one line\n", b"\xff", b"x" * (1024 * 1024 + 1), b"x" * 12000 + b"\ny"],
                         ids=["missing", "empty", "short", "invalid-utf8", "file-limit", "excerpt-limit"])
def test_rejects_missing_invalid_short_or_oversized_source(source):
    with pytest.raises(EvidencePreparationError):
        prepare(sources={} if source is None else {"example.py": source})


def test_source_uses_python_physical_lines():
    source = 'def run():\r\n    return "a\u2028b"\r\nnot_selected = 1\r\n'.encode()
    assert prepare(sources={"example.py": source}).source_excerpt == 'def run():\n    return "a\u2028b"'


def test_prepared_evidence_reaches_mock_model_without_client_source():
    prepared = prepare()
    responses = FakeResponses(generated())
    result = generate_interpretation(prepared.packet, prepared.source_excerpt,
                                     client=SimpleNamespace(responses=responses), model="test-model")
    assert result.classification == "interpretation"
    assert "SECRET_OUTSIDE_RANGE" not in responses.arguments["input"][1]["content"]


def test_real_analyzer_fixture_prepares_all_symbol_evidence():
    from analyzer import analyze_repository

    root = Path(__file__).parent / "tests" / "fixtures" / "portable-report"
    graph = analyze_repository(root)
    graph["snapshot"] = {"commit_sha": PIN}  # Synthetic pin for this local fixture.
    sources = {node["path"]: (root / node["path"]).read_bytes()
               for node in graph["nodes"] if node["kind"] == "file"}
    symbols = [node for node in graph["nodes"] if "evidence_packet" in node]
    assert symbols
    for node in symbols:
        prepared = prepare_symbol_evidence(graph, sources, node["id"], commit_sha=PIN)
        assert prepared.packet.node_id == node["id"]
        assert prepared.source_excerpt
