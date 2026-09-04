# Acceptance testing — 2026-09-03

Status: early beta; core journeys work, but acceptance is not complete.
Tested public Sites version 15, source `3601f835ba4a6ba64f784714f5e9f4a1c2bade3b`,
with deep enabled. No application source or public configuration was changed
during this pass. Exactly one hosted analysis was submitted by this pass.

### Pinned OpenAI interpretation policy (superseded, never activated)

- Began the model implementation with `gpt-5.4-mini-2026-03-17`, a reproducible snapshot supporting Responses API Structured Outputs. The reviewed preset records the official 2026-09-04 rates as 750,000 input and 4,500,000 output microdollars per million tokens. It caps input at 32,000 tokens, output at 2,048 tokens, provider timeout at 30 seconds, and policy lifetime at seven days. This is a code/configuration boundary, not a guarantee that pricing remains current.
- The fail-closed loader requires exact explicit enablement, exact pinned model, future short-lived expiry, an existing separately initialized ledger path, and a positive operator-approved preflight allowance. Browser requests cannot change these values. It does not read a key, create an SDK client, initialize/increase a budget, or call the provider.
- Thirteen new tests cover the accepted configuration plus disabled/case-mismatched enablement, model aliases/substitution, expired/overlong/non-numeric validity, absent/NUL ledger paths, and invalid preflight amounts. The 58 focused execution/route/policy tests pass; full Python suite: 424 passed, 4 skipped, 9 subtests, 2 existing dependency warnings. Mock transport only; no API key or paid call.
- The secure local reference route is not yet constructed from this policy at startup, the public site remains deterministic, and semantic evaluation with independently reviewed provider outputs remains required before activation. No website, Oracle, quota, model key or public configuration changed.

The owner subsequently required a provider that cannot bill beyond a free allocation. ChatGPT Plus does not include API usage, so this OpenAI policy was removed before activation and never received a key, ledger, provider call, public route or deployment. Its generic offline execution tests remain useful for retry, citation and reservation behavior.

### Cloudflare Workers AI provider boundary (local, disabled)

- Selected `@cf/meta/llama-3.3-70b-instruct-fp8-fast`: an active Workers AI model with a 24,000-token context window and documented JSON Mode support. Cloudflare's Free plan provides 10,000 Neurons daily and rejects operations after that allocation; account plan verification remains a release gate.
- Added a provider-neutral interface and Workers AI adapter. It accepts only a server-prepared evidence packet and source excerpt, caps the source at 12,000 characters and total serialized input at 80 KiB, makes one non-streaming request with at most 1,024 output tokens and temperature zero, and never retries. Source text is explicitly untrusted data.
- Response validation is independent of JSON Mode: exact fields only, three non-empty <=1,200-character sections, confidence 0–0.85, 1–10 evidence references per section drawn from the supplied packet, and at most five bounded uncertainties. Malformed output, unknown citations and provider errors fail without a usable interpretation.
- Nine new adapter tests and all 121 JavaScript tests pass; TypeScript and the production build pass with existing Vite JSON-import and route-classification warnings. Mock binding only: no Cloudflare AI call, Neuron use, public route, AI binding, website publication, Oracle change or user-visible behavior.

### Hosted server-retained evidence boundary (local, not deployed)

- The isolated deep-analysis worker now analyzes only a commit-verified staged snapshot and emits a separate private evidence artifact with opaque filenames. The parent validates the manifest, commit, graph paths, counts, per-file sizes, total size, symlink status, and exact file lengths before retaining any source. Source never appears in the graph response.
- A successful analysis registers the graph and source under the server-derived network owner key and returns only a random 43-character reference and its 900-second lifetime in response headers. The store is process-local, bounded, loses all references on restart, and allows three live references per owner to match the hourly analysis allowance.
- `POST /api/evidence/prepare` requires the service bearer token, the same server-derived owner key, and an exact `{reportId,nodeId}` body. Wrong-owner, missing, and expired references are indistinguishable. Browser-supplied packets, excerpts, ranges, claims, and owner fields are rejected rather than merged.
- Focused evidence/service tests pass (87 passed, 3 Linux-only skipped); all root Python tests pass (413 passed, 4 skipped, 9 subtests) and all JavaScript tests pass (121). The Docker input allowlist includes every new runtime module. The final local Docker run could not start because the Codex sandbox account lacks permission to the Docker named pipe; this is an environment validation gap, not a test pass.
- The evidence-capable Oracle image was subsequently validated at 384 MiB and deployed as pinned image `sha256:cc2cd37fd541362533d940816a4a0af46ea9b16a46bb3a856ef86bf013e69555`. HTTPS analysis, authorization, quota preservation, opaque-reference creation, wrong-owner isolation, and exact source preparation passed. Backup: `/etc/codebase-archaeologist/pre-evidence-reference-v1.service`.

### Same-origin interpretation Worker bridge (local, disabled)

- Added `/api/interpret/deep`. It accepts only an exact `{reportId,nodeId}` same-origin request, derives the same owner key from Cloudflare's connecting address and the private Oracle token, and calls only the fixed Oracle evidence endpoint. Browser-supplied source, packets, endpoint, token, owner key, ranges, and extra fields are rejected before upstream access or inference.
- The route validates exact prepared-evidence fields, commit shape, selected node identity, a non-empty 12,000-character source cap, and an 80 KiB response cap before invoking the adapter once. It has one 25-second request deadline, no retry, sanitized error responses, and labels every generated section as model interpretation with provider provenance. The analysis proxy now requires and forwards Oracle's opaque 43-character reference and 900-second TTL headers without adding them to exported graph JSON.
- Four route tests cover fail-closed configuration, origin/input/network rejection, fixed authenticated retrieval plus one grounded provider call, expiry, malformed evidence, invalid model citations, sanitization, and no retries. All 125 JavaScript tests pass; TypeScript and the production build pass with the existing Vite JSON-import and route-classification warnings.
- No AI binding is declared by the current Sites manifest, `ARCHAEOLOGIST_INTERPRETATION_ENABLED` remains false, and the frontend does not retain or submit the runtime-only report reference yet. No provider call, Neuron use, Sites publication, public behavior, or Oracle change occurred in this slice. Next gate: confirm a supported hosted binding/account on Workers Free, then add runtime-only frontend reference handling and an explicitly activated interpretation panel.

## Observed browser results

| Check | Result | Evidence / limitation |
| --- | --- | --- |
| File-node selection | Pass | Cosmic Python `domain/model.py` opens its source and symbols. |
| Symbol selection | Pass with metadata issue | `Product.allocate` displays only lines 15–31, classified explanation and pinned GitHub range. Header metadata still describes the file (A-01). |
| Relationship navigation | Pass | `Product.allocate` -> `events.Allocated` opens lines 9–14 and its class relationships. |
| Patterns / risks | Pass for rendering | Three Cosmic Python patterns retain heuristic labels and evidence; nine risk cards show severity, confidence and source. Not a precision/recall evaluation of risks. |
| Unsupported flows | Pass | Cosmic Python has no recognized flows; UI explicitly states only FastAPI/APIRouter entrypoints are supported. |
| Fresh hosted analysis | Pass | Browser form -> `pallets/itsdangerous` -> 15 files, 144 symbols; commit `672971d66a2ef9f85151e53283113f33d642dabd`. Loading disables conflicting controls and exposes Cancel. Cancellation itself was not exercised. |
| File filtering | Pass | `signer.py` narrows to its node. Unmatched query shows an explicit empty result. |
| Local report import | Pass | Current analyzer's 3-file FastAPI fixture opens as 7 nodes with an imported/unverified notice and AI disabled. |
| Positive flow / ORM view | Pass with evidence-label issue | `GET /items` -> `repository.list_items` -> `models.ItemModel`; unresolved `select` remains visible. Model step opens table `items`, column `id`, lines 8–10. See A-02. |
| Invalid import preserves report | Pass | `{}` rejected with schema error; existing FastAPI graph remains installed. |
| Download report | Inconclusive | Button clicked; no page error logged, but browser download event timed out. No successful file download or browser export/import round trip is claimed. |
| Desktop layout | Partial | At requested test override 1440x1000, document client/scroll width both 1425 (no horizontal document overflow). Screenshot output did not expose the whole graph; do not treat this as full visual approval. Override reset. |
| Minimap | Not present | Current source contains no MiniMap component; no minimap was observed in the UI. This pass does not validate the earlier requested minimap feature. |

The browser was left on the restored bundled example, not the small test report.
Console inspection returned no captured error/warning entries at the checked points;
that does not prove there were no browser, download, or network errors.

## Known acceptance issues

- **A-01 — Selected-symbol metadata (medium):** select `Product.allocate`.
  The badge says method and the code range is correct, but Kind says Python file
  and Node ID is the containing file. Display selected-symbol metadata distinctly
  from file size/path metadata.
- **A-02 — Misleading execution evidence location (medium):** import the portable
  FastAPI fixture and open Execution flows. The model step says
  `models.py · evidence line 6`, but that read edge's actual evidence is
  `repository.py:6`; likewise the repository step shows its target filename
  beside a call-site line from `api.py`. Show the evidence's own path and line,
  separately from the destination symbol. The underlying report is correct.
