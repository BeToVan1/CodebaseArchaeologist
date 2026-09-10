import hashlib

import pytest

from scripts.benchmark_fastapi_flows import CASES, PREFIX, check_flows, check_persistence_boundary, verify_snapshot


def manifest_for(content):
    return {"files": [{"path": "example.py", "size": len(content),
                       "gitBlobSha": hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()}]}


@pytest.mark.parametrize("original,transport", [(b"value = 1\n", b"value = 1\n"),
                                                   (b"value = 1\n", b"value = 1"),
                                                   (b"", b"\n")])
def test_snapshot_accepts_only_hash_verified_bytes_or_terminal_newline(tmp_path, original, transport):
    (tmp_path / "example.py").write_bytes(transport)
    assert verify_snapshot(tmp_path, manifest_for(original))["example.py"] == original


def test_snapshot_rejects_source_change(tmp_path):
    (tmp_path / "example.py").write_text("value = 2\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_snapshot(tmp_path, manifest_for(b"value = 1\n"))


def test_snapshot_rejects_extra_python(tmp_path):
    (tmp_path / "example.py").write_bytes(b"")
    (tmp_path / "extra.py").write_bytes(b"")
    with pytest.raises(ValueError, match="inventory"):
        verify_snapshot(tmp_path, manifest_for(b""))


def test_missing_report_cannot_pass_three_flow_gate():
    results = check_flows({"nodes": [], "edges": [], "flows": []})
    assert len(results) == 3
    assert all(not result["routeRecognized"] and not result["sourceCallLinked"]
               and not result["representativeIncludesUseCase"] for result in results)


@pytest.mark.parametrize("mutation", ["none", "wrong-line", "wrong-path", "wrong-edge", "target-later"])
def test_flow_requires_exact_call_evidence_and_first_hop(mutation):
    _, route_name, target_name, expression, line = CASES[0]
    nodes = [{"id": "route", "kind": "function", "path": "route.py", "qualified_name": route_name, "entrypoint": {"kind": "route"}},
             {"id": "target", "kind": "method", "qualified_name": target_name},
             {"id": "other", "kind": "function", "qualified_name": "other"}]
    edge = {"id": "call", "source": "route", "target": "target", "kind": "may-dispatch-to",
            "evidence": {"expression": expression, "path": "other.py" if mutation == "wrong-path" else "route.py",
                         "line": line + int(mutation == "wrong-line")}}
    flow = {"entrypoint_id": "route", "ordered_node_ids": ["route", "target"],
            "ordered_edge_ids": ["wrong" if mutation == "wrong-edge" else "call"], "unresolved_steps": []}
    if mutation == "target-later":
        flow["ordered_node_ids"] = ["route", "other", "target"]
    [result, *_] = check_flows({"nodes": nodes, "edges": [edge], "flows": [flow]})
    assert result["representativeIncludesUseCase"] == (mutation == "none")


def test_missing_boundary_evidence_cannot_pass():
    checks = check_persistence_boundary({"nodes": [], "edges": [], "flows": []})
    assert not all(checks.values())
    assert not checks["dependency-chain-visible"]
    assert not checks["interface-dispatch-gap-visible"]
    assert not checks["sqlalchemy-model-recognized"]


def test_exact_dispatch_claim_fails_boundary_guard():
    graph = {"nodes": [{"id": "use-case", "qualified_name": PREFIX + "health.application.use_cases.HealthUseCases.alembic_version"}],
             "edges": [{"source": "use-case", "target": "missing", "kind": "calls",
                        "evidence": {"expression": "self.repository.get_alembic_version"}}], "flows": []}
    assert check_persistence_boundary(graph)["interface-dispatch-not-promoted"] is False
