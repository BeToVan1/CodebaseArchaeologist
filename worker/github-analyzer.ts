const GITHUB_REPOSITORY = /^https:\/\/github\.com\/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\/([A-Za-z0-9._-]+?)(?:\.git)?\/?$/i;
export const MAX_PYTHON_FILES = 40;
export const MAX_SOURCE_BYTES = 200_000;
const GITHUB_HEADERS = { Accept: "application/vnd.github+json", "User-Agent": "Codebase-Archaeologist", "X-GitHub-Api-Version": "2022-11-28" };
type Fetcher = typeof fetch;
type MetadataStage = "repository" | "commit" | "tree";

/** Emit only fixed labels: never exception messages, URLs, headers, or source. */
function reportFailure(stage: string, operation: string, error: unknown) {
  const message = error instanceof Error ? error.message : "";
  const name = error instanceof Error && ["TypeError", "SyntaxError", "RangeError", "AbortError", "TimeoutError"].includes(error.name) ? error.name : "Error";
  const category = /illegal invocation|incorrect.*this/i.test(message) ? "invocation"
    : /redirect/i.test(message) ? "redirect"
    : /not implemented|unsupported|not supported/i.test(message) ? "unsupported"
    : /denied|not allowed|forbidden|permission/i.test(message) ? "permission"
    : /network|fetch failed|connect|dns/i.test(message) ? "network" : "other";
  console.error("hosted-analysis-failure", { stage, operation, name, category });
}
type TreeEntry = { path: string; type: string; mode?: string; size?: number };
type SourceFile = { path: string; size: number; source: string; truncated: boolean; sourceError?: string };

class AnalysisError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: {
    "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...(status === 405 ? { Allow: "POST" } : {}),
  } });
}

/** Bound memory even if the origin ignores Range or Content-Length. */
export async function readBoundedBody(body: ReadableStream<Uint8Array> | null, limit: number) {
  if (!body) return { text: "", bytes: 0, truncated: false };
  const reader = body.getReader();
  const buffer = new Uint8Array(limit);
  let bytes = 0;
  let truncated = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      const remaining = limit - bytes;
      const retained = value.subarray(0, remaining);
      buffer.set(retained, bytes);
      bytes += retained.byteLength;
      if (value.byteLength > remaining) { truncated = true; break; }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  // Streaming decode drops an incomplete final UTF-8 character at the cap.
  const text = new TextDecoder("utf-8", { fatal: true }).decode(buffer.subarray(0, bytes), { stream: truncated });
  return { text, bytes, truncated };
}

async function githubJson(url: string, fetchImpl: Fetcher, signal: AbortSignal, stage: MetadataStage): Promise<Record<string, unknown>> {
  let operation = "fetch";
  try {
    // The hosted runtime rejects redirect: "error". Manual returns 3xx to the
    // status check below without following Location or leaving the fixed origin.
    const response = await fetchImpl(url, { headers: GITHUB_HEADERS, redirect: "manual", signal });
    operation = "status";
    if (!response.ok) {
      await response.body?.cancel();
      if (response.status >= 300 && response.status < 400) throw new AnalysisError("GitHub redirected this repository. Enter its current public GitHub URL.", 502);
      if (response.status === 404) throw new AnalysisError("Repository not found. Confirm that it is public and the URL is correct.", 404);
      if (response.status === 403 || response.status === 429) throw new AnalysisError("GitHub's public request limit was reached. Please try again later.", 429);
      throw new AnalysisError(`GitHub request failed (${response.status}).`, 502);
    }
    operation = "body";
    const body = await readBoundedBody(response.body, 8 * 1024 * 1024);
    if (body.truncated) throw new AnalysisError("Repository metadata exceeds the hosted analysis limit. Use the full Python analyzer.", 413);
    operation = "json";
    const value: unknown = JSON.parse(body.text);
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new AnalysisError("GitHub returned invalid repository metadata.", 502);
    return value as Record<string, unknown>;
  } catch (error) {
    if (!(error instanceof AnalysisError)) reportFailure(stage, operation, error);
    throw error;
  }
}

function parseRepositoryUrl(repositoryUrl: string) {
  const match = GITHUB_REPOSITORY.exec(repositoryUrl.trim());
  if (!match || [".", ".."].includes(match[2])) throw new AnalysisError("Enter a public GitHub URL in the form https://github.com/owner/repository.", 400);
  return { owner: match[1], repository: match[2], url: `https://github.com/${match[1]}/${match[2]}` };
}