- **A-03 — File-level explanation overreach (medium):** the fixture's root
  `models.py` is described as supporting configuration/package setup at 90%
  heuristic confidence, despite containing a proven SQLAlchemy model. Selecting
  ItemModel gives a correct framework-aware explanation. Prefer known symbol
  evidence for file explanations; use a clearly uncertain fallback otherwise.
- **A-04 — Current report versus next request (low):** initial bundled Cosmic
  Python deep graph is shown while request mode says Inventory. Label the current
  report's origin/tier separately from the next request mode to avoid confusion.
- **A-05 — Export verification:** owner/manual browser download confirmation is
  required before closing this acceptance item; do not infer a code defect solely
  from the automation timeout.

## Local fix verification — A-01 through A-04

The four UI issues above are fixed and published in public version 16, source
`246ad59a2966d00a6d976faed2d36ff47082d869`. The production build was first
checked locally, then rechecked on the public site with the same portable
FastAPI fixture:

- A-01: selecting the model flow step displays Kind `Python class`, Symbol
  `models.ItemModel`, lines `8–10`, and ID `symbol:models.py:models.ItemModel:8`.
  File size and containing path are shown separately.
- A-02: the repository step displays destination `repository.py`, evidence
  `api.py:9`; the model step displays destination `models.py`, evidence
  `repository.py:6`. The unresolved `select` remains visible.
- A-03: the whole `models.py` file reports its recorded SQLAlchemy model as a
  framework-backed fact. Author intent is explicitly not established. Generic
  unrecognized files no longer receive the high-confidence configuration claim.
- A-04: the current report changes from `Bundled example · deep report` to
  `Imported report · deep report`, independently of `Next analysis mode`.

All 53 JavaScript tests passed, including six presentation regressions; TypeScript
and the production build passed. No error/warning entries were captured during
the checked local browser interactions. These checks submitted no hosted analysis
jobs and changed no Oracle settings. Export (A-05), minimap and broader release
checks remain open. Existing build warnings about JSON import attributes and
route classification are unchanged.

Public deployment `appgdep_6a994406afc481918cdcc919aa467ef3` succeeded with
unchanged environment revision 3. All 63 archived files matched the validated
build/hosting metadata before saving. Live checks confirmed A-01 through A-04,
the deep-mode option remained available, and the browser was restored to the
bundled Cosmic Python map. No hosted analysis jobs were submitted in this
publication pass; export and backend lifecycle behavior were not retested.

## Repeatable semantic regression checks

`tests/architecture-acceptance.test.mjs` adds two offline checks. It reconstructs
only the embedded Python sources of the pinned Cosmic Python example into an
isolated temporary directory, runs the **current analyzer**, and checks documented
repository/UoW boundaries, source-visible inheritance, and domain import direction.
Copied repository code and its dependencies are never executed. The second test
checks an exact route -> function -> ORM-model path and retained unresolved work
in the existing synthetic FastAPI fixture. Temporary reconstructed sources are
removed after the test; neither the public fixture nor the live ledger is changed.

