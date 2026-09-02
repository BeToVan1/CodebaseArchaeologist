import assert from "node:assert/strict";
import test from "node:test";
import { analyzePublicGithubRepository, handleAnalyzeRequest, MAX_SOURCE_BYTES, readBoundedBody } from "../worker/github-analyzer.ts";
import { inventoryStatus, inventoryUnavailable } from "../app/analysis-status.ts";

const commitSha = "a".repeat(40);
const treeSha = "b".repeat(40);

function fixtureFetch(url) {
  const value = String(url);
  if (value === "https://api.github.com/repos/example/project") {
    return Promise.resolve(Response.json({ default_branch: "main" }));
  }
  if (value.endsWith("/commits/main")) {
    return Promise.resolve(Response.json({ sha: commitSha, commit: { tree: { sha: treeSha } } }));
  }
  if (value.includes(`/git/trees/${treeSha}`)) {
    return Promise.resolve(Response.json({ tree: [
      { path: "project/__init__.py", type: "blob", size: 0 },
      { path: "project/api.py", type: "blob", size: 28 },
      { path: "project/service.py", type: "blob", size: 13 },
      { path: "README.md", type: "blob", size: 10 },
    ] }));
  }
  if (value.endsWith("/project/api.py")) {
    return Promise.resolve(new Response("from project import service\n"));
  }
  if (value.endsWith("/project/service.py")) {
    return Promise.resolve(new Response("def run():\n    pass\n"));
  }
  if (value.endsWith("/project/__init__.py")) return Promise.resolve(new Response(""));
  return Promise.resolve(new Response("not found", { status: 404 }));
}

test("hosted analyzer pins and maps a public Python repository inventory", async () => {
  const graph = await analyzePublicGithubRepository("https://github.com/example/project", fixtureFetch);

  assert.equal(graph.schema_version, "1.1");
  assert.equal(graph.snapshot.commit_sha, commitSha);
  assert.equal(graph.repository.pinned_url, `https://github.com/example/project/tree/${commitSha}`);
  assert.equal(graph.analysis.tier, "inventory");
  assert.equal(graph.nodes.length, 3);
  assert.deepEqual(graph.edges.map((edge) => [edge.source, edge.target]), [
    ["file:project/api.py", "file:project/service.py"],
  ]);
  assert.equal(graph.flows.length, 0);
  assert.equal(graph.findings.length, 0);
  assert.equal(graph.patterns.length, 0);
  assert.ok(graph.edges.every((edge) => edge.classification === "heuristic" && edge.confidence < 1));
  assert.ok(graph.edges.every((edge) => edge.resolution_method.includes("not Python AST")));
});

function repositoryFixture(sources, { truncated = false, failed = [], invalidTree = false } = {}) {
  const calls = [];
  const fetcher = async (url, options) => {
    const value = String(url);
    calls.push({ url: value, options });
    assert.equal(options.redirect, "error");
    assert.ok(options.signal instanceof AbortSignal);
    if (value.endsWith("/project")) return Response.json({ default_branch: "main" });
    if (value.endsWith("/commits/main")) return Response.json({ sha: commitSha, commit: { tree: { sha: treeSha } } });
    if (value.includes("/git/trees/")) return Response.json(invalidTree ? {} : {
      truncated,
      tree: Object.entries(sources).map(([path, source]) => ({ path, type: "blob", mode: "100644", size: new TextEncoder().encode(source).byteLength })),
    });
    assert.ok(value.startsWith(`https://raw.githubusercontent.com/example/project/${commitSha}/`));
    const path = decodeURIComponent(value.split(`/${commitSha}/`)[1]);
    if (failed.includes(path)) throw new Error("Simulated network failure");
    if (sources[path] === "" && options.headers?.Range) return new Response("", { status: 416 });
    return new Response(sources[path]); // Deliberately ignores Range.
  };
  return { fetcher, calls };
}

test("source reads enforce the byte cap when Range is ignored", async () => {
  const fixture = repositoryFixture({ "big.py": "#\n".repeat(MAX_SOURCE_BYTES), "empty.py": "" });
  const graph = await analyzePublicGithubRepository("https://github.com/example/project", fixture.fetcher);
  const big = graph.nodes.find((node) => node.path === "big.py");
  assert.equal(new TextEncoder().encode(big.source).length, MAX_SOURCE_BYTES);
  assert.equal(big.source_truncated, true);
  assert.equal(graph.coverage.source_truncations, 1);
  assert.equal(graph.nodes.find((node) => node.path === "empty.py").source_truncated, false);
  assert.equal(graph.nodes.find((node) => node.path === "empty.py").source_error, undefined);
});

test("bounded reads cancel oversized streams and preserve UTF-8 boundaries", async () => {
  let cancelled = false;
  const body = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode("a😀z")); }, cancel() { cancelled = true; } });
  const result = await readBoundedBody(body, 3);
  assert.equal(result.text, "a");
  assert.equal(result.bytes, 3);
  assert.equal(result.truncated, true);
  assert.equal(cancelled, true);
});

