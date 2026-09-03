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


@pytest.mark.parametrize("filename,pattern", [
    ("repository.py", "repository-boundary"),
    ("unit_of_work.py", "unit-of-work"),
])
def test_names_without_relationships_do_not_establish_boundaries(tmp_path, filename, pattern):
    graph = analyze_sources(tmp_path, {filename: "def first():\n    pass\n\ndef second():\n    pass\n"})
    assert not any(item["pattern_id"] == pattern for item in graph["patterns"])


def test_test_only_dependency_injection_is_not_production_architecture(tmp_path):
    graph = analyze_sources(tmp_path, {"tests/test_routes.py": """from fastapi import FastAPI, Depends
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
