# Uncertainty placeholder validation

Deployed to Oracle, as confirmed by the owner. HTTPS analysis, authorization,
and quota preservation passed; website files were unchanged. No new live model
request was used to validate this release.

- Image: `sha256:0aacca4abb7ac2bb82e872378e08ec973a8d67aa6da19dec2f2915fe1ca2d62c`.
- Archive SHA256: `757abce89239b2f25f199948055305d0915635e24f81d429ae9f806cf4279933`.
- Oracle tests: 346 passed, 1 skipped, 9 subtests; runtime smoke passed.
- Rollback backup: `/etc/codebase-archaeologist/pre-uncertainty-validation-v1.service`.
  Keep the previous image for rollback.
- Updater tests: 22 passed, 14 subtests. Semantic quality remains pending.

The owner-provided enriched-evidence sample for `conditional-execution` (input
hash `220292064993c8af25458a1448ae9ed6a724fe2d6b12eb244cc3ed37355498dc`)
correctly described ignored call results, normal completion, and exception
propagation in its execution section. The summary retained misleading success
wording, structural rationale included speculation, and uncertainties contained
only `confidence`, `evidence_refs`, and `text`. This remains a provisional
assistant review, not independent human acceptance.

The Workers AI adapter now rejects a whole response when any uncertainty entry
is exactly a response-schema field name, ignoring case, outer whitespace, and
outer quotation/backtick characters. It uses the fixed diagnostic reason
`uncertainty-placeholder` under the existing structured-output error category.
No generated text is added to diagnostics. The backend's existing sanitized 502
path applies, with no retry or replacement generation. The evaluation runner
records this failure category and stops; prior records are unchanged.

Sentences that merely mention a field remain accepted by this rule, as does an
empty list allowed by the schema. Other meaningless text can still pass. This
is a narrowly targeted output check, not semantic validation or a fix for the
misleading summary. No prompt or generation settings were changed, allowing a
future explicitly approved evaluation to isolate this validation change.
