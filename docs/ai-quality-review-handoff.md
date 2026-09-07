# AI explanation quality: next evaluation

Status: the first owner-approved batch stopped on its second case; the separately
approved revised batch recorded all six outputs with semantic issues remaining.
See [the first batch review](ai-six-case-batch-review.md) and
[the revised batch review](ai-revised-batch-review.md).
No further provider requests are authorized or submitted by this checklist.
Independent human review remains pending.

## Automated regression prerequisite

The Python regression workflow runs the root test suite on pull requests and
pushes to main using Linux/Python 3.13, read-only repository permissions, and no
model/deployment credentials. Dependencies are downloaded during setup; tests
use synthetic inputs and mocked provider transports. Owner-only updater tests
under `scripts/` remain separate because they require validated local image
archives. This workflow does not run live model evaluation, publish the website,
or upgrade Oracle. A green run is a regression result, not a semantic quality pass.

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
Oracle provider adapter; do not use the paid legacy local prototype. Synthetic
evaluation is a separate operator-run path, not the public report API.

The synthetic runner is now implemented locally with a no-network default:

```powershell
python interpretation_eval_runner.py --case direct-transform --max-requests 1
```

This prints a plan only. Execution requires Linux, `--execute`, explicit
`--confirm-free-only`, a new `--output` directory, and server-side Cloudflare
configuration. Do not execute until a specific request count is approved.
The confirmation is an operator attestation, not an automatic account billing
check. The cap is per run, not a global spending ledger. Do not repeatedly start
new runs to bypass the agreed allowance.

### Reproducible offline plan

Dry runs now include the same `plan` metadata written during execution: exact
input hashes for selected cases, prompt and provider-adapter hashes, model,
settings, and request cap. They do not read credentials or create the output
directory even if `--output` is supplied. This is a proposal, not authorization
or an enforced approval lock. Compare the plan again on the execution host;
adapter byte hashes can differ with line endings, so investigate any mismatch.

Prepare all six cases without calling the model:

```powershell
python interpretation_eval_runner.py --case direct-transform --case conditional-execution --case dependency-injection --case generic-base-candidate --case misleading-name --case source-instruction-injection --max-requests 6
```

Local checkpoint, 2026-09-07: this dry run completed with zero model requests.
The evaluator with no candidate/review files supplied correctly reported pending
for all six cases. This empty-input baseline does not erase or assess earlier
private samples. Thirty focused runner/evaluator tests passed with mocked
providers. At that checkpoint no execution budget had been approved; the later
approved batch is now closed as recorded in the linked review.

This runner uses only checked-in synthetic cases and the same screened provider
adapter as Oracle. It does not send a fabricated report to the hosted reference
API, and does not inherit that API's admission quotas. Records are created with
owner-only permissions and never overwritten. It stores exact synthetic inputs,
validated output, and sanitized failure categories; it stops on the first failure
without retrying. An interrupted attempt remains recorded as started, with an
unknown outcome. Failed cases remain missing from `candidates.json` and therefore
cannot pass corpus evaluation. Answer keys are never included in model input.

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
