from dataclasses import replace
import json

import httpx
from openai import OpenAI, InternalServerError, APITimeoutError
import pytest

import interpretation_budget as budget
import interpretation_execution as execution
from evidence_store import EvidenceSnapshotStore, SnapshotUnavailable
from interpretation import InterpretationGroundingError
from test_interpretation import generated
from test_interpretation_evidence import NODE, PIN, report


@pytest.fixture
def setup(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    ledger = directory / "budget.sqlite3"
    budget.initialize(ledger, limit_units=10000)
    store = EvidenceSnapshotStore()
    ref = store.register_trusted_snapshot(owner_key="owner", graph=report(),
        source_files={"example.py": b"def run():\n    pass"}, commit_sha=PIN)
    # Synthetic rates, not actual model pricing or an approved live policy.
    policy = execution.ExecutionPolicy("test-model", 1000000, 2000000, 1, 200,
                                        max_input_tokens=1000, max_output_tokens=100)
    return dict(store=store, owner_key="owner", report_id=ref.report_id, node_id=NODE,
                policy=policy, ledger=ledger, enabled=True, clock=lambda: 100)


def response(reference="pattern:layered-architecture"):
    return {"id": "resp_test", "object": "response", "created_at": 0, "status": "completed",
        "model": "test-model", "output": [{"id": "msg_test", "type": "message", "role": "assistant",
            "status": "completed", "content": [{"type": "output_text", "annotations": [],
                "text": generated(reference).model_dump_json()}]}],
        "parallel_tool_calls": False, "tool_choice": "auto", "tools": []}


def run(setup, handler):
    # Real SDK, fake transport: even malformed routing cannot reach a network.
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        with OpenAI(api_key="synthetic-offline-key", http_client=transport, max_retries=4) as sdk:
            return execution.generate_budgeted_interpretation(**setup, client=sdk)


def test_sdk_count_and_generation_match_and_reservation_precedes_transport(setup):
    requests = []
    def handler(request):
        assert budget.status(setup["ledger"]).reserved_units == 1201
        requests.append(json.loads(request.content))
        assert request.extensions["timeout"]["read"] == 30
        assert request.headers["x-stainless-retry-count"] == "0"
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        return httpx.Response(200, json=response())
    result = run(setup, handler)
    assert result.classification == "interpretation"
    assert len(requests) == 2
    count, create = requests
    assert count["input"] == create["input"]
    assert count["text"] == create["text"]
    assert create["max_output_tokens"] == 100
    assert create["service_tier"] == "default"
    assert create["background"] is False and create["store"] is False
    assert count["truncation"] == create["truncation"] == "disabled"
    assert not {"tools", "conversation", "previous_response_id"} & create.keys()
    assert budget.status(setup["ledger"]).reserved_units == 1201  # no refund for shorter input/output


@pytest.mark.parametrize("stage", ["count", "generate"])
def test_provider_500_is_not_retried_or_refunded(setup, stage):
    requests = []
    def handler(request):
        requests.append(request.url.path)
        if stage == "generate" and request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"input_tokens": 100, "object": "response.input_tokens"})
        return httpx.Response(500, json={"error": {"message": "synthetic failure", "type": "server_error"}})
    with pytest.raises(InternalServerError): run(setup, handler)
    assert len(requests) == (1 if stage == "count" else 2)
    assert budget.status(setup["ledger"]).reserved_units == 1201


@pytest.mark.parametrize("count", [0, -1, None, 1001])
def test_bad_input_count_prevents_generation_without_refund(setup, count):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"input_tokens": count, "object": "response.input_tokens"})
    with pytest.raises(execution.ExecutionUnavailable): run(setup, handler)
    assert len(calls) == 1
    assert budget.status(setup["ledger"]).reserved_units == 1201


@pytest.mark.parametrize("case", ["disabled", "expired", "owner", "missing-budget", "exhausted"])
def test_denials_happen_before_any_provider_request(setup, case):
    if case == "disabled": setup["enabled"] = False
    if case == "expired": setup["policy"] = replace(setup["policy"], valid_until=100)
    if case == "owner": setup["owner_key"] = "other"
    if case == "missing-budget": setup["ledger"] = setup["ledger"].parent / "missing.sqlite3"
    if case == "exhausted": budget.reserve(setup["ledger"], units=10000)
    calls = []
    def handler(request):
        calls.append(request)
        raise AssertionError("must not call provider")
    with pytest.raises((execution.ExecutionUnavailable, SnapshotUnavailable, budget.BudgetUnavailable, budget.BudgetExceeded)):
        run(setup, handler)
    assert calls == []


def test_unknown_citation_stays_rejected_after_budgeted_call(setup):
    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"input_tokens": 100, "object": "response.input_tokens"})
        return httpx.Response(200, json=response("invented"))
    with pytest.raises(InterpretationGroundingError): run(setup, handler)
    assert budget.status(setup["ledger"]).reserved_units == 1201


def test_quote_uses_integer_ceiling():
    policy = execution.ExecutionPolicy("test-model", 1, 1, 1, 200, max_input_tokens=1, max_output_tokens=1)
    policy.validate(100)
    assert policy.reservation_units == 3


def test_network_timeout_keeps_reservation_and_does_not_retry(setup):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("synthetic timeout", request=request)
    with pytest.raises(APITimeoutError): run(setup, handler)
    assert len(calls) == 1
    assert budget.status(setup["ledger"]).reserved_units == 1201


def test_policy_expiring_during_count_prevents_generation(setup):
    now = [100]
    setup["clock"] = lambda: now[0]
    calls = []
    def handler(request):
        calls.append(request)
        now[0] = 200
        return httpx.Response(200, json={"input_tokens": 100, "object": "response.input_tokens"})
    with pytest.raises(execution.ExecutionUnavailable, match="expired"): run(setup, handler)
    assert len(calls) == 1
    assert budget.status(setup["ledger"]).reserved_units == 1201


def test_busy_dispatcher_consumes_no_budget(setup):
    calls = []
    execution._execution_slot.acquire()
    try:
        with pytest.raises(execution.ExecutionUnavailable, match="busy"):
            run(setup, lambda request: calls.append(request))
    finally:
        execution._execution_slot.release()
    assert not calls
    assert budget.status(setup["ledger"]).reserved_units == 0


def test_incomplete_response_cannot_be_presented_as_finished(setup):
    def handler(request):
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"input_tokens": 100, "object": "response.input_tokens"})
        payload = response()
        payload["status"] = "incomplete"
        return httpx.Response(200, json=payload)
    with pytest.raises(execution.ExecutionUnavailable, match="did not complete"): run(setup, handler)
    assert budget.status(setup["ledger"]).reserved_units == 1201


@pytest.mark.parametrize("changes", [{"max_output_tokens": 8193}, {"max_input_tokens": 32001},
    {"input_units_per_million": 0}, {"preflight_units": -1}, {"timeout_seconds": 61},
    {"output_units_per_million": True}, {"model": ""}])
def test_invalid_execution_policies_are_rejected(changes):
    policy = execution.ExecutionPolicy("test-model", 1, 1, 1, 200)
    with pytest.raises(execution.ExecutionUnavailable): replace(policy, **changes).validate(100)