function moduleName(path: string) {
  const parts = path.replace(/^src\//, "").slice(0, -3).split("/");
  if (parts.at(-1) === "__init__") parts.pop();
  return parts.join(".");
}

/** Conservative lexical masking, not a Python parser. Edges remain heuristics. */
function maskStringsAndComments(source: string) {
  let result = "", quote = "";
  let triple = false;
  for (let i = 0; i < source.length; i++) {
    const char = source[i];
    if (quote) {
      if (char === "\\") { result += " " + (source[i + 1] === "\n" ? "\n" : " "); i++; }
      else if (source.startsWith(quote.repeat(triple ? 3 : 1), i)) {
        result += " ".repeat(triple ? 3 : 1); i += triple ? 2 : 0; quote = "";
      } else result += char === "\n" ? "\n" : " ";
    } else if (char === "#") {
      while (i < source.length && source[i] !== "\n") { result += " "; i++; }
      if (i < source.length) result += "\n";
    } else if (char === '"' || char === "'") {
      quote = char; triple = source.startsWith(char.repeat(3), i);
      result += " ".repeat(triple ? 3 : 1); i += triple ? 2 : 0;
    } else result += char;
  }
  return result;
}

function importedModules(source: string, sourcePath: string) {
  const imports: Array<{ module: string; fallback?: string; line: number; expression: string }> = [];
  const parts = moduleName(sourcePath).split(".");
  const sourcePackage = sourcePath.endsWith("/__init__.py") ? parts : parts.slice(0, -1);
  const originals = source.split("\n");
  maskStringsAndComments(source).split("\n").forEach((line, index) => {
    const evidence = { line: index + 1, expression: originals[index].trim() };
    const plain = /^\s*import\s+(.+)$/.exec(line);
    if (plain) for (const item of plain[1].split(",")) {
      const name = item.trim().split(/\s+as\s+/)[0];
      if (/^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$/.test(name)) imports.push({ module: name, ...evidence });
    }
    const from = /^\s*from\s+([.A-Za-z_][\w.]*)\s+import\s+(.+)$/.exec(line);
    if (!from) return;
    const dots = from[1].match(/^\.+/)?.[0].length ?? 0;
    if (dots > sourcePackage.length) return;
    const base = dots ? sourcePackage.slice(0, sourcePackage.length - dots + 1) : [];
    const name = [...base, ...from[1].slice(dots).split(".").filter(Boolean)].join(".");
    // Multiline and dynamic imports are outside this scanner's scope.
    if (!name || from[2].includes("(") || from[2].includes("\\")) return;
    for (const item of from[2].split(",")) {
      const member = item.trim().split(/\s+as\s+/)[0];
      if (member === "*") imports.push({ module: name, ...evidence });
      else if (/^[A-Za-z_]\w*$/.test(member)) imports.push({ module: `${name}.${member}`, fallback: name, ...evidence });
    }
  });
  return imports;
}

async function fetchSources(owner: string, repository: string, sha: string, files: TreeEntry[], fetchImpl: Fetcher, signal: AbortSignal) {
  const results: SourceFile[] = [];
  for (let offset = 0; offset < files.length; offset += 5) {
    signal.throwIfAborted();
    results.push(...await Promise.all(files.slice(offset, offset + 5).map(async (entry): Promise<SourceFile> => {
      try {
        const path = entry.path.split("/").map(encodeURIComponent).join("/");
        const response = await fetchImpl(`https://raw.githubusercontent.com/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}/${sha}/${path}`,
          { headers: entry.size === 0 ? {} : { Range: `bytes=0-${MAX_SOURCE_BYTES - 1}` }, redirect: "manual", signal });
        if (!response.ok) { await response.body?.cancel(); throw new Error("Source unavailable"); }
        const body = await readBoundedBody(response.body, MAX_SOURCE_BYTES);
        return { path: entry.path, size: entry.size ?? body.bytes, source: body.text, truncated: body.truncated || (entry.size ?? 0) > body.bytes };
      } catch {
        signal.throwIfAborted();
        return { path: entry.path, size: entry.size ?? 0, source: "", truncated: false, sourceError: "Source could not be loaded as UTF-8 from this snapshot." };
      }
    })));
  }
  return results;
}

export async function analyzePublicGithubRepository(repositoryUrl: string, fetchImpl: Fetcher = fetch, callerSignal?: AbortSignal) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30_000);
  const signal = callerSignal ? AbortSignal.any([callerSignal, controller.signal]) : controller.signal;
  try { signal.throwIfAborted(); return await analyzeSnapshot(repositoryUrl, fetchImpl, signal); }
  finally { clearTimeout(timeout); }
}

