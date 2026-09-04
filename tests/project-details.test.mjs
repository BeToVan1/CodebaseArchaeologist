import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const source = readFileSync(new URL("../app/project-details.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  .replaceAll('"react/jsx-runtime"', JSON.stringify(import.meta.resolve("react/jsx-runtime")));
const { ProjectDetails } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const metadata = { status: "parsed", path: "pyproject.toml", sha256: "a".repeat(64), declarations: [], warnings: [], limitations: ["Root manifest only."] };
const render = (project, imported = false, tier = "deep") => renderToStaticMarkup(createElement(ProjectDetails, { graph: { analysis: { tier }, ...(project ? { project_discovery: project } : {}) }, imported }));

test("does not display a panel before a report is loaded", () => {
  assert.equal(renderToStaticMarkup(createElement(ProjectDetails, { graph: null })), "");
});
test("older and inventory reports explain unavailable metadata without claiming no dependencies", () => {
  assert.match(render(), /does not include project metadata/);
  assert.match(render(null, false, "inventory"), /not analyzed in this inventory report/);
});
test("missing and failed manifests remain distinct", () => {
  for (const [status, message] of [["missing", /does not mean the project has no dependencies/], ["skipped", /safety limits/], ["unreadable", /could not read/], ["invalid", /could not be parsed/]]) {
    assert.match(render({ ...metadata, status, sha256: null }), message);
  }
});
test("declarations, warnings, evidence and imported trust are displayed with correct caveats", () => {
  const html = render({ ...metadata, declarations: [{ key: ["project", "dependencies"], value: ["click>=8"], provenance: "Literal pyproject.toml declaration" }], warnings: ["Dynamic version unresolved."] }, true);
  for (const text of [/Imported metadata · unverified/, /not independently checked/, /not installed versions/, /not proven execution flows/, /click&gt;=8/, /Dynamic version unresolved/, /Reported SHA-256/, /Exact declaration line spans are not recorded/]) assert.match(html, text);
  assert.doesNotMatch(html, /<details[^>]* open/);
});
test("empty declarations do not imply no dependencies and empty lists are explicit", () => {
  assert.match(render(metadata), /0 recorded declarations/);
  assert.match(render(metadata), /absent field is not proof of no dependencies/);
  assert.match(render({ ...metadata, declarations: [{ key: ["project", "dependencies"], value: [], provenance: "Literal" }] }), /Explicitly declared empty list/);
});
test("imported values render as text, never executable markup or script links", () => {
  const html = render({ ...metadata, declarations: [{ key: ["project", "scripts", "<script>"], value: '<img src=x onerror="alert(1)">', provenance: "<script>alert(2)</script>" }] }, true);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img|<script|href=/);
});
