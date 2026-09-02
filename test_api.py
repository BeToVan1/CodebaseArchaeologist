from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import api


client = TestClient(api.app)


def evidence_packet() -> dict[str, object]:
    statement = {
        "text": "Defines one function.",
        "classification": "fact",
        "confidence": 1,
        "provenance": "Python AST",
    }
    return {
        "version": "1",
        "node_id": "symbol:example.py:run",
        "source_range": {"path": "example.py", "start_line": 1, "end_line": 2},
        "summary": statement,
        "execution_role": statement,
        "structural_rationale": statement,
        "related_edge_ids": ["edge:1"],
        "flow_ids": [],
        "finding_ids": [],
        "claims": [{**statement, "id": "claim:1", "evidence_refs": ["symbol:example.py:run"]}],
    }


def test_health() -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_analyze_rejects_non_github_url() -> None:
    response = client.post("/api/analyze", json={"repositoryUrl": "https://example.com/repo"})

    assert response.status_code == 400
    assert "not a valid" in response.json()["detail"]


def test_cors_allows_local_frontend_on_any_port() -> None:
    response = client.options(
        "/api/analyze",
        headers={
            "Origin": "http://localhost:3010",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3010"


def test_analyze_returns_graph_and_cleans_up() -> None:
    checkout = Path("temporary-checkout")
    graph = {
        "schema_version": "0.2",
        "repo_root": str(checkout),
        "nodes": [],
        "edges": [],
    }

    with (
        patch.object(api, "load_repository", return_value=checkout),
        patch.object(api, "resolve_commit_sha", return_value="a" * 40),
        patch.object(api, "analyze_repository", return_value=graph),
        patch.object(api, "cleanup_repository") as cleanup,
    ):
        response = client.post(
            "/api/analyze",
            json={"repositoryUrl": "https://github.com/cosmicpython/code"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert "repo_root" not in payload
    assert payload["repository"] == {
        "name": "cosmicpython/code",
        "url": "https://github.com/cosmicpython/code",
        "pinned_url": f"https://github.com/cosmicpython/code/tree/{'a' * 40}",
        "source": "github",
    }
    assert payload["snapshot"] == {"commit_sha": "a" * 40}
    cleanup.assert_called_once_with(checkout)


def test_interpret_requires_optional_api_key() -> None:
    with patch.dict("os.environ", {}, clear=True):
        response = client.post(
            "/api/interpret",
            json={"evidencePacket": evidence_packet(), "sourceExcerpt": "def run():\n    pass"},
        )

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_interpret_rejects_oversized_source_excerpt() -> None:
    response = client.post(
        "/api/interpret",
        json={"evidencePacket": evidence_packet(), "sourceExcerpt": "x" * 12_001},
    )

    assert response.status_code == 422


