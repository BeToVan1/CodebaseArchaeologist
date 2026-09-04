from dataclasses import replace
import hashlib
import json

import httpx
from openai import OpenAI
import pytest

import interpretation_budget as budget
import local_evidence_api as local
from test_interpretation_execution import setup, response
from test_interpretation_evidence import NODE, PIN, report
from test_local_evidence_api import TOKEN, AUTH, client, analyze_fixture
from test_snapshot_capture import checkout


@pytest.fixture
def route(setup, monkeypatch):
    monkeypatch.setenv("ARCHAEOLOGIST_LOCAL_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("ARCHAEOLOGIST_LOCAL_EVIDENCE_TOKEN", TOKEN)
    calls = []
    outcome = [response()]
    def handler(request):
        calls.append(json.loads(request.content))
        assert budget.status(setup["ledger"]).reserved_units == 1201
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        if isinstance(outcome[0], Exception):
            raise outcome[0]
        if outcome[0] == "error":
            return httpx.Response(500, json={"error": {"message": "private-provider-secret", "type": "server_error"}})
        return httpx.Response(200, json=outcome[0])
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        with OpenAI(api_key="synthetic-offline-key", http_client=transport) as sdk:
            runtime = local.LocalInterpretationRuntime(
                replace(setup["policy"], valid_until=4102444800), setup["ledger"], sdk, enabled=True)
            app = local.create_local_evidence_app(interpretation_runtime=runtime)
            owner = hashlib.sha256(b"local-evidence-owner\0" + TOKEN.encode()).hexdigest()
            ref = app.state.evidence_store.register_trusted_snapshot(owner_key=owner, graph=report(),
                source_files={"example.py": b"def run():\n    pass"}, commit_sha=PIN)
            yield app, {"reportId": ref.report_id, "nodeId": NODE}, runtime, calls, outcome


def test_reference_only_interpretation_uses_real_sdk_and_server_evidence(route):
    app, selection, runtime, calls, _ = route
    result = client(app).post("/interpret", json=selection, headers=AUTH)
    assert result.status_code == 200, result.text
    assert result.json()["reportId"] == selection["reportId"]
    assert result.json()["nodeId"] == NODE
    assert result.json()["interpretation"]["classification"] == "interpretation"
    assert result.headers["cache-control"] == "no-store"
    assert len(calls) == 2 and calls[0]["input"] == calls[1]["input"]
    assert "def run()" in json.dumps(calls[1]["input"])
    assert calls[1]["max_output_tokens"] == 100
    assert TOKEN not in json.dumps(calls) + result.text
    assert budget.status(runtime.ledger).reserved_units == 1201


@pytest.mark.parametrize("configured", [False, True])
def test_interpretation_requires_explicit_server_enablement(route, configured):
    _, selection, runtime, calls, _ = route
    app = local.create_local_evidence_app(interpretation_runtime=replace(runtime, enabled=False) if configured else None)
    result = client(app).post("/interpret", json=selection, headers=AUTH)
    assert result.status_code == 503
    assert not calls and budget.status(runtime.ledger).reserved_units == 0


@pytest.mark.parametrize("case,expected", [("unauthenticated", 401), ("origin", 403),
    ("remote", 403), ("extra-code", 422), ("extra-policy", 422), ("missing", 404),
    ("owner", 404), ("node", 422), ("exhausted", 429), ("missing-ledger", 503), ("expired-policy", 503)])
def test_rejected_requests_never_reach_provider(route, monkeypatch, case, expected):
    app, selection, runtime, calls, _ = route
    headers, peer = dict(AUTH), "127.0.0.1"
    if case == "unauthenticated": headers = {}
    if case == "origin": headers["Origin"] = "http://localhost:3000"
    if case == "remote": peer = "192.0.2.1"
    if case == "extra-code": selection["sourceExcerpt"] = "forged code"
    if case == "extra-policy": selection["enabled"] = True
    if case == "missing": selection["reportId"] = "x" * 43
    if case == "owner":
        monkeypatch.setenv("ARCHAEOLOGIST_LOCAL_EVIDENCE_TOKEN", "b" * 40)
        headers = {"Authorization": "Bearer " + "b" * 40, "X-Owner": "owner"}
    if case == "node": selection["nodeId"] = "missing-symbol"
    if case == "exhausted": budget.reserve(runtime.ledger, units=10000)
    if case in {"missing-ledger", "expired-policy"}:
        changed = replace(runtime, ledger=runtime.ledger.parent / "missing.sqlite3") if case == "missing-ledger" else replace(runtime, policy=replace(runtime.policy, valid_until=1))
        original_store = app.state.evidence_store
        app = local.create_local_evidence_app(interpretation_runtime=changed)
        app.state.evidence_store = original_store
    result = client(app, peer=peer).post("/interpret", json=selection, headers=headers)
    assert result.status_code == expected, result.text
    assert result.headers["cache-control"] == "no-store"
    assert not calls
    assert budget.status(runtime.ledger).reserved_units == (10000 if case == "exhausted" else 0)


@pytest.mark.parametrize("case,expected", [("unknown-citation", 502), ("refusal", 502),
    ("provider-error", 502), ("timeout", 502), ("incomplete", 503)])
def test_failed_model_output_is_sanitized_without_retry_or_refund(route, case, expected):
    app, selection, runtime, calls, outcome = route
    if case == "unknown-citation": outcome[0] = response("private-provider-secret")
    if case == "refusal":
        outcome[0]["output"][0]["content"] = [{"type": "refusal", "refusal": "private-provider-secret"}]
    if case == "provider-error": outcome[0] = "error"
    if case == "timeout": outcome[0] = httpx.ReadTimeout("private-provider-secret")
    if case == "incomplete": outcome[0]["status"] = "incomplete"
    result = client(app).post("/interpret", json=selection, headers=AUTH)
    assert result.status_code == expected, result.text
    assert "private-provider-secret" not in result.text
    assert "interpretation" not in result.json()
    assert result.headers["cache-control"] == "no-store"
    assert len(calls) == 2
    assert budget.status(runtime.ledger).reserved_units == 1201


def test_git_capture_to_authenticated_reference_interpretation(route, checkout):
    app, _, _, calls, outcome = route
    analyzed, selection = analyze_fixture(app, checkout)
    outcome[0] = response(selection["nodeId"])
    result = client(app).post("/interpret", json=selection, headers=AUTH)
    assert result.status_code == 200, result.text
    assert result.json()["reportId"] == analyzed.json()["reportId"]
    assert "return 1" in json.dumps(calls[1]["input"])
