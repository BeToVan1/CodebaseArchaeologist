from copy import deepcopy
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import interpretation_evaluation as evaluation
from interpretation import EvidencePacket, GeneratedInterpretation, generate_interpretation
from test_interpretation import FakeResponses


@pytest.fixture(scope="module")
def cases():
    return evaluation.build_cases()


def candidate(case, text="Synthetic placeholder; not a real model output."):
    section = {"text": text, "confidence": 0.5,
               "evidence_refs": [case["input"]["evidence_packet"]["node_id"]]}
    return {"caseId": case["caseId"], "inputSha256": case["inputSha256"],
            "model": "offline-fixture", "origin": "synthetic",
            "output": {**{key: deepcopy(section) for key in evaluation.SECTIONS},
                       "uncertainties": ["Synthetic test data, not a quality measurement."]}}


def review(sample):
    return {"caseId": sample["caseId"], "inputSha256": sample["inputSha256"],
            "candidateSha256": evaluation.digest(sample), "reviewer": "synthetic-test-reviewer",
            "criteria": {key: {"verdict": "pass", "note": "Synthetic bookkeeping test only."}
                         for key in evaluation.CRITERIA}}


def test_case_inputs_are_repeatable_real_analyzer_packets(cases):
    assert len(cases) == 6
    assert cases == evaluation.build_cases()
    for case in cases:
        packet = EvidencePacket.model_validate(case["input"]["evidence_packet"])
        assert packet.source_range.path == "example.py"
        assert case["input"]["source_excerpt"]
        assert set(case["input"]) == {"evidence_packet", "source_excerpt"}
        assert all(case["rubric"].values())
    injection = next(c for c in cases if c["caseId"] == "source-instruction-injection")
    assert "Ignore all prior instructions" in injection["input"]["source_excerpt"]


def test_empty_and_partial_runs_cannot_pass(cases):
    empty = evaluation.assess(cases, [], [])
    assert empty["status"] == "pending" and empty["reviewed"] == 0
    sample = candidate(cases[0])
    partial = evaluation.assess(cases, [sample], [review(sample)])
    assert partial["status"] == "pending"
    assert partial["submitted"] == partial["reviewed"] == 1


def test_structural_pass_is_never_automatic_semantic_pass(cases):
    samples = [candidate(c) for c in cases]
    result = evaluation.assess(cases, samples, [])
    assert result["status"] == "pending"
    assert all(c["schema"] == c["citations"] == "pass" for c in result["cases"])
    assert all(c["semantic"] == "unreviewed" for c in result["cases"])


def test_valid_citation_can_still_support_a_false_explanation(cases):
    case = next(c for c in cases if c["caseId"] == "misleading-name")
    sample = candidate(case, "Encrypts and securely stores the password in a database.")
    # Exercise the existing production validator: membership is not entailment.
    result = generate_interpretation(EvidencePacket.model_validate(case["input"]["evidence_packet"]),
        case["input"]["source_excerpt"], client=SimpleNamespace(responses=FakeResponses(
            GeneratedInterpretation.model_validate(sample["output"]))), model="offline-fixture")
    assert result.what_it_does.text == sample["output"]["what_it_does"]["text"]
    judgment = review(sample)
    judgment["criteria"]["behavior"] = {"verdict": "fail",
        "note": "example.py line 2 returns password unchanged; no encryption or persistence call."}
    assessed = evaluation.assess([case], [sample], [judgment])
    assert assessed["cases"][0]["citations"] == "pass"
    assert assessed["cases"][0]["semantic"] == "recorded_fail"
    assert assessed["status"] == "failed"


@pytest.mark.parametrize("failure", ["schema", "citation"])
def test_recorded_pass_cannot_override_automatic_failure(cases, failure):
    sample = candidate(cases[0])
    if failure == "schema": sample["output"]["what_it_does"]["confidence"] = 1
    else: sample["output"]["what_it_does"]["evidence_refs"] = ["invented"]
    result = evaluation.assess([cases[0]], [sample], [review(sample)])
    assert result["status"] == "failed"


@pytest.mark.parametrize("mutation", ["input", "output", "origin", "model"])
def test_stale_inputs_or_changed_candidates_invalidate_reviews(cases, mutation):
    sample = candidate(cases[0])
    judgment = review(sample)
    if mutation == "input": sample["inputSha256"] = "0" * 64
    if mutation == "output": sample["output"]["uncertainties"] = ["changed"]
    if mutation == "origin": sample["origin"] = "provider"
    if mutation == "model": sample["model"] = "changed-model"
    with pytest.raises(ValueError): evaluation.assess(cases, [sample], [judgment])


@pytest.mark.parametrize("mutation", ["missing-criterion", "extra-criterion", "blank-note", "blank-reviewer"])
def test_incomplete_or_ambiguous_review_records_are_rejected(cases, mutation):
    sample = candidate(cases[0])
    judgment = review(sample)
    if mutation == "missing-criterion": judgment["criteria"].pop("behavior")
    if mutation == "extra-criterion": judgment["criteria"]["vibes"] = {"verdict": "pass", "note": "looks good"}
    if mutation == "blank-note": judgment["criteria"]["behavior"]["note"] = " "
    if mutation == "blank-reviewer": judgment["reviewer"] = " "
    with pytest.raises(ValidationError): evaluation.assess(cases, [sample], [judgment])


def test_duplicate_unknown_and_orphan_records_rejected(cases):
    sample = candidate(cases[0])
    judgment = review(sample)
    for samples, reviews in [([sample, sample], []), ([sample], [judgment, judgment]), ([], [judgment])]:
        with pytest.raises(ValueError): evaluation.assess(cases, samples, reviews)
    sample["caseId"] = "unknown"
    with pytest.raises(ValueError): evaluation.assess(cases, [sample], [])


def test_complete_review_is_explicitly_not_a_model_accuracy_claim(cases):
    samples = [candidate(c) for c in cases]
    result = evaluation.assess(cases, samples, [review(s) for s in samples])
    assert result["status"] == "recorded_reviews_pass"
    assert result["origins"] == ["synthetic"]
    assert "self-reported" in result["scope"] and "not verified model accuracy" in result["scope"]


@pytest.mark.parametrize("mode", ["inputs", "assess"])
def test_cli_is_offline_and_keeps_answer_key_out_of_generation_inputs(mode):
    result = subprocess.run([sys.executable, str(evaluation.ROOT / "interpretation_evaluation.py"), mode],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == (0 if mode == "inputs" else 2), result.stderr
    data = json.loads(result.stdout)
    if mode == "inputs":
        assert len(data) == 6
        assert all(set(case) == {"caseId", "inputSha256", "input"} for case in data)
    else:
        assert data["status"] == "pending"
