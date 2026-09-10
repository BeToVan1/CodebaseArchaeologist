"""PDF 17-18 flow checks against a pinned, source-only public snapshot.

No repository imports, downloads, credentials, model calls, or service requests.
Exit 1 means unmet expectations, not permission to weaken them.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyzer import analyze_repository

MANIFEST = Path(__file__).resolve().parents[1] / "evals/fastapi_flow_snapshot.json"
PREFIX = "app.modules."
# Selected from README endpoint table and source before running the analyzer.
CASES = [
    ("health", PREFIX + "health.presentation.routers.health",
     PREFIX + "health.application.use_cases.HealthUseCases.health", "use_case.health", 45),
    ("migration-version", PREFIX + "health.presentation.routers.alembic_version",
     PREFIX + "health.application.use_cases.HealthUseCases.alembic_version", "use_case.alembic_version", 81),
    ("example", PREFIX + "example.presentation.routers.hello",
     PREFIX + "example.application.use_cases.ExampleUseCases.hello", "use_case.hello", 34),
]


def verify_snapshot(root, manifest):
    """Permit text-transport newline changes only when original blob verifies.

    The connector strips terminal newlines, while apply_patch restores one.
    Try a single terminal newline removal/addition; never normalize other bytes.
    """
    verified = {}
    for entry in manifest["files"]:
        path = root / entry["path"]
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing or non-regular snapshot file: " + entry["path"])
        content = path.read_bytes()
        candidates = [content, content + b"\n"]
        if content.endswith(b"\n"):
            candidates.append(content[:-1])
        original = next((raw for raw in candidates if len(raw) == entry["size"]
                         and hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
                         == entry["gitBlobSha"]), None)
        if original is None:
            raise ValueError("Snapshot hash mismatch: " + entry["path"])
        verified[entry["path"]] = original
    expected = {entry["path"] for entry in manifest["files"] if entry["path"].endswith(".py")}
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*.py")}
    if actual != expected:
        raise ValueError("Python file inventory differs from pinned manifest")
    return verified


def check_flows(graph):
    nodes = {node["id"]: node for node in graph["nodes"]}
    symbols = {node.get("qualified_name"): node for node in graph["nodes"] if node["kind"] != "file"}
    results = []
    for case_id, route_name, target_name, expression, line in CASES:
        route, target = symbols.get(route_name), symbols.get(target_name)
        route_id = route["id"] if route else None
        target_id = target["id"] if target else None
        flows = [flow for flow in graph["flows"] if flow["entrypoint_id"] == route_id]
        expected_edges = [edge for edge in graph["edges"]
                          if route and target and edge["source"] == route_id and edge["target"] == target_id
                          and edge["kind"] in {"calls", "may-dispatch-to"}
                          and edge["evidence"].get("path") == route["path"]
                          and edge["evidence"]["expression"] == expression
                          and edge["evidence"]["line"] == line]
        results.append({
            "caseId": case_id,
            "routeRecognized": bool(route and route.get("entrypoint")),
            "selectedFlowCount": len(flows),
            "sourceCallLinked": bool(expected_edges),
            "linkKinds": sorted({edge["kind"] for edge in expected_edges}),
            "representativeIncludesUseCase": bool(expected_edges) and any(
                flow["ordered_node_ids"][:2] == [route_id, target_id]
                and flow["ordered_edge_ids"] and flow["ordered_edge_ids"][0] in {edge["id"] for edge in expected_edges}
                for flow in flows),
            "unresolvedCallVisible": any(step["evidence"].get("expression") == expression
                                         for flow in flows for step in flow["unresolved_steps"]),
            "paths": [[nodes[key]["qualified_name"] for key in flow["ordered_node_ids"]] for flow in flows],
            "gapReasons": sorted({step["reason"] for flow in flows for step in flow["unresolved_steps"]}),
        })
    return results


def check_persistence_boundary(graph):
    """Reviewed source declarations, distinct from a proven runtime chain."""
    health = PREFIX + "health."
    nodes = {node["id"]: node for node in graph["nodes"]}
    symbols = {node.get("qualified_name"): node for node in graph["nodes"]}
    expected = [
        (health + "presentation.routers.alembic_version", health + "presentation.dependencies.get_health_use_cases", "depends-on", 77),
        (health + "presentation.dependencies.get_health_use_cases", health + "presentation.dependencies.get_health_repository", "depends-on", 19),
        (health + "presentation.dependencies.get_health_repository", "app.core.database.get_async_session", "depends-on", 13),
        (health + "presentation.dependencies.get_health_repository", health + "infrastructure.repositories.PostgresHealthRepository", "calls", 15),
        (health + "infrastructure.repositories.PostgresHealthRepository.get_alembic_version", health + "infrastructure.models.AlembicModel", "reads", 23),
    ]
    checks = {}
    for source, target, kind, line in expected:
        checks[f"{kind}:{source}->{target}"] = any(
            nodes.get(edge["source"], {}).get("qualified_name") == source
            and nodes.get(edge["target"], {}).get("qualified_name") == target
            and edge["kind"] == kind and edge.get("evidence", {}).get("line") == line
            and edge["evidence"].get("path") == symbols.get(source, {}).get("path")
            for edge in graph["edges"])
    chain = [expected[0][0], expected[0][1], expected[1][1], expected[2][1]]
    checks["dependency-chain-visible"] = any(
        [nodes[key].get("qualified_name") for key in flow["ordered_node_ids"][:4]] == chain
        and all(next((edge["kind"] for edge in graph["edges"] if edge["id"] == key), None) == "depends-on"
                for key in flow["ordered_edge_ids"][:3])
        for flow in graph["flows"])
    use_case = health + "application.use_cases.HealthUseCases.alembic_version"
    checks["interface-dispatch-gap-visible"] = any(
        nodes.get(step["source_id"], {}).get("qualified_name") == use_case
        and step["evidence"].get("path") == "app/modules/health/application/use_cases.py"
        and step["evidence"].get("line") == 48
        and step["evidence"].get("expression") == "self.repository.get_alembic_version"
        and step["reason"] == "target-outside-or-unresolved"
        for flow in graph["flows"] if nodes.get(flow["entrypoint_id"], {}).get("qualified_name") == chain[0]
        for step in flow["unresolved_steps"])
    checks["interface-dispatch-not-promoted"] = not any(
        nodes.get(edge["source"], {}).get("qualified_name") == use_case
        and edge.get("evidence", {}).get("expression") == "self.repository.get_alembic_version"
        and edge["kind"] == "calls" for edge in graph["edges"])
    model = symbols.get(health + "infrastructure.models.AlembicModel", {})
    checks["sqlalchemy-model-recognized"] = model.get("sqlalchemy", {}).get("kind") == "model"
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    try:
        verify_snapshot(args.snapshot, manifest)
        graph = analyze_repository(args.snapshot)
        cases = check_flows(graph)
        boundary = check_persistence_boundary(graph)
    except (ValueError, OSError) as exc:
        print(json.dumps({"result": "STOP", "reason": str(exc)}))
        return 2
    passed = all(case["routeRecognized"] and case["sourceCallLinked"]
                 and case["representativeIncludesUseCase"] for case in cases) and all(boundary.values())
    print(json.dumps({"result": "PASS" if passed else "GAPS", "commit": manifest["commit"],
                      "scope": "source-only static flow benchmark; human and UI acceptance pending",
                      "selectedFlowLabels": [flow["label"] for flow in graph["flows"]],
                      "cases": cases, "persistenceBoundary": boundary}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
