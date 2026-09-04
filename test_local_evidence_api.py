import asyncio
import subprocess
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

import local_evidence_api as local
from test_snapshot_capture import checkout

TOKEN = "synthetic-local-test-token-" + "a" * 32
AUTH = {"Authorization": "Bearer " + TOKEN}
URL = {"repositoryUrl": "https://github.com/example/fixture"}


@pytest.fixture
def application(monkeypatch):
    monkeypatch.setenv("ARCHAEOLOGIST_LOCAL_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("ARCHAEOLOGIST_LOCAL_EVIDENCE_TOKEN", TOKEN)
    return local.create_local_evidence_app()


def client(application, peer="127.0.0.1"):
    return TestClient(application, client=(peer, 50000))


def analyze_fixture(application, checkout):
    with patch.object(local, "load_repository", return_value=checkout), patch.object(local, "cleanup_repository") as cleanup:
        response = client(application).post("/analyze", json=URL, headers=AUTH)
    cleanup.assert_called_once_with(checkout)
    assert response.status_code == 200, response.text
    graph = response.json()["graph"]
    node = next(n for n in graph["nodes"] if n.get("name") == "run")
    return response, {"reportId": response.json()["reportId"], "nodeId": node["id"]}


def test_authenticated_git_capture_to_reference_only_preparation(application, checkout):
    response, selection = analyze_fixture(application, checkout)
    with patch.object(local, "load_repository") as clone:
        result = client(application).post("/prepare", json=selection, headers=AUTH)
    assert result.status_code == 200
    assert result.json()["sourceExcerpt"] == "def run():\n    return 1"
    assert result.json()["commitSha"] == response.json()["graph"]["snapshot"]["commit_sha"]
    assert result.json()["modelCalled"] is False
    assert result.headers["cache-control"] == "no-store"
    assert TOKEN not in response.text + result.text
    clone.assert_not_called()


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"},
    {"X-Owner": "owner", "oai-authenticated-user-id": "owner"}])
def test_missing_or_forged_auth_never_starts_work(application, headers):
    with patch.object(local, "load_repository") as clone:
        response = client(application).post("/analyze", content=b"x" * 5000, headers=headers)
    assert response.status_code == 401
    clone.assert_not_called()


@pytest.mark.parametrize("key,value", [("ARCHAEOLOGIST_LOCAL_EVIDENCE_ENABLED", "false"),
    ("ARCHAEOLOGIST_LOCAL_EVIDENCE_TOKEN", ""), ("ARCHAEOLOGIST_LOCAL_EVIDENCE_TOKEN", "short")])
