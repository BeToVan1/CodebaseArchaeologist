from __future__ import annotations

import json
import asyncio
from unittest.mock import patch

import httpx
import pytest

from test_interpretation import packet
import workers_ai_client as provider
from workers_ai_client import WORKERS_AI_MODEL, WorkersAIConfig, WorkersAIError, generate_workers_ai


def generated(reference: str = "pattern:layered-architecture") -> dict:
    section = {"text": "Coordinates the operation.", "confidence": 0.8, "evidence_refs": [reference]}
    return {
        "what_it_does": section,
        "execution_role": section,
        "structural_rationale": section,
        "uncertainties": ["Dynamic behavior is not observed."],
    }


def config() -> WorkersAIConfig:
    value = WorkersAIConfig.optional("a" * 32, "token-" + "x" * 40)
    assert value is not None
    return value


def run_with(handler, source="def run(): pass"):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await generate_workers_ai(packet(), source, config(), client=client)
    return asyncio.run(scenario())


def test_sends_one_bounded_json_mode_request_and_validates_grounding():
    calls = 0

    async def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        assert request.url == f"https://api.cloudflare.com/client/v4/accounts/{'a' * 32}/ai/run/{WORKERS_AI_MODEL}"
        assert request.headers["authorization"] == "Bearer token-" + "x" * 40
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["temperature"] == 0
        assert body["max_tokens"] == 1024
        assert body["response_format"]["type"] == "json_schema"
        schema = body["response_format"]["json_schema"]
        assert schema["additionalProperties"] is False
        assert schema["$defs"]["GeneratedSection"]["additionalProperties"] is False
        allowed = schema["$defs"]["GeneratedSection"]["properties"]["evidence_refs"]["items"]["enum"]
        assert allowed == sorted({
            "claim:1", "edge:1", "flow:1",
            "pattern:layered-architecture", "symbol:example.py:run",
        })
        supplied = json.loads(body["messages"][1]["content"])
        assert supplied["evidence_packet"]["node_id"] == packet().node_id
        return httpx.Response(200, json={"success": True, "result": {"response": generated()}})

    result = run_with(handler)
    assert calls == 1
    assert result["model"] == WORKERS_AI_MODEL
    assert result["classification"] == "interpretation"
    assert result["what_it_does"]["classification"] == "interpretation"
    assert "server-retained evidence" in result["what_it_does"]["provenance"]


@pytest.mark.parametrize("status,category", [(401, "authentication"), (403, "authentication"), (429, "quota"), (400, "request"), (500, "availability")])
def test_provider_status_is_safely_classified_without_retry(status, category):
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="private provider detail")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await generate_workers_ai(packet(), "pass", config(), client=client)
    with pytest.raises(WorkersAIError) as caught:
        asyncio.run(scenario())
    assert caught.value.category == category
    assert caught.value.provider_status == status
    assert "private provider detail" not in str(caught.value)
    assert calls == 1


@pytest.mark.parametrize("response,reason", [
    ({"success": True, "result": {"response": generated("unknown")}}, "unknown-evidence"),
    ({"success": True, "result": {"response": {**generated(), "extra": "private"}}}, "response-shape"),
    ({"success": True, "result": {"response": {**generated(), "what_it_does": {**generated()["what_it_does"], "extra": "private"}}}}, "response-shape"),
    ({"success": True, "result": {"response": {**generated(), "execution_role": {**generated()["execution_role"], "confidence": 1}}}}, "schema-validation"),
])
def test_invalid_or_ungrounded_output_is_rejected(response, reason):
    async def handler(_request):
        return httpx.Response(200, json=response)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await generate_workers_ai(packet(), "pass", config(), client=client)
    with pytest.raises(WorkersAIError) as caught:
        asyncio.run(scenario())
    assert caught.value.category == "structured-output"
    assert caught.value.structured_reason == reason


@pytest.mark.parametrize("response,reason", [
    ({"success": False, "result": {}}, "provider-envelope"),
    ({"success": True, "result": {}}, "provider-result"),
    ({"success": True, "result": {"response": "not-an-object"}}, "response-shape"),
])
def test_provider_envelope_failures_have_only_fixed_reason_codes(response, reason):
    async def handler(_request):
        return httpx.Response(200, json=response)
    with pytest.raises(WorkersAIError) as caught:
        run_with(handler)
    assert caught.value.category == "structured-output"
    assert caught.value.structured_reason == reason


@pytest.mark.parametrize("content,reason", [
    (b"not-json", "response-json"),
    (b"x" * (64 * 1024 + 1), "response-size"),
], ids=["invalid-json", "oversized"])
def test_invalid_or_oversized_provider_body_has_fixed_reason(content, reason):
    async def handler(_request):
        return httpx.Response(200, content=content)
    with pytest.raises(WorkersAIError) as caught:
        run_with(handler)
    assert caught.value.category == "structured-output"
    assert caught.value.structured_reason == reason


def test_oversized_provider_request_fails_before_network():
    async def handler(_request):
        raise AssertionError("network must not be reached")
    with patch.object(provider, "_request_body", return_value={"oversized": "x" * (128 * 1024)}):
        with pytest.raises(WorkersAIError) as caught:
            run_with(handler)
    assert caught.value.category == "request"
    assert caught.value.provider_status is None


def test_config_requires_exact_safe_formats():
    assert WorkersAIConfig.optional("a" * 32, "x" * 32)
    for account, token in [("A" * 32, "x" * 32), ("a" * 31, "x" * 32), ("a" * 32, "short"), ("a" * 32, "x" * 32 + "\n")]:
        assert WorkersAIConfig.optional(account, token) is None
