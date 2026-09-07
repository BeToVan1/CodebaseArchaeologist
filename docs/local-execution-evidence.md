# Local execution evidence: first bounded slice

Implemented locally; not deployed. No model request has validated its effect
on explanation quality.

The analyzer recognizes an exact synchronous function-body pattern: an initial
parameter `is None` guard returning False, a standalone call, and a final True
return. An optional docstring is accepted. It emits three fact-classified claims
with source-line provenance and symbol references:

- None returns False before evaluating the call; it is not general validation.
- Otherwise the call result is ignored, and True follows normal completion.
  This is not evidence of successful persistence or other business outcomes.
- There is no handler in this body; an exception during call evaluation leaves
  the body rather than producing either return. Exception occurrence is unknown.

Claims describe the original body, not behavior introduced by decorators,
callers, runtime instrumentation, or an unknown delegated implementation. They
do not assert that a callable is invoked successfully or that its effects finish.

Async functions, generators, alternate comparisons, assignments of call results,
handlers, context managers, and other body shapes are omitted by this first
recognizer. Nested functions are analyzed separately. An omitted claim means
unsupported, not safe, exception-free, or untested. This is not a general control
flow or exception analysis engine.

The facts are attached to symbol evidence packets using the existing claims
schema and become available to the screened model request. No new UI panel is
added. Synthetic input hashes change because analyzer evidence changed; retain
old samples and generate new input hashes before another approved evaluation.
Tests establish extraction and transport into model input, not semantic model
accuracy. Human acceptance remains pending.
