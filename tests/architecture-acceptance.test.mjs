import assert from "node:assert/strict";
import { readFile, mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { resolve, dirname, relative, isAbsolute, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { MAX_REPORT_BYTES, validateReport } from "../app/graph-report.ts";

const root = fileURLToPath(new URL("../", import.meta.url));
function analyze(path) {
  const result = spawnSync(process.env.PYTHON ?? "python", ["-c",
    "import json, sys; from pathlib import Path; from analyzer import analyze_repository; print(json.dumps(analyze_repository(Path(sys.argv[1]))))", path],
  { cwd: root, encoding: "utf8", maxBuffer: MAX_REPORT_BYTES, timeout: 30000 });
  assert.equal(result.status, 0, result.stderr || String(result.error));
  return validateReport(JSON.parse(result.stdout));
}

// Semantic expectations, not a golden copy of analyzer output. The book's
// chapters 2 and 6 document repository/UoW boundaries; exact inheritance and
// imports below are independently visible in this pinned source snapshot.
// https://www.cosmicpython.com/book/chapter_02_repository.html
// https://www.cosmicpython.com/book/chapter_06_uow.html
test("current analyzer preserves documented Cosmic Python boundaries from pinned source", async () => {
  const fixture = JSON.parse(await readFile(new URL("../public/graph.json", import.meta.url), "utf8"));
  assert.equal(fixture.snapshot.commit_sha, "14c84797ffa77255d53cf1a02fe6aafda2b68aeb");
  const parent = resolve(root, "artifacts");
  await mkdir(parent, { recursive: true });
  const temp = await mkdtemp(resolve(parent, "architecture-acceptance-"));
  try {
    for (const node of fixture.nodes.filter(node => node.kind === "file")) {
      assert.equal(typeof node.source, "string");
      assert.equal(node.source_truncated, false);
      const target = resolve(temp, node.path);
      const child = relative(temp, target);
      assert.ok(child && !isAbsolute(child) && child !== ".." && !child.startsWith(".." + sep));
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, node.source);
    }
    // Only our analyzer executes: copied repository sources are parsed as ASTs.
    const graph = analyze(temp);
    const patterns = new Map(graph.patterns.map(pattern => [pattern.id, pattern]));
    for (const id of ["pattern:layered-architecture", "pattern:repository-boundary", "pattern:unit-of-work"]) {
      assert.equal(patterns.get(id)?.classification, "heuristic", id);
      assert.ok(patterns.get(id).evidence_refs.length > 0);
    }
    const symbols = new Map(graph.nodes.map(node => [node.qualified_name, node]));
    for (const [child, base] of [
      ["allocation.adapters.repository.SqlAlchemyRepository", "allocation.adapters.repository.AbstractRepository"],
      ["allocation.service_layer.unit_of_work.SqlAlchemyUnitOfWork", "allocation.service_layer.unit_of_work.AbstractUnitOfWork"],
    ]) {
      assert.ok(symbols.has(child) && symbols.has(base));
      assert.ok(graph.edges.some(edge => edge.kind === "extends" && edge.source === symbols.get(child).id && edge.target === symbols.get(base).id));
    }
    const domainImports = graph.edges.filter(edge => edge.kind === "imports" && edge.source === "file:src/allocation/domain/model.py");
    assert.ok(domainImports.length > 0);
    assert.ok(domainImports.every(edge => edge.target.startsWith("file:src/allocation/domain/")));
    // Flask routes/classical ORM are not silently upgraded to supported FastAPI
    // routes or SQLAlchemy declarative models by a naming heuristic.
    assert.equal(graph.flows.length, 0);
    assert.ok(!graph.nodes.some(node => node.entrypoint?.framework === "fastapi"));
  } finally {
    assert.equal(dirname(temp), parent);
    assert.ok(temp.startsWith(resolve(parent, "architecture-acceptance-")));
    await rm(temp, { recursive: true, force: true });
  }
});

test("known FastAPI flow reaches its SQLAlchemy model and retains unresolved external work", () => {
  const graph = analyze(resolve(root, "tests/fixtures/portable-report"));
  const nodes = new Map(graph.nodes.map(node => [node.id, node]));
  const flow = graph.flows.find(flow => flow.label === "GET /items");
  assert.ok(flow);
  assert.deepEqual(flow.ordered_node_ids.map(id => nodes.get(id).qualified_name), ["api.list_items_route", "repository.list_items", "models.ItemModel"]);
  assert.equal(flow.completeness, "partial");
  assert.ok(flow.unresolved_steps.some(step => step.evidence.expression === "select"));
  const model = graph.nodes.find(node => node.qualified_name === "models.ItemModel");
  assert.equal(model.sqlalchemy.table_name, "items");
  assert.ok(model.sqlalchemy.columns.some(column => column.name === "id"));
  assert.ok(graph.edges.some(edge => edge.kind === "reads" && edge.target === model.id));
});
