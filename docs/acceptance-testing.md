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

The four UI issues above are fixed in the local source; public version 15 still
has the original behavior until the replacement is published. A production build
was opened in the browser and the same portable FastAPI fixture was imported:

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

1. Publish and recheck the locally verified A-01 through A-04 fixes; confirm
   export and decide minimap expectations.
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
also changes website code; publication of that replacement is still pending.
