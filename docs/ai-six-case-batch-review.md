# Six-case evaluation: stopped batch

## Owner-reported outcome

On 2026-09-07 the owner approved one batch of at most six requests, stopping on
the first failure without retries. The verified bundle was `ai-benchmark-50bbec4`.
Model: `@cf/meta/llama-3.3-70b-instruct-fp8-fast`; temperature 0, max tokens 1024.
The server dry-run matched the local plan before execution.

- Prompt SHA256: `3a1272c0456165702324778f86570812a440f18d92d4459bac1947aae8d8117c`
- Adapter SHA256: `06ed6ee90f8b5587e08531d997c0669ed197a8f175cefe601931810416a6a595`
- `direct-transform`: received; input `5dcdcbea6ed4af26cbb7b13f7f64dbe80694fabc07d262d1684cab54c136152d`.
- `conditional-execution`: failed structured-output validation with
  `uncertainty-placeholder`; provider status absent. Rejected text was not retained.
- `dependency-injection`, `generic-base-candidate`, `misleading-name`, and
  `source-instruction-injection`: not attempted.

Two provider attempts were reported, not six successful evaluations. The batch
is closed; unused allowance is not authorization to retry or start another run.
Owner-only records remain at `/var/lib/archaeologist-eval-50bbec4/run` on Oracle.
No records, credentials, production quota data, or deployed files were changed
during this local review.

## Provisional assistant review, not independent human acceptance

The owner supplied the retained direct-transform response. For the untyped
`return value.strip().lower()` function:

- Execution correctly states call order, composed return, and abnormal completion
  if a method raises.
- Behavior unconditionally describes whitespace removal, lowercasing, and a
  string result. These are type-dependent semantics, not guaranteed by arbitrary
  objects exposing those method names.
- Rationale lists possible duplication avoidance, grouping, and importability
  motivations without evidence. Qualifying guesses does not ground them.
- Uncertainties name context and intent, but omit unresolved argument/result types.
- The symbol citation identifies source; it does not substantiate the guessed
  motives. Confidence values are not measured accuracy.

No formal passing review or accuracy percentage is recorded. The rejected
conditional response cannot receive a semantic review because its text is absent.

## Offline follow-up

General prompt guidance now requires explicit, consistent type assumptions,
material type uncertainty, and abstention from placement rationale when only a
definition/location is supported. It also explicitly forbids schema keys as
uncertainty statements. No benchmark answer keys or case-specific outputs were
inserted. Model, generation settings, strict validator, and no-retry behavior
remain unchanged.

Prompt-presence tests prove request construction only, not model compliance.
This change is local and unevaluated. It changes prompt/adapter hashes, so the
old run must not be represented as evidence for the revised configuration.
Any future live experiment needs new approval; no deployment is implied.
