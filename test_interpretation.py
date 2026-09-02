from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from interpretation import (
    EvidencePacket,
    GeneratedInterpretation,
    InterpretationGroundingError,
    build_interpretation_input,
    generate_interpretation,
)


def packet() -> EvidencePacket:
    statement = {
        "text": "Defines run.",
        "classification": "fact",
        "confidence": 1,
        "provenance": "Python AST",
    }
    return EvidencePacket.model_validate({
        "version": "1",
        "node_id": "symbol:example.py:run",
        "source_range": {"path": "example.py", "start_line": 1, "end_line": 2},
        "summary": statement,
        "execution_role": statement,
        "structural_rationale": statement,
        "related_edge_ids": ["edge:1"],
        "flow_ids": ["flow:1"],
        "finding_ids": [],
        "pattern_ids": ["pattern:layered-architecture"],
        "claims": [{**statement, "id": "claim:1", "evidence_refs": ["symbol:example.py:run"]}],
    })


def generated(reference: str = "pattern:layered-architecture") -> GeneratedInterpretation:
    section = {"text": "Coordinates the selected operation.", "confidence": 0.8, "evidence_refs": [reference]}
    return GeneratedInterpretation.model_validate({
        "what_it_does": section,
        "execution_role": section,
        "structural_rationale": section,
        "uncertainties": ["Dynamic dispatch may add runtime behavior."],
    })


class FakeResponses:
    def __init__(self, output: GeneratedInterpretation) -> None:
        self.output = output
        self.arguments: dict[str, object] = {}

    def parse(self, **kwargs: object) -> object:
        self.arguments = kwargs
        return SimpleNamespace(output_parsed=self.output)


def test_generate_interpretation_uses_structured_responses_and_preserves_labels() -> None:
    responses = FakeResponses(generated())
    result = generate_interpretation(
        packet(),
        "def run():\n    pass",
        client=SimpleNamespace(responses=responses),
        model="test-model",
    )

    assert responses.arguments["text_format"] is GeneratedInterpretation
    assert responses.arguments["store"] is False
    assert result.classification == "interpretation"
    assert result.what_it_does.classification == "interpretation"
    assert result.what_it_does.evidence_refs == ["pattern:layered-architecture"]
    assert "test-model" in result.what_it_does.provenance


def test_generate_interpretation_rejects_unknown_evidence_reference() -> None:
    responses = FakeResponses(generated("invented:reference"))

    with pytest.raises(InterpretationGroundingError, match="unknown evidence"):
        generate_interpretation(packet(), "def run(): pass", client=SimpleNamespace(responses=responses))


def test_interpretation_input_contains_only_packet_and_bounded_source() -> None:
    payload = json.loads(build_interpretation_input(packet(), "x" * 20_000))

    assert set(payload) == {"evidence_packet", "source_excerpt"}
    assert payload["evidence_packet"]["node_id"] == "symbol:example.py:run"
    assert len(payload["source_excerpt"]) == 12_000
