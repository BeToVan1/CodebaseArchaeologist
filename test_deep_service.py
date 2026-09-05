"""Offline HTTP/lifecycle tests; real process-group checks run only on Linux."""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

import deep_service as service
from deep_quota import initialize

TOKEN = "test-only-not-a-production-secret-123456"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "X-Archaeologist-Client-Key": "a" * 64}
PIN = "c" * 40
@pytest.fixture(autouse=True)
def quota_storage(tmp_path, monkeypatch):
    path = tmp_path / "quota.sqlite3"
    initialize(path)
    monkeypatch.setenv("ARCHAEOLOGIST_QUOTA_PATH", str(path))
    for key in ("ARCHAEOLOGIST_INTERPRETATION_ENABLED", "ARCHAEOLOGIST_CF_ACCOUNT_ID", "ARCHAEOLOGIST_CF_AI_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    return path


URL = "https://github.com/example/project"
GRAPH = {"schema_version": "1.1", "analysis": {"tier": "deep"}, "snapshot": {"commit_sha": PIN}, "nodes": [], "edges": []}


def job_result(graph=GRAPH, sources=None):
    payload = json.dumps(graph).encode()
    return service.JobResult(payload, graph, {} if sources is None else sources, PIN)


@pytest.mark.parametrize("token", ["", "short", "x" * 32 + " ", "é" * 40])
def test_missing_or_unsafe_secret_prevents_startup(token):
    with pytest.raises(RuntimeError, match="SERVICE_TOKEN"):
        service.create_app(token)


def test_health_only_and_auth_before_any_job():
    client = TestClient(service.create_app(TOKEN))
    with patch.object(service, "run_job", new_callable=AsyncMock) as job:
        assert client.get("/health").status_code == 200
        assert client.get("/docs").status_code == 404
        assert client.post("/api/interpret", json={}).status_code == 404
        assert client.post("/api/evidence/prepare", json={}).status_code == 401
        assert client.post("/api/analyze", json={"repositoryUrl": URL}).status_code == 401
        assert client.post("/api/analyze", headers={"Authorization": "Bearer wrong"}, content=b"{" * 3000).status_code == 401
        job.assert_not_called()


@pytest.mark.parametrize("body,status", [(b"x" * 2049, 413), (b"{", 400), (b"null", 400), (b"[]", 400), (b'{"repositoryUrl":"https://evil.test/a/b"}', 400)])
def test_invalid_input_never_starts_job(body, status):
    client = TestClient(service.create_app(TOKEN))
    with patch.object(service, "run_job", new_callable=AsyncMock) as job:
        assert client.post("/api/analyze", headers=HEADERS, content=body).status_code == status
        job.assert_not_called()


def test_success_and_safe_failures_release_slot():
    client = TestClient(service.create_app(TOKEN))
    payload = json.dumps(GRAPH).encode()
    with patch.object(service, "run_job", new_callable=AsyncMock) as job:
        for failure, status in [(service.JobFailure(504, "Timed out"), 504), (RuntimeError("private-secret"), 502)]:
            job.side_effect = failure
            response = client.post("/api/analyze", headers=HEADERS, json={"repositoryUrl": URL})
            assert response.status_code == status
            assert "private-secret" not in response.text
        job.side_effect = None
        job.return_value = job_result()
        response = client.post("/api/analyze", headers=HEADERS, json={"repositoryUrl": URL + ".git"})
        assert response.status_code == 200
        assert response.json() == GRAPH
        assert response.headers["cache-control"] == "no-store"
        assert len(response.headers["x-archaeologist-report-id"]) == 43
        assert response.headers["x-archaeologist-report-ttl"] == "900"
        job.assert_awaited_with(URL)


def test_prepare_uses_only_server_retained_evidence_and_owner_binding():
    from evidence_store import EvidenceSnapshotStore
    from test_interpretation_evidence import NODE, PIN as EVIDENCE_PIN, report

    store = EvidenceSnapshotStore()
    graph = report()
    ref = store.register_trusted_snapshot(
        owner_key="a" * 64, graph=graph,
        source_files={"example.py": b"def run():\n    pass\n"}, commit_sha=EVIDENCE_PIN,
    )
    client = TestClient(service.create_app(TOKEN, evidence_store=store))
    response = client.post("/api/evidence/prepare", headers=HEADERS,
                           json={"reportId": ref.report_id, "nodeId": NODE})
    assert response.status_code == 200
    assert response.json()["sourceExcerpt"] == "def run():\n    pass"
    assert response.json()["evidencePacket"]["node_id"] == NODE
    assert response.json()["commitSha"] == EVIDENCE_PIN

    wrong_owner = {**HEADERS, "X-Archaeologist-Client-Key": "b" * 64}
    assert client.post("/api/evidence/prepare", headers=wrong_owner,
                       json={"reportId": ref.report_id, "nodeId": NODE}).status_code == 404
    assert client.post("/api/evidence/prepare", headers=HEADERS, json={
        "reportId": ref.report_id, "nodeId": NODE,
        "sourceExcerpt": "browser supplied", "evidencePacket": {},
    }).status_code == 400


def test_oracle_interpretation_uses_only_retained_owner_bound_evidence(monkeypatch):
    from evidence_store import EvidenceSnapshotStore
    from test_interpretation_evidence import NODE, PIN as EVIDENCE_PIN, report

    monkeypatch.setenv("ARCHAEOLOGIST_INTERPRETATION_ENABLED", "true")
    monkeypatch.setenv("ARCHAEOLOGIST_CF_ACCOUNT_ID", "a" * 32)
    monkeypatch.setenv("ARCHAEOLOGIST_CF_AI_TOKEN", "token-" + "x" * 40)
    store = EvidenceSnapshotStore()
    ref = store.register_trusted_snapshot(
        owner_key="a" * 64, graph=report(),
        source_files={"example.py": b"def run():\n    pass\n"}, commit_sha=EVIDENCE_PIN,
    )
    generated = {
        "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "classification": "interpretation",
        "what_it_does": {"text": "Runs.", "confidence": 0.7, "evidence_refs": [NODE], "classification": "interpretation", "provenance": "safe"},
        "execution_role": {"text": "Called.", "confidence": 0.6, "evidence_refs": [NODE], "classification": "interpretation", "provenance": "safe"},
        "structural_rationale": {"text": "Unknown.", "confidence": 0.3, "evidence_refs": [NODE], "classification": "interpretation", "provenance": "safe"},
        "uncertainties": ["Runtime behavior is not observed."],
    }
    client = TestClient(service.create_app(TOKEN, evidence_store=store))
    with patch.object(service, "generate_workers_ai", new_callable=AsyncMock, return_value=generated) as provider:
        response = client.post("/api/interpret/quota-v1", headers=HEADERS,
                               json={"reportId": ref.report_id, "nodeId": NODE})
        assert response.status_code == 200
        assert response.json() == {**generated, "commitSha": EVIDENCE_PIN, "nodeId": NODE}
        packet_arg, source_arg, config_arg = provider.await_args.args
        assert packet_arg.node_id == NODE
        assert source_arg == "def run():\n    pass"
        assert config_arg.account_id == "a" * 32
        assert config_arg.token == "token-" + "x" * 40
        assert provider.await_count == 1

    wrong_owner = {**HEADERS, "X-Archaeologist-Client-Key": "b" * 64}
    with patch.object(service, "generate_workers_ai", new_callable=AsyncMock) as provider:
        assert client.post("/api/interpret/quota-v1", headers=wrong_owner,
                           json={"reportId": ref.report_id, "nodeId": NODE}).status_code == 404
        assert client.post("/api/interpret/quota-v1", headers=HEADERS, json={
            "reportId": ref.report_id, "nodeId": NODE, "sourceExcerpt": "forged",
        }).status_code == 400
        provider.assert_not_awaited()


def test_oracle_interpretation_fails_closed_without_configuration():
    client = TestClient(service.create_app(TOKEN))
    with patch.object(service, "generate_workers_ai", new_callable=AsyncMock) as provider:
        assert client.post("/api/interpret/quota-v1", headers=HEADERS,
                           json={"reportId": "R" * 43, "nodeId": "symbol:x"}).status_code == 503
        provider.assert_not_awaited()


def test_concurrent_request_is_rejected_without_queueing():
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def job(_):
            started.set()
            await release.wait()
            return job_result()
        app = service.create_app(TOKEN)
        with patch.object(service, "run_job", side_effect=job):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                first = asyncio.create_task(client.post("/api/analyze", headers=HEADERS, json={"repositoryUrl": URL}))
                await started.wait()
                second = await client.post("/api/analyze", headers=HEADERS, json={"repositoryUrl": URL})
                assert second.status_code == 429
                assert second.headers["retry-after"] == "5"
                release.set()
                assert (await first).status_code == 200
                assert (await client.post("/api/analyze", headers=HEADERS, json={"repositoryUrl": URL})).status_code == 200
    asyncio.run(scenario())


def test_disconnect_cancels_job_and_frees_slot():
    async def scenario():
        started, cleaned = asyncio.Event(), asyncio.Event()
        async def job(_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        sent = []
        first_receive = True
        async def receive():
            nonlocal first_receive
            if first_receive:
                first_receive = False
                return {"type": "http.request", "body": json.dumps({"repositoryUrl": URL}).encode(), "more_body": False}
            await started.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            sent.append(message)
        app = service.create_app(TOKEN)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
                 "path": "/api/analyze", "raw_path": b"/api/analyze", "query_string": b"", "root_path": "",
                 "server": ("test", 80), "client": ("test", 1), "headers": [(key.lower().encode(), value.encode()) for key, value in HEADERS.items()]}
        with patch.object(service, "run_job", side_effect=job):
            await asyncio.wait_for(app(scope, receive, send), 3)
        assert cleaned.is_set()
        assert next(m["status"] for m in sent if m["type"] == "http.response.start") == 499
        with patch.object(service, "run_job", return_value=job_result()):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.post("/api/analyze", headers=HEADERS, json={"repositoryUrl": URL})).status_code == 200
    asyncio.run(scenario())


