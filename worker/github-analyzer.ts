const GITHUB_REPOSITORY = /^https:\/\/github\.com\/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\/([A-Za-z0-9._-]+?)(?:\.git)?\/?$/i;
const MAX_PYTHON_FILES = 40;
const MAX_SOURCE_CHARACTERS = 200_000;
const GITHUB_HEADERS = {
  Accept: "application/vnd.github+json",
  "User-Agent": "Codebase-Archaeologist",
  "X-GitHub-Api-Version": "2022-11-28",
};

type Fetcher = typeof fetch;
type GithubTreeEntry = { path?: string; type?: string; size?: number };
type SourceFile = { path: string; size: number; source: string; sourceError?: string };

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

async function githubJson(url: string, fetchImpl: Fetcher): Promise<Record<string, unknown>> {
  const response = await fetchImpl(url, { headers: GITHUB_HEADERS });
  if (!response.ok) {
    if (response.status === 404) throw new Error("Repository not found. Confirm that it is public and the URL is correct.");
    if (response.status === 403 || response.status === 429) throw new Error("GitHub's public request limit was reached. Please try again later.");
    throw new Error(`GitHub request failed (${response.status}).`);
  }
  return await response.json() as Record<string, unknown>;
}

function parseRepositoryUrl(repositoryUrl: string) {
  const match = GITHUB_REPOSITORY.exec(repositoryUrl.trim());
  if (!match) throw new Error("Enter a public GitHub URL in the form https://github.com/owner/repository.");
  return { owner: match[1], repository: match[2], url: `https://github.com/${match[1]}/${match[2]}` };
}

function moduleName(path: string) {
  const withoutSuffix = path.slice(0, -3);
  const segments = withoutSuffix.split("/");
  if (segments.at(-1) === "__init__") segments.pop();
  return segments.join(".");
}

function importedModules(source: string, sourcePath: string) {
  const imports: Array<{ module: string; line: number; expression: string }> = [];
  const sourcePackage = moduleName(sourcePath).split(".").slice(0, -1);
  source.split("\n").forEach((line, index) => {
    const plainImport = /^\s*import\s+(.+)$/.exec(line);
    if (plainImport) {
      for (const item of plainImport[1].split(",")) {
        const name = item.trim().split(/\s+as\s+/i)[0];
        if (/^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$/.test(name)) imports.push({ module: name, line: index + 1, expression: line.trim() });
      }
    }
    const fromImport = /^\s*from\s+([.A-Za-z_][\w.]*)\s+import\s+(.+)$/.exec(line);
    if (fromImport) {
      const raw = fromImport[1];
      const dots = raw.match(/^\.+/)?.[0].length ?? 0;
      const suffix = raw.slice(dots);
      const base = dots ? sourcePackage.slice(0, Math.max(0, sourcePackage.length - dots + 1)) : [];
      const name = [...base, ...suffix.split(".").filter(Boolean)].join(".");
      const importedNames = fromImport[2].replace(/[()]/g, "").split(",")
        .map((item) => item.trim().split(/\s+as\s+/i)[0])
        .filter((item) => /^[A-Za-z_]\w*$/.test(item));
      if (name) {
        for (const importedName of importedNames.length ? importedNames : [""]) {
          imports.push({ module: importedName ? `${name}.${importedName}` : name, line: index + 1, expression: line.trim() });
        }
      }
    }
  });
  return imports;
}

function resolveModule(name: string, modules: Map<string, string>) {
  let candidate = name;
  while (candidate) {
    const path = modules.get(candidate);
    if (path) return path;
    candidate = candidate.includes(".") ? candidate.slice(0, candidate.lastIndexOf(".")) : "";
  }
  return null;
}

async function fetchSources(owner: string, repository: string, commitSha: string, files: GithubTreeEntry[], fetchImpl: Fetcher) {
  const results: SourceFile[] = [];
  for (let offset = 0; offset < files.length; offset += 8) {
    const batch = files.slice(offset, offset + 8);
    results.push(...await Promise.all(batch.map(async (entry) => {
      const path = String(entry.path);
      const rawPath = path.split("/").map(encodeURIComponent).join("/");
      const response = await fetchImpl(
        `https://raw.githubusercontent.com/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/${commitSha}/${rawPath}`,
        { headers: { Range: `bytes=0-${MAX_SOURCE_CHARACTERS - 1}` } },
      );
      if (!response.ok) return { path, size: Number(entry.size ?? 0), source: "", sourceError: `Source request failed (${response.status})` };
      const source = await response.text();
      return { path, size: Number(entry.size ?? source.length), source: source.slice(0, MAX_SOURCE_CHARACTERS) };
    })));
  }
  return results;
}

