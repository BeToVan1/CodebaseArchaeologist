"""Small adversarial acceptance cases, not a real-repository accuracy score.

Fixture code is parsed only. Expectations come from each deliberately constructed
source case, not from a saved copy of the analyzer's output.
"""

from pathlib import Path

import pytest

import analyzer


def analyze_sources(root: Path, sources: dict[str, str]) -> dict:
    for name, source in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return analyzer.analyze_repository(root)


@pytest.mark.parametrize("path", ["test_api.py", "api_test.py", "tests.py", "conftest.py", "pkg/tests/helpers.py", "test/helpers.py", "src/pkg/tests/test_service.py"])
def test_common_test_paths_have_heuristic_role(path):
    assert analyzer.is_test_path(path)
    assert analyzer.evidence_layer(path)[0] == "test"


@pytest.mark.parametrize("path", ["latest.py", "contest.py", "testing.py", "testimony.py", "tests_support/helpers.py", "pkg/test_utils/service.py", "spec/service.py", "test_notes.md"])
def test_similar_names_and_custom_layouts_are_not_guessed_as_tests(path):
    assert not analyzer.is_test_path(path)
    assert analyzer.evidence_layer(path)[0] != "test"


def test_repeated_calls_do_not_create_many_caller_or_collaborator_hotspots(tmp_path):
    graph = analyze_sources(tmp_path, {"app.py": "def helper():\n    pass\n\ndef caller():\n" + "    helper()\n" * 10})
    assert len([edge for edge in graph["edges"] if edge["kind"] == "calls"]) == 10
    assert not any(finding["rule_id"] in {"high-fan-in", "high-fan-out"} for finding in graph["findings"])


@pytest.mark.parametrize("test_path", ["tests/test_app.py", "test_app.py", "package/tests/test_app.py", "app_test.py", "package/tests.py", "test/helpers.py", "conftest.py"])
def test_test_callers_do_not_create_production_hotspot(tmp_path, test_path):
    graph = analyze_sources(tmp_path, {
        "app.py": "def helper():\n    pass\n",
        test_path: "from app import helper\n" + "\n".join(
            f"def test_case_{i}():\n    helper()\n" for i in range(10)),
    })
    assert len([edge for edge in graph["edges"] if edge["kind"] == "calls"]) == 10
    assert not any(finding["rule_id"] == "high-fan-in" for finding in graph["findings"])


def test_distinct_production_callers_still_create_hotspot(tmp_path):
    graph = analyze_sources(tmp_path, {"app.py": "def helper():\n    pass\n" + "\n".join(
        f"def caller_{i}():\n    helper()\n    helper()\n" for i in range(8))})
    finding = next(item for item in graph["findings"] if item["rule_id"] == "high-fan-in")
    assert finding["metrics"]["fan_in"] == 8
    assert "distinct" in finding["summary"]
    assert len(finding["related_node_ids"]) == 8


def test_distinct_production_collaborators_still_create_hotspot(tmp_path):
    graph = analyze_sources(tmp_path, {"app.py": "\n".join(
        f"def helper_{i}():\n    pass\n" for i in range(8))
        + "\ndef caller():\n" + "".join(f"    helper_{i}()\n    helper_{i}()\n" for i in range(8))})
    finding = next(item for item in graph["findings"] if item["rule_id"] == "high-fan-out")
    assert finding["metrics"]["fan_out"] == 8
    assert len(finding["related_node_ids"]) == 8


def test_mixed_test_and_production_callers_only_score_distinct_production(tmp_path):
    graph = analyze_sources(tmp_path, {
        "app.py": "def helper():\n    pass\n" + "\n".join(
            f"def caller_{i}():\n    helper()\n" for i in range(8)),
        "tests/test_app.py": "from app import helper\n" + "\n".join(
            f"def test_case_{i}():\n    helper()\n" for i in range(10)),
    })
    finding = next(item for item in graph["findings"] if item["rule_id"] == "high-fan-in")
    assert finding["metrics"]["fan_in"] == 8
    assert finding["severity"] == "medium"
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert all(nodes[node_id]["path"] == "app.py" for node_id in finding["related_node_ids"])
    assert len([edge for edge in graph["edges"] if edge["kind"] == "calls"]) == 18


def test_test_symbol_hotspots_remain_visible(tmp_path):
    graph = analyze_sources(tmp_path, {"tests/test_app.py": "def helper():\n    pass\n" + "\n".join(
        f"def test_case_{i}():\n    helper()\n" for i in range(8))})
    finding = next(item for item in graph["findings"] if item["rule_id"] == "high-fan-in")
    assert finding["metrics"]["fan_in"] == 8
    assert "distinct repository caller symbols" in finding["summary"]


