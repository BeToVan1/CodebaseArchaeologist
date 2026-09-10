"""PDF sections 17-18: bounded static paths must display unexpanded steps.

Synthetic source is parsed, never executed; these are not real-repository or
runtime-flow acceptance results.
"""
import pytest

from test_architecture_acceptance import analyze_sources


@pytest.mark.parametrize("body, expected", [
    ("    return route()\n", ["api.route"]),
    ("    return helper()\n\ndef helper():\n    return route()\n",
     ["api.route", "api.helper"]),
    ("    route()\n    return helper()\n\ndef helper():\n    return 1\n",
     ["api.route", "api.helper"]),
])
def test_recursive_flow_has_source_backed_gap(tmp_path, body, expected):
    source = "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/items')\ndef route():\n" + body
    graph = analyze_sources(tmp_path, {"api.py": source})
    nodes = {node["id"]: node for node in graph["nodes"]}
    [flow] = graph["flows"]
    assert [nodes[key]["qualified_name"] for key in flow["ordered_node_ids"]] == expected
    assert flow["completeness"] == "partial"
    [gap] = [step for step in flow["unresolved_steps"]
             if step["reason"] == "recursive-flow-not-expanded"]
    assert gap["evidence"]["path"] == "api.py"
    assert "route()" in source.splitlines()[gap["evidence"]["line"] - 1]
    assert gap["source_id"] in flow["ordered_node_ids"]
    assert any(edge["source"] == gap["source_id"] and edge["evidence"] == gap["evidence"]
               for edge in graph["edges"] if edge["kind"] == "calls")


def test_acyclic_flow_does_not_acquire_recursion_gap(tmp_path):
    graph = analyze_sources(tmp_path, {"api.py": """from fastapi import FastAPI
app = FastAPI()
@app.get('/items')
def route():
    return helper()
def helper():
    return 1
"""})
    [flow] = graph["flows"]
    assert flow["completeness"] == "complete"
    assert flow["unresolved_steps"] == []


def test_more_than_three_entrypoints_remain_accessible(tmp_path):
    source = "from fastapi import FastAPI\napp = FastAPI()\n"
    for index in range(8):
        source += f"@app.get('/{index}')\ndef route_{index}():\n    return {index}\n"
    graph = analyze_sources(tmp_path, {"api.py": source})
    assert len({flow["entrypoint_id"] for flow in graph["flows"]}) == 8
    assert len({flow["id"] for flow in graph["flows"]}) == len(graph["flows"])


def test_busy_first_branch_does_not_hide_later_branch_or_entrypoint(tmp_path):
    source = "from fastapi import FastAPI\napp = FastAPI()\n"
    source += "@app.get('/busy')\ndef busy():\n    first()\n    last()\n"
    source += "def first():\n" + "".join(f"    leaf_{i}()\n" for i in range(30))
    source += "def last():\n    return 1\n"
    source += "".join(f"def leaf_{i}():\n    return {i}\n" for i in range(30))
    source += "@app.get('/later')\ndef later():\n    return last()\n"
    graph = analyze_sources(tmp_path, {"api.py": source})
    nodes = {node["id"]: node for node in graph["nodes"]}
    busy = [flow for flow in graph["flows"] if flow["label"] == "GET /busy"]
    assert len(busy) <= 12
    assert any(nodes[flow["ordered_node_ids"][1]]["name"] == "last" for flow in busy)
    assert all(flow["completeness"] == "partial" and any(
        gap["reason"] == "flow-path-budget-reached" for gap in flow["unresolved_steps"]
    ) for flow in busy)
    assert any(flow["label"] == "GET /later" for flow in graph["flows"])


def test_global_budget_is_bounded_and_deterministic():
    import analyzer
    symbols = [{"id": f"route:{i}", "path": "api.py", "definition_line": i + 1,
                "qualified_name": f"api.route_{i}", "entrypoint": {"label": str(i), "framework": "fastapi"}}
               for i in range(analyzer.MAX_FLOW_CANDIDATES + 5)]
    first = analyzer.discover_representative_flows(symbols, [], [])
    second = analyzer.discover_representative_flows(list(reversed(symbols)), [], [])
    assert len(first) == analyzer.MAX_FLOW_CANDIDATES
    assert len({flow["entrypoint_id"] for flow in first}) == analyzer.MAX_FLOW_CANDIDATES
    assert first == second


def test_nested_dependency_factories_preserve_wiring_without_becoming_routes(tmp_path):
    graph = analyze_sources(tmp_path, {"api.py": """from fastapi import FastAPI, Depends
from typing import Annotated
app = FastAPI()
def session():
    return None
async def repository(value: Annotated[object, Depends(session)]):
    return value
def service(value=Depends(repository)):
    return value
@app.get('/items')
def route(value=Depends(service)):
    return value
"""})
    nodes = {node["id"]: node for node in graph["nodes"]}
    dependencies = [edge for edge in graph["edges"] if edge["kind"] == "depends-on"]
    assert {(nodes[edge["source"]]["name"], nodes[edge["target"]]["name"], edge["evidence"]["line"])
            for edge in dependencies} == {("repository", "session", 6), ("service", "repository", 8), ("route", "service", 11)}
    assert graph["coverage"]["fastapi_routes"] == 1
    [flow] = graph["flows"]
    assert [nodes[key]["name"] for key in flow["ordered_node_ids"]] == ["route", "service", "repository", "session"]
    assert all(edge["kind"] == "depends-on" for edge in dependencies)


def test_dependency_lookalike_does_not_create_wiring(tmp_path):
    graph = analyze_sources(tmp_path, {"api.py": """def Depends(value):
    return value
def session():
    return None
def repository(value=Depends(session)):
    return value
"""})
    assert not any(edge["kind"] == "depends-on" for edge in graph["edges"])


def test_unresolved_nested_dependency_keeps_exact_gap(tmp_path):
    graph = analyze_sources(tmp_path, {"api.py": """from fastapi import FastAPI, Depends
app = FastAPI()
def provider(value=Depends(missing)):
    return value
@app.get('/items')
def route(value=Depends(provider)):
    return value
"""})
    [flow] = graph["flows"]
    assert flow["completeness"] == "partial"
    assert any(gap["reason"] == "unresolved-fastapi-dependency" and gap["evidence"]["line"] == 3
               and gap["evidence"]["expression"] == "Depends(missing)" for gap in flow["unresolved_steps"])
