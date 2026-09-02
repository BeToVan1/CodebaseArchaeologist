import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const fixtureUrl = new URL("../public/graph.json", import.meta.url);

test("Cosmic Python fixture follows the frontend graph contract", async () => {
  const graph = JSON.parse(await readFile(fixtureUrl, "utf8"));
  const nodeIds = new Set(graph.nodes.map((node) => node.id));
  const fileNodes = graph.nodes.filter((node) => node.kind === "file");
  const symbolNodes = graph.nodes.filter((node) => node.kind !== "file");

  assert.equal(graph.schema_version, "0.9");
  assert.equal(graph.repository.name, "cosmicpython/code");
  assert.equal(graph.repository.url, "https://github.com/cosmicpython/code");
  assert.match(graph.snapshot.commit_sha, /^[0-9a-f]{40}$/);
  assert.ok(fileNodes.length > 30);
  assert.ok(symbolNodes.length > 100);
  assert.ok(fileNodes.every((node) => typeof node.source === "string"));
  assert.ok(fileNodes.every((node) => Number.isInteger(node.size_bytes) && node.size_bytes >= 0));
  assert.ok(symbolNodes.every((node) => Number.isInteger(node.start_line) && Number.isInteger(node.end_line)));
  assert.ok(symbolNodes.every((node) =>
    node.evidence_packet?.node_id === node.id
    && node.evidence_packet.summary.classification === "fact"
    && node.evidence_packet.claims.every((claim) => claim.evidence_refs.length > 0)
  ));
  assert.ok(graph.edges.every((edge) => edge.id && nodeIds.has(edge.source) && nodeIds.has(edge.target)));
  assert.ok(graph.findings.length > 0);
  assert.ok(graph.findings.every((finding) =>
    nodeIds.has(finding.node_id)
    && ["low", "medium", "high"].includes(finding.severity)
    && ["fact", "heuristic", "interpretation"].includes(finding.classification)
    && finding.evidence.path
    && finding.provenance
  ));
  assert.ok(graph.patterns.length >= 2);
  assert.ok(graph.patterns.every((pattern) =>
    ["fact", "heuristic"].includes(pattern.classification)
    && pattern.confidence >= 0
    && pattern.confidence <= 1
    && pattern.node_ids.every((id) => nodeIds.has(id))
    && pattern.evidence_refs.length > 0
    && pattern.provenance
  ));
});


