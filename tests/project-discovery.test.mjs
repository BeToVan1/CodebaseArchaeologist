import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { validateGraph } from "../app/graph-validation.ts";
import { serializeReport, validateReport } from "../app/graph-report.ts";

const fixture = JSON.parse(readFileSync(new URL("../public/graph.json", import.meta.url), "utf8"));
const metadata = { version: "1", scope: "root-pyproject-only", status: "parsed", path: "pyproject.toml",
  sha256: "a".repeat(64), declarations: [{key: ["project", "requires-python"], value: ">=3.11",
    classification: "fact", confidence: 1, provenance: "Literal declaration; not installed version"}],
  warnings: [], limitations: ["Only root standard metadata is inspected."] };

test("optional project metadata survives report export and import", () => {
  const graph = {...structuredClone(fixture), project_discovery: structuredClone(metadata)};
  const restored = validateReport(JSON.parse(serializeReport(graph)));
  assert.deepEqual(restored.project_discovery, metadata);
  assert.doesNotThrow(() => validateGraph(fixture));
});

for (const [name, change] of [
  ["unknown status", p => {p.status = "verified-runtime";}],
  ["missing hash", p => {p.sha256 = null;}],
  ["malformed hash", p => {p.sha256 = "bad";}],
  ["unknown source", p => {p.path = "../../outside";}],
  ["invalid value", p => {p.declarations[0].value = {secret: "hidden"};}],
  ["unsupported confidence", p => {p.declarations[0].confidence = 2;}],
  ["unreadable with facts", p => {p.status = "unreadable";}],
  ["too many declarations", p => {p.declarations = Array(129).fill(p.declarations[0]);}],
]) test(`rejects ${name} in imported project metadata`, () => {
  const p = structuredClone(metadata); change(p);
  assert.throws(() => validateGraph({...fixture, project_discovery: p}), /project discovery/);
});
