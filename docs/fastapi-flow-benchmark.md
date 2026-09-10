# FastAPI flow acceptance — PDF sections 17.1, 17.2 and 18

## Scope and evidence

Repository: `BrunoTanabe/fastapi-clean-architecture-ddd-template`, one of the PDF's
section 19 benchmark repositories. Pin: `38d6eb0501a4481a6ed9722a60231199b1289f0a`.
Tree: `b441989e7b466a288565318b8a1660a4cb0f62a0`.

[Pinned README](https://github.com/BrunoTanabe/fastapi-clean-architecture-ddd-template/blob/38d6eb0501a4481a6ed9722a60231199b1289f0a/README.md):
architecture/layer responsibilities at lines 141–184; request sequence at
279–310; health/example endpoint descriptions at 437–446. Documentation describes
intended design, not proof of an executed path. Expectations below were selected
by assistant source review before running the analyzer; independent human review
remains pending.

`evals/fastapi_flow_snapshot.json` records every regular Python file plus root
pyproject, README and license from that tree, with Git blob SHA and byte length.
The local acquisition is source-only, not a Git checkout and not a deployed report.
The benchmark validates the inventory and original blob hashes before parsing.
It tolerates only a single terminal newline difference from text transport, never
arbitrary content changes. No target modules or dependencies are imported or run.

## Three selected paths

| Case | Source-derived expected first hop | Important boundary |
| --- | --- | --- |
| Health check | `health.presentation.routers.health` → `HealthUseCases.health`, route line 45 | Builds a domain Health object; this does not prove database health. |
| Migration version | `health.presentation.routers.alembic_version` → `HealthUseCases.alembic_version`, route line 81 | Use case calls `self.repository.get_alembic_version`; interface dispatch must not silently become an exact implementation claim. |
| Example | `example.presentation.routers.hello` → `ExampleUseCases.hello`, route line 34 | README explicitly describes no persistence; no database path should be invented. |

All symbol names above start with `app.modules.`. The health router source is
[here](https://github.com/BrunoTanabe/fastapi-clean-architecture-ddd-template/blob/38d6eb0501a4481a6ed9722a60231199b1289f0a/app/modules/health/presentation/routers.py),
and the example router is
[here](https://github.com/BrunoTanabe/fastapi-clean-architecture-ddd-template/blob/38d6eb0501a4481a6ed9722a60231199b1289f0a/app/modules/example/presentation/routers.py).

The migration path's further reviewed evidence is
`health/presentation/dependencies.py` (factory returns `PostgresHealthRepository`),
`health/application/use_cases.py` (awaits interface method), and
`health/infrastructure/repositories.py` (`select(AlembicModel)` followed by awaited
session execution). These establish source constructs and intended wiring, not a
guaranteed successful query. Factory construction is not the same as calling a
repository method. Branches, exception handlers and unresolved framework behavior
must remain explicit. A candidate edge is not an exact runtime target.

## Run locally

```text
python scripts/benchmark_fastapi_flows.py <source-snapshot-directory>
```

Exit 0 means the three source-backed first-hop checks and the selected dependency/
persistence boundary checks passed; exit 1 reports gaps; exit 2 rejects an invalid
snapshot. A passing first hop is necessary, not
sufficient, for PDF acceptance. Full route/dependency/service/persistence paths,
ordered steps, explicit gaps, UI display and independent review remain gates.
The runner makes no downloads, live service requests or model calls.

## Review status

- Benchmark guard tests: 11 passed locally on 2026-09-09.
- Full Python suite: 519 passed, 4 skipped, 9 subtests; two existing dependency warnings.
- Pinned-source analyzer run: all 225 source/document files passed original Git
  blob verification, then the analyzer completed locally with result `GAPS`.
- All three selected routes were recognized. All three route-to-use-case links
  retained the expected file, expression and source line, classified as
  `may-dispatch-to` rather than proven calls. None had a selected report flow.
- The three globally selected labels were `DELETE /{id}/`, `DELETE /{id}/`, and
  `GET /`. The repeated labels belong to selected paths; this run does not verify
  fully composed router prefixes. `unresolvedCallVisible: false` with no selected
  flow does not mean the underlying graph has no unresolved calls.
- Next implementation requirement: entry-point-specific access to bounded paths,
  instead of allowing a global three-flow summary to be the only available
  flow set. Retain an explicit distinction between candidate and exact edges.
- Three-flow PDF acceptance: pending; do not treat the test count as acceptance.

No repository code was executed, dependencies installed, live analysis/model
requests made, or production settings changed. The source-only snapshot remains
under ignored local artifacts; only the manifest, runner, tests and review are
project changes. The preceding local recursion-gap fix remains preserved.

## Entry-point access follow-up — local, 2026-09-09

The analyzer now retains its bounded path candidates instead of discarding all
but three globally. Budgets are shared across entrypoints and their first hops:
at most 500 total paths, 12 per entrypoint, 8 edges deep. Reaching a branch budget
marks retained paths for that entrypoint partial with a source-backed
`flow-path-budget-reached` gap. The first 500 entrypoints are eligible; reports
and UI explicitly warn that bounded selections are not exhaustive.

The existing flow view now selects an entrypoint by identity and shows only its
saved paths. Same-labelled routes remain distinct by source path/symbol. It shows
represented/recognized counts and explains missing paths in older reports.
Selection does not trigger an analysis or AI request; imported claims remain
unverified. Static call paths are explicitly not runtime traces or branch order.

Re-running the same hash-verified benchmark passes all three first-hop checks:
health 10 paths, migration-version 10, example 9. The full snapshot retains 206
paths for all 23 recognized entrypoints. Its report passes browser validation
and portable serialization (approximately 7.1 MB, below 10 MiB). Candidate links
remain `may-dispatch-to`; this is not a new exact-dispatch claim.

Validation: 522 Python tests passed, 4 skipped, 9 subtests; 150 JavaScript tests
passed; TypeScript and production build passed. Existing dependency/build warnings
remain. Regression cases include recursion, a busy early branch, more than three
entrypoints, the global cap, deterministic ordering, duplicate route labels,
report replacement, and older reports. Browser interaction/visual review and
Oracle resource validation have not been performed for this local change.

Still open: independently reviewed full persistence/dependency paths, router
prefix composition, and UI acceptance. The benchmark's PASS is deliberately only
the three source-backed first-hop checks, not overall PDF acceptance. Nothing
was deployed, no model called, and no credentials or quota data changed.

## Nested dependency declarations — local follow-up, 2026-09-09

Review exposed a missing link: dependency extraction previously ran only on route
handlers. It now recognizes imported FastAPI `Depends` declarations on provider
functions too, including async providers and Annotated parameters. Providers do
not become routes. Lookalike local `Depends` functions do not establish wiring.
Unresolved provider targets retain exact source-backed gaps.

Bounded selection prioritizes nested dependency declarations before constructor
and utility calls within a branch. This preserves the provider chain without
claiming a runtime evaluation order or increasing traversal limits.

The expanded pinned benchmark passes three first-hop cases plus nine checks:
three declared dependency edges (router line 77, provider lines 19 and 13), the
repository factory constructor at line 15, the repository/model read at line 23,
the retained dependency chain, the visible interface-call gap at use-case line
48, no promotion of that interface call to an exact call, and recognized model
metadata. Source paths/lines and targets are checked, not just edge counts.

The implementation-to-interface gap is still unresolved. Separate wiring and
read edges do not demonstrate one proven route-to-database runtime execution.
Independent review, full execution semantics and UI acceptance remain pending.

Validation: 527 Python tests passed, 4 skipped, 9 subtests; 150 JavaScript tests
passed. No frontend changes in this follow-up. No deployment, live model call,
credential or quota changes. Previous local changes remain intact.

## Declared route paths — local follow-up, 2026-09-09

The pinned example router obtains its prefix from `example_docs` in an imported
module. Prefix composition is not implemented. UI route labels and generated
deterministic role text now explicitly describe declarations, not verified
mounted URLs or successful runtime registration. This applies to older imported
report flow displays too; it does not rewrite their underlying claims.

Literal `path=` decorator arguments are now recognized. An empty string remains
an empty declaration, not an unknown dynamic path. Nonliteral paths and conflicting
explicit positional/keyword path values remain unknown. Expanded options alone
do not establish a path; an explicit literal remains an observed declaration
even alongside expanded options, not a guarantee that registration succeeds.

Validation: 536 Python tests passed, 4 skipped, 9 subtests; 161 JavaScript tests
passed and production build passed. The pinned first-hop and persistence-boundary
checks still pass. Router/mount prefix composition and multiple route decorators
remain explicit limitations, not completed acceptance criteria. No deployment,
model request, credentials or quota changes.