@pytest.mark.parametrize("mode,status", [("success", None), ("limit", 413), ("invalid", 502), ("timeout", 504), ("cancel", None)])
def test_job_lifecycle_reaps_process_and_removes_directory(tmp_path, monkeypatch, mode, status):
    async def scenario():
        directories = []
        process = SimpleNamespace(pid=123, returncode=3 if mode == "limit" else 0)
        async def communicate(_):
            if mode in {"timeout", "cancel"}:
                await asyncio.Event().wait()
        process.communicate = communicate
        async def spawn(*args, **kwargs):
            assert args[1] == "-I"
            assert kwargs["start_new_session"] is True
            assert "ARCHAEOLOGIST_SERVICE_TOKEN" not in kwargs["env"]
            directories.append(Path(kwargs["cwd"]))
            (directories[-1] / "result.json").write_bytes(b"null" if mode == "invalid" else json.dumps(GRAPH).encode())
            evidence = directories[-1] / "evidence"
            evidence.mkdir()
            (evidence / "manifest.json").write_text(json.dumps({"commit_sha": PIN, "files": []}))
            return process
        monkeypatch.setattr(service, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        monkeypatch.setattr(service, "JOB_TIMEOUT_SECONDS", 0.03)
        with patch.object(service.asyncio, "create_subprocess_exec", side_effect=spawn), patch.object(service, "terminate_group", new_callable=AsyncMock) as cleanup:
            task = asyncio.create_task(service.run_job(URL))
            if mode == "cancel":
                while not directories:
                    await asyncio.sleep(0)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif status:
                with pytest.raises(service.JobFailure) as error:
                    await task
                assert error.value.status == status
            else:
                assert (await task).graph == GRAPH
            cleanup.assert_awaited_once_with(process)
        assert directories and all(not path.exists() for path in directories)
    asyncio.run(scenario())


def test_cancellation_during_spawn_reaps_late_child(monkeypatch):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        directories = []
        process = SimpleNamespace(pid=123)
        async def spawn(*args, **kwargs):
            directories.append(Path(kwargs["cwd"]))
            started.set()
            await release.wait()
            return process
        monkeypatch.setattr(service, "sys", SimpleNamespace(platform="linux", executable=sys.executable))
        with patch.object(service.asyncio, "create_subprocess_exec", side_effect=spawn), patch.object(service, "terminate_group", new_callable=AsyncMock) as cleanup:
            task = asyncio.create_task(service.run_job(URL))
            await started.wait()
            task.cancel()
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            cleanup.assert_awaited_once_with(process)
        assert not directories[0].exists()
    asyncio.run(scenario())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux process groups; run Docker validation")
def test_real_linux_timeout_reaps_child(tmp_path, monkeypatch):
    fake_worker = tmp_path / "sleeper.py"
    fake_worker.write_text("import time\ntime.sleep(60)\n")
    monkeypatch.setattr(service, "WORKER_PATH", fake_worker)
    monkeypatch.setattr(service, "JOB_TIMEOUT_SECONDS", 0.2)
    async def scenario():
        with patch.object(service, "terminate_group", wraps=service.terminate_group) as cleanup:
            with pytest.raises(service.JobFailure) as error:
                await service.run_job(URL)
            assert error.value.status == 504
            process = cleanup.call_args.args[0]
            assert process.returncode is not None
            with pytest.raises(ProcessLookupError):
                os.kill(process.pid, 0)
    asyncio.run(scenario())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux process groups; run Docker validation")
def test_real_linux_timeout_stops_git_like_descendant(tmp_path, monkeypatch):
    record = tmp_path / "descendant.pid"
    fake_worker = tmp_path / "parent.py"
    fake_worker.write_text(
        "import subprocess, sys, time\nfrom pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"Path({str(record)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setattr(service, "WORKER_PATH", fake_worker)
    monkeypatch.setattr(service, "JOB_TIMEOUT_SECONDS", 1)
    async def scenario():
        with pytest.raises(service.JobFailure) as error:
            await service.run_job(URL)
        assert error.value.status == 504
        assert record.exists(), "test child failed to start before deadline"
        pid = int(record.read_text())
        proc_stat = Path(f"/proc/{pid}/stat")
        # A stopped zombie is awaiting Docker --init reaping, not live work.
        for _ in range(20):
            try:
                assert proc_stat.read_text().split(") ", 1)[1].startswith("Z ")
                return
            except FileNotFoundError:
                return
            except AssertionError:
                await asyncio.sleep(0.05)
        pytest.fail("descendant remained running after job timeout")
    asyncio.run(scenario())

@pytest.mark.parametrize("endpoint", ["/api/analyze", "/api/analyze/quota-v1"])
def test_quota_denial_precedes_job_and_survives_app_recreation(endpoint):
    with patch.object(service, "run_job", new_callable=AsyncMock, return_value=job_result()) as job:
        for _ in range(3):
            assert TestClient(service.create_app(TOKEN)).post(endpoint, headers=HEADERS, json={"repositoryUrl": URL}).status_code == 200
        denied = TestClient(service.create_app(TOKEN)).post(endpoint, headers=HEADERS, json={"repositoryUrl": URL})
        assert denied.status_code == 429
        assert denied.headers["retry-after"] == "3600"
        assert denied.headers["x-archaeologist-limit"] == "quota"
        assert job.await_count == 3


def test_missing_storage_and_network_key_fail_closed(tmp_path):
    client = TestClient(service.create_app(TOKEN, str(tmp_path / "absent.sqlite3")))
    with patch.object(service, "run_job", new_callable=AsyncMock) as job:
        assert client.post("/api/analyze/quota-v1", headers=HEADERS, json={"repositoryUrl": URL}).status_code == 503
        for value in ("", "raw-ip", "a" * 65):
            headers = {**HEADERS, "X-Archaeologist-Client-Key": value}
            assert client.post("/api/analyze/quota-v1", headers=headers, json={"repositoryUrl": URL}).status_code == 400
        job.assert_not_called()
    assert not (tmp_path / "absent.sqlite3").exists()


def test_invalid_auth_body_and_duplicate_key_never_consume_quota(quota_storage):
    import sqlite3
    from contextlib import closing
    client = TestClient(service.create_app(TOKEN))
    with patch.object(service, "run_job", new_callable=AsyncMock) as job:
        assert client.post("/api/analyze/quota-v1", json={"repositoryUrl": URL}).status_code == 401
        assert client.post("/api/analyze/quota-v1", headers=HEADERS, json={}).status_code == 400
        duplicate = [(key, value) for key, value in HEADERS.items()] + [("X-Archaeologist-Client-Key", "b" * 64)]
        assert client.post("/api/analyze/quota-v1", headers=duplicate, json={"repositoryUrl": URL}).status_code == 400
        job.assert_not_called()
    with closing(sqlite3.connect(quota_storage)) as db:
        assert db.execute("SELECT count(*) FROM deep_admissions").fetchone()[0] == 0


def test_failed_admissions_count_and_routes_share_one_ledger():
    client = TestClient(service.create_app(TOKEN))
    with patch.object(service, "run_job", new_callable=AsyncMock, side_effect=RuntimeError("secret")) as job:
        for route in ("/api/analyze", "/api/analyze/quota-v1", "/api/analyze"):
            assert client.post(route, headers=HEADERS, json={"repositoryUrl": URL}).status_code == 502
        assert client.post("/api/analyze/quota-v1", headers=HEADERS, json={"repositoryUrl": URL}).status_code == 429
        assert job.await_count == 3
