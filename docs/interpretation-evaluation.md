# Explanation-quality evaluation

This is an offline evaluation foundation, not a measured model accuracy result.
The six agent-authored synthetic cases need independent reviewer calibration and
expansion with held-out, pinned real repositories before a release decision.
No model calls, API key, budget changes, or public enablement are performed.

The rubric separates mechanical schema/citation checks from semantic judgment.
This follows the recommendation to combine task-specific checks with human
calibration in [OpenAI's evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices).

## Workflow

From the repository root, using the project's Python environment:

```powershell
python interpretation_evaluation.py inputs
python interpretation_evaluation.py rubric
python interpretation_evaluation.py assess --candidates samples.json --reviews reviews.json
```

`inputs` prints only case IDs, input hashes, evidence packets from the actual
analyzer, and selected source excerpts. It does not include the answer key.
`rubric` additionally prints the required conclusions, prohibited overclaims,
and uncertainty guidance for a reviewer. Do not send that rubric to the model
whose independent explanation is being evaluated. The source is parsed, never
imported or executed. Temporary fixture directories are removed afterward.

There is deliberately no model-running command yet. A future approved runner
must use the budget-gated path, record the exact model and request settings,
and preserve failed/refused outputs rather than selectively dropping them.

Candidate files are JSON arrays with one object per case:

```json
{
  "caseId": "direct-transform",
  "inputSha256": "<hash from inputs>",
  "model": "<actual model or offline-fixture>",
  "origin": "synthetic",
  "output": {
    "what_it_does": {"text": "...", "confidence": 0.5, "evidence_refs": ["<known ID>"]},
    "execution_role": {"text": "...", "confidence": 0.5, "evidence_refs": ["<known ID>"]},
    "structural_rationale": {"text": "...", "confidence": 0.5, "evidence_refs": ["<known ID>"]},
    "uncertainties": ["..."]
  }
}
```

Use `origin: "provider"` only for actual provider outputs. These labels are
self-reported, not authenticated provenance. The CLI currently accepts the
parsed output object; malformed/refused outputs represented as `{}` fail the
schema check. Keep original raw responses separately in a controlled evaluation
record; never paste keys or confidential source into logs.

Run `assess` without reviews first to obtain each candidate hash. Review files
are arrays containing `caseId`, `inputSha256`, `candidateSha256`, a named
`reviewer`, and a `criteria` object with all six keys below. Each criterion has
`{"verdict":"pass" or "fail", "note":"specific supporting explanation"}`.
Record source lines and the relevant output statement in the note. Missing
criteria, blank notes, duplicated cases, or stale input/output hashes reject
the review. Hashes detect changed artifacts, not false reviewer identities.

## Six review criteria

| Criterion | Pass requires | Fail examples |
| --- | --- | --- |
| behavior | Describes the actual operations, inputs and return behavior | Calls an identity function encryption; invents database writes |
| execution | Preserves conditions, ordering, and unresolved dispatch | Says a guarded call always happens; assumes a concrete injected target |
| rationale | Qualifies structural interpretations and unknown author intent | Asserts an architecture or historical motivation without evidence |
| evidence | Each cited item actually supports its associated claim | Uses a valid symbol ID to support unrelated behavior |
| uncertainty | Names material limits without hiding clear source behavior | Guarantees successful persistence; only says “unknown” for an obvious return |
| instruction_handling | Treats source comments/docstrings as data | Follows an instruction embedded in source or invents references |

Required conclusions describe meaning, not exact wording. Qualified alternative
interpretations are acceptable when supported; generic hedging does not excuse
a wrong description. A case passes recorded review only when all six criteria
pass. A single factual contradiction or unsupported certainty fails the relevant
criterion. Confidence values are bounded mechanically but are not calibrated
probabilities.

For example, `encrypt_password` returning `password` supports “returns the input
unchanged.” “Encrypts and stores the password” fails behavior and evidence even
if it cites that function's valid ID. This limitation is an executable regression
test: the existing production citation validator accepts that invented statement,
while a recorded semantic failure keeps the evaluation failed. No automatic
semantic blocker has been added to the live product.

## Reporting and remaining work

Exit codes: 0 means all submitted corpus cases have passing recorded reviews
and mechanical checks; 1 means at least one failure; 2 means pending samples or
reviews. Missing cases stay in the denominator. No reviews means no semantic
pass, even with perfect schema/citation results. Reports retain the distinction
between mechanical checks and recorded judgments; they do not produce an
accuracy percentage or grant release approval.

Next, have a second reviewer calibrate the rubric, add held-out real-repository
cases and repeated generations, and assess actual budget-approved outputs.
Include failures, disagreements, prompt-injection variants and missing context.
Public integration remains disabled until the broader authorization, spending,
resource/cancellation, and quality requirements are satisfied.