export async function analyzePublicGithubRepository(repositoryUrl: string, fetchImpl: Fetcher = fetch) {
  const { owner, repository, url } = parseRepositoryUrl(repositoryUrl);
  const apiRoot = `https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}`;
  const metadata = await githubJson(apiRoot, fetchImpl);
  const defaultBranch = typeof metadata.default_branch === "string" ? metadata.default_branch : "main";
  const commit = await githubJson(`${apiRoot}/commits/${encodeURIComponent(defaultBranch)}`, fetchImpl);
  const commitSha = typeof commit.sha === "string" ? commit.sha.toLowerCase() : "";
  const commitData = commit.commit as Record<string, unknown> | undefined;
  const treeData = commitData?.tree as Record<string, unknown> | undefined;
  const treeSha = typeof treeData?.sha === "string" ? treeData.sha : "";
  if (!/^[0-9a-f]{40}$/.test(commitSha) || !treeSha) throw new Error("GitHub did not return a stable commit snapshot.");

  const tree = await githubJson(`${apiRoot}/git/trees/${treeSha}?recursive=1`, fetchImpl);
  const entries = Array.isArray(tree.tree) ? tree.tree as GithubTreeEntry[] : [];
  const discovered = entries
    .filter((entry) => entry.type === "blob" && typeof entry.path === "string" && entry.path.endsWith(".py"))
    .sort((a, b) => String(a.path).localeCompare(String(b.path)));
  const selected = discovered.slice(0, MAX_PYTHON_FILES);
  const sources = await fetchSources(owner, repository, commitSha, selected, fetchImpl);
  const modules = new Map(sources.map((file) => [moduleName(file.path), file.path]));
  const nodes = sources.map((file) => ({
    id: `file:${file.path}`,
    kind: "file",
    path: file.path,
    size_bytes: file.size,
    source: file.source,
    source_truncated: file.size > MAX_SOURCE_CHARACTERS,
    ...(file.sourceError ? { source_error: file.sourceError } : {}),
  }));
  const edgeKeys = new Set<string>();
  const edges: Array<Record<string, unknown>> = [];
  for (const file of sources) {
    for (const imported of importedModules(file.source, file.path)) {
      const targetPath = resolveModule(imported.module, modules);
      if (!targetPath || targetPath === file.path) continue;
      const key = `${file.path}->${targetPath}`;
      if (edgeKeys.has(key)) continue;
      edgeKeys.add(key);
      edges.push({
        id: `import:${file.path}:${targetPath}`,
        source: `file:${file.path}`,
        target: `file:${targetPath}`,
        kind: "imports",
        confidence: 1,
        resolution_method: "GitHub snapshot module map",
        evidence: { path: file.path, line: imported.line, expression: imported.expression },
      });
    }
  }

  return {
    schema_version: "1.1",
    repository: { name: `${owner}/${repository}`, url, pinned_url: `${url}/tree/${commitSha}`, source: "github" },
    snapshot: { commit_sha: commitSha },
    source_url: url,
    nodes,
    edges,
    flows: [],
    findings: [],
    patterns: [],
    analysis: {
      tier: "inventory",
      engine: "hosted-github-inventory",
      limitations: [
        "Hosted analysis currently maps Python files and resolved internal imports only.",
        "Symbols, execution flows, architecture patterns, risks, and remediation require the full Python analyzer.",
      ],
    },
    coverage: {
      python_files_total_found: discovered.length,
      python_files_analyzed: selected.length,
      python_files_truncated: discovered.length > selected.length || tree.truncated === true,
      github_tree_truncated: tree.truncated === true,
      source_failures: sources.filter((file) => file.sourceError).length,
    },
  };
}

export async function handleAnalyzeRequest(request: Request, fetchImpl: Fetcher = fetch) {
  if (request.method !== "POST") return jsonResponse({ detail: "Method not allowed." }, 405);
  try {
    const body = await request.json() as { repositoryUrl?: unknown };
    if (typeof body.repositoryUrl !== "string" || body.repositoryUrl.length > 300) {
      return jsonResponse({ detail: "repositoryUrl must be a URL of at most 300 characters." }, 400);
    }
    return jsonResponse(await analyzePublicGithubRepository(body.repositoryUrl, fetchImpl));
  } catch (error) {
    const detail = error instanceof Error ? error.message : "Repository analysis failed.";
    const status = detail.startsWith("Enter a public") ? 400
      : detail.startsWith("Repository not found") ? 404
      : detail.includes("request limit") ? 429
      : 502;
    return jsonResponse({ detail }, status);
  }
}
