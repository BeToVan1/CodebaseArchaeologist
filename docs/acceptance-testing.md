# Acceptance testing — 2026-09-03

Status: early beta; core journeys work, but acceptance is not complete.
Tested public Sites version 15, source `3601f835ba4a6ba64f784714f5e9f4a1c2bade3b`,
with deep enabled. No application source or public configuration was changed
during this pass. Exactly one hosted analysis was submitted by this pass.

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

### Keyboard and narrow-screen follow-up (local fixes, not published)

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
is pending; no Oracle image update is required.

1. Confirm export and decide minimap expectations. A-01 through A-04 have been
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
