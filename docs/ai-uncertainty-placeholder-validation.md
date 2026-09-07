# Uncertainty placeholder validation

Implemented locally; not deployed or tested with another live model request.

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