def test_disabled_or_misconfigured_fails_closed(application, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    assert client(application).post("/analyze", json=URL, headers=AUTH).status_code == 503


def test_non_loopback_and_browser_origin_rejected(application):
    assert client(application, "198.51.100.2").post("/analyze", json=URL,
        headers={**AUTH, "X-Forwarded-For": "127.0.0.1"}).status_code == 403
    assert client(application).post("/analyze", json=URL,
        headers={**AUTH, "Origin": "http://localhost:3000"}).status_code == 403


@pytest.mark.parametrize("extra", [{"sourceExcerpt": "forged"}, {"evidencePacket": {}}, {"ownerKey": "other"}])
def test_prepare_rejects_client_evidence_and_identity(application, extra):
    response = client(application).post("/prepare", json={"reportId": "a" * 43, "nodeId": "node", **extra}, headers=AUTH)
    assert response.status_code == 422


def test_token_rotation_cannot_access_old_snapshot(application, checkout, monkeypatch):
    _, selection = analyze_fixture(application, checkout)
    new_token = "b" * 64
    monkeypatch.setenv("ARCHAEOLOGIST_LOCAL_EVIDENCE_TOKEN", new_token)
    response = client(application).post("/prepare", json=selection, headers={"Authorization": "Bearer " + new_token})
    assert response.status_code == 404
    assert client(application).post("/prepare", json=selection, headers=AUTH).status_code == 401


def test_request_formats_and_limits_before_clone(application):
    with patch.object(local, "load_repository") as clone:
        assert client(application).post("/analyze", content=b" " * 4097,
            headers={**AUTH, "Content-Type": "application/json"}).status_code == 413
        assert client(application).post("/analyze", content="bad", headers=AUTH).status_code == 415
        assert client(application).post("/analyze", json=URL,
            headers={**AUTH, "Content-Encoding": "gzip"}).status_code == 415
        assert client(application).post("/analyze", json={"repositoryUrl": "https://example.com/repo"}, headers=AUTH).status_code == 400
    clone.assert_not_called()


def test_busy_slot_rejects_without_cloning(application):
    application.state.analysis_slot.acquire()
    try:
        with patch.object(local, "load_repository") as clone:
            assert client(application).post("/analyze", json=URL, headers=AUTH).status_code == 429
        clone.assert_not_called()
    finally:
        application.state.analysis_slot.release()


def test_capture_failure_cleans_up_releases_slot_and_hides_private_error(application, checkout):
    with patch.object(local, "load_repository", return_value=checkout), \
         patch.object(local, "analyze_verified_snapshot", side_effect=local.SnapshotCaptureError("private-path")), \
         patch.object(local, "cleanup_repository") as cleanup:
        response = client(application).post("/analyze", json=URL, headers=AUTH)
    assert response.status_code == 422
    assert "private-path" not in response.text
    cleanup.assert_called_once_with(checkout)
    assert application.state.analysis_slot.acquire(blocking=False)
    application.state.analysis_slot.release()


def test_body_timeout_and_disconnect_never_dispatch(application, monkeypatch):
    monkeypatch.setattr(local, "BODY_TIMEOUT_SECONDS", 0.01)
    async def run(disconnect):
        dispatched, sent = [], []
        async def endpoint(*args): dispatched.append(True)
        async def receive():
            if disconnect: return {"type": "http.disconnect"}
            await asyncio.Future()
        async def send(message): sent.append(message)
        scope = {"type": "http", "method": "POST", "path": "/analyze", "client": ("127.0.0.1", 1),
            "headers": [(b"authorization", AUTH["Authorization"].encode()), (b"content-type", b"application/json")]}
        await local.LocalEvidenceBoundary(endpoint)(scope, receive, send)
        assert not dispatched
        if not disconnect: assert sent[0]["status"] == 408
    asyncio.run(run(False))
    asyncio.run(run(True))


def test_capacity_response_and_missing_reference_are_safe(application, checkout):
    from evidence_store import EvidenceSnapshotStore
    application.state.evidence_store = EvidenceSnapshotStore(max_snapshots=1)
    _, selection = analyze_fixture(application, checkout)
    with patch.object(local, "load_repository", return_value=checkout), patch.object(local, "cleanup_repository"):
        assert client(application).post("/analyze", json=URL, headers=AUTH).status_code == 503
    assert client(application).post("/prepare", json=selection, headers=AUTH).status_code == 200
    selection["reportId"] = "a" * 43
    assert client(application).post("/prepare", json=selection, headers=AUTH).status_code == 404


@pytest.mark.parametrize("enabled,expected", [("false", "404"), ("true", "401")])
def test_main_api_mount_is_opt_in_at_startup(enabled, expected):
    # Isolate module startup from test_api's already-imported app. No server or
    # network is started, and no real credentials enter the child environment.
    from repository_loader import public_git_environment
    env = {**public_git_environment(), "ARCHAEOLOGIST_LOCAL_EVIDENCE_ENABLED": enabled,
           "ARCHAEOLOGIST_LOCAL_EVIDENCE_TOKEN": TOKEN}
    code = "from fastapi.testclient import TestClient; from api import app; print(TestClient(app, client=('127.0.0.1', 1)).post('/api/evidence/prepare', json={}).status_code)"
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
