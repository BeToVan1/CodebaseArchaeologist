from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import api


client = TestClient(api.app)


def test_health() -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_analyze_rejects_non_github_url() -> None:
    response = client.post("/api/analyze", json={"repositoryUrl": "https://example.com/repo"})

    assert response.status_code == 400
    assert "not a valid" in response.json()["detail"]


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

