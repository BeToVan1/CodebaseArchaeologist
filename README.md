# Codebase Archaeologist

The current development build analyzes public Python repositories through a local FastAPI service and displays the resulting file/import graph in the web explorer.

## Local development

Install both dependency sets:

```powershell
python -m pip install -r requirements-dev.txt
pnpm install
```

Start the analyzer API in one terminal:

```powershell
pnpm run dev:api
```

To enable the optional AI interpretation panel, set `OPENAI_API_KEY` before starting the API.
You may override its default model with `OPENAI_MODEL`. The API sends only the selected symbol's
evidence packet and a source excerpt capped at 12,000 characters. Generated claims are always
labelled as interpretations and are rejected if they cite evidence outside that packet.

Start the web application in another terminal:

```powershell
pnpm run dev
```

Open `http://localhost:3000`, enter a public GitHub URL, and select **Analyze repository**.

Each completed analysis records the repository's full commit SHA. Repository and file links in
the explorer are pinned to that immutable snapshot, so the displayed evidence cannot silently
move when the repository's default branch changes.

Graph schema v0.3 adds deterministic class, function, method, and nested-function nodes. Each
symbol records its qualified name, decorators, parent, and exact source range. Select a file in
the map, then choose one of its symbols to inspect only that range or open the same lines at the
pinned GitHub commit.

Graph schema v0.4 adds symbol-level `extends`, `calls`, and `may-dispatch-to` relationships.
Every relationship records its confidence, resolution method, and exact call or base-class
evidence. Ambiguous dynamic calls remain unresolved unless one bounded internal candidate exists.

Graph schema v0.5 recognizes routes registered on proven FastAPI and `APIRouter` instances,
resolves `Depends(...)` providers, and traces up to three bounded representative execution paths.
Each flow records its ordered nodes and edges, combined confidence, completeness, and every
unresolved call site encountered along the path. The explorer's Execution flows view keeps those
gaps visible and lets each path step open its exact source range.

Graph schema v0.6 recognizes SQLAlchemy declarative bases, abstract model bases, concrete models,
mapped columns, and ORM relationships. Conservative `reads` and `writes` edges connect functions
to proven model classes for common `select`, `get`, `add`, `merge`, `delete`, mutation, and SQL
statement operations. These edges retain source evidence and confidence, extend representative
FastAPI flows into persistence, and appear alongside mapping metadata in the symbol inspector.

Graph schema v0.7 adds deterministic structural risk findings for large callables, high fan-in,
high fan-out, and circular import components. Every finding records severity, classification,
confidence, the rule threshold, exact source evidence, and provenance. The Risk findings view
ranks these hotspots and opens the relevant file or symbol without presenting the heuristic as a
proven defect.

Graph schema v0.8 attaches a retrieval-ready evidence packet to every symbol. A packet combines
the exact source range, docstring or framework metadata, resolved relationship IDs, representative
flow IDs, risk finding IDs, and separately classified fact, heuristic, and interpretation claims.
The explorer now renders its symbol summary, execution role, and structural rationale from this
packet and shows the confidence and provenance for each section. This is the grounding boundary an
LLM can consume later without being allowed to rewrite static-analysis facts.

Graph schema v0.9 makes architectural patterns first-class evidence records. The analyzer now
detects layered architecture, FastAPI boundaries, dependency injection, SQLAlchemy Data Mapper
persistence, repository boundaries, and Unit of Work boundaries when their bounded static evidence is present. Every
pattern retains a fact-or-heuristic classification, confidence, provenance, metrics, and exact
node and edge references. Pattern IDs are also attached to participating symbol evidence packets.

Graph schema v1.0 adds deterministic remediation guidance to every structural risk finding. Each
plan explains why the heuristic matters, proposes ordered actions with effort and confidence, and
lists concrete validation checks. Guidance cites both the finding and its source nodes, remains
classified as heuristic, and never raises confidence above the underlying static finding.

The API is intentionally local-only for this milestone. The hosted site continues to use the committed Cosmic Python fixture until repository analysis runs in an isolated worker service.





