"""Offline semantic checks; supplied checkouts are parsed, never imported.

Run with --itsdangerous PATH --click PATH --flask PATH --cosmicpython PATH.
Checkouts must match the pins below
and be clean. This script neither downloads repositories nor executes their code.
Expectations are selected from project documentation and direct source review,
not generated from a previous analyzer report. It is not an accuracy percentage.
"""
import argparse
import ast
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyzer import analyze_repository

PINS = {
    "itsdangerous": "672971d66a2ef9f85151e53283113f33d642dabd",
    "click": "36baa15ff831b939a22bc527cd76ce653ef6f66d",
    "flask": "2c1b30d0503cfb064f1cb252e6614a06915a362a",
    "cosmicpython": "14c84797ffa77255d53cf1a02fe6aafda2b68aeb",
}
PACKAGES = {"itsdangerous": "itsdangerous", "click": "click", "flask": "flask",
            "cosmicpython": "allocation"}
BASES = {
    "itsdangerous": [
        ("itsdangerous.exc.BadSignature", "itsdangerous.exc.BadData"),
        ("itsdangerous.exc.BadTimeSignature", "itsdangerous.exc.BadSignature"),
        ("itsdangerous.exc.SignatureExpired", "itsdangerous.exc.BadTimeSignature"),
        ("itsdangerous.timed.TimestampSigner", "itsdangerous.signer.Signer"),
        ("itsdangerous.timed.TimedSerializer", "itsdangerous.serializer.Serializer"),
        ("itsdangerous.url_safe.URLSafeTimedSerializer", "itsdangerous.timed.TimedSerializer"),
    ],
    "click": [
        ("click.core.Group", "click.core.Command"),
        ("click.core.CommandCollection", "click.core.Group"),
        ("click.core.Option", "click.core.Parameter"),
        ("click.core.Argument", "click.core.Parameter"),
    ],
    "flask": [
        ("flask.app.Flask", "flask.sansio.app.App"),
        ("flask.blueprints.Blueprint", "flask.sansio.blueprints.Blueprint"),
        ("flask.views.MethodView", "flask.views.View"),
        ("flask.json.provider.DefaultJSONProvider", "flask.json.provider.JSONProvider"),
    ],
    "cosmicpython": [
        ("allocation.adapters.repository.SqlAlchemyRepository", "allocation.adapters.repository.AbstractRepository"),
        ("allocation.service_layer.unit_of_work.SqlAlchemyUnitOfWork", "allocation.service_layer.unit_of_work.AbstractUnitOfWork"),
        ("allocation.adapters.notifications.EmailNotifications", "allocation.adapters.notifications.AbstractNotifications"),
        ("allocation.domain.commands.Allocate", "allocation.domain.commands.Command"),
    ],
}
IMPORTS = {
    "itsdangerous": [("serializer", "signer"), ("timed", "serializer"), ("timed", "signer"), ("url_safe", "timed")],
    "click": [("decorators", "core"), ("core", "exceptions"), ("core", "formatting"), ("core", "globals")],
    "flask": [("app", "ctx"), ("app", "globals"), ("app", "sessions"), ("wrappers", "helpers")],
    "cosmicpython": [("bootstrap", "service_layer/handlers"),
                     ("adapters/repository", "domain/model"),
                     ("service_layer/handlers", "domain/model"),
                     ("service_layer/unit_of_work", "adapters/repository")],
}
EXPECTED_PATTERNS = {
    "itsdangerous": set(), "click": set(), "flask": set(),
    "cosmicpython": {"layered-architecture", "repository-boundary", "unit-of-work"},
}

# Selected by reading the pinned test sources, before inspecting their reports.
# Each tuple records a test, its explicit call, and an unrelated function in the
# same imported module. The latter must not acquire a call link from the import.
TEST_EVIDENCE = {
    "itsdangerous": ("tests/test_itsdangerous/test_encoding.py", "test_want_bytes",
                     "src/itsdangerous/encoding.py", "want_bytes", "base64_encode"),
    "click": ("tests/test_parser.py", "test_split_arg_string",
              "src/click/shell_completion.py", "split_arg_string", "shell_complete"),
    "flask": ("tests/test_cli.py", "test_find_best_app",
              "src/flask/cli.py", "find_best_app", "locate_app"),
}


