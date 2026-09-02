import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { MAX_REPORT_BYTES, MAX_REPORT_NODES, canonicalGithubUrl, readReportFile, reportFilename, serializeReport, validateReport } from "../app/graph-report.ts";
import { analyzePublicGithubRepository } from "../worker/github-analyzer.ts";

const fixture = JSON.parse(await readFile(new URL("../public/graph.json", import.meta.url), "utf8"));
const copy = () => structuredClone(fixture);

test("current Python analyzer output opens with FastAPI flows and SQLAlchemy metadata", () => {
  // Static analysis only: none of the fixture's imports or repository code run.
  const result = spawnSync(process.env.PYTHON ?? "python", ["-c", "import json; from pathlib import Path; from analyzer import analyze_repository; print(json.dumps(analyze_repository(Path('tests/fixtures/portable-report'))))"], {
    cwd: new URL("../", import.meta.url), encoding: "utf8", maxBuffer: MAX_REPORT_BYTES,
  });
  assert.equal(result.status, 0, result.stderr || String(result.error));
  const graph = validateReport(JSON.parse(result.stdout));
  assert.ok(graph.flows.length > 0);
  assert.ok(graph.nodes.some((node) => node.sqlalchemy?.kind === "model"));
  assert.ok(graph.nodes.some((node) => node.entrypoint?.framework === "fastapi"));
  assert.deepEqual(validateReport(JSON.parse(serializeReport(graph))), graph);
});

test("deep reports round-trip without losing source, provenance or classification", async () => {
  const contents = serializeReport(fixture);
  const graph = await readReportFile(new File([contents], "graph.json"));
  assert.deepEqual(graph, fixture);
  assert.match(reportFilename(graph), /^cosmicpython-code-[0-9a-f]{12}\.graph\.json$/);
});

test("opening a report performs no network request", async (t) => {
  t.mock.method(globalThis, "fetch", () => { throw new Error("Unexpected network request"); });
  assert.equal((await readReportFile(new File([JSON.stringify(fixture)], "report.json"))).analysis.tier, "deep");
});

test("reject oversized files before reading their contents", async () => {
  await assert.rejects(readReportFile({ size: MAX_REPORT_BYTES + 1, text() { assert.fail("must not read"); } }), /10 MiB/);
  await assert.rejects(readReportFile(new File(["{broken"], "report.json")), /not valid JSON/);
});

test("reject unsupported schema, missing tier and oversized graph collections", () => {
  for (const value of [null, [], {}, { ...copy(), schema_version: "999" }, { ...copy(), analysis: undefined }]) {
    assert.throws(() => validateReport(value), /Invalid report/);
  }
  assert.throws(() => validateReport({ ...copy(), nodes: Array(MAX_REPORT_NODES + 1).fill(fixture.nodes[0]) }), /node limit/);
});

test("reject unsafe or inconsistent links and repository paths", () => {
  for (const url of ["javascript:alert(1)", "https://evil.test/repo", "https://github.com@evil.test/a/b", "https://github.com/a/..", "https://github.com/a/b?next=evil"]) {
    const graph = copy(); graph.repository.url = url;
    assert.throws(() => validateReport(graph), /repository links/);
  }
  for (const path of ["../secret.py", "/absolute.py", "dir/../secret.py", "dir\\secret.py"]) {
    const graph = copy(); graph.nodes[0].path = path;
    assert.throws(() => validateReport(graph), /repository-relative/);
  }
  const graph = copy(); graph.repository.pinned_url = "https://github.com/a/b/tree/" + "a".repeat(40);
  assert.throws(() => validateReport(graph), /snapshot link/);
  assert.equal(canonicalGithubUrl("https://github.com/example/project.git/"), "https://github.com/example/project");
});

test("reject malformed nested data before it reaches UI rendering", () => {
  const mutations = [
    (g) => { g.nodes.find((n) => n.evidence_packet).evidence_packet.claims[0].text = {}; },
    (g) => { g.nodes.find((n) => n.evidence_packet).evidence_packet.claims[0].confidence = 9; },
    (g) => { g.nodes[0].sqlalchemy = { kind: "model", columns: null }; },
    (g) => { g.nodes[0].entrypoint = { label: {} }; },
    (g) => { g.nodes[0].decorators = [null]; },
    (g) => { g.findings[0].remediation.actions = null; },
    (g) => { g.findings[0].remediation.actions[0].title = {}; },
    (g) => { g.findings[0].remediation.validation_steps = [null]; },
    (g) => { g.patterns[0].metrics.layers = {}; },
    (g) => { g.edges[0].evidence = { expression: {} }; },
  ];
  for (const mutate of mutations) {
    const graph = copy(); mutate(graph);
    assert.throws(() => validateReport(graph), /Invalid (report|graph)/);
  }
});

test("reject duplicate records, dangling evidence and inflated remediation confidence", () => {
  const mutations = [
    (g) => { g.edges.push(g.edges[0]); },
    (g) => { g.nodes.find((n) => n.evidence_packet).evidence_packet.related_edge_ids.push("missing"); },
    (g) => { g.nodes.find((n) => n.evidence_packet).evidence_packet.claims[0].evidence_refs = ["missing"]; },
    (g) => { g.nodes.find((n) => n.evidence_packet).evidence_packet.source_range.end_line++; },
    (g) => { g.findings[0].remediation.confidence = 1; },
    (g) => { g.findings[0].remediation.actions[0].evidence_refs = ["missing"]; },
    (g) => { g.patterns[0].evidence_refs.push("missing"); },
  ];
  for (const mutate of mutations) {
    const graph = copy(); mutate(graph);
    assert.throws(() => validateReport(graph), /Invalid report/);
  }
});

test("inventory exports stay inventory-only on reopening", async () => {
  const graph = await analyzePublicGithubRepository("https://github.com/example/project", async (url) => {
    if (String(url).endsWith("/project")) return Response.json({ default_branch: "main" });
    if (String(url).includes("/commits/")) return Response.json({ sha: "a".repeat(40), commit: { tree: { sha: "b".repeat(40) } } });
    if (String(url).includes("/git/trees/")) return Response.json({ tree: [{ type: "blob", path: "app.py", size: 4 }] });
    return new Response("pass");
  });
  assert.deepEqual(await readReportFile(new File([serializeReport(graph)], "inventory.json")), graph);
  graph.nodes[0].kind = "function";
  Object.assign(graph.nodes[0], { name: "fake", qualified_name: "fake", start_line: 1, end_line: 1 });
  assert.throws(() => validateReport(graph));
});

test("local directory reports need no GitHub links and retain a safe filename", () => {
  const graph = copy();
  delete graph.repository; delete graph.snapshot; delete graph.source_url;
  graph.repo_root = "C:\\project";
  assert.equal(validateReport(graph), graph);
  assert.equal(reportFilename(graph), "local-repository-local.graph.json");
});
