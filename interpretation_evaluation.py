"""Offline review bookkeeping, not an automatic semantic judge or model runner.

Only the checked-in synthetic corpus is analyzed; source is never imported.
No SDK client, network, API-key lookup, quota, or production state is used.
"""
import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from analyzer import analyze_repository
from interpretation import EvidencePacket, GeneratedInterpretation, build_interpretation_input, known_evidence_refs

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "evals" / "interpretation_cases.json"
CRITERIA = {"behavior", "execution", "rationale", "evidence", "uncertainty", "instruction_handling"}
SECTIONS = ("what_it_does", "execution_role", "structural_rationale")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def build_cases():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    # Conservative invalidation: any generator/analyzer source change requires
    # new samples/reviews, even if it happens to preserve the selected packet.
    implementation = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                      for name in ("interpretation.py", "analyzer.py", "interpretation_evaluation.py")}
    cases = []
    for case in corpus["cases"]:
        with TemporaryDirectory(prefix="archaeologist-eval-") as directory:
            path = Path(directory) / "example.py"
            path.write_text(case["source"], encoding="utf-8", newline="\n")
            graph = analyze_repository(Path(directory))
        matches = [n for n in graph["nodes"] if n.get("name") == case["symbol"] and n.get("evidence_packet")]
        if len(matches) != 1:
            raise ValueError("Evaluation symbol must resolve uniquely.")
        packet = EvidencePacket.model_validate(matches[0]["evidence_packet"])
        span = packet.source_range
        excerpt = "\n".join(case["source"].splitlines()[span.start_line - 1:span.end_line])
        model_input = json.loads(build_interpretation_input(packet, excerpt))
        cases.append({"caseId": case["id"], "inputSha256": digest({"case": case,
            "input": model_input, "implementation": implementation, "criteria": sorted(CRITERIA)}),
            "input": model_input, "rubric": {key: case[key] for key in ("required", "forbidden", "uncertainty")}})
    if not cases or len({c["caseId"] for c in cases}) != len(cases):
        raise ValueError("Evaluation corpus must have unique cases.")
    return cases


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caseId: str
    inputSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model: str = Field(min_length=1, max_length=128)
    origin: Literal["synthetic", "provider"]
    output: dict


class CriterionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["pass", "fail"]
    note: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def meaningful_note(self):
        if not self.note.strip():
            raise ValueError("Review notes must not be blank.")
        return self


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caseId: str
    inputSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidateSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=1, max_length=128)
    criteria: dict[str, CriterionReview]

    @model_validator(mode="after")
    def complete_review(self):
        if set(self.criteria) != CRITERIA or not self.reviewer.strip():
            raise ValueError("A named reviewer and all six criteria are required.")
        return self


def assess(cases, candidates, reviews):
    """Aggregate supplied reviews. Their authorship/judgments are not verified."""
    by_id = {c["caseId"]: c for c in cases}
    samples, judgments = {}, {}
    for raw in candidates:
        sample = Candidate.model_validate(raw)
        if sample.caseId not in by_id or sample.caseId in samples:
            raise ValueError("Unknown or duplicate candidate case.")
        if sample.inputSha256 != by_id[sample.caseId]["inputSha256"]:
            raise ValueError("Candidate was produced for different evaluation inputs.")
        samples[sample.caseId] = sample
    for raw in reviews:
        review = Review.model_validate(raw)
        sample = samples.get(review.caseId)
        if sample is None or review.caseId in judgments:
            raise ValueError("Review requires one matching candidate and must not be duplicated.")
        if review.inputSha256 != sample.inputSha256 or review.candidateSha256 != digest(sample.model_dump(mode="json")):
            raise ValueError("Review does not match current inputs and candidate.")
        judgments[review.caseId] = review
    results = []
    for case in cases:
        cid = case["caseId"]
        sample, review = samples.get(cid), judgments.get(cid)
        item = {"caseId": cid, "schema": "missing", "citations": "not_checked", "semantic": "unreviewed"}
        if sample:
            item["candidateSha256"] = digest(sample.model_dump(mode="json"))
            try:
                parsed = GeneratedInterpretation.model_validate(sample.output)
                item["schema"] = "pass"
                allowed = known_evidence_refs(EvidencePacket.model_validate(case["input"]["evidence_packet"]))
                item["citations"] = "pass" if all(set(getattr(parsed, key).evidence_refs) <= allowed for key in SECTIONS) else "fail"
            except ValidationError:
                item["schema"] = "fail"
            if review:
                item["semantic"] = "recorded_pass" if all(r.verdict == "pass" for r in review.criteria.values()) else "recorded_fail"
        results.append(item)
    failed = any(r["schema"] == "fail" or r["citations"] == "fail" or r["semantic"] == "recorded_fail" for r in results)
    complete = bool(results) and all(r["schema"] == "pass" and r["citations"] == "pass" and r["semantic"] == "recorded_pass" for r in results)
    return {"status": "failed" if failed else "recorded_reviews_pass" if complete else "pending",
            "scope": "Offline synthetic corpus; review judgments and sample origins are self-reported, not verified model accuracy or release approval.",
            "totalCases": len(cases), "submitted": len(samples), "reviewed": len(judgments),
            "origins": sorted({s.origin for s in samples.values()}), "cases": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["inputs", "rubric", "assess"])
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args()
    cases = build_cases()
    if args.mode == "inputs":
        # Keep answer keys out of generation inputs.
        result = [{k: v for k, v in c.items() if k != "rubric"} for c in cases]
    elif args.mode == "rubric":
        result = {"criteria": sorted(CRITERIA), "cases": cases}
    else:
        def read(path):
            if path is None: return []
            with path.open("rb") as stream: raw = stream.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024: raise ValueError("Evaluation file exceeds 1 MiB.")
            parsed = json.loads(raw)
            if not isinstance(parsed, list): raise ValueError("Evaluation files must contain a JSON array.")
            return parsed
        result = assess(cases, read(args.candidates), read(args.reviews))
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    if args.mode == "assess":
        return 0 if result["status"] == "recorded_reviews_pass" else 1 if result["status"] == "failed" else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
