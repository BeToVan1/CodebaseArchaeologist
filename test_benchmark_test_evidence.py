"""Offline mutation checks for the real-repository benchmark assertions."""
import copy

import pytest

from analyzer import analyze_repository
from scripts.benchmark_pinned_repositories import TEST_EVIDENCE, test_evidence_checks as evidence_checks


@pytest.fixture(params=list(TEST_EVIDENCE))
def sample(request, tmp_path):
    name = request.param
    source, test, target, called, unrelated = TEST_EVIDENCE[name]
    module = target.removeprefix("src/").removesuffix(".py").replace("/", ".")
    for path, code in {
        source: f"from {module} import {called}\ndef {test}():\n    return {called}('value')\n",
        target: f"def {called}(value): return value\ndef {unrelated}(): return None\n",
    }.items():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(code, encoding="utf-8")
    return name, tmp_path, analyze_repository(tmp_path)


def test_valid_selected_evidence(sample):
    name, root, graph = sample
    assert all(check["pass"] for check in evidence_checks(name, root, graph))


@pytest.mark.parametrize("fault", ["missing", "truncated", "call_missing", "line", "edge_target",
                                   "import_missing", "import_kind", "invented_call", "fact", "score"])
def test_benchmark_detects_corrupted_evidence(sample, fault):
    name, root, graph = sample
    report = graph["test_proximity"]
    call = next(link for link in report["links"] if link["signal"] == "symbol-call")
    imported = next(link for link in report["links"] if link["signal"] == "module-import")
    edge = next(edge for edge in graph["edges"] if edge["id"] == call["edge_id"])
    if fault == "missing":
        del graph["test_proximity"]
    elif fault == "truncated":
        report["links_truncated"] = True
    elif fault == "call_missing":
        report["links"].remove(call)
    elif fault == "line":
        edge["evidence"]["line"] = 999
    elif fault == "edge_target":
        edge["target"] = "missing"
    elif fault == "import_missing":
        report["links"].remove(imported)
    elif fault == "import_kind":
        next(edge for edge in graph["edges"] if edge["id"] == imported["edge_id"])["kind"] = "calls"
    elif fault == "invented_call":
        invented = copy.deepcopy(call)
        invented["target_node_id"] = next(node["id"] for node in graph["nodes"]
            if node.get("name") == TEST_EVIDENCE[name][4])
        report["links"].append(invented)
    elif fault == "fact":
        call["classification"] = "fact"
    else:
        call["confidence"] = 1
    assert not all(check["pass"] for check in evidence_checks(name, root, graph)), fault
