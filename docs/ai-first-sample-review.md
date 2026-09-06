# First synthetic sample: provisional review

Source: owner-pasted provider output for `direct-transform`, input hash
`07c699f5ccfe7ffe4f7dc9d806e2fede3a78618d01054ecf34dd7eb9d062adf3`.
Model: `@cf/meta/llama-3.3-70b-instruct-fp8-fast`.
This is an assistant's provisional assessment, not an independent human review
or a corpus acceptance result. Original records remain on Oracle.

The behavior summary correctly described string stripping and lowercasing but
omitted the returned result and assumed string semantics without qualification.
The execution section said the role was not established, omitting the local
operation sequence that the excerpt does establish. Structural rationale avoided
inventing author intent. Uncertainty was too broad about the symbol's purpose.
All three confidence values were zero; their cause is not established and they
must not be treated as calibrated accuracy scores. This small case does not
establish source-instruction resistance or broader model quality.

Local prompt revision asks for observable return behavior, local order and
conditions separately from unknown callers, qualified runtime-type assumptions,
and specific uncertainty. No case-specific answer key is added. Evaluation plans
now retain system-prompt and provider-adapter hashes so subsequent runs can be
distinguished even when synthetic input hashes match.

Offline tests verify prompt wiring, unchanged request limits, and source/system
separation. They do not prove the model will follow the revised instructions.
The revised prompt is not deployed, and no repeat model request has been made.
Next comparison requires separate one-request approval and the same synthetic
case. Human review remains pending; do not replace the original sample.
