# Codebase Archaeologist

The public website supports both a bounded file/import inventory and explicit **Deep analysis**
for public Python repositories. Deep analysis uses the isolated Oracle Python service for AST
symbols, supported execution flows, architectural patterns, risks and remediation guidance.
Optional LLM interpretation is implemented behind a disabled, fail-closed hosted route; it is not
enabled on the public website. This is an early beta, not a complete runtime understanding of
arbitrary Python programs. See [acceptance results](docs/acceptance-testing.md) for verified
workflows, known issues and remaining release checks.

## Local development

Local JSON reports also include a bounded `test_proximity` index of recorded
test-associated symbol calls and module imports. Links reference existing graph
edges for source evidence. These are path-based heuristics, not execution or
coverage results; absent links do not mean untested code. The explorer's
Test evidence section displays these links for the selected file or symbol,
with buttons to open source and separate module-import context. This feature
is deployed on Oracle and published on the website. Use a fresh Deep report;
older reports and the bundled example are not retroactively enriched.

Local analyzer reports now include optional `project_discovery` metadata from
the root `pyproject.toml`: standard project name/version, Python requirement,
dependencies/optional groups, scripts, GUI scripts, and build-system declarations.
These are literal declarations, not verified installed versions or resolved CLI
flows. Dynamic fields stay unresolved. No backend, setup hook, dependency install,
or referenced URL is executed. Missing/invalid/skipped manifests are distinguished
and do not prevent the Python graph from being analyzed.

This first discovery slice is deployed on Oracle and the public website; it is
available in new Deep analysis JSON reports and the explorer's expandable **Project details**
panel. The panel distinguishes unavailable metadata from missing manifests and
labels imported declarations as unverified. Older public reports remain supported.
Older reports and the bundled example are not retroactively enriched. Nested project discovery,
tool-specific/legacy metadata, lockfiles, and exact manifest line spans remain
future work. Limits: 256 KiB manifest, 128 records, 128 list items, 512 characters
per value, and 64 KiB declaration output. Fields containing direct dependency
references are omitted with a warning; this is not a comprehensive secret scanner.

Install both dependency sets:

```powershell
python -m pip install -r requirements-dev.txt
pnpm install
```

Start the analyzer API in one terminal:

```powershell
pnpm run dev:api
```

The older `/api/interpret` prototype can read `OPENAI_API_KEY` and `OPENAI_MODEL`, but it accepts
caller-provided evidence and must not be exposed publicly. New work uses the authenticated,
reference-only route described below. It sends only the selected symbol's server-retained evidence
packet and a source excerpt capped at 12,000 characters. Generated claims are labelled as
interpretations and rejected if they cite evidence outside that packet.

### Explanation-quality evaluation

The offline [evaluation workflow](docs/interpretation-evaluation.md) provides six
synthetic code cases and a review rubric for behavior, execution, rationale,
evidence, uncertainty, and source-instruction handling. Run
`python interpretation_evaluation.py assess` to see coverage; missing outputs or
reviews remain pending. It makes no model calls and does not claim measured
model accuracy. Valid citation IDs alone do not establish a true explanation.

### Local authenticated evidence preview (LLM disabled by default)

An opt-in developer API connects commit-verified source capture to the expiring
report store. It is not connected to the website's AI panel or to `/api/interpret`.
Enable `ARCHAEOLOGIST_LOCAL_EVIDENCE_ENABLED=true` before starting `api:app` and
configure a separate cryptographically random `ARCHAEOLOGIST_LOCAL_EVIDENCE_TOKEN`
of 32–256 printable ASCII characters (no spaces). Bind to `127.0.0.1`, use one
worker, and keep the token in local process configuration—not browser code,
public environment variables, source control, or pasted logs. Do not reuse the
Oracle service token. No token is generated or configured automatically.

Use a local HTTP client with `Authorization: Bearer <local token>` and JSON:

- `POST /api/evidence/analyze` accepts only `{"repositoryUrl":"https://github.com/owner/repo"}`
  and returns `graph`, `reportId`, and `expiresInSeconds`.
