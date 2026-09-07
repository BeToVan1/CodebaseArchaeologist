# Python MVP release gates

Status: working beta; no release acceptance is implied by passing unit tests.
Use these gates instead of counting prompt edits or merged PRs as completion.

## 1. Facts-first explorer

Implementation: local commits 078f415 and c093d3a. Publication and visual review
are pending. Review a fresh deep report with a supported guarded-call symbol,
an older report, and an imported report. Do not generate an AI response just to
test layout; recorded/mocked responses are sufficient.

- Desktop and narrow viewport: facts are readable before the AI section, with
  no clipped text or horizontal page overflow.
- The source link reaches the selected symbol's source area with keyboard access.
- Facts stay visible when AI is unavailable; no model request happens on selection.
- Imported claims say unverified. Missing facts say unsupported/unavailable,
  not safe, fully analyzed, or exception-free.
- Facts are not repeated in the general claims list. Unknown context and AI
  confidence limitations remain explicit.

Record viewport, report origin, selected symbol, outcome, and any defect. All
items must pass before publication approval. Automated source checks are not
a substitute for this visual/interaction pass.

### Local review checkpoint — 2026-09-07

- Owner confirmed locating the AI panel after selecting a symbol in an imported
  report. Earlier file selection hid the symbol-only panel and caused confusion.
- Imported/otherwise unavailable symbol AI now has a visible explanation instead
  of disappearing. An opt-in handwritten layout sample is explicitly unrelated
  to the selected code, has no confidence scores or citations, and makes no request.
- Verification: 133 JavaScript tests passed; production build passed. Existing
  JSON-import-attribute and route-classification build warnings remain.
- This is not full visual acceptance: exact symbol/report identity and viewport
  were not recorded. Narrow layout, keyboard source navigation, supported fresh
  facts, and older-report behavior remain pending.
- No deployment, live inference, credentials, or quota changes in this UI slice.

## 2. Bounded explanation benchmark

Freeze the six checked-in cases and exact input/prompt/adapter hashes per run.
Do not silently reuse reviews after changing inputs or outputs. Keep original
responses, rejected outcomes, and pending cases visible. Current independent
human review coverage is incomplete; do not report model accuracy.

Each case requires review of behavior, execution, rationale, citations,
uncertainty, and source-instruction handling under the existing rubric. Any
incorrect branch/return statement, invented concrete implementation, or unsupported
success guarantee fails its criterion even if another section is correct.
Schema checks cannot grant a semantic pass. Empty or schema-field uncertainty
lists must not be taken as evidence that no material uncertainty exists.

Start with a fixed, explicitly approved request budget. Stop after a failure;
do not add retries or new requests during review. Before expanding the benchmark,
record results for all six cases and independent reviewer disagreements. Then
add held-out pinned real-repository examples with documented expected facts.

## 3. Persistence and caching

Design before implementation: saved snapshot identity, access boundaries,
retention/deletion, storage quota, and explanation cache keys combining commit,
evidence, prompt and model versions. Verify expiration, isolation, stale-cache
invalidation and reproducible source links. Export/import is not durable storage.
Do not change infrastructure or incur charges without approval.

## 4. Release acceptance

Demonstrate documented repository ingestion, exact source navigation, at least
three representative flows with explicit gaps, explainable risks, and the human
benchmark. Measure time to map, bounded resource use and failure recovery on
agreed repository sizes. Record actual results rather than inventing performance
targets after observing them. Wider frameworks and mixed languages are not
prerequisites for the first Python MVP.
