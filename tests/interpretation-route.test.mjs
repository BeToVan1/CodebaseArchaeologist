import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { networkKey } from "../worker/deep-limits.ts";
import { INTERPRETATION_ENDPOINT, handleInterpretationRequest, interpretationConfigured } from "../worker/interpretation-route.ts";
import { WORKERS_AI_MODEL } from "../worker/interpretation-provider.ts";

const graph = JSON.parse(await readFile(new URL("../public/graph.json", import.meta.url), "utf8"));
const symbol = graph.nodes.find(node => node.evidence_packet);
const reportId = "R".repeat(43);
const token = "private-service-token-" + "x".repeat(40);
const provenance = `Cloudflare Workers AI ${WORKERS_AI_MODEL} interpretation of server-retained evidence`;
const section = { text: "Explains behavior.", confidence: 0.7, evidence_refs: [symbol.id], classification: "interpretation", provenance };
const completed = {
  model: WORKERS_AI_MODEL, classification: "interpretation", commitSha: graph.snapshot.commit_sha, nodeId: symbol.id,
  what_it_does: section, execution_role: section, structural_rationale: section,
  uncertainties: ["Runtime behavior is not observed."],
};
const environment = (overrides = {}) => ({
  ARCHAEOLOGIST_DEEP_ENABLED: "true",
  ARCHAEOLOGIST_INTERPRETATION_ENABLED: "true",
  ARCHAEOLOGIST_SERVICE_TOKEN: token,
  ...overrides,
});
const request = (body = { reportId, nodeId: symbol.id }, options = {}) => new Request(
  "https://site.test/api/interpret/deep", {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", Origin: "https://site.test", "CF-Connecting-IP": "192.0.2.1", ...options.headers },
    ...Object.fromEntries(Object.entries(options).filter(([key]) => key !== "headers")),
  },
);
const forbidden = () => { assert.fail("Upstream must not be called"); };

test("interpretation stays fail-closed without the enable flag and deep-service secret", async () => {
  assert.equal(interpretationConfigured(environment()), true);
  for (const env of [
    environment({ ARCHAEOLOGIST_INTERPRETATION_ENABLED: "false" }),
    environment({ ARCHAEOLOGIST_DEEP_ENABLED: "false" }),
    environment({ ARCHAEOLOGIST_SERVICE_TOKEN: "short" }),
  ]) {
    assert.equal(interpretationConfigured(env), false);
    assert.equal((await handleInterpretationRequest(request(), env, forbidden)).status, 503);
  }
});

test("rejects untrusted methods, origins, body fields and network identity before Oracle", async () => {
  assert.equal((await handleInterpretationRequest(new Request("https://site.test/api/interpret/deep"), environment(), forbidden)).status, 405);
  assert.equal((await handleInterpretationRequest(request(undefined, { headers: { Origin: "https://evil.test" } }), environment(), forbidden)).status, 403);
  assert.equal((await handleInterpretationRequest(request({ reportId, nodeId: symbol.id, sourceExcerpt: "forged" }), environment(), forbidden)).status, 400);
  assert.equal((await handleInterpretationRequest(request(undefined, { headers: { "CF-Connecting-IP": "", "X-Forwarded-For": "192.0.2.1" } }), environment(), forbidden)).status, 503);
});

test("forwards only the owner-bound selection to the fixed Oracle interpretation route", async () => {
  let calls = 0;
  const response = await handleInterpretationRequest(request(), environment(), async (url, options) => {
    calls++;
    assert.equal(url, INTERPRETATION_ENDPOINT);
    assert.equal(options.redirect, "manual");
    assert.deepEqual(JSON.parse(options.body), { reportId, nodeId: symbol.id });
    assert.deepEqual(options.headers, {
      "Content-Type": "application/json", Authorization: `Bearer ${token}`,
      "X-Archaeologist-Client-Key": await networkKey("192.0.2.1", token),
    });
    return Response.json(completed);
  });
  assert.equal(response.status, 200);
  assert.equal(calls, 1);
  assert.deepEqual(await response.json(), completed);
  assert.equal(response.headers.get("cache-control"), "no-store");
});

test("Oracle errors and invalid results are sanitized without retries", async () => {
  for (const [upstream, expected] of [[404, 404], [422, 422], [429, 429], [502, 502], [503, 503]]) {
    let calls = 0;
    const response = await handleInterpretationRequest(request(), environment(), async () => {
      calls++;
      return new Response("private upstream detail", { status: upstream });
    });
    assert.equal(response.status, expected);
    assert.ok(!(await response.text()).includes("private upstream detail"));
    assert.equal(calls, 1);
  }
  for (const value of [
    { ...completed, commitSha: "bad" },
    { ...completed, nodeId: "forged" },
    { ...completed, what_it_does: { ...section, provenance: "forged" } },
    { ...completed, execution_role: { ...section, confidence: 1 } },
  ]) {
    const response = await handleInterpretationRequest(request(), environment(), async () => Response.json(value));
    assert.equal(response.status, 502);
    assert.ok(!(await response.text()).includes("forged"));
  }
});
