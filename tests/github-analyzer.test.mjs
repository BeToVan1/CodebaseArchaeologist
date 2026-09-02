import assert from "node:assert/strict";
import test from "node:test";
import { analyzePublicGithubRepository, handleAnalyzeRequest } from "../worker/github-analyzer.ts";

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
