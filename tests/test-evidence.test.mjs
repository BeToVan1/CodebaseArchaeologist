import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { validateGraph } from "../app/graph-validation.ts";
import { testEvidenceForSelection } from "../app/test-proximity.ts";
import { serializeReport, validateReport } from "../app/graph-report.ts";

// Generate a real report with the current Python analyzer; no fixture code executes.
const result = spawnSync(process.env.PYTHON || "python", ["-c", `
import json, tempfile
from pathlib import Path
from analyzer import analyze_repository
with tempfile.TemporaryDirectory() as d:
    p=Path(d)
    (p/'service.py').write_text('def run(): return 1\\ndef other(): return 2\\n')
    (p/'test_service.py').write_text('from service import run\\ndef test_run(): return run()\\n')
    print(json.dumps(analyze_repository(p)))
`], { cwd: new URL("../", import.meta.url), encoding: "utf8" });
assert.equal(result.status, 0, result.stderr);
const fixture = JSON.parse(result.stdout);
const selected = fixture.nodes.find(n => n.name === "run");
const source = readFileSync(new URL("../app/test-evidence.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  .replaceAll('"react/jsx-runtime"', JSON.stringify(import.meta.resolve("react/jsx-runtime")))
  .replaceAll('"./test-proximity"', JSON.stringify(new URL("../app/test-proximity.ts", import.meta.url).href));
const { TestEvidence } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const render = (graph = fixture, node = selected, imported = false) => renderToStaticMarkup(createElement(TestEvidence, { graph, selected: node, imported, onOpen() {} }));

test("real Python report passes validation and survives export/import", () => {
  assert.doesNotThrow(() => validateGraph(fixture));
  assert.deepEqual(validateReport(JSON.parse(serializeReport(fixture))).test_proximity, fixture.test_proximity);
});
test("selection distinguishes module imports from exact-symbol calls", () => {
  assert.equal(testEvidenceForSelection(fixture, selected).length, 2);
  const other = fixture.nodes.find(n => n.name === "other");
  assert.deepEqual(testEvidenceForSelection(fixture, other).map(x => x.link.signal), ["module-import"]);
  assert.equal(testEvidenceForSelection(fixture, fixture.nodes.find(n => n.id === "file:service.py")).length, 2);
});
for (const [name, mutate] of [
  ["dangling edge", g => {g.test_proximity.links[0].edge_id = "missing";}],
  ["wrong target", g => {g.test_proximity.links[0].target_node_id = "file:service.py";}],
  ["wrong signal", g => {g.test_proximity.links[0].signal = "module-import";}],
  ["invented confidence", g => {g.test_proximity.links[0].confidence = 1;}],
  ["invented classification", g => {g.test_proximity.links[0].classification = "fact";}],
  ["incorrect count", g => {g.test_proximity.test_files_identified = 0;}],
  ["hidden truncation", g => {g.test_proximity.links.pop();}],
  ["duplicate link", g => {g.test_proximity.links[1] = {...g.test_proximity.links[0]};}],
  ["wrong source path", g => {g.edges.find(e => e.id === g.test_proximity.links[0].edge_id).evidence.path = "elsewhere.py";}],
  ["missing source line", g => {delete g.edges.find(e => e.id === g.test_proximity.links[0].edge_id).evidence.line;}],
  ["too many links", g => {g.test_proximity.links = Array(1001).fill(g.test_proximity.links[0]);}],
]) test(`rejects ${name}`, () => {
  const graph = structuredClone(fixture); mutate(graph);
  assert.throws(() => validateGraph(graph), /Invalid graph/);
});
test("explicitly truncated data is allowed and visibly marked", () => {
  const graph = structuredClone(fixture); graph.test_proximity.links.pop(); graph.test_proximity.links_truncated = true;
  assert.doesNotThrow(() => validateGraph(graph));
  assert.match(render(graph), /omits some links/);
});
test("old and inventory reports distinguish not analyzed from no evidence", () => {
  const graph = structuredClone(fixture); delete graph.test_proximity;
  assert.doesNotThrow(() => validateGraph(graph));
  assert.match(render(graph), /does not include test-proximity data/);
  graph.analysis.tier = "inventory";
  assert.match(render(graph), /not analyzed in this inventory/);
});
test("rendering labels imported trust, call/import scope and limitations", () => {
  const html = render(fixture, selected, true);
  for (const pattern of [/Imported evidence · unverified/, /not test coverage/, /Recorded calls to selected code/, /Imports of containing module/, /not evidence for every symbol/, /Open test_service.py/, /Line 2/]) assert.match(html, pattern);
});
test("open button callback selects the source node", () => {
  let opened;
  const tree = TestEvidence({graph: fixture, selected, imported: false, onOpen: node => {opened = node;}});
  const buttons = [];
  function walk(node) { if (Array.isArray(node)) return node.forEach(walk); if (!node?.props) return; if (node.type === "button") buttons.push(node); walk(node.props.children); }
  walk(tree); assert.equal(buttons.length, 2);
  buttons[0].props.onClick(); assert.equal(opened.name, "test_run");
  buttons[1].props.onClick(); assert.equal(opened.id, "file:test_service.py");
});
