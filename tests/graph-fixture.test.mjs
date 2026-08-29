import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const fixtureUrl = new URL("../public/graph.json", import.meta.url);

test("Cosmic Python fixture follows the frontend graph contract", async () => {
  const graph = JSON.parse(await readFile(fixtureUrl, "utf8"));
  const nodeIds = new Set(graph.nodes.map((node) => node.id));

  assert.equal(graph.repository.name, "cosmicpython/code");
  assert.equal(graph.repository.url, "https://github.com/cosmicpython/code");
  assert.equal(graph.nodes.length, 37);
  assert.equal(graph.edges.length, 65);
  assert.ok(graph.nodes.every((node) => typeof node.source === "string"));
  assert.ok(graph.nodes.every((node) => Number.isInteger(node.size_bytes) && node.size_bytes >= 0));
  assert.ok(graph.edges.every((edge) => edge.id && nodeIds.has(edge.source) && nodeIds.has(edge.target)));
});