Architecture intent references: the project's own
[Repository chapter](https://www.cosmicpython.com/book/chapter_02_repository.html)
and [Unit of Work chapter](https://www.cosmicpython.com/book/chapter_06_uow.html).
These document the abstractions; they are not a line-by-line oracle for the later
snapshot `14c84797ffa77255d53cf1a02fe6aafda2b68aeb`. Exact inheritance/import
expectations were checked against the snapshot's embedded source. This is a
small semantic regression set, not a broad accuracy benchmark.

Validation in this pass:

- 47 JavaScript tests passed, including the two new acceptance tests.
- 110 Python tests passed, 4 Windows/platform skips, 9 subtests; two existing
  dependency deprecation warnings. This is separate from earlier owner-reported
  Oracle validation (113 passed, 1 skipped); do not add overlapping counts.
- Live end-to-end deployment, unauthorized rejection, invalid input, and network
  separation were verified in preceding turns. No quota reset or secret exposure.

## Still required before a finished release

### Analyzer false-positive follow-up (local only)

Ten adversarial cases in `test_architecture_acceptance.py` cover empty
repository/Unit-of-Work naming, test-only and mixed production/test dependency
injection, framework lookalikes, cyclic versus acyclic imports, and the large
callable threshold. Three cases failed before the fix: both naming-only
boundaries and test-only dependency injection. All ten now pass.

The detector now requires resolved relationships for repository/Unit-of-Work
heuristics and uses only production-to-production relationships in architecture
patterns. Test relationships remain in the complete graph. Production/test
classification still uses the existing top-level `tests/` convention; this is
not comprehensive test discovery. Naming plus relationships remains a heuristic,
not proof of author intent or transaction semantics.

Validation: 120 Python tests passed, 4 platform skips, 9 subtests, with two existing
dependency warnings; all 53 JavaScript tests passed, including the pinned Cosmic
Python boundary check. The explicit Docker build-context allowlist now includes
the new test file. Docker/Oracle validation and backend rollout are still pending.
Neither the live analyzer nor the bundled example was regenerated or changed.
These synthetic counterexamples improve regression coverage, but are not the
broader independently reviewed real-repository benchmark described below.

Owner-reported Oracle follow-up: bundle `oracle-d795c0f209b842c8978a676e419ae111`
passed 123 tests (1 skipped, 2 existing warnings, 9 subtests) and the actual
runtime smoke test returned 159 nodes / 298 edges for `pallets/itsdangerous`, with
authorization and invalid-input checks passing. Archive SHA256 is
`32fe7e84e012dcd59b865cc6b50e6694bdf3629e6b86ec0ee0ff0b081268aa65`.
Oracle reported image ID
`sha256:7f4021648d5af75be41a5c7044c0fe5c120145aa3f9d54c38676abd95548a5bf`,
also verified against the archive's OCI index/manifest/config chain locally.

The owner approved backend deployment. `scripts/upgrade_oracle_patterns.py`
and its adjacent `upgrade_oracle_quota.py` helper prepare an image-only update;
25 offline updater tests pass. Default mode is read-only preflight. `--apply`
backs up the quota-enabled unit separately, compares the existing ledger across
replacement, submits one HTTPS analysis (no retry), and attempts rollback on
verification failure. It never initializes, clears or replaces quota storage.
Concurrent live admissions can make the exact fingerprint check fail
conservatively; that must not be addressed by resetting quota. Actual deployment
and public-path confirmation have now completed: the owner reported successful
image-only replacement, HTTPS analysis, authorization and quota preservation.
The quota-enabled rollback unit remains at
`/etc/codebase-archaeologist/pre-pattern-accuracy-v1.service`.

### Post-upgrade public browser check

Exactly one fresh Deep analysis was submitted through the public website for
`pallets/itsdangerous`, commit `672971d66a2ef9f85151e53283113f33d642dabd`.
The result displayed 15 files, 144 symbols, 32 internal imports, 122 symbol
relationships, 0 patterns, 0 flows and 3 risk findings. The current-report label
correctly changed to `Analysis result · deep report`. Loading disabled conflicting
controls and offered Cancel (not exercised).

Selecting `BadSignature` opened its class ID, lines 22–33, docstring explanation
and commit-pinned source link. Following `extends` opened `BadData`, lines 7–19.
Risk cards displayed source ranges, heuristic classification and qualified
remediation guidance. No error/warning entries were captured at the checked
points. The browser was left on this fresh analysis. Export, cancellation,
concurrency and mobile layout were not tested in this pass.

**A-06 — Symbol-level explanation confidence (open):** `BadSignature` still
receives the generic role “Provides reusable behavior or configuration to other
repository modules” at 90% heuristic confidence based on its path. Its statement
that intent is not proven also carries 72% interpretation confidence. A-03 fixed
file explanations only; symbol evidence packets need the same conservative
treatment of unknown roles and intent. Navigation and source-range checks pass,
but this is not acceptance of the semantic explanation quality.

A-06 local fix: five new regression cases initially failed and now pass.
Unrecognized symbol roles explicitly remain unknown at zero confidence;
recognized path conventions use qualified wording at 0.6 heuristic confidence.
Framework-backed role facts retain their existing 0.98 confidence. All static
author-intent rationales now explicitly state intent is not established and use
zero confidence, even for framework symbols. The role claim and packet section
share identical text, classification, confidence and provenance. These confidence
values are configured evidence weights, not empirically calibrated probabilities.
Validation: 125 Python tests passed, 4 skipped, 9 subtests and two existing
dependency warnings; 53 JavaScript tests passed. The existing Docker allowlist
already includes the updated regression test file. New Docker/Oracle validation
and rollout are pending; existing reports and the live service are unchanged.

A-06 Oracle follow-up: owner reported 128 passed, 1 skipped, 9 subtests and two
existing warnings, plus a successful actual-runtime smoke test (159 nodes, 298
edges, authorization and invalid-input rejection). Bundle
`oracle-c0267f400f9440a0bf208c1a57e8b3a6` has SHA256
`f1a1ba42e9b02da3a2734a0acc90d4c9347b3a4e8f5c77ee5f4d32e20a86f4c9`.
Its OCI image pin, independently checked against the archive and owner output, is
`sha256:e9cb3a10b15461e67c27d63fef9b8004e26cd6c82fb4b518d9e10bed0e78fe31`.
The owner approved deployment. `scripts/upgrade_oracle_confidence.py` reuses the
image-only updater with fixed old/new pins and a separate
`/etc/codebase-archaeologist/pre-symbol-confidence-v1.service` backup. All 36
updater tests passed across the release entrypoint and both helper suites.
The owner reported successful deployment, HTTPS analysis, authorization and
quota preservation. Post-deployment public verification also passed: exactly
one fresh Deep analysis of `pallets/itsdangerous` returned 15 files and 144 symbols
at commit `672971d66a2ef9f85151e53283113f33d642dabd`. Selecting `BadSignature`
showed the unknown role at 0% heuristic confidence and unestablished author
intent at 0% interpretation confidence, with a matching role claim. Source
range 22–33, commit-pinned link and inheritance navigation to `BadData` (7–19)
remained correct. No error/warning entries were captured at the checked points.
A-06 is closed for this tested fresh-report path. Existing reports and the
bundled example retain their older evidence packets. Browser export verification,
broader accuracy calibration and the other release checks remain open.

### Remaining release checks

### Pinned real-repository benchmark and generic inheritance fix (local)

`scripts/benchmark_pinned_repositories.py` adds an opt-in, offline benchmark for
clean checkouts of two public libraries. It verifies exact commits before parsing
and never installs dependencies or executes their repository code. Checkouts were
downloaded into ignored `artifacts/benchmark-20260903-{itsdangerous,click}` folders.
They are retained for repeat runs, not embedded in the application or test suite.

| Repository | Pinned commit | Selected checks after fix | Nodes / edges | Risk findings |
| --- | --- | --- | --- | --- |
| pallets/itsdangerous | `672971d66a2ef9f85151e53283113f33d642dabd` | 16 passed | 159 / 302 | 3 |
| pallets/click | `36baa15ff831b939a22bc527cd76ce653ef6f66d` | 14 passed | 2028 / 3394 | 73 |

Expectations were selected before observing benchmark results from direct source
inspection and primary documentation: ItsDangerous
[serialization](https://itsdangerous.palletsprojects.com/en/stable/serializer/)
and [exceptions](https://itsdangerous.palletsprojects.com/en/stable/exceptions/),
and Click's [commands/groups](https://click.palletsprojects.com/en/stable/commands-and-groups/).
Documentation explains the abstractions; pinned source is the oracle for exact
inheritance and imports. Both are libraries, not evidence of broad framework coverage.

The initial pass missed two selected generic inheritance relationships:
`TimedSerializer(Serializer[_TSerialized])` and
`URLSafeTimedSerializer(..., TimedSerializer[str])`, directly visible in the
pinned ItsDangerous [timed.py](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/src/itsdangerous/timed.py#L170)
and [url_safe.py](https://github.com/pallets/itsdangerous/blob/672971d66a2ef9f85151e53283113f33d642dabd/src/itsdangerous/url_safe.py#L79).
The analyzer previously handled only dotted base expressions. It now resolves the
class beneath a subscript, retains the full base expression in evidence, and caps
confidence at 0.85 with `ast-parameterized-base-candidate` provenance. This is not
a proven runtime MRO: `__class_getitem__`/`__mro_entries__` can customize it.
Unresolved parameterized bases do not use the repo-unique-name fallback, and
factory-call bases remain unsupported. The new candidate edges do not satisfy
the existing >=0.9 inheritance threshold for declarative ORM propagation.

Three regression cases cover imported aliases/full evidence, unresolved external
bases with local name collisions, and factory bases. The positive case failed
before the fix. All 128 Python tests passed (4 platform skips, 9 subtests, 2
existing dependency warnings), and all 65 JavaScript tests passed afterward.

The benchmark independently parses package-source callable ranges and compares
all >=80-line callables with the large-symbol findings: 1 in ItsDangerous and 15
in Click, with no missing or unexpected ranges. It also checks sampled imports
and inheritance, absence of unsupported FastAPI/ORM/flow and repository/UoW claims,
heuristic risk classification, and zero confidence for unestablished author intent.
This verifies structural predicates, not whether all 76 risk findings are useful
or correct maintenance advice. No precision/recall or calibrated accuracy claim
is justified; another human review and more varied projects remain necessary.

Repeat with `python scripts/benchmark_pinned_repositories.py --itsdangerous
artifacts/benchmark-20260903-itsdangerous --click artifacts/benchmark-20260903-click`.
The command exits nonzero if any selected expectation fails or a checkout differs
from its pin. Docker/Oracle validation and deployment of this fix are pending.
No hosted jobs, public website changes, or live backend changes occurred.

Generic-inheritance Oracle validation follow-up: the owner reported 131 passed,
1 skipped, 2 existing warnings and 9 subtests, plus the runtime smoke PASS with
159 nodes / 302 edges, authorization enforced and invalid input rejected. Bundle
`oracle-b689f627222f437391c0a8199ae10291` has archive SHA256
`5949ee977dec46cf6815f126b87a370421d20e1ec47644b10d1684b674ccdaca`;
all 27 manifest source hashes match the checkout. The owner-reported image pin
`sha256:90a65edb90bdbeae100ed0aee294cd7549f02d6bb722ef58523fc08002e530da`
was verified through the archive OCI index/platform/config chain as Linux amd64,
user `10001:10001`.

The owner approved deployment. `scripts/upgrade_oracle_inheritance.py` reuses
the image-only updater with the current symbol-confidence image as its old pin
and this validated image as its new pin. Its separate rollback backup is
`/etc/codebase-archaeologist/pre-generic-inheritance-v1.service`. All 47 updater
tests pass across the four release/helper suites. Default invocation is read-only;
`--apply` changes only the image, preserves quota/token settings, attempts rollback
on verification failure, and submits one HTTPS analysis without automatic retry.
The owner must run the scripts on Oracle; actual rollout confirmation is pending.

Owner subsequently reported successful generic-inheritance rollout, HTTPS analysis,
authorization and quota preservation. The rollback backup was retained. One fresh
public-browser Deep analysis then returned ItsDangerous at the pinned commit with
15 files, 144 symbols, 32 imports and 126 symbol relationships. TimedSerializer
showed the new 85% inheritance candidate and the original parameterized source at
line 170; following its outgoing link opened Serializer at lines 40–404.

**A-08 — Candidate relationship claims mislabeled as facts:** this browser check
found that the evidence packet called the 85% generic inheritance edge a fact,
despite candidate provenance. The relationship builder was correct; the claim
builder classified every non-dispatch edge as fact. This is a trust-label defect,
not a successful uncertainty-label acceptance check.

Local correction: dispatch and sub-0.9-confidence relationship claims now use
heuristic classification and explicit `Candidate relationship:` wording. Recorded
relationship-count text acknowledges candidates. Direct inheritance remains a
fact. The generic-base regression failed before this change and passes afterward;
a direct-inheritance case guards against weakening exact relationships. Both
real-repository benchmarks now also check candidate-claim classifications.
All 129 Python tests passed (4 skipped, 9 subtests, 2 existing warnings), all 65
JavaScript tests passed, and all 32 selected benchmark checks passed. This local
correction still requires container/Oracle validation and approved rollout.
Only one public analysis was submitted in this verification pass. Existing reports
and the deployed analyzer still retain the old candidate-claim labeling.

A-08 deployment preparation: owner reported Oracle validation of bundle
`oracle-cb1f3067c7534355bb3323e7f10f1b30`: 132 passed, 1 skipped, 2 warnings,
9 subtests, and runtime smoke PASS (159 nodes / 302 edges, authorization and
invalid-input rejection). Archive SHA256 is
`392b97c2b15141bce76ae192c89a858aed8a5c0d95cc8413d965a3ae76e4011e`.
The owner-approved replacement image
`sha256:08c6b31f4c9f2b69a0f59b80c892c9fb07d9ee385ebdccc6d1fc92e398bd5777`
matches the archive's verified OCI index/platform/config chain (Linux amd64,
user 10001:10001). `scripts/upgrade_oracle_candidate_claims.py` pins the current
generic-inheritance image as its old version and uses the separate backup
`/etc/codebase-archaeologist/pre-candidate-claims-v1.service`. All 58 updater
safety tests passed across five suites. Actual owner-run rollout and a fresh
public-report check are pending. No website files or live configuration changed
during this preparation.

A-08 rollout and public verification completed: owner reported the image update
passed HTTPS analysis, authorization and quota preservation, retaining
`/etc/codebase-archaeologist/pre-candidate-claims-v1.service`. Exactly one fresh
Deep analysis through the public website returned ItsDangerous at commit
`672971d66a2ef9f85151e53283113f33d642dabd`, with 15 files / 144 symbols and
126 symbol relationships. TimedSerializer now displays `Candidate relationship:
extends itsdangerous.serializer.Serializer.` as a heuristic at 85% confidence,
with parameterized-base provenance at `timed.py:170` and its pinned source link.
Direct inheritance BadSignature -> BadData remains a fact at 100% confidence.
Following the candidate edge opens Serializer at lines 40–404. No captured
console errors were returned at the checked point. A-08 is closed for this
fresh-report path; existing imported/bundled reports are not rewritten. The
browser remains on the fresh report. No website deployment or settings changes
were needed for this verification.

### A-09 — Hotspot count inflation (local correction)

Review of the pinned Click report found production high-fan-in findings whose
recorded callers were entirely under `tests/` (for example Command and Option),
and repeated call sites counted as multiple callers. HelpFormatter.write had 19
recorded incoming relationships from only five distinct caller symbols. These
counts did not match the intended many-callers/many-collaborators interpretation.

Hotspot scoring now deduplicates neighbor symbol IDs. For production symbols it
excludes neighbors under the existing top-level `tests/` convention; findings on
test symbols can still count repository neighbors. Full graph edges are retained.
Summaries explicitly state distinct non-test/repository neighbors, provenance
notes the test convention and possible candidate contributions, and
`related_node_ids` contains exactly the scored neighbors. Thresholds and heuristic
classification are unchanged. Missing static calls, alternate test layouts, and
uncertain call targets remain limitations; this is not runtime coverage or a
calibrated maintenance-risk probability.

Six regressions cover repeated calls, test-only callers, distinct production
callers and collaborators, mixed test/production callers, and retained test-symbol
hotspots. The first four failed before correction. All 135 Python tests passed
(4 platform skips, 9 subtests, 2 existing warnings) and all 65 JavaScript tests
passed. Both pinned benchmarks passed all 32 selected checks. Graph size stayed
159/302 for ItsDangerous and 2028/3394 for Click; risk counts changed from 3 to 2
and 73 to 29 respectively. Fewer findings do not establish higher precision or
absence of defects. No external repository code was executed and no hosted
analysis was submitted. Container validation and approved Oracle rollout remain
pending; the live service and existing reports are unchanged.

A-09 rollout preparation: the owner reported Oracle validation of bundle
`oracle-ffcf743f3651412092fa68d8a822851b`: 138 passed, 1 skipped, 2 warnings,
9 subtests, plus runtime smoke PASS with 159 nodes / 302 edges, authorization
enforced and invalid input rejected. Archive SHA256 is
`43ec153ee23471930e845f6e325160dc86eff4563434f8fd703a07705ff3198a`.
The owner-reported replacement image
`sha256:ffb55b2b037b558f3d23a010a555b42ad4d6c89fcbffdf6d5bed57977674f080`
was verified against the archive's OCI index/platform/config chain as Linux amd64
and user 10001:10001. The owner approved deployment.
`scripts/upgrade_oracle_hotspots.py` pins the current candidate-claims image as
its old version and uses `/etc/codebase-archaeologist/pre-hotspot-scoring-v1.service`
as its separate rollback backup. All 69 updater tests passed across six suites.
Default invocation is read-only preflight; apply preserves the token and quota
mount, submits one HTTPS analysis without retry, and attempts rollback if checks
fail. Owner-run application and post-rollout verification are still pending.

A-09 rollout and public verification completed: the owner reported successful
image-only upgrade, HTTPS analysis, authorization and quota preservation, retaining
`/etc/codebase-archaeologist/pre-hotspot-scoring-v1.service`. Exactly one fresh
public Deep analysis returned ItsDangerous at the pinned commit with 15 files,
144 symbols, 32 imports, 126 symbol relationships and 2 risk findings. The risk
view now describes want_bytes as having 16 distinct non-test caller symbols and
explicitly notes the tests/ exclusion and possible candidate contribution. The
remaining large-callable finding is TimestampSigner.unsign, 87 lines (72–158).
Both remain heuristic findings; remediation explicitly says this is a review
prompt, not a proven defect or automatic refactor. Open source evidence selects
want_bytes at encoding.py lines 11–17. No captured browser errors were returned
at the checked point. A-09 is closed for this fresh public-report path. Broader
real-repository risk usefulness/calibration and alternate test layouts remain
limitations. No public settings or website files changed during verification.

### Local optional-LLM input boundary preparation

The existing interpretation endpoint remains local-only; it was not added to
Oracle or Sites. Evidence packets previously had no overall size limit and
accepted unknown versions and reversed source ranges. They now require version
1, ordered ranges, and at most 64 KiB of canonical UTF-8 JSON. Direct function
calls revalidate packets before provider setup/calls; oversized packets are
rejected, not silently trimmed. Source excerpt truncation remains the existing
12,000-character limit. This is a provider-input bound after parsing, not an
HTTP request-body/memory admission limit.

Six new tests cover those three API rejection cases, mutation after validation,
multibyte byte-budget enforcement, and missing structured output. All 141 Python
tests passed (4 skipped, 9 subtests, 2 existing warnings), as did all 65 JavaScript
tests. All 144 ItsDangerous and 1,949 Click symbol packets at the benchmark pins
passed the new validation. Tests used mocked responses only: no API key creation,
model calls, billable usage or public changes. Oracle's runtime image excludes
interpretation.py and api.py, so this change needs no Oracle deployment.

The [official Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs)
informed the continued separation between output schema validation and semantic
grounding. Citation membership does not prove the cited material supports a claim;
caller-supplied evidence is not authenticated source truth. Before public LLM
enablement, server-owned evidence retrieval, request admission/body limits,
timeouts/cancellation, output budgets, durable spending limits, and real-model
quality/adversarial evaluation are still required. Model/provider settings were
not changed in this pass.

### Internal server-snapshot evidence preparation (not connected to HTTP)

`interpretation_evidence.py` prepares a symbol packet and exact source excerpt
from an already server-owned deep report and captured source mapping. It checks
the expected 40-character commit against the report, unique symbol selection,
packet/node path and range agreement, repository-relative paths, reference
membership in the corresponding graph collections, UTF-8 source availability,
physical source line bounds, and file/excerpt limits. It performs no filesystem,
network, repository execution, or model calls. The prepared packet is retained
as immutable JSON and returned as a fresh model to avoid caller mutation.

This is an internal consistency helper, not an authenticated report store or an
HTTP security boundary. Its caller MUST supply graph and source bytes captured
by the same trusted analysis job, enforce snapshot isolation and ownership, and
resolve the Git commit itself. Comparing commit metadata does not prove arbitrary
source bytes belong to that commit. Imported/browser-provided reports must never
be registered as trusted snapshots. The existing local `/api/interpret` still
accepts caller-supplied evidence; it has NOT been connected to this helper.

All 171 root Python tests passed (4 skipped, 9 subtests, 2 existing warnings),
plus all 65 JavaScript tests. Coverage includes rejection cases, immutable-copy
behavior, Unicode physical-line handling, mocked interpretation, and the actual
analyzer's portable-report fixture. On the retained benchmark checkouts, 143 of
144 ItsDangerous symbols and 1,943 of 1,949 Click symbols prepared successfully;
the remaining seven exceed the 12,000-character excerpt limit and are rejected
without silent truncation. These are compatibility checks, not semantic accuracy
or proof of source authenticity. No paid model calls or public changes occurred.

The OpenAI Docs guidance above continues to inform the distinction between
structural validation and factual support. This internal module is not included
in the deployed runtime or wired into the website. Next: bounded server-owned
snapshot capture/storage and ownership, reference-only endpoint integration,
then admission/time/output limits and durable spending controls before any
hosted LLM enablement. Public website and Oracle deployment remain unchanged.

### Bounded internal snapshot store (not connected to HTTP)

`evidence_store.py` adds a process-local store behind the preparation helper.
It retains a detached serialized graph plus immutable captured source bytes,
issues random 256-bit report references, and checks a server-established owner
key before resolving or discarding a report. Wrong-owner, missing, and expired
references share one unavailable error. Reads do not extend expiration. Restart
invalidates references; there is no fallback to browser-supplied data.

Defaults are eight snapshots globally, two per owner, 4 MiB serialized payload
per snapshot, 16 MiB total retained payload, and a 15-minute lifetime. Source
maps are additionally bounded by file count, path length, and the preparation
helper's 1 MiB file limit. Expired entries are purged on the next store operation;
there is no background timer or secure-erasure claim. Active reports are not
evicted to admit new ones. Lock-protected admission prevents concurrent writers
from oversubscribing the limits. These byte counters are NOT a process RSS cap:
Python object overhead, source analysis inputs, deserialized graphs, temporary
serialization buffers, and returned prepared evidence also use memory.

All 194 root Python tests passed (4 skipped, 9 subtests, 2 existing warnings) and
all 65 JavaScript tests passed. New tests exercise ownership isolation, detached
copies, fixed expiry, capacity and per-owner bounds, byte accounting including
Unicode, discard, restart, concurrent registration, cross-report selection, and
the actual analyzer's portable-report fixture. Read-only checks verified both
retained benchmark Git pins and clean working trees before analysis. ItsDangerous
fit the defaults (920,449 retained payload bytes) and resolved selected source.
Click exceeded the 4 MiB default and was rejected with zero retained entries or
bytes. Larger-report storage is therefore an explicit current limitation; no
Oracle memory limit or deployment setting was increased to accommodate it.

Registration remains internal-only and requires graph/source bytes from one
trusted server analysis job. Owner keys must come from authenticated server
context, not request JSON or freely supplied headers. The store checks ownership
but does not establish authentication, verify source capture against Git, or
provide durable/multi-process storage. No endpoint, source-capture pipeline,
frontend control, paid model call, or deployment was added in this pass. Existing
local `/api/interpret` behavior is unchanged. Next: same-job source capture and
reference-only endpoint integration with an established caller identity; retain
the spending/admission gates before hosted LLM enablement. Sites configuration
and public publishing workflow were preserved.

### Commit-verified source capture and internal store bridge

`snapshot_capture.py` now resolves the server-owned checkout's HEAD commit and
reads its Git tree. For each included regular Python file it verifies the exact
checkout bytes against that commit's Git blob object ID and declared size. It
then stages only those verified bytes in a fresh private temporary source tree,
runs the existing analyzer on that tree, and returns that same source mapping
with the pinned report. `analyze_and_store_snapshot` joins capture, analysis, and
owner-scoped registration without accepting a caller-supplied graph or excerpt.
The temporary tree is removed on success and parser failure; no repository code
is executed. This connects the internal capture/store/preparation path, not HTTP.

Modified or missing committed Python files fail closed, as do newline/filter
transformations that alter blob bytes. Untracked files, ignored directories,
symlinks, and submodules are excluded and that scope is recorded as a report
limitation. Canonical path checks reject traversal, Windows reserved names,
trailing-dot/space aliases, and case-colliding directory prefixes before staging.
Limits are 2,000 included Python files, 1 MiB per file and 8 MiB captured source.
Git inspection uses a sanitized environment, no replacement objects, no hooks
or credential helpers, and a ten-second timeout per command. Its 8 MiB stdout
limit is checked after subprocess capture, NOT a streaming/process memory cap.
The caller still needs job admission, process memory/deadline/cancellation
controls, and a trusted checkout with server-controlled Git metadata.

All 213 root Python tests passed (4 skipped, 9 subtests, 2 existing warnings),
and all 65 JavaScript tests passed. Nineteen new cases cover real disposable Git
repositories, capture/store/source selection, source mismatch, exclusions,
staging cleanup, original-checkout mutation after capture, limits, and unsafe
paths. A fixture with a top-level exception is parsed without executing it.
Read-only capture on the retained benchmark pins verified 15 ItsDangerous source
files (159 nodes / 302 edges) and 79 Click files (2,028 nodes / 3,394 edges).
ItsDangerous was stored and `want_bytes` resolved to encoding.py lines 11-17.
Click capture succeeded but the unchanged 4 MiB store budget rejected its report
with no retained entry. Temporary staging copies only were cleaned up; the
benchmark checkouts and their Git data were preserved.

No API route, frontend behavior, model settings, or deployed files changed.
There were no model calls or public analysis jobs. Next: authenticated
reference-only request handling, followed by spending/admission controls and
frontend integration before any hosted LLM enablement. This module remains out
of the current Oracle runtime image. Sites publishing configuration is unchanged.

### Opt-in authenticated local evidence API (no LLM)

`local_evidence_api.py` connects verified capture, server-owned storage, and
reference-only evidence preparation to local HTTP routes. `api.py` mounts
`/api/evidence` only when `ARCHAEOLOGIST_LOCAL_EVIDENCE_ENABLED=true` at startup.
The boundary also requires a separately configured 32–256-character printable
ASCII token. Neither setting was enabled in a running service. New example
configuration defaults to disabled, with an empty token; no secret was generated.

POST `/analyze` accepts only repositoryUrl and returns the graph plus a report
reference. POST `/prepare` accepts only reportId/nodeId and resolves source and
evidence server-side, returning modelCalled=false. The token is authenticated
before reading JSON. The owner namespace is derived server-side from the token;
request owner fields and identity headers cannot set it. Wrong tokens get 401,
non-loopback peers and browser Origin requests get 403, and oversized bodies get
413. Uncompressed JSON is bounded to 4 KiB with a five-second total body-read
deadline. Responses are marked no-store. One synchronous analysis is admitted
at a time on this new path; competing work gets 429. Cleanup and slot release
run on capture failures. Missing/expired/wrong-owner references share a 404.

All 232 root Python tests passed (4 skipped, 9 subtests, 2 existing warnings),
as did all 65 JavaScript tests.
Nineteen new tests include disposable-Git capture through the HTTP API and
reference-based source retrieval, startup opt-in behavior, bad configuration,
forged credentials/identity, client-evidence injection, token rotation, body
limits/timeouts/disconnect-before-dispatch, transport/origin restrictions, busy
admission, capacity rejection, and cleanup/error sanitization. Repository cloning
was mocked to local fixtures; Git parsing/capture/storage were real. No network
clone, model call, credential setup, or production analysis was performed.

This adapter represents one local operator, not separate users sharing a token.
It is not intended for Oracle/Sites deployment, reverse proxies, multiple
workers, or browser authentication. Active synchronous jobs may finish after a
client disconnect; body-read cancellation is not active-job cancellation. The
legacy `/api/interpret` path remains caller-evidence-based and unchanged. Next:
durable spending/admission controls and provider execution limits, then replace
the legacy client-evidence integration with reference-based interpretation and
the appropriate authenticated hosted ownership boundary. Website behavior,
hosting access, and Oracle configuration remain unchanged.

### Persistent interpretation reservation budget (internal, not provider-wired)

`interpretation_budget.py` adds an explicit-initialization SQLite lifetime
reservation counter in a separate ledger. It reuses the existing filesystem and
connection safeguards, not the Oracle quota schema or allowance. Application ID,
schema, version, and singleton-state checks reject foreign or malformed ledgers.
The configured positive integer limit is persisted; reopening or calling setup
again cannot reset it, and a changed limit is rejected. Request operations open
existing storage only and never initialize, repair, refill, or refund it.

Reservations use BEGIN IMMEDIATE and synchronous FULL commits before approval is
returned. Every reservation permanently consumes capacity. Denied reservations
do not consume capacity; missing/corrupt/locked storage denies work. An ambiguous
commit must not dispatch a provider request or refund a possibly committed charge.
The single aggregate row stores only the allowance, reserved units, and count:
no repository data, owner identities, credentials, prompts, or generated content.
The singleton primary key is sufficient; following the SQLite skill guidance,
no redundant or speculative indexes were added. POSIX permission checks are
inherited; Windows ACL provisioning remains an operator responsibility.

All 252 root Python tests passed (4 skipped, 9 subtests, 2 existing warnings), as
did all 65 JavaScript tests. Twenty new tests cover exact exhaustion, invalid
amounts, reopening, policy-change rejection, missing/corrupt/foreign storage,
concurrent connections, lock contention, schema/state failures, a separate
process reservation, and a simulated lost acknowledgment after successful COMMIT.
The ambiguous-commit test confirms a retained charge without a returned approval.
An existing synthetic deep-quota database remained byte-for-byte unchanged when
mistakenly supplied as the budget file. All ledgers were disposable test fixtures.

Units are currently abstract integers, NOT currency or a proven billing bound.
The caller still must compute a conservative request charge from approved model
pricing, bound all billable input/output (including reasoning where applicable),
disable automatic retries, and reserve before each dispatch. No provider or API
route uses this budget yet, and existing local interpretation behavior is unchanged.
No real budget was initialized, no paid request was made, and no public files,
Oracle limits, or database were changed. Next: provider execution limits and
budget-gated dispatch, then reference-based interpretation integration. Deployment
must also protect the ledger from deletion, rollback, or mount replacement;
an application counter cannot prevent administrative restoration of old state.

### Budget-gated interpretation dispatcher (internal, mocked transport only)

`interpretation_execution.py` connects owner-scoped report/node lookup, persistent
reservation, input-token counting, and one structured generation attempt. It has
no default SDK client, real pricing, API-key lookup, route, or enabled configuration.
An internal caller must explicitly enable it and supply an unexpired server-owned
model/rate policy and matching-unit initialized ledger. The existing local and
public routes are unchanged and do not call this dispatcher.

The policy reserves ceil(max-input-tokens * input-rate / 1,000,000) plus the
separately rounded maximum-output charge and an explicit token-count/preflight
allowance. It reserves before either provider request, never uses a cache discount,
and never refunds unused capacity, failures, refusals, or timeouts. The count request
includes the same model, messages, and strict output schema as generation. Invalid
or excessive counts prevent generation, and policy expiry is rechecked afterward.
Generation specifies max_output_tokens, truncation disabled, default service tier,
store=false and background=false, with no tools or previous conversation. The
client copy fixes the OpenAI HTTPS base URL, max_retries=0, and a bounded network
timeout. A process-local slot rejects competing executions before reservation.

The OpenAI Docs [token-count reference](https://developers.openai.com/api/reference/python/resources/responses/subresources/input_tokens/methods/count)
informed request counting; the [response-creation reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
documents that max_output_tokens covers visible output and reasoning tokens.
Network timeouts are not a wall-clock job deadline or proof of remote cancellation;
the reservation remains charged after any uncertain outcome. Incomplete responses
are rejected, and the existing citation-membership and interpretation-label checks
still apply. Those checks do not prove factual correctness.

All 277 root Python tests passed (4 skipped, 9 subtests, 2 existing warnings), and
all 65 JavaScript tests passed. Twenty-five new cases use the installed OpenAI SDK
with HTTPX MockTransport and a synthetic key; no request can reach the network.
They verify actual serialized count/generation input and schema equality, output
caps, timeout configuration, zero retries after 500s/timeouts, pre-dispatch durable
reservation, no refunds, input-count rejection, ownership/budget/policy failures,
expiry during counting, busy rejection, incomplete responses, unknown citations,
and integer round-up arithmetic. Prices and model names are synthetic test values.

This is not yet a verified dollar spending cap. Before live enablement, choose
approved rates/units and model context limits, account for token-count pricing and
any applicable price multipliers, protect policy freshness and ledger persistence,
and run an explicitly approved real-provider evaluation. No real budget or key
was provisioned. Next: authenticated reference-only interpretation integration
and end-to-end disabled/failure handling; public/multi-user ownership, job
deadlines/cancellation, and frontend integration remain unfinished. Sites and
Oracle deployment settings were preserved; no publishing was needed for this
unwired local module.

### Reference-only local interpretation route (disabled, mocked provider)

- Validation: 297 Python tests passed, 4 skipped, 9 subtests passed, with the
  existing 2 dependency deprecation warnings; 65 JavaScript tests passed.
  The focused local API suite passed all 39 tests, including 20 new route tests.
- Added `/api/evidence/interpret` behind the existing loopback, no-Origin,
  bearer-token and bounded-body boundary. Requests contain only report/node IDs;
  the authenticated owner resolves previously captured server evidence.
- Explicit server-side `LocalInterpretationRuntime` injection is required.
  The normal `api:app` mount provides none and remains disabled, independent of
  legacy API-key configuration. No automatic key lookup, ledger creation, live
  policy, model selection, or public enablement was added.
- Offline tests exercise the real SDK via HTTPX MockTransport, including a
  disposable Git capture -> report reference -> generated interpretation flow.
  Other cases cover disabled/missing configuration, absent authentication,
  browser/remote peers, forged request fields, owner mismatch, missing report or
  symbol, exhausted/missing ledger, expired policy, provider errors/timeouts,
  refusals, incomplete responses, and invented citations.
- Dispatch reserves before token counting and generation. Failed provider calls
  retain the reservation with no retry/refund; success stays classified as
  interpretation. Citation membership does not establish semantic correctness.
  Error bodies do not echo provider messages, secrets, or model refusal content.
- The route is synchronous: client disconnect does not establish remote model
  cancellation. Repeated submissions are separate reservations, not idempotent
  replays. Concurrency admission is process-local; use one local worker.
- Public site, Oracle image, real quota, and credentials were unchanged. Live
  pricing/budget approval, public user ownership, cancellation/resource tests,
  frontend integration, and semantic interpretation evaluation remain pending.

### Offline semantic-evaluation foundation (no model calls)

- Validation: 18 new evaluation tests pass; full suite 315 Python tests passed,
  4 skipped, 9 subtests passed, 2 existing dependency warnings; 65 JavaScript
  tests passed. Empty evaluation correctly reports six missing/unreviewed cases.
- Added six agent-authored synthetic cases: direct transformation, conditional
  execution, injected dependency, parameterized base candidate, misleading
  security-related name, and source-embedded instruction injection. Inputs use
  current analyzer packets and selected source, never executed fixture code.
- Added a six-criterion human-review rubric, separate generation inputs without
  answer keys, and offline coverage aggregation. Missing outputs/reviews stay
  pending; stale hashes, incomplete reviews, duplicates, and orphan reviews are
  rejected. Review identity and provider-origin labels remain self-reported.
- An executable negative example proves that the current production validator
  accepts an invented encryption claim citing a valid identity-function ID.
  The evaluation records the semantic failure separately. This is evidence of
  a known validation limitation, not a newly implemented semantic safety gate.
- No actual provider outputs were generated or graded. No model accuracy number,
  independent reviewer calibration, or public release approval is established.
  Public site, Oracle service, model enablement, prices and credentials unchanged.

### Consistent sidebar scrolling and prominent example context (published)

- Desktop (>1000px) now uses a viewport-height flex shell with a naturally sized
  header and a shrinkable workspace. Both sidebars scroll independently and have
  keyboard-focusable named regions; the graph uses remaining available height.
  Tablet/mobile stacked sidebars use normal document scrolling without the old
  inspector-only viewport cap.
- A visible banner above the map identifies the preloaded Cosmic Python example
  and explains how to analyze another repository. It depends on the loaded report
  origin, so imported reports and submitted analyses are not labeled as examples,
  even when the submitted repository is cosmicpython/code.
- Production build passed; 68 JavaScript tests passed, including three source-level
  layout/copy regressions. Local preview HTTP response was 200. Actual browser
  scroll/viewport interaction has not been verified in this turn.
- Owner approved public deployment and the remote-build fallback after Windows
  denied the local packaging helper. Sites version 18 deployed successfully on
  2026-09-04 from UI-only commit `5ae0459e90aa08ceb19106452e1da03ca9d27143`.
  Deployment `appgdep_6a9a7c2a365c819182cfc59feb5e1856` reports succeeded with
  unchanged environment revision 3. Oracle, credentials, and optional LLM
  enablement unchanged; prior unrelated local backend work remains uncommitted.

### Root pyproject discovery (local, not deployed)

- Added bounded TOML-only inspection of standard root `[project]` and
  `[build-system]` declarations using the standard-library parser. No setup hook,
  backend import, installation, referenced path, or network lookup is performed.
  Interpretation follows the [PyPA pyproject specification](https://packaging.python.org/en/latest/specifications/pyproject-toml/).
- Literal declarations carry fact classification, confidence 1 in the recorded
  declaration only, manifest path and SHA256. Dynamic fields are unresolved even
  when partially statically declared; tool-specific tables are not guessed.
  A parsed manifest is not a fully validated packaging configuration. Missing,
  skipped, unreadable, and invalid states are explicit; diagnostics omit raw
  parser errors and direct-reference dependency URLs.
- Existing commit-verified capture now includes the root manifest when tracked,
  checks its Git blob, and excludes untracked replacements. The symbol store still
  retains only Python source bytes; graph metadata retains the manifest digest.
  The existing capture byte/file caps also apply to the manifest. Raw manifest
  source and exact key line spans are not retained by this first implementation.
- Added optional metadata types and bounded import validation; old reports remain
  compatible and metadata round-trips through JSON export/import. No new website
  panel, CLI flow edge, or resolver behavior was added.
- 337 Python tests passed, 4 skipped, 9 subtests passed, with 2 existing dependency
  warnings; 77 JavaScript tests, TypeScript checking and production build passed.
  Read-only checks of the retained pinned ItsDangerous and Click fixtures parsed
  both root manifests and found declared Python `>=3.10` and Flit build metadata.
- Docker runtime copy rules and owner-run build-context allowlist include the new
  module/tests. Docker/Oracle validation and rollout have not been performed.
  Public Sites version 18, Oracle image, credentials, and model settings unchanged.

### Pinned test-evidence acceptance (offline)

- Added the non-Pallets application `cosmicpython/code` at clean commit `14c84797ffa77255d53cf1a02fe6aafda2b68aeb`, whose book-backed example architecture is the source of the bundled report. Fifteen source-selected checks passed: exact inheritance for repository, unit-of-work, notification and command abstractions; exact imports across bootstrap, adapters, domain and service layer; exact presence of layered, repository-boundary and unit-of-work patterns; no unsupported FastAPI/SQLAlchemy/HTTP-flow claims; independently derived large-callable ranges; heuristic risks/candidate edges; and unknown-intent confidence. Observed graph: 196 nodes, 498 edges, 3 patterns, 1 heuristic risk, and no >=80-line callable. Repository code was downloaded into an ignored clean checkout, parsed offline, and never installed or executed.
- Across four pins, all 77 selected checks pass: 22 ItsDangerous, 20 Click, 20 Flask, and 15 Cosmic Python. The complete command now also requires `--cosmicpython artifacts/benchmark-20260904-cosmicpython`. The full Python suite remains 411 passed, 4 skipped, 9 subtests and 2 existing dependency warnings. This closes the immediate ecosystem-variety gap, but it remains a small author-selected sample rather than independent review, measured precision/recall, or calibrated confidence.
- Broadened the benchmark from two utility libraries to a third repository type: Flask 3.1.2 at clean commit `2c1b30d0503cfb064f1cb252e6614a06915a362a`. The ignored checkout was downloaded from the public Pallets repository, then analyzed offline without installing dependencies or executing its code. Twenty selected Flask checks passed: four exact inheritance relationships, four imports, exact independently parsed large-callable ranges (8), absence of unsupported FastAPI/SQLAlchemy/repository/UoW/HTTP-flow claims, evidence classifications, and the selected `test_find_best_app` call/import/non-call evidence. Observed graph size: 1,658 nodes / 2,584 edges, with 19 heuristic risk findings. These counts are observations, not thresholds or accuracy estimates.
- The benchmark now requires all three clean pins: `python scripts/benchmark_pinned_repositories.py --itsdangerous artifacts/benchmark-20260903-itsdangerous --click artifacts/benchmark-20260903-click --flask artifacts/benchmark-20260904-flask`. Its test-evidence mutation suite automatically covers Flask too: 33 focused tests pass. The full Python suite passes with 411 passed, 4 skipped, 9 subtests and 2 existing dependency warnings. This improves variety, but all three projects share the Pallets ecosystem; an independently selected application repository and reviewer are still needed.
- Extended the existing clean-checkout benchmark with five test-proximity checks per repository. Source-selected expectations: ItsDangerous `tests/test_itsdangerous/test_encoding.py::test_want_bytes` calls `encoding.want_bytes`, not `encoding.base64_encode`; Click `tests/test_parser.py::test_split_arg_string` calls `shell_completion.split_arg_string`, not `shell_completion.shell_complete`. Their module imports must remain file-level signals. Expected call lines are independently read from the pinned source AST, not copied from an analyzer report. Existing pins remain unchanged.
- All 42 selected benchmark checks passed (22 ItsDangerous, 20 Click), including exact call-source locations, presence of separate module import evidence, absent invented calls, untruncated metadata and heuristic scores. This is a small source-reviewed regression set, not test execution, measured coverage, broad accuracy or independent human review.
- Added 22 offline benchmark self-tests: valid synthetic counterparts plus ten corruptions for each repository expectation, including missing/truncated evidence, wrong source line or edge target, absent/mistyped imports, invented calls and unjustified fact/confidence labels. All 36 focused tests and the full Python suite passed: 400 passed, 4 skipped, 9 subtests, 2 existing dependency warnings.
- Only test tooling and this record changed. No repository code was executed, downloaded or modified; no public requests, quota usage, model calls, website publication or Oracle update occurred. Public version 21 and deployed backend remain unchanged. Broader held-out repositories and independent reviewer calibration remain open.

### Evidence navigation and scope alignment (published)

- Fixed a v20 integration gap: source navigation could select a test file that the active production scope or search immediately hid, triggering the visible-file effect to redirect selection. Shared reveal logic now updates scope/query and selected file/symbol together, only broadening to All files for test targets and clearing incompatible search text. Existing compatible filters are retained; missing targets/containing files do nothing.
- Explorer layer grouping, initial production selection, file filtering and risk filtering now share the same common-test-path rules used by the test-proximity validator/analyzer. Manual filtering that hides the selected file clears the stale symbol selection as well.
- Eleven new regression tests cover seven test layouts, target visibility after navigation, filter preservation, missing targets, near-match production paths and page wiring. All 112 JavaScript tests passed; TypeScript and production build passed with existing warnings. Local route HTTP 200, preview handoff queued. No interactive browser/visual QA performed.
- Published with owner approval on 2026-09-04 as Sites version 21, source `c7468fbf81ea96fe0347b4709d309c8bfddb1e70`, deployment `appgdep_6a9a8e1f46d88191a60676e2818c1a11` succeeded. Environment revision 3 unchanged. Only two frontend source files and the navigation regression test were committed; unrelated local work remains uncommitted. Used the established remote-build fallback for blocked local packaging. No Docker rebuild, Oracle upgrade, quota change, browser interaction/visual QA or new repository job was performed.

### Test evidence runtime release checks (deployed)

- Final rollout status: owner confirmed the pinned image upgrade with HTTPS analysis, authorization and quota preservation; backup `/etc/codebase-archaeologist/pre-test-proximity-v1.service`. Website inspector separately published with owner approval on 2026-09-04 as Sites version 20, source commit `45c49bd8776f1c8ea4183cf525c696fe8f8d6cd8`, deployment `appgdep_6a9a8b79417c8191af220e48134145a2` succeeded, environment revision 3 unchanged.
- Publication includes six frontend files only. The cross-language integration tests remain local alongside their uncommitted analyzer prerequisite; unrelated local AI work was not pushed. All 101 JavaScript tests, TypeScript and production build passed. Used the established remote-build fallback for blocked local packaging. No browser interaction/visual QA or live repository jobs were run during publication; deployed URL handoff queued. Earlier preparation notes below are historical.

- Owner validated `oracle-cdeab4828b3442c3ba258e40df8d2cba` on Oracle: 205 passed, 1 skipped, 9 subtests, 2 warnings; runtime smoke passed project discovery and test proximity. Archive SHA-256 `57c46927ce852d19485d72b8380a2f11841c880c36be8c044068669292653f86`; image `sha256:a64484345dd1943c2df908a51d66e27ddd8d2f67aa3b25bff7de2cc6482a2112`, linux/amd64, user 10001:10001.
- Owner approved a backend-only image upgrade. Prepared `scripts/upgrade_oracle_test_proximity.py`, pinned from the project-discovery image, with separate `/etc/codebase-archaeologist/pre-test-proximity-v1.service` backup. Reuses one HTTPS response for project-discovery and proximity checks; no extra analysis admission or retry. Requires adjacent project-discovery, patterns, quota helpers and `container_smoke.py` (importing the latter never runs its server).
- Local updater suites: 24 passed, 28 subtests passed, including exported archive hash/image-pin match and rollback/quota-preservation checks. Actual owner application/confirmation pending; website unchanged.

- Runtime smoke now requires both symbol-call and module-import proximity signals, checks graph edge endpoints, source locations, path-based test direction, classification/score, unique references, counts and no truncation for the smoke repository. It prints `test_proximity: call-and-import-evidence-verified` only after these checks pass. Existing authorization, invalid URL, project-discovery and health checks remain.
- Eight adversarial smoke-probe tests reject absent/empty metadata, wrong targets/signals/scores, duplicate references, invalid source lines and incorrect test counts. Full local Python suite: 378 passed, 4 skipped, 9 subtests, 2 existing warnings. Focused UI/import/build-context suite: 18 passed. No application/UI source changed in this preparation turn; prior successful production build remains applicable.
- Read-only analysis of the existing ItsDangerous benchmark checkout produced 6 identified test files, 12 call links and 11 import links and passed the new probe. These counts are observations, not hardcoded success requirements or coverage measurements.
- Next owner-run step: Windows PowerShell `scripts/Test-DeepService.ps1 -MemoryMiB 384 -ExportOracleBundle`. The runtime stays unchanged until an exact new image has passed Docker/Oracle validation and deployment is approved. Do not reuse the project-discovery image updater for this next release.

### Test evidence inspector (published as version 20)

- Added a Test evidence section for selected files/symbols, separating exact symbol-call links from imports of the containing module. Source buttons use existing file/symbol selection; recorded line numbers are displayed, not claimed as an automatic exact-line jump. Up to 50 items per signal are rendered, with an explicit limit notice. No added internal scroll region.
- Old/inventory reports explain unavailable analysis; absent retained links never imply untested code. Imported reports are labeled unverified, even after consistency validation. Output truncation and method limitations remain visible.
- Optional metadata validation bounds shape, text, link count and total bytes; checks IDs, edge endpoints/kinds, source line/path, score/classification, test-path direction, test-file/candidate counts, duplicate references and truncation consistency. These checks do not independently verify repository truth or coverage.
- Validation: 101 JavaScript tests passed, including real Python report round-trip, malformed-reference tests, actual component rendering and source-selection callbacks. TypeScript and production build passed with existing warnings. Local route returned HTTP 200; preview handoff queued. No interactive browser or visual QA performed.
- No public deployment or backend mutation. Next: exact runtime validation for test-proximity output and owner-approved coordinated rollout. Earlier JSON-only notes below describe the preceding milestone.

### Test proximity evidence index (local JSON, not deployed)

- Added optional `test_proximity` report metadata, version 1, scope `recorded-direct-edges`. Each heuristic link references an existing edge and its source/target node IDs. `symbol-call` and `module-import` are separate signals; import evidence is not expanded to every module symbol.
- Only recorded `calls`/`imports` edges with confidence at least 0.9 from a heuristically identified test path to a non-test path contribute. No transitive traversal, candidate dispatch, same-name guessing, test execution, dependency installation, or model requests. Helpers/fixtures can contribute; custom collection and actual assertions are not evaluated.
- Output includes identified test-file count, candidate-link count, explicit truncation, provenance and limitations. Links capped at 1,000 and 256 KiB summed serialized link bytes. Confidence is a fixed 0.6 heuristic score, not calibrated probability or coverage. No absence-based risk finding is created.
- Six new tests cover source references, import-only behavior, no execution, absent/unresolved evidence, exclusion of indirect/candidate/test-target/dangling relationships, serialization and deterministic caps. Full suite: 370 Python passed, 4 skipped, 9 subtests and 2 existing warnings. 84 JavaScript tests passed. The new test is included in the explicit Docker staging list and existing `!test_*.py` allowlist.
- UI rendering and imported-metadata validation remain next; existing website does not display this field. No new image built or deployed. Source/parse/file limits mean absent links must never be described as proof of no tests.

### Test-path recognition foundation (local, not deployed)

- Added one common Python test-path heuristic shared by analyzer hotspot scoring, architecture-pattern selection, and symbol path roles. Recognizes nested `test/` and `tests/` directories, `test_*.py`, `*_test.py`, `tests.py`, and `conftest.py`; test helpers within those directories remain test-associated.
- All calls remain in the graph. Test-path neighbors do not inflate production fan-in/fan-out, and test-only framework code does not establish production patterns. Finding provenance explicitly identifies the path heuristic and its unhandled custom collection settings.
- Adversarial tests cover top-level/nested layouts and similar production names such as `testing.py`, `contest.py`, and `tests_support/helpers.py`. Custom `spec/` layouts are not guessed. A production module named `test_*.py` can be a false positive; this is not test collection or coverage proof.
- Local validation: 364 Python tests passed, 4 skipped, 9 subtests, 2 existing warnings; 84 JavaScript tests passed. No new dependencies or runtime files, no repository execution, no image build or deployment. Existing public UI filtering has not been changed by this analyzer-only slice.
- This is a prerequisite, not completion of test proximity. Next: bounded source-linked test-to-production evidence, preserving the distinction between direct calls, file imports and unresolved coverage.

### Project discovery release gate (deployed)

- Final status: owner confirmed image upgrade, HTTPS analysis, authorization and quota preservation. Rollback backup is `/etc/codebase-archaeologist/pre-project-discovery-v1.service`.
- Public panel published with owner approval on 2026-09-04: Sites version 19, commit `31dde97b5de699c11aa3224ca36183bb37740737`, deployment `appgdep_6a9a853714c88191a9e2f80b2576d2f1` succeeded; environment revision 3 unchanged. Selected seven frontend/type/validation/test files only; unrelated local AI work excluded. 84 JavaScript tests, TypeScript and production build passed. Used established remote-build fallback because local packaging is blocked. No interactive browser QA or new public analysis was submitted in this publication turn. Earlier preparation notes below are historical.

- Owner subsequently validated bundle `oracle-0cbfe602abbd4826b0a344c5b0bc1f3b` on Oracle: 165 passed, 1 skipped, 9 subtests, 2 warnings; runtime smoke included `root-pyproject-declarations-present`. Archive SHA-256 `f8fe8981d018937f3c5ae2eea45cec782d3c7693b3647fa4df328a38c412c137`; image `sha256:b49b941d126a2289a6ccf7151e8c3c24b4bfa18427a6a190a5ef6950c85afe40`, linux/amd64, user 10001:10001.
- Owner approved the backend-only upgrade. Prepared `scripts/upgrade_oracle_project_discovery.py`, pinned from the current hotspot image with separate `pre-project-discovery-v1.service` backup. Default is read-only; `--apply` performs one HTTPS analysis including metadata assertions and rolls back on validation failure without resetting quotas. Offline updater suite: 12 passed, 14 subtests passed, including matching the exported archive hash/image pin. Actual application and owner confirmation remain pending. Website unchanged.

- The exact-runtime container smoke test now requires parsed root `pyproject.toml` metadata for ItsDangerous, a valid source hash, literal name declaration, fact/confidence labels and limitations. Healthy older images without discovery must fail this gate.
- Added an offline public-worker test covering metadata propagation through pinned report construction and JSON serialization, source-byte hash matching, cleanup, and rejection of absent/invalid discovery by the smoke probe.
- Full local Python suite: 338 passed, 4 skipped, 9 subtests passed, 2 existing dependency deprecation warnings. This is not Linux/container or Oracle validation.
- Owner next step in Windows PowerShell: `& .\scripts\Test-DeepService.ps1 -MemoryMiB 384 -ExportOracleBundle` from the project folder. The existing explicit build allowlist includes the new parser/tests; the runtime image does not include the local LLM routes.
- No new image digest or deployment approval exists yet. After the owner supplies bundle validation, validate that exact bundle on Oracle before preparing a pinned image-only updater. Preserve the current image and quota mount; do not reuse an older release updater.

### Project details explorer panel (published as version 19)

- Added a collapsed Project details section in the repository sidebar, with expandable literal declarations and source hash/limitations.
- Distinguishes older reports without metadata, inventory reports, missing manifests, skipped/unreadable/invalid manifests, and parsed declarations. Missing metadata never implies no dependencies.
- Imported metadata is explicitly unverified. Confidence refers to recording literals, not installed dependencies or proven script execution; warnings about unresolved fields remain visible when expanded.
- No additional network requests, code execution, model calls, or internal panel scrollbar. Long values wrap; native disclosure controls support keyboard use.
- Validation: 83 JavaScript tests passed (including six actual component-render tests), TypeScript checking and production build passed. Existing build warnings remain. No interactive browser/visual QA was performed.
- Oracle and the public website remain unchanged; root manifest discovery requires a newer local report until the backend update is separately validated and approved.

### Keyboard and narrow-screen follow-up (published)

Owner confirmed that a Cosmic Python report downloaded and reopened with the
same repository. Selecting `handlers.py` is the existing highest-degree default,
not a redirect to another report. A-05 passes for this basic owner-confirmed
round trip; no claim is made that all browsers or reports were tested.

Public browser testing exposed A-07: Enter on a focused graph node left the
inspector unchanged. Local code now forwards React Flow selection changes to
the inspector and keeps visual selection consistent. Local production-build
checks confirmed Enter selects `domain/model.py` and Space selects
`service_layer/handlers.py`. A new regression covers selected/deselected,
hidden-node and position-only changes.

Mobile controls and explanation text were enlarged, with visible graph-node
focus outlines. Requested widths 320 and 390 had equal document client/scroll
widths (305 and 375 respectively, accounting for scrollbar). At width 960 a
970px-wide desktop grid caused overflow; the adjusted tablet breakpoint reduced
document scroll width to its 945px client width. Temporary viewport overrides
were reset. Dense graphs still need zoom, the controls precede the graph on
phones, and a full screen-reader/keyboard/touch accessibility audit remains open.
All 54 JavaScript tests, TypeScript checks and the production build passed.
No repository analyses were submitted. Website publication of these UI fixes
completed in version 17, source `a9dc5d482a0d6dc147a720146526e8a158f3137d`.
Deployment `appgdep_6a99fbf7a24c8191a1e907a8deed048f` succeeded with unchanged
environment revision 3. The checks above were against the local production
build; post-publication browser verification is recorded below. No Oracle
image update was required.

### Public keyboard, layout and cancellation follow-up

On public version 17, Enter selected `src/allocation/domain/model.py` and Space
selected `src/allocation/service_layer/handlers.py`, with matching inspector
metadata. At requested width 390, document client/scroll widths were both 375
and the repository input font was 16px. At requested width 960, both widths were
945. These are narrow overflow checks, not complete touch/screen-reader or visual
acceptance. The viewport override was reset.

Exactly one Deep request for `pallets/itsdangerous` was initiated through the
public form, then canceled using its visible Cancel analysis button. While
loading, repository/mode inputs and report controls were disabled. After cancel,
the controls re-enabled and the page showed its cancellation alert. The existing
bundled Cosmic Python deep report remained at 37 files / 159 symbols, with
`handlers.py` still selected. It remained unchanged on a later observation after
the layout checks; no late result replacement was observed. No captured console
errors were returned at the checked point. The cancellation alert repeats the
map-preservation sentence; this is a minor copy issue, not a failed cancellation.

This passes the observed public UI cancellation/map-preservation path only. It
does not establish whether Oracle admitted the request, started an active job,
reaped processes, or charged quota. No second analysis was submitted, no live
quota was deliberately exhausted, and no backend/configuration changes were
made. Browser busy/quota/outage feedback, active-job cancellation propagation
through Sites and cross-browser behavior remain open.

### Offline request-failure integration checks

`tests/analysis-failure-paths.test.mjs` connects the actual browser request helper
to the actual Sites deep proxy through an in-memory transport. Eight new tests
cover busy, quota, outage, backend authorization failure, repository-size limits,
deadline responses, malformed/wrong-repository/wrong-tier reports, and cancellation
while reading a stalled upstream response body. Errors remain sanitized, rejected
requests do not return a replacement graph, and no automatic retry or inventory
fallback occurs. Each of the six status-error cases also verifies a later explicit
request can succeed. Cancellation closes the stalled stream.

All 62 JavaScript tests passed. Only tests and this acceptance record changed;
no application rebuild or deployment is needed. These tests make no network
requests and use synthetic credentials, with zero analysis jobs or quota changes.
They do not mount the React UI or prove visual map preservation, actual platform
disconnect propagation, live concurrent admission, or real quota exhaustion.
Those public-path acceptance checks remain open and must use a controlled budget.

### Browser failure-feedback checks with a loopback fixture

`scripts/serve-ui-failures.mjs` serves a local-only acceptance proxy on
`127.0.0.1:3002` in front of the existing production build on `127.0.0.1:3001`.
It injects synthetic upstream responses into the real deep proxy, blocks other
API requests and writes, uses no real credentials, and forwards page/asset reads
only to the fixed loopback build server without following redirects. It is not
part of the deployed application and must never be used as a real analyzer.

To repeat with Node 24, run `node node_modules/vinext/dist/cli.js start --hostname
127.0.0.1 --port 3001` and, separately, `node scripts/serve-ui-failures.mjs` from
the repository. Open the local fixture, select Deep, and submit
`https://github.com/fixture/busy`, replacing `busy` with `quota`, `outage`,
`limit`, or `timeout` for the remaining scenarios. Stop both servers afterward.

The in-app browser exercised all five scenarios against the existing version-17
production build. Each displayed the corresponding sanitized error and preserved
the Cosmic Python report at 37 files / 159 symbols with `domain/model.py`
selected. Graph-ready status and the enabled Analyze button returned after each
failure. Following the final timeout, keyboard navigation to `handlers.py` still
worked. The browser was restored to the public site afterward.

All 65 JavaScript tests passed, including three tests for fixture routing and
failure mapping. Zero live analyses were submitted and no quota was consumed.
Only test tooling and documentation changed; no new application build or public
deployment was needed. These tests close local browser feedback/map-preservation
coverage for the five failure responses, not real concurrency, quota admission,
elapsed timeout enforcement, or platform-to-Oracle disconnect propagation.

1. Broaden export verification and decide minimap expectations. A-01 through A-04 have been
   published and rechecked.
2. Broader pinned real-repository benchmark with independently reviewed expected
   and absent relationships, risks and patterns; current examples are insufficient
   to estimate accuracy or calibrated confidence.
3. Full public-path cancellation, concurrent/busy/quota feedback, failed analysis
   preserving the map, and larger-repository limits. Use a controlled test budget;
   do not exhaust the shared production quota or restart production casually.
4. Cross-browser/mobile accessibility, keyboard navigation and dense-map usability.
5. Owner-approved reboot/rollback drills and longer runtime stability observation.
6. Hosted LLM interpretation remains unfinished functionality, separate from tests.

The original acceptance pass changed only tests and documentation. The follow-up
also changes website code; that replacement is now published and verified.
