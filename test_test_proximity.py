import json
import copy
import pytest
from unittest.mock import patch

import analyzer
from scripts.container_smoke import check_test_proximity


def analyze(root, sources):
    for name, content in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return analyzer.analyze_repository(root)


def test_call_and_import_have_separate_existing_evidence(tmp_path):
    graph = analyze(tmp_path, {
        "service.py": "def execute(): return 1\ndef untouched(): return 2\n",
        "test_service.py": "from service import execute\ndef test_execute():\n    assert execute() == 1\n",
    })
    report = graph["test_proximity"]
    assert report["test_files_identified"] == 1
    assert not report["links_truncated"]
    assert report["candidate_links"] == 2
    edges = {edge["id"]: edge for edge in graph["edges"]}
    nodes = {node["id"]: node for node in graph["nodes"]}
    for link in report["links"]:
        edge = edges[link["edge_id"]]
        assert edge["source"] == link["source_node_id"]
        assert edge["target"] == link["target_node_id"]
        assert edge["evidence"]["line"] > 0
        assert nodes[edge["source"]]["path"] == "test_service.py"
        assert link["classification"] == "heuristic" and link["confidence"] == 0.6
    calls = [link for link in report["links"] if link["signal"] == "symbol-call"]
    assert len(calls) == 1
    assert nodes[calls[0]["target_node_id"]]["name"] == "execute"
    imports = [link for link in report["links"] if link["signal"] == "module-import"]
    assert nodes[imports[0]["target_node_id"]]["kind"] == "file"
    assert json.loads(json.dumps(report)) == report
    assert check_test_proximity(graph) == "call-and-import-evidence-verified"


@pytest.mark.parametrize("fault", ["absent", "empty", "wrong_target", "wrong_signal", "wrong_score", "duplicate", "bad_line", "wrong_test_count"])
def test_runtime_probe_rejects_old_or_broken_evidence(tmp_path, fault):
    graph = analyze(tmp_path, {"service.py": "def run(): return 1\n",
        "test_service.py": "from service import run\ndef test_run(): return run()\n"})
    report = graph["test_proximity"]
    if fault == "absent":
        del graph["test_proximity"]
    elif fault == "empty":
        report["links"] = []
    elif fault == "wrong_target":
        report["links"][0]["target_node_id"] = "file:service.py"
    elif fault == "wrong_signal":
        report["links"][0]["signal"] = "module-import"
    elif fault == "wrong_score":
        report["links"][0]["confidence"] = 1
    elif fault == "duplicate":
        report["links"].append(copy.deepcopy(report["links"][0]))
        report["candidate_links"] += 1
    elif fault == "bad_line":
        next(edge for edge in graph["edges"] if edge["id"] == report["links"][0]["edge_id"])["evidence"]["line"] = 0
    else:
        report["test_files_identified"] += 1
    with pytest.raises(AssertionError):
        check_test_proximity(graph)


def test_import_only_does_not_claim_symbol_calls_or_execute_code(tmp_path):
    marker = tmp_path / "ran"
    graph = analyze(tmp_path, {"service.py": "def execute(): return 1\n",
        "tests/test_service.py": f"import service\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n"})
    assert not marker.exists()
    assert [link["signal"] for link in graph["test_proximity"]["links"]] == ["module-import"]


def test_no_tests_and_unresolved_tests_are_not_coverage_claims(tmp_path):
    first = analyze(tmp_path, {"service.py": "def execute(): return 1\n"})["test_proximity"]
    assert first["test_files_identified"] == 0 and first["links"] == []
    second = analyze(tmp_path, {"test_service.py": "def test_call(client):\n    client.unknown()\n"})["test_proximity"]
    assert second["test_files_identified"] == 1 and second["links"] == []
    assert any("Absent links do not mean untested" in line for line in second["limitations"])


def test_helpers_do_not_create_transitive_production_links(tmp_path):
    graph = analyze(tmp_path, {
        "service.py": "def helper(): return 1\ndef execute(): return helper()\n",
        "tests/test_service.py": "from service import execute\ndef test_execute(): return execute()\n",
    })
    nodes = {node["id"]: node for node in graph["nodes"]}
    calls = [link for link in graph["test_proximity"]["links"] if link["signal"] == "symbol-call"]
    assert [nodes[link["target_node_id"]]["name"] for link in calls] == ["execute"]


def test_candidate_dispatch_test_targets_and_dangling_edges_are_excluded():
    symbols = [{"id": "test", "path": "pkg/tests/test_a.py"}, {"id": "prod", "path": "service.py"}]
    edges = [dict(id=str(i), source=source, target=target, kind=kind, confidence=confidence) for i, (source, target, kind, confidence) in enumerate([
        ("test", "prod", "calls", 0.55), ("test", "prod", "may-dispatch-to", 1),
        ("prod", "test", "calls", 1), ("test", "missing", "calls", 1), ("test", "test", "calls", 1)])]
    assert analyzer.build_test_proximity([], symbols, [], edges)["links"] == []


def test_limits_are_explicit_and_deterministic():
    symbols = [{"id": "test", "path": "test_a.py"}, {"id": "prod", "path": "service.py"}]
    edges = [dict(id=str(i), source="test", target="prod", kind="calls", confidence=1) for i in range(3)]
    with patch.object(analyzer, "MAX_TEST_PROXIMITY_LINKS", 2):
        report = analyzer.build_test_proximity([], symbols, [], edges)
        assert report == analyzer.build_test_proximity([], symbols, [], list(reversed(edges)))
        assert report["candidate_links"] == 3 and report["links_truncated"]
        assert len(report["links"]) == 2
    with patch.object(analyzer, "MAX_TEST_PROXIMITY_BYTES", 1):
        report = analyzer.build_test_proximity([], symbols, [], edges)
        assert report["links"] == [] and report["links_truncated"]
