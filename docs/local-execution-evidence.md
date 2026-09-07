# Local execution evidence: first bounded slice

Deployed to Oracle; the owner reported successful HTTPS analysis, authorization,
and quota preservation after the pinned upgrade. No model request has validated
its effect on explanation quality. Website files were unchanged.

## Deployment record

- Runtime image: `sha256:bffade606742f89ccee058aed950110e413380581db5631cf7f8c769b5f9937b`.
- Archive: `oracle-d6776fa807fd4fbd8a32e6cc728750cf/deep-service.tar`.
- Archive SHA256: `f482d9bef41b80250c28e36ac7f7d696cf4e521765de557495e8ff71b21e3765`.
- Owner-run Oracle suite: 332 passed, 1 skipped, 9 subtests; runtime smoke passed.
- Pinned updater: 22 offline tests and 14 subtests passed.
- Rollback backup: `/etc/codebase-archaeologist/pre-local-execution-v1.service`.
  Retain the previous credential-screening image for rollback.
- Includes the previously merged prompt revisions; semantic evaluation with
  the enriched evidence remains pending. No model calls occurred during upgrade.

## Evidence scope

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