- `POST /api/evidence/prepare` accepts only `{"reportId":"<returned reference>","nodeId":"<selected symbol ID>"}`
  and returns the pinned `commitSha`, `evidencePacket`, `sourceExcerpt`, and
  `modelCalled: false`. It does not accept source text, claims, or an owner ID.
- `POST /api/evidence/interpret` accepts the same reference-only selection and
  returns `reportId`, `nodeId`, and a citation-checked `interpretation` when
  explicitly configured. The normal `api:app` startup leaves this route disabled
  (503), even if the legacy `OPENAI_API_KEY` is present.

Developer wiring only: `create_local_evidence_app(interpretation_runtime=...)`
accepts a server-owned `LocalInterpretationRuntime` containing an execution
policy, a separately initialized budget ledger path, an SDK client, and explicit
`enabled=True`. The caller must manage the client's lifetime. No request can set
these fields, create the ledger, select a model, or supply replacement evidence.
No live pricing, real key, or paid budget has been configured. The OpenAI execution prototype
is retained only for offline compatibility tests; it is not the selected public provider.

The selected hosted direction is Cloudflare Workers AI on the Free plan. The adapter uses
`@cf/meta/llama-3.3-70b-instruct-fp8-fast` because it supports JSON Mode and remains available on
the free plan. A same-origin Worker route is implemented but remains disabled without an explicit
enable flag and either a managed AI binding or server-only Cloudflare account ID and scoped API
token. The REST option calls only Cloudflare's fixed account/model endpoint. It sends one bounded request,
does not stream or retry, treats source as untrusted data, and validates every returned section,
confidence and evidence reference. Free-allocation enforcement is owned by Cloudflare, not this
adapter; production must remain disabled until the account is verified as Workers Free.

The private deep-analysis service now has the server side of the public trust boundary locally:
its isolated worker captures the exact commit-verified Python bytes used for analysis, passes them
to a bounded in-memory store, and returns an opaque 15-minute report reference in response headers.
`POST /api/evidence/prepare` accepts only that reference and a selected symbol ID, requires the
service token and the same server-derived network owner key, and returns the retained packet and
source excerpt. It never accepts a client packet, source range, source text, or owner identity.
The evidence-capable image is deployed on Oracle. The frontend now retains the opaque report
reference only in memory and sends only that reference plus the selected node ID to the same-origin
route. It verifies the returned commit and node before rendering. The reference is never added to
downloaded reports. These website changes have not been published, and inference remains disabled.
Provider/grounding failures get a sanitized 502; exhausted budget gets 429;
unavailable execution policy, capacity, storage, or incomplete output gets 503.
None causes an automatic retry or refund. Repeating a valid request is a new
reservation and can incur another provider call; there is no idempotency cache.

Requests require a loopback transport peer and no browser `Origin` header.
This is a single-local-operator credential, **not public multi-user authentication**.
Rotating it prevents the new token from accessing the old token's references.
Restarting loses all references; they otherwise expire after 15 minutes. Existing
storage limits apply (including 4 MiB per report), and concurrent analysis gets
429. Oversized bodies get 413; unavailable storage gets 503; missing, expired,
or wrong-owner references get 404. The routes are absent when disabled at startup.

The legacy local `/api/interpret` still accepts caller-supplied evidence and can
call the provider when configured; this preview does not secure or replace it.
Do not expose either local development API publicly. Public LLM integration still needs Workers
Free account verification, scoped server credentials, explicit activation approval, and semantic
review. Body-read timeout handling here
does not cancel an already-running synchronous analysis job.

### Start the web application

In another terminal:

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

As of 2026-09-03, hosted deep analysis is enabled on the public site. Select it
explicitly in the analysis-mode menu; inventory remains a separate mode with no
automatic fallback. The website forwards bounded requests to the authenticated
Oracle service without exposing its token to the browser. Repository code is
parsed, never executed.

The service permits one active analysis, with a persistent SQLite ledger on the
existing Oracle disk enforcing 3 admitted attempts per network per 10 minutes and
30 total per hour. Failed/cancelled admitted attempts count. No Sites D1 resource
is required. Network limits are not authenticated user identity or bot protection.
See [hosted deep integration](docs/hosted-deep-integration.md) and
[deep service validation](docs/deep-service-validation.md) for the trust boundaries,
resource limits and historical backend validation.

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





