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

The API is intentionally local-only for this milestone. The hosted site continues to use the committed Cosmic Python fixture until repository analysis runs in an isolated worker service.