async function analyzeSnapshot(repositoryUrl: string, fetchImpl: Fetcher, signal: AbortSignal) {
  const { owner, repository, url } = parseRepositoryUrl(repositoryUrl);
  const apiRoot = `https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repository)}`;
  const metadata = await githubJson(apiRoot, fetchImpl, signal, "repository");
  const branch = typeof metadata.default_branch === "string" ? metadata.default_branch : "main";
  const commit = await githubJson(`${apiRoot}/commits/${encodeURIComponent(branch)}`, fetchImpl, signal, "commit");
  const commitSha = typeof commit.sha === "string" ? commit.sha.toLowerCase() : "";
  const treeSha = (commit.commit as { tree?: { sha?: string } } | undefined)?.tree?.sha;
  if (!/^[0-9a-f]{40}$/.test(commitSha) || typeof treeSha !== "string" || !/^[0-9a-f]{40}$/i.test(treeSha)) throw new AnalysisError("GitHub did not return a stable commit snapshot.", 502);
  const tree = await githubJson(`${apiRoot}/git/trees/${treeSha}?recursive=1`, fetchImpl, signal, "tree");
  if (!Array.isArray(tree.tree)) throw new AnalysisError("GitHub returned an invalid repository tree.", 502);
  const discovered = (tree.tree as TreeEntry[])
    .filter((entry) => entry && entry.type === "blob" && entry.mode !== "120000" && typeof entry.path === "string" && entry.path.endsWith(".py"))
    .sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  const sources = await fetchSources(owner, repository, commitSha, discovered.slice(0, MAX_PYTHON_FILES), fetchImpl, signal);
  const modules = new Map<string, string[]>();
  for (const file of sources) { const name = moduleName(file.path); modules.set(name, [...(modules.get(name) ?? []), file.path]); }
  const nodes = sources.map((file) => ({ id: `file:${file.path}`, kind: "file", path: file.path, size_bytes: file.size,
    source: file.source, source_truncated: file.truncated, ...(file.sourceError ? { source_error: file.sourceError } : {}) }));
  const edgeKeys = new Set<string>();
  const edges: Array<Record<string, unknown>> = [];
  let unmatchedImports = 0;
  for (const file of sources) {
    const scanSource = file.truncated ? file.source.slice(0, file.source.lastIndexOf("\n") + 1) : file.source;
    for (const imported of importedModules(scanSource, file.path)) {
      const targets = modules.get(imported.module) ?? (imported.fallback ? modules.get(imported.fallback) : undefined);
      if (!targets || targets.length !== 1) { unmatchedImports++; continue; }
      const targetPath = targets[0];
      if (targetPath === file.path) continue;
      const key = `${file.path}->${targetPath}`;
      if (edgeKeys.has(key)) continue;
      edgeKeys.add(key);
      edges.push({ id: `import:${file.path}:${targetPath}`, source: `file:${file.path}`, target: `file:${targetPath}`, kind: "imports",
        classification: "heuristic", confidence: 0.75, resolution_method: "Lexical import candidate matched to snapshot paths (not Python AST)",
        evidence: { path: file.path, line: imported.line, expression: imported.expression } });
    }
  }
  return {
    schema_version: "1.1", repository: { name: `${owner}/${repository}`, url, pinned_url: `${url}/tree/${commitSha}`, source: "github" },
    snapshot: { commit_sha: commitSha }, source_url: url, nodes, edges, flows: [], findings: [], patterns: [],
    analysis: { tier: "inventory", engine: "hosted-github-inventory", limitations: [
      "File inventory comes from a pinned GitHub tree; import connections are lexical heuristics, not Python AST facts.",
      "Multiline imports, dynamic imports, and custom module roots may be missed. Unmatched imports may be external, ambiguous, or outside the file limit.",
      "Symbols, execution flows, architecture patterns, risks, and remediation are not analyzed in inventory mode.",
    ] },
    coverage: { python_files_total_found: discovered.length, python_files_analyzed: sources.length,
      python_files_truncated: discovered.length > sources.length || tree.truncated === true, github_tree_truncated: tree.truncated === true,
      source_failures: sources.filter((file) => file.sourceError).length, source_truncations: sources.filter((file) => file.truncated).length,
      unmatched_imports: unmatchedImports, file_limit: MAX_PYTHON_FILES, source_byte_limit: MAX_SOURCE_BYTES },
  };
}

export async function handleAnalyzeRequest(request: Request, fetchImpl: Fetcher = fetch) {
  if (request.method !== "POST") return jsonResponse({ detail: "Method not allowed." }, 405);
  try {
    const incoming = await readBoundedBody(request.body, 2048);
    if (incoming.truncated) return jsonResponse({ detail: "Request body exceeds the 2 KB limit." }, 413);
    let body: unknown;
    try { body = JSON.parse(incoming.text); } catch { return jsonResponse({ detail: "Request body must be valid JSON." }, 400); }
    const repositoryUrl = body && typeof body === "object" && !Array.isArray(body) ? (body as Record<string, unknown>).repositoryUrl : undefined;
    if (typeof repositoryUrl !== "string" || repositoryUrl.length > 300) return jsonResponse({ detail: "repositoryUrl must be a URL of at most 300 characters." }, 400);
    return jsonResponse(await analyzePublicGithubRepository(repositoryUrl, fetchImpl, request.signal));
  } catch (error) {
    if (error instanceof AnalysisError) return jsonResponse({ detail: error.message }, error.status);
    if (error instanceof Error && ["AbortError", "TimeoutError"].includes(error.name)) return jsonResponse({ detail: "Analysis timed out or was cancelled. Try a smaller repository or use the full Python analyzer.", }, 504);
    reportFailure("request", "analysis", error);
    return jsonResponse({ detail: "Repository analysis could not complete. Please retry or use the full Python analyzer." }, 502);
  }
}
