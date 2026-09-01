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

The API is intentionally local-only for this milestone. The hosted site continues to use the committed Cosmic Python fixture until repository analysis runs in an isolated worker service.