def test_parameterized_base_retains_candidate_edge_and_original_evidence(tmp_path):
    graph = analyze_sources(tmp_path, {
        "base.py": "class Base:\n    pass\nclass Item:\n    pass\n",
        "child.py": "from base import Base as Parent, Item\nclass Child(Parent[Item]):\n    pass\n",
    })
    nodes = {node["id"]: node for node in graph["nodes"]}
    edges = [edge for edge in graph["edges"] if edge["kind"] == "extends"]
    assert len(edges) == 1
    edge = edges[0]
    assert nodes[edge["source"]]["qualified_name"] == "child.Child"
    assert nodes[edge["target"]]["qualified_name"] == "base.Base"
    assert edge["confidence"] <= 0.85
    assert edge["resolution_method"] == "ast-parameterized-base-candidate"
    assert edge["evidence"]["expression"] == "Parent[Item]"
    assert edge["evidence"]["line"] == 2
    claim = next(claim for claim in nodes[edge["source"]]["evidence_packet"]["claims"]
                 if claim["evidence_refs"] == [edge["id"]])
    assert claim["classification"] == "heuristic"
    assert claim["text"] == "Candidate relationship: extends base.Base."
    assert claim["confidence"] == edge["confidence"]


def test_direct_inheritance_claim_remains_fact(tmp_path):
    graph = analyze_sources(tmp_path, {"base.py": "class Base:\n    pass\nclass Child(Base):\n    pass\n"})
    child = next(node for node in graph["nodes"] if node.get("qualified_name") == "base.Child")
    claim = next(claim for claim in child["evidence_packet"]["claims"] if ":edge:" in claim["id"])
    assert claim["classification"] == "fact"
    assert claim["text"] == "Extends base.Base."


def test_unresolved_parameterized_base_does_not_guess_same_named_local_class(tmp_path):
    graph = analyze_sources(tmp_path, {
        "base.py": "class Base:\n    pass\n",
        "child.py": "from external import Base\nclass Child(Base[int]):\n    pass\n",
    })
    assert not any(edge["kind"] == "extends" for edge in graph["edges"])


def test_factory_base_is_not_inferred_as_inheritance(tmp_path):
    graph = analyze_sources(tmp_path, {
        "base.py": "class Base:\n    pass\nclass Child(Base()):\n    pass\n",
    })
    assert not any(edge["kind"] == "extends" for edge in graph["edges"])


@pytest.mark.parametrize("filename,pattern", [
    ("repository.py", "repository-boundary"),
    ("unit_of_work.py", "unit-of-work"),
])
def test_names_without_relationships_do_not_establish_boundaries(tmp_path, filename, pattern):
    graph = analyze_sources(tmp_path, {filename: "def first():\n    pass\n\ndef second():\n    pass\n"})
    assert not any(item["pattern_id"] == pattern for item in graph["patterns"])


@pytest.mark.parametrize("test_path", ["tests/test_routes.py", "test_routes.py", "package/tests/test_routes.py", "routes_test.py", "package/tests.py", "conftest.py"])
def test_test_only_dependency_injection_is_not_production_architecture(tmp_path, test_path):
    graph = analyze_sources(tmp_path, {test_path: """from fastapi import FastAPI, Depends
app = FastAPI()
def dependency():
    return 1
@app.get('/test')
def route(value=Depends(dependency)):
    return value
"""})
    assert any(edge["kind"] == "depends-on" for edge in graph["edges"])
    assert graph["patterns"] == []


@pytest.mark.parametrize("include_tests", [False, True])
def test_production_dependency_injection_keeps_exact_evidence(tmp_path, include_tests):
    sources = {"api.py": """from fastapi import FastAPI, Depends
app = FastAPI()
def dependency():
    return 1
@app.get('/items')
def route(value=Depends(dependency)):
    return value
"""}
    if include_tests:
        sources["tests/test_routes.py"] = """from fastapi import FastAPI, Depends
from api import dependency
test_app = FastAPI()
@test_app.get('/test')
def test_route(value=Depends(dependency)):
    return value
"""
    graph = analyze_sources(tmp_path, sources)
    pattern = next(item for item in graph["patterns"] if item["pattern_id"] == "dependency-injection")
    assert pattern["classification"] == "fact"
    edges = {edge["id"]: edge for edge in graph["edges"]}
    assert len(pattern["edge_ids"]) == 1
    edge = edges[pattern["edge_ids"][0]]
    assert edge["evidence"]["path"] == "api.py"
    assert edge["evidence"]["line"] == 6