test("file caps, truncated GitHub trees, and source failures remain explicit", async () => {
  const sources = Object.fromEntries(Array.from({ length: 45 }, (_, i) => [`file${String(i).padStart(2, "0")}.py`, "# source\n"]));
  const fixture = repositoryFixture(sources, { truncated: true, failed: ["file00.py"] });
  const graph = await analyzePublicGithubRepository("https://github.com/example/project", fixture.fetcher);
  assert.equal(graph.nodes.length, 40);
  assert.equal(fixture.calls.filter((call) => call.url.includes("raw.githubusercontent")).length, 40);
  assert.equal(graph.coverage.python_files_total_found, 45);
  assert.equal(graph.coverage.python_files_truncated, true);
  assert.equal(graph.coverage.github_tree_truncated, true);
  assert.equal(graph.coverage.source_failures, 1);
  assert.ok(graph.nodes.find((node) => node.path === "file00.py").source_error);
  const status = inventoryStatus(graph.coverage);
  assert.equal(status.partial, true);
  assert.match(status.summary, /40 of at least 45/);
  assert.ok(status.warnings.some((message) => message.includes("5 discovered Python files were omitted")));
  assert.ok(status.warnings.some((message) => message.includes("could not be read")));
});

test("scanner ignores docstrings and comments, resolves src and __init__ relative imports", async () => {
  const fixture = repositoryFixture({
    "src/pkg/__init__.py": "from . import service\n",
    "src/pkg/api.py": '"""\nimport pkg.fake\n"""\n# import pkg.fake\nfrom pkg import service # comment\nfrom . import service\nimport pkg.missing\n',
    "src/pkg/service.py": "pass\n", "src/pkg/fake.py": "pass\n",
  });
  const graph = await analyzePublicGithubRepository("https://github.com/example/project", fixture.fetcher);
  assert.deepEqual(graph.edges.map((edge) => [edge.source, edge.target]), [
    ["file:src/pkg/__init__.py", "file:src/pkg/service.py"],
    ["file:src/pkg/api.py", "file:src/pkg/service.py"],
  ]);
  assert.equal(graph.edges[1].evidence.line, 5);
  assert.equal(graph.coverage.unmatched_imports, 1);
});

test("ambiguous module roots do not produce arbitrary edges", async () => {
  const fixture = repositoryFixture({ "main.py": "import pkg.service\n", "pkg/service.py": "pass\n", "src/pkg/service.py": "pass\n" });
  const graph = await analyzePublicGithubRepository("https://github.com/example/project", fixture.fetcher);
  assert.equal(graph.edges.length, 0);
  assert.equal(graph.coverage.unmatched_imports, 1);
});

test("unsupported analysis views cannot imply a clean risk assessment", () => {
  for (const view of ["patterns", "risks", "flows"]) {
    const state = inventoryUnavailable(view);
    assert.match(state.title, /not analyzed/);
    assert.match(state.detail, /not evidence of absence/);
  }
  assert.equal(inventoryStatus({ python_files_analyzed: 3, python_files_total_found: 3 }).partial, false);
});

test("malformed, null, oversized and non-GitHub request bodies fail before fetching", async () => {
  for (const [body, status] of [
    ["{", 400], ["null", 400], ["[]", 400], [" ".repeat(2049), 413],
    [JSON.stringify({ repositoryUrl: "https://github.com/example/.." }), 400],
    [JSON.stringify({ repositoryUrl: "https://github.com@localhost/example/project" }), 400],
  ]) {
    const request = new Request("https://site.test/api/analyze", { method: "POST", body });
    const response = await handleAnalyzeRequest(request, () => { throw new Error("Unexpected fetch"); });
    assert.equal(response.status, status);
  }
  const response = await handleAnalyzeRequest(new Request("https://site.test/api/analyze"));
  assert.equal(response.status, 405);
  assert.equal(response.headers.get("Allow"), "POST");
});

test("invalid GitHub tree is an error, not an empty successful inventory", async () => {
  const fixture = repositoryFixture({}, { invalidTree: true });
  await assert.rejects(() => analyzePublicGithubRepository("https://github.com/example/project", fixture.fetcher), /invalid repository tree/);
});

test("GitHub public errors preserve actionable status codes", async () => {
  for (const status of [404, 429]) {
    const request = new Request("https://site.test/api/analyze", { method: "POST", body: JSON.stringify({ repositoryUrl: "https://github.com/example/project" }) });
    const response = await handleAnalyzeRequest(request, async () => new Response("", { status }));
    assert.equal(response.status, status);
  }
});

test("caller cancellation is propagated to hosted fetches", async () => {
  const controller = new AbortController();
  const result = analyzePublicGithubRepository("https://github.com/example/project", async (_url, { signal }) => {
    controller.abort();
    signal.throwIfAborted();
  }, controller.signal);
  await assert.rejects(result, { name: "AbortError" });
});

test("the analysis deadline aborts a stalled upstream request", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const result = analyzePublicGithubRepository("https://github.com/example/project", (_url, { signal }) =>
    new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(signal.reason), { once: true })),
  );
  context.mock.timers.tick(30_001);
  await assert.rejects(result, { name: "AbortError" });
});

test("hosted endpoint rejects non-GitHub input without fetching", async () => {
  const request = new Request("https://archaeologist.example/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repositoryUrl: "https://example.com/project" }),
  });
  const response = await handleAnalyzeRequest(request, () => {
    throw new Error("fetch should not be called");
  });

  assert.equal(response.status, 400);
  assert.match((await response.json()).detail, /public GitHub URL/);
});
