import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { networkKey } from "../worker/deep-limits.ts";
import { EVIDENCE_ENDPOINT, handleInterpretationRequest, interpretationConfigured } from "../worker/interpretation-route.ts";
import { WORKERS_AI_MODEL } from "../worker/interpretation-provider.ts";

const graph = JSON.parse(await readFile(new URL("../public/graph.json", import.meta.url), "utf8"));
const symbol = graph.nodes.find(node => node.evidence_packet);
const reportId = "R".repeat(43);
const token = "private-service-token-" + "x".repeat(40);
const generated = {
  what_it_does: { text: "Explains behavior.", confidence: 0.7, evidence_refs: [symbol.id] },
  execution_role: { text: "Explains execution.", confidence: 0.6, evidence_refs: [symbol.id] },
  structural_rationale: { text: "Structure is not proven.", confidence: 0.4, evidence_refs: [symbol.id] },
  uncertainties: ["Runtime behavior is not observed."],
};
const ai = { calls: 0, async run(model, input) {
  this.calls++;
  assert.equal(model, WORKERS_AI_MODEL);
  assert.equal(input.stream, false);
  return { response: generated };
} };
const environment = (overrides = {}) => ({
  ARCHAEOLOGIST_DEEP_ENABLED: "true",
  ARCHAEOLOGIST_INTERPRETATION_ENABLED: "true",
  ARCHAEOLOGIST_SERVICE_TOKEN: token, AI: ai, ...overrides,
});
const request = (body = { reportId, nodeId: symbol.id }, options = {}) => new Request(
  "https://site.test/api/interpret/deep", {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", Origin: "https://site.test", "CF-Connecting-IP": "192.0.2.1", ...options.headers },
    ...Object.fromEntries(Object.entries(options).filter(([key]) => key !== "headers")),
  },
);
const prepared = () => Response.json({
  commitSha: graph.snapshot.commit_sha,
  evidencePacket: symbol.evidence_packet,
  sourceExcerpt: "def selected_symbol():\n    pass",
});
const forbidden = () => { assert.fail("Upstream must not be called"); };

test("interpretation stays fail-closed without every server binding", async () => {
  assert.equal(interpretationConfigured(environment()), true);
  for (const env of [
    environment({ ARCHAEOLOGIST_INTERPRETATION_ENABLED: "false" }),
    environment({ ARCHAEOLOGIST_DEEP_ENABLED: "false" }),
    environment({ AI: undefined }),
  ]) {
    assert.equal(interpretationConfigured(env), false);
    assert.equal((await handleInterpretationRequest(request(), env, forbidden)).status, 503);
  }
});

test("REST credentials can enable interpretation without a managed AI binding", async () => {
  const account = "a".repeat(32);
  const restToken = "cloudflare-test-token-" + "x".repeat(32);
  const env = environment({ AI: undefined, ARCHAEOLOGIST_CF_ACCOUNT_ID: account, ARCHAEOLOGIST_CF_AI_TOKEN: restToken });
  assert.equal(interpretationConfigured(env), true);
  let inferenceCalls = 0;
  const response = await handleInterpretationRequest(request(), env, async () => prepared(), async (url, options) => {
    inferenceCalls++;
    assert.equal(url, `https://api.cloudflare.com/client/v4/accounts/${account}/ai/run/${WORKERS_AI_MODEL}`);
    assert.equal(options.headers.Authorization, `Bearer ${restToken}`);
    return Response.json({ success: true, result: { response: generated }, errors: [], messages: [] });
  });
  assert.equal(response.status, 200);
  assert.equal(inferenceCalls, 1);
});

test("rejects untrusted methods, origins, body fields and network identity before evidence or AI", async () => {
  const start = ai.calls;
  assert.equal((await handleInterpretationRequest(new Request("https://site.test/api/interpret/deep"), environment(), forbidden)).status, 405);
  assert.equal((await handleInterpretationRequest(request(undefined, { headers: { Origin: "https://evil.test" } }), environment(), forbidden)).status, 403);
  assert.equal((await handleInterpretationRequest(request({ reportId, nodeId: symbol.id, sourceExcerpt: "forged" }), environment(), forbidden)).status, 400);
  assert.equal((await handleInterpretationRequest(request(undefined, { headers: { "CF-Connecting-IP": "", "X-Forwarded-For": "192.0.2.1" } }), environment(), forbidden)).status, 503);
  assert.equal(ai.calls, start);
});

test("fetches only owner-bound Oracle evidence then makes one grounded model call", async () => {
  ai.calls = 0;
  let fetches = 0;
  const response = await handleInterpretationRequest(request(), environment(), async (url, options) => {
    fetches++;
    assert.equal(url, EVIDENCE_ENDPOINT);
    assert.equal(options.redirect, "manual");
    assert.deepEqual(JSON.parse(options.body), { reportId, nodeId: symbol.id });
    assert.deepEqual(options.headers, {
      "Content-Type": "application/json", Authorization: `Bearer ${token}`,
      "X-Archaeologist-Client-Key": await networkKey("192.0.2.1", token),
    });
    return prepared();
  });
  assert.equal(response.status, 200);
  assert.equal(fetches, 1);
  assert.equal(ai.calls, 1);
  const body = await response.json();
  assert.equal(body.classification, "interpretation");
  assert.equal(body.nodeId, symbol.id);
  assert.equal(body.commitSha, graph.snapshot.commit_sha);
  assert.equal(body.what_it_does.classification, "interpretation");
  assert.match(body.what_it_does.provenance, /Cloudflare Workers AI/);
  assert.equal(response.headers.get("cache-control"), "no-store");
});

test("expired, malformed and model-invalid paths are sanitized without retries", async () => {
  ai.calls = 0;
  assert.equal((await handleInterpretationRequest(request(), environment(), async () => new Response("private", { status: 404 }))).status, 404);
  assert.equal((await handleInterpretationRequest(request(), environment(), async () => Response.json({ sourceExcerpt: "forged" }))).status, 503);
  const badAi = { calls: 0, async run() { this.calls++; return { response: { ...generated, what_it_does: { ...generated.what_it_does, evidence_refs: ["unknown"] } } }; } };
  const response = await handleInterpretationRequest(request(), environment({ AI: badAi }), async () => prepared());
  assert.equal(response.status, 502);
  assert.equal(badAi.calls, 1);
  assert.equal(ai.calls, 0);
  assert.ok(!(await response.text()).includes("unknown"));
});