def test_framework_lookalikes_do_not_create_proven_routes_or_models(tmp_path):
    graph = analyze_sources(tmp_path, {"lookalikes.py": """class FastAPI:
    def get(self, path):
        return lambda handler: handler
class DeclarativeBase:
    pass
app = FastAPI()
@app.get('/fake')
def route():
    return 1
class ItemModel(DeclarativeBase):
    __tablename__ = 'items'
"""})
    assert not any(node.get("entrypoint") for node in graph["nodes"])
    assert not any(node.get("sqlalchemy") for node in graph["nodes"])
    assert graph["flows"] == []
    assert not any(pattern["classification"] == "fact" for pattern in graph["patterns"])


@pytest.mark.parametrize("cyclic", [False, True])
def test_cycle_finding_distinguishes_closed_cycle_from_import_chain(tmp_path, cyclic):
    graph = analyze_sources(tmp_path, {
        "a.py": "import b\n",
        "b.py": "import c\n",
        "c.py": "import a\n" if cyclic else "VALUE = 1\n",
    })
    cycles = [finding for finding in graph["findings"] if finding["rule_id"] == "import-cycle"]
    assert len(cycles) == int(cyclic)
    if cyclic:
        finding = cycles[0]
        assert finding["classification"] == "heuristic"
        assert {finding["node_id"], *finding["related_node_ids"]} == {"file:a.py", "file:b.py", "file:c.py"}
        assert finding["metrics"]["component_size"] == 3


@pytest.mark.parametrize("span_delta", [-1, 0])
def test_large_callable_threshold_is_exact(tmp_path, span_delta):
    span = analyzer.LARGE_SYMBOL_LINES + span_delta
    graph = analyze_sources(tmp_path, {"work.py": "def work():\n" + "    value = 1\n" * (span - 1)})
    findings = [finding for finding in graph["findings"] if finding["rule_id"] == "large-symbol"]
    assert len(findings) == int(span_delta == 0)
    if findings:
        assert findings[0]["classification"] == "heuristic"
        assert findings[0]["metrics"]["line_span"] == span
        assert findings[0]["evidence"]["path"] == "work.py"
        assert findings[0]["evidence"]["end_line"] == span


@pytest.mark.parametrize("path,confidence", [
    ("src/package/exc.py", 0.0),
    ("src/package/domain/model.py", 0.6),
    ("src/package/adapters/store.py", 0.6),
    ("tests/test_helpers.py", 0.6),
])
def test_symbol_path_roles_are_qualified_and_unknown_intent_has_no_confidence(tmp_path, path, confidence):
    graph = analyze_sources(tmp_path, {path: "class Example:\n    pass\n"})
    symbol = next(node for node in graph["nodes"] if node["kind"] == "class")
    packet = symbol["evidence_packet"]
    role = packet["execution_role"]
    assert role["classification"] == "heuristic"
    assert role["confidence"] == confidence
    assert ("path suggests" if confidence else "not established") in role["text"]
    role_claim = next(claim for claim in packet["claims"] if claim["id"].endswith(":role"))
    for key in ("text", "confidence", "classification", "provenance"):
        assert role_claim[key] == role[key]
    assert role_claim["evidence_refs"] == [symbol["id"]]
    rationale = packet["structural_rationale"]
    assert rationale["classification"] == "interpretation"
    assert rationale["confidence"] == 0
    assert "not established" in rationale["text"]


def test_framework_role_facts_do_not_establish_author_intent(tmp_path):
    fixture = Path(__file__).parent / "tests/fixtures/portable-report"
    graph = analyze_sources(tmp_path, {path.name: path.read_text(encoding="utf-8") for path in fixture.glob("*.py")})
    framework_symbols = [node for node in graph["nodes"]
        if node.get("entrypoint", {}).get("kind") == "route" or node.get("sqlalchemy", {}).get("kind") == "model"]
    assert len(framework_symbols) == 2
    for symbol in framework_symbols:
        packet = symbol["evidence_packet"]
        assert packet["execution_role"]["classification"] == "fact"
        assert packet["execution_role"]["confidence"] == 0.98
        assert packet["structural_rationale"]["confidence"] == 0
        assert "not established" in packet["structural_rationale"]["text"]
