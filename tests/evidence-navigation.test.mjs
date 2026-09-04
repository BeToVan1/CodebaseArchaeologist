import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { pathMatchesScope, revealedNodeSelection } from "../app/graph-presentation.ts";

for (const path of ["tests/test_api.py", "test_api.py", "src/pkg/tests/helper.py", "api_test.py", "pkg/tests.py", "conftest.py", "test/helper.py"]) {
  test(`reveals filtered test evidence: ${path}`, () => {
    const file = {id: `file:${path}`, kind: "file", path};
    const symbol = {id: `symbol:${path}:check`, kind: "function", path};
    const next = revealedNodeSelection([file, symbol], symbol.id, "production", "does-not-match");
    assert.deepEqual(next, {fileId: file.id, symbolId: symbol.id, scope: "all", query: ""});
    assert.ok(pathMatchesScope(file.path, next.scope, next.query), "selection must survive the visible-file effect");
    assert.equal(pathMatchesScope(path, "production", ""), false);
  });
}
test("compatible scope/search are retained and file selection clears the symbol", () => {
  const file = {id: "custom-file-id", kind: "file", path: "src/service.py"};
  assert.deepEqual(revealedNodeSelection([file], file.id, "production", "SERVICE"),
    {fileId: file.id, symbolId: null, scope: "production", query: "SERVICE"});
  assert.equal(revealedNodeSelection([file], file.id, "all", "src").scope, "all");
});
test("similar names stay production and search remains case insensitive", () => {
  for (const path of ["testing.py", "contest.py", "tests_support/helper.py"]) assert.ok(pathMatchesScope(path, "production", ""));
  assert.ok(pathMatchesScope("tests/test_api.py", "all", "API"));
  assert.equal(pathMatchesScope("service.py", "all", "missing"), false);
});
test("missing nodes or missing containing files do not change selection", () => {
  assert.equal(revealedNodeSelection([], "missing", "production", "query"), null);
  assert.equal(revealedNodeSelection([{id: "orphan", kind: "function", path: "test_a.py"}], "orphan", "production", "query"), null);
});
test("page navigation and both filters use shared helpers", () => {
  const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(page, /const selectFileNode = revealNode/);
  assert.match(page, /const selectSymbolNode = revealNode/);
  assert.match(page, /setScope\(selection.scope\)/);
  assert.match(page, /setQuery\(selection.query\)/);
  assert.match(page, /pathMatchesScope\(node.path, scope, query\)/);
  assert.match(page, /pathMatchesScope\(finding.evidence.path, scope, query\)/);
  assert.doesNotMatch(page, /startsWith\("tests\/"\)/);
});
