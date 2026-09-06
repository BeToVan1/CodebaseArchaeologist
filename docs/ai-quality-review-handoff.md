# AI explanation quality: next evaluation

Status: prepared, not executed. No new provider requests are authorized or
submitted by this checklist. Human review may be done later.

## Offline preparation

Use the project's Python environment from the repository root:

```powershell
python interpretation_evaluation.py inputs
python interpretation_evaluation.py rubric
python interpretation_evaluation.py assess
```

The last command must report pending coverage until actual samples and reviews
exist (exit 2 is expected). See [the evaluation format and rubric](interpretation-evaluation.md).
Keep the answer-key rubric separate from model inputs. Do not fabricate provider
outputs or passing reviews to fill missing cases.

## Next approved model experiment

Before running, agree on an explicit maximum request count and verify the
free-only provider configuration. Use the existing fixed model and screened
Oracle request boundary; do not use the paid legacy local prototype. An automated
corpus runner through that boundary still needs implementation and testing.

Start with one selected symbol. Retain the exact input hash, pinned repository
commit, node ID, model, request settings, returned output, and screening/refusal
outcome in a controlled record. Do not retry automatically. Screening rejections,
provider failures, and unusable outputs remain visible outcomes, not discarded
samples. No credentials belong in an evaluation record or Git.

## Review when ready

Have a reviewer other than the sample author inspect code and each explanation
section against all six rubric criteria: behavior, execution, rationale,
evidence, uncertainty, and instruction handling. Record concrete supporting
source lines and contradictions. A valid citation ID is not proof that its
associated claim is supported. Model confidence is not measured accuracy.

Begin with the six synthetic cases, then expand the corpus with independently
selected, pinned real-repository examples and repeated generations. Do not call
one successful response a quality pass. Report missing reviews and disagreements
explicitly; defer any completion percentage until acceptance criteria are met.
