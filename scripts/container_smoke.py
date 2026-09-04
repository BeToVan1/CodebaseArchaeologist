"""Run inside the validation image. Token is generated here, never printed."""
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import tempfile
from pathlib import Path


def check_project_discovery(graph):
    """Require this release's feature, not merely a healthy older analyzer."""
    metadata = graph.get("project_discovery")
    assert isinstance(metadata, dict), "Project discovery is absent."
    assert metadata["version"] == "1"
    assert metadata["scope"] == "root-pyproject-only"
    assert metadata["status"] == "parsed", "Smoke repository manifest was not parsed."
    assert metadata["path"] == "pyproject.toml"
    digest = metadata["sha256"]
    assert isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
    declarations = metadata["declarations"]
    assert 0 < len(declarations) <= 128
    assert any(item["key"] == ["project", "name"] and item["value"] == "itsdangerous" for item in declarations)
    assert all(item["classification"] == "fact" and item["confidence"] == 1 for item in declarations)
    assert metadata["limitations"]
    return "root-pyproject-declarations-present"


def check_test_proximity(graph):
    """Require both evidence signals in the smoke repository, with real refs."""
    report = graph.get("test_proximity")
    assert isinstance(report, dict), "Test proximity is absent."
    assert report["version"] == "1" and report["scope"] == "recorded-direct-edges"
    assert report["test_files_identified"] > 0
    assert report["provenance"] and report["limitations"]
    assert report["links_truncated"] is False
    links = report["links"]
    assert 0 < len(links) <= 1000 and report["candidate_links"] == len(links)
    assert {link["signal"] for link in links} == {"symbol-call", "module-import"}
    nodes = {node["id"]: node for node in graph["nodes"]}
    edges = {edge["id"]: edge for edge in graph["edges"]}
    assert len({link["edge_id"] for link in links}) == len(links)
    def test_path(path):
        parts = path.split('/')
        name = parts[-1]
        return name.endswith('.py') and (any(part in {'test', 'tests'} for part in parts[:-1])
            or name.startswith('test_') or name.endswith('_test.py') or name in {'tests.py', 'conftest.py'})
    assert report["test_files_identified"] == sum(node['kind'] == 'file' and test_path(node['path']) for node in graph['nodes'])
    for link in links:
        assert link["classification"] == "heuristic" and link["confidence"] == 0.6
        edge = edges[link["edge_id"]]
        assert (edge["source"], edge["target"]) == (link["source_node_id"], link["target_node_id"])
        source, target = nodes[edge["source"]], nodes[edge["target"]]
        assert test_path(source["path"]) and not test_path(target["path"])
        assert 0.9 <= edge["confidence"] <= 1
        assert edge["evidence"]["line"] >= 1
        assert edge["evidence"].get("path", source["path"]) == source["path"]
        if link["signal"] == "symbol-call":
            assert edge["kind"] == "calls" and source["kind"] != "file" and target["kind"] != "file"
        else:
            assert edge["kind"] == "imports" and source["kind"] == target["kind"] == "file"
    return "call-and-import-evidence-verified"


def main():
    token = secrets.token_hex(32)
    temporary = tempfile.TemporaryDirectory(prefix="quota-smoke-")
    ledger = Path(temporary.name) / "quota.sqlite3"
    # Run as a module from /app: this helper also runs as scripts/container_smoke.py,
    # whose sys.path otherwise contains only /app/scripts, not the service modules.
    subprocess.run([sys.executable, "-m", "deep_quota", "init", str(ledger)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "deep_service:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000", "--workers", "1", "--no-access-log"],
        env={**os.environ, "ARCHAEOLOGIST_SERVICE_TOKEN": token, "ARCHAEOLOGIST_QUOTA_PATH": str(ledger)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    def request(path, payload=None, authenticated=False, include_headers=False):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Archaeologist-Client-Key"] = "b" * 64
        req = urllib.request.Request("http://127.0.0.1:8000" + path, headers=headers,
                                     data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=70) as response:
                result = (response.status, json.load(response))
                return (*result, response.headers) if include_headers else result
        except urllib.error.HTTPError as error:
            result = (error.code, json.load(error))
            return (*result, error.headers) if include_headers else result
    try:
        for _ in range(50):
            if server.poll() is not None:
                raise RuntimeError("Service exited before startup.")
            try:
                if request("/health")[0] == 200:
                    break
            except urllib.error.URLError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Service did not become healthy.")
        assert request("/api/analyze/quota-v1", {"repositoryUrl": "https://github.com/pallets/itsdangerous"})[0] == 401
        assert request("/api/analyze/quota-v1", {"repositoryUrl": "https://example.com/a/b"}, True)[0] == 400
        status, graph, response_headers = request(
            "/api/analyze/quota-v1",
            {"repositoryUrl": "https://github.com/pallets/itsdangerous"}, True, True,
        )
        if status != 200:
            raise RuntimeError(f"Live analysis returned HTTP {status}: {graph.get('detail', 'unknown failure')}")
        assert graph["analysis"]["tier"] == "deep"
        assert len(graph["snapshot"]["commit_sha"]) == 40
        assert any(node["kind"] != "file" for node in graph["nodes"])
        assert "repo_root" not in graph
        discovery_check = check_project_discovery(graph)
        proximity_check = check_test_proximity(graph)
        report_id = response_headers["X-Archaeologist-Report-Id"]
        assert len(report_id) == 43 and response_headers["X-Archaeologist-Report-TTL"] == "900"
        symbol = next(node for node in graph["nodes"] if node.get("evidence_packet"))
        prepared_status, prepared = request(
            "/api/evidence/prepare", {"reportId": report_id, "nodeId": symbol["id"]}, True,
        )
        assert prepared_status == 200
        assert prepared["commitSha"] == graph["snapshot"]["commit_sha"]
        assert prepared["evidencePacket"]["node_id"] == symbol["id"]
        assert prepared["sourceExcerpt"].strip()
        assert request("/api/evidence/prepare", {
            "reportId": report_id, "nodeId": symbol["id"], "sourceExcerpt": "forged",
        }, True)[0] == 400
        assert request("/health")[0] == 200
        print(json.dumps({"result": "PASS", "repository": "pallets/itsdangerous", "tier": "deep",
                          "nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
                          "authorization": "verified", "invalid_url": "rejected",
                          "project_discovery": discovery_check,
                          "test_proximity": proximity_check,
                          "evidence_reference": "owner-bound-source-verified"}))
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        temporary.cleanup()


if __name__ == "__main__":
    main()
