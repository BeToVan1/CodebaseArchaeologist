# Codebase Archaeologist

The hosted build ingests public Python repositories into a bounded file/import inventory. Local
development can additionally use the full FastAPI analyzer for AST symbols, execution flows,
architecture patterns, risks, remediation guidance, and optional LLM interpretation.

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

Development defaults to the full local API at `http://127.0.0.1:8000`; production defaults to
the same-origin hosted inventory endpoint. Set `NEXT_PUBLIC_ANALYZER_API_URL` to override this
choice (an empty value selects the same-origin worker in development too).

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

Graph schema v1.1 identifies the analysis tier explicitly. The hosted site can now ingest a public
GitHub URL at an immutable commit and build a bounded Python file/import inventory without a local
service. This hosted inventory deliberately makes no symbol, flow, pattern, or risk claims. The
full Python analyzer continues to produce the deep tier, and the interface shows which tier is
active so a partial hosted result cannot be mistaken for a complete analysis.

The full AST and framework-aware API remains local-only. The hosted worker provides the bounded
inventory tier; deploying deep analysis still requires an isolated Python analysis service.

The new private Linux service scaffold (`deep_service:create_app`) is separate from the existing
local API. It adds token authentication, bounded jobs, process-group cleanup and Linux resource
limits. It is **not connected to the public site or production-approved**. See
[deep service validation](docs/deep-service-validation.md) for the PowerShell container test
script, exact limits and remaining deployment requirements.

### Explore and share full analysis reports

Generate a report with the Python analyzer:

```powershell
python analyzer.py https://github.com/cosmicpython/code --output graph.json
```

Choose **Open report JSON** in the web app. The file is read in your browser tab, not uploaded.
The map can display the report's symbols, source, execution flows, architectural patterns,
risk findings, and remediation. **Download report (includes source)** saves the current graph;
your coworker can open that JSON in their own browser. **Restore Cosmic Python example** returns
to the bundled example. Reloading discards an imported report; there is no cloud report storage.

Reports must use schema v1.1 and fit within 10 MiB, 500 files, 10,000 nodes and 30,000 edges.
Malformed records, unsafe links, dangling evidence references and unsupported versions are
rejected without replacing the current map. Imported claims and commit metadata are supplied
by the report, not independently attested by the site; the imported-report notice remains visible.
AI requests are disabled for imported reports, including when a local API is configured.

Reports contain source code and may include local directory paths. Review them for sensitive
information before sharing. Importing an inventory report does not upgrade it to deep analysis.
This is a portable-report workflow, not hosted execution of the Python analyzer.

The JavaScript tests include a Python-to-browser report contract check; `python` must be on PATH
or `PYTHON` must name its executable. Repository fixture code is parsed, never executed.

### Hosted inventory limits and trust boundary

- At most 40 Python files are inventoried, sorted by repository path. Symlinks are excluded.
- Source reads are capped at 200,000 **bytes**, even if the upstream server ignores the Range
  header. A 30-second deadline applies to GitHub analysis requests; metadata and incoming JSON
  bodies are also bounded. No repository code is executed.
- Import candidates come from a lightweight lexical scan, not the Python AST analyzer. They are
  always labeled **heuristic**, with rule-based (not statistically calibrated) confidence.
  Comments and strings are ignored; basic `src/` and relative imports are supported. Multiline
  imports, dynamic imports, custom module roots, and ambiguous paths may remain unmatched.
- The interface reports omitted files, incomplete GitHub trees, unreadable or truncated source,
  and unmatched import references. File totals from truncated trees are lower bounds.
- Symbols, flows, patterns, risks, and remediation say **not analyzed** in inventory mode. Empty
  results must not be interpreted as a clean bill of health. File details do not invent AST facts
  or architectural intent from an inventory-only response.