def test_evidence_checks(name, root, graph):
    """Check selected real-source expectations, not counts copied from reports."""
    source_path, test_name, target_path, called_name, unrelated_name = TEST_EVIDENCE[name]
    tree = ast.parse((root / source_path).read_text(encoding="utf-8"))
    test = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == test_name)
    call_lines = {node.lineno for node in ast.walk(test) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name) and node.func.id == called_name}
    nodes = {node["id"]: node for node in graph["nodes"]}
    edges = {edge["id"]: edge for edge in graph["edges"]}
    report = graph.get("test_proximity", {})
    links = report.get("links", [])

    def matches(link, signal, target_name=None):
        source = nodes.get(link.get("source_node_id"), {})
        target = nodes.get(link.get("target_node_id"), {})
        return (link.get("signal") == signal and source.get("path") == source_path
                and target.get("path") == target_path
                and (signal == "module-import" or
                     (source.get("name") == test_name and target.get("name") == target_name)))

    calls = [link for link in links if matches(link, "symbol-call", called_name)]
    valid_call = bool(calls) and all(
        (edge := edges.get(link.get("edge_id"), {})).get("kind") == "calls"
        and edge.get("source") == link.get("source_node_id")
        and edge.get("target") == link.get("target_node_id")
        and edge.get("evidence", {}).get("path") == source_path
        and edge.get("evidence", {}).get("line") in call_lines
        for link in calls)
    imports = [link for link in links if matches(link, "module-import")]
    unrelated_exists = any(node.get("path") == target_path and node.get("name") == unrelated_name
                           for node in graph["nodes"])
    checks = {
        "test evidence metadata present and untruncated": report.get("version") == "1"
            and report.get("links_truncated") is False and bool(report.get("limitations")),
        "selected direct test call retains exact source evidence": valid_call,
        "selected module import remains file-level evidence": bool(imports) and all(
            nodes[link["source_node_id"]]["kind"] == nodes[link["target_node_id"]]["kind"] == "file"
            and edges.get(link.get("edge_id"), {}).get("kind") == "imports" for link in imports),
        "import does not invent a call to unrelated module function": unrelated_exists and not any(
            matches(link, "symbol-call", unrelated_name) for link in links),
        "test proximity remains heuristic, not coverage": bool(links) and all(
            link.get("classification") == "heuristic" and link.get("confidence") == 0.6 for link in links),
    }
    return [{"check": label, "pass": bool(value)} for label, value in checks.items()]


def benchmark(name, root):
    root = root.resolve()
    git = ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root)]
    commit = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    if commit != PINS[name] or subprocess.check_output([*git, "status", "--porcelain"], text=True).strip():
        raise ValueError(f"{name}: expected a clean checkout at {PINS[name]}")
    graph = analyze_repository(root)
    nodes = {node["id"]: node for node in graph["nodes"]}
    classes = {node.get("qualified_name"): node for node in graph["nodes"] if node["kind"] == "class"}
    checks = []

    def check(label, condition):
        checks.append({"check": label, "pass": bool(condition)})

    for child, base in BASES[name]:
        source, target = classes.get(child), classes.get(base)
        check(f"extends:{child}->{base}", source and target and any(
            e["kind"] == "extends" and e["source"] == source["id"] and e["target"] == target["id"]
            for e in graph["edges"]))
    package = PACKAGES[name]
    for source, target in IMPORTS[name]:
        check(f"imports:{source}->{target}", any(
            e["kind"] == "imports" and e["source"] == f"file:src/{package}/{source}.py"
            and e["target"] == f"file:src/{package}/{target}.py" for e in graph["edges"]))
    check("no FastAPI or declarative SQLAlchemy claims", not any(
        node.get("entrypoint") or node.get("sqlalchemy") for node in graph["nodes"]))
    check("no supported HTTP execution flows", not graph["flows"])
    check("selected architecture patterns exactly match", {
        p["pattern_id"] for p in graph["patterns"]} == EXPECTED_PATTERNS[name])

    # Independently parse source ranges: large means >=80 physical lines in the
    # current rule, not 80 executable statements or proof of a maintenance defect.
    expected = set()
    for path in sorted((root / "src" / package).rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno - node.lineno + 1 >= 80:
                expected.add((path.relative_to(root).as_posix(), node.lineno, node.end_lineno))
    actual = set()
    for finding in graph["findings"]:
        if finding["rule_id"] == "large-symbol" and finding["evidence"]["path"].startswith(f"src/{package}/"):
            evidence = finding["evidence"]
            actual.add((evidence["path"], evidence["line"], evidence["end_line"]))
    check("large-callable ranges exactly match independent AST ranges", actual == expected)
    check("risk findings remain heuristics, not proven defects", all(
        finding["classification"] == "heuristic" for finding in graph["findings"]))
    symbols = [node for node in graph["nodes"] if node["kind"] != "file"]
    candidates = {edge["id"] for edge in graph["edges"]
                  if edge["kind"] == "may-dispatch-to" or float(edge.get("confidence", 1)) < 0.9}
    edge_claims = [claim for node in symbols for claim in node.get("evidence_packet", {}).get("claims", [])
                   if ":edge:" in claim["id"] and any(ref in candidates for ref in claim["evidence_refs"])]
    check("candidate relationship claims remain heuristic", bool(edge_claims) and all(
        claim["classification"] == "heuristic" and claim["text"].startswith("Candidate relationship:")
        for claim in edge_claims))
    check("unknown author intent has zero confidence", bool(symbols) and all(
        node.get("evidence_packet", {}).get("structural_rationale", {}).get("confidence") == 0
        for node in symbols))
    if name in TEST_EVIDENCE:
        checks.extend(test_evidence_checks(name, root, graph))
    repository = "cosmicpython/code" if name == "cosmicpython" else f"pallets/{name}"
    return {"repository": repository, "commit": commit, "checks": checks,
            "nodes": len(nodes), "edges": len(graph["edges"]),
            "patterns": len(graph["patterns"]), "risks": len(graph["findings"]),
            "large_callables_expected": len(expected),
            "large_callables_missing": sorted(expected - actual),
            "large_callables_unexpected": sorted(actual - expected)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in PINS:
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    results = [benchmark(name, getattr(args, name)) for name in PINS]
    print(json.dumps(results, indent=2))
    return int(any(not check["pass"] for result in results for check in result["checks"]))


if __name__ == "__main__":
    raise SystemExit(main())
