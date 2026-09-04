"""Opt-in dispatcher; the local route is unconfigured by default.

All policy values are server-owned. Units must match the initialized ledger;
prices must cover the approved model/tier/context and token-count request.
No refund or retry is attempted, even when the provider outcome is uncertain.
"""
from dataclasses import dataclass
import re
from threading import BoundedSemaphore
import time
from types import SimpleNamespace

from interpretation import GeneratedInterpretation, generate_interpretation
from interpretation_budget import MAX_UNITS, reserve

_execution_slot = BoundedSemaphore(1)


class ExecutionUnavailable(RuntimeError):
    """Do not dispatch or retry; configuration, admission or token count failed."""


@dataclass(frozen=True)
class ExecutionPolicy:
    model: str
    input_units_per_million: int
    output_units_per_million: int
    preflight_units: int
    valid_until: int
    max_input_tokens: int = 32000
    max_output_tokens: int = 4096
    timeout_seconds: int = 30

    def validate(self, now):
        limits = ((self.input_units_per_million, MAX_UNITS), (self.output_units_per_million, MAX_UNITS),
                  (self.preflight_units, MAX_UNITS), (self.max_input_tokens, 32000),
                  (self.max_output_tokens, 8192), (self.timeout_seconds, 60))
        if (not isinstance(self.model, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", self.model)
                or any(type(value) is not int or not 1 <= value <= limit for value, limit in limits)
                or type(self.valid_until) is not int or self.valid_until <= now):
            raise ExecutionUnavailable("Interpretation execution policy is invalid or expired.")
        if self.reservation_units > MAX_UNITS:
            raise ExecutionUnavailable("Interpretation reservation exceeds the supported budget.")

    @property
    def reservation_units(self):
        # Integer arithmetic rounds each independent charge up; no cache discount
        # or optimistic estimate of generated output is used.
        def charge(tokens, rate):
            return (tokens * rate + 999999) // 1000000
        return (charge(self.max_input_tokens, self.input_units_per_million)
                + charge(self.max_output_tokens, self.output_units_per_million) + self.preflight_units)


def _strict_output_format():
    # This model has only required properties. Keep count and SDK parse schemas
    # identical; the mock-transport test checks the actual serialized requests.
    schema = GeneratedInterpretation.model_json_schema()
    def strict(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            for child in list(value.values()): strict(child)
        elif isinstance(value, list):
            for child in value: strict(child)
    strict(schema)
    return {"type": "json_schema", "name": "GeneratedInterpretation", "strict": True, "schema": schema}


class _ReservedResponses:
    def __init__(self, client, policy, ledger, clock):
        self.client, self.policy, self.ledger, self.clock = client, policy, ledger, clock
        self.used = False

    def parse(self, **request):
        if self.used:
            raise ExecutionUnavailable("Interpretation dispatch cannot be replayed.")
        self.used = True
        policy = self.policy
        policy.validate(self.clock())
        if (set(request) != {"model", "input", "text_format", "store"}
                or request["model"] != policy.model or request["text_format"] is not GeneratedInterpretation
                or request["store"] is not False):
            raise ExecutionUnavailable("Unexpected interpretation request shape.")
        reserve(self.ledger, units=policy.reservation_units)
        count = self.client.responses.input_tokens.count(model=policy.model, input=request["input"],
            text={"format": _strict_output_format()}, truncation="disabled")
        tokens = getattr(count, "input_tokens", None)
        if type(tokens) is not int or not 0 < tokens <= policy.max_input_tokens:
            raise ExecutionUnavailable("Interpretation input token count is invalid or exceeds policy.")
        policy.validate(self.clock())
        response = self.client.responses.parse(**request, max_output_tokens=policy.max_output_tokens,
            truncation="disabled", service_tier="default", background=False)
        if getattr(response, "status", None) != "completed":
            raise ExecutionUnavailable("Interpretation response did not complete.")
        return response


def generate_budgeted_interpretation(*, store, owner_key, report_id, node_id, policy,
                                     ledger, client, enabled=False, clock=time.time):
    """Resolve server evidence, reserve, count, and generate at most once.

The caller owns the configured SDK client's lifecycle and authenticated identity.
SDK timeouts bound network waits, not end-to-end duration or remote generation.
This function has no default client, API-key lookup, live price, or HTTP route.
"""
    if enabled is not True:
        raise ExecutionUnavailable("Budgeted interpretation is disabled.")
    policy.validate(clock())
    if not _execution_slot.acquire(blocking=False):
        raise ExecutionUnavailable("Interpretation execution is busy.")
    try:
        evidence = store.prepare(owner_key=owner_key, report_id=report_id, node_id=node_id)
        bounded_client = client.with_options(max_retries=0, timeout=policy.timeout_seconds,
                                             base_url="https://api.openai.com/v1")
        adapter = SimpleNamespace(responses=_ReservedResponses(bounded_client, policy, ledger, clock))
        return generate_interpretation(evidence.packet, evidence.source_excerpt, client=adapter, model=policy.model)
    finally:
        _execution_slot.release()
