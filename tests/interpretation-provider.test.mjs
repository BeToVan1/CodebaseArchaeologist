import assert from "node:assert/strict";
import test from "node:test";
import { CloudflareWorkersAIProvider, CloudflareWorkersAIRestProvider, WORKERS_AI_MODEL, validateGeneratedInterpretation } from "../worker/interpretation-provider.ts";

const packet = { version: "1", node_id: "symbol:run", related_edge_ids: ["edge:1"], flow_ids: [],
  finding_ids: [], pattern_ids: ["pattern:1"], claims: [{ id: "claim:1", evidence_refs: ["edge:1"] }] };
const section = (ref = "symbol:run") => ({ text: "Grounded explanation.", confidence: .6, evidence_refs: [ref] });
const output = () => ({ what_it_does: section(), execution_role: section("edge:1"),
  structural_rationale: section("pattern:1"), uncertainties: ["Runtime behavior is not executed."] });

test("provider sends one bounded deterministic JSON-mode request", async () => {
  const calls = [];
  const provider = new CloudflareWorkersAIProvider({ run: async (...args) => { calls.push(args); return { response: output() }; } });
  const result = await provider.generate(packet, "def run(): return 1");
  assert.equal(result.what_it_does.confidence, .6);
  assert.equal(calls.length, 1);
  const [model, request] = calls[0];
  assert.equal(model, WORKERS_AI_MODEL);
  assert.equal(request.response_format.type, "json_schema");
  assert.equal(request.max_tokens, 1024);
  assert.equal(request.temperature, 0);
  assert.equal(request.stream, false);
  assert.match(request.messages[0].content, /untrusted data/);
  assert.match(request.messages[1].content, /def run/);
});

for (const [name, mutate] of [
  ["unknown reference", value => value.what_it_does.evidence_refs = ["invented"]],
  ["confidence above policy", value => value.what_it_does.confidence = .9],
  ["empty evidence", value => value.execution_role.evidence_refs = []],
  ["extra field", value => value.extra = true],
  ["blank text", value => value.what_it_does.text = ""],
  ["too many uncertainties", value => value.uncertainties = Array(6).fill("unknown")],
]) test(`validator rejects ${name}`, () => {
  const value = output(); mutate(value);
  assert.throws(() => validateGeneratedInterpretation(value, packet), /invalid or ungrounded/);
});

test("provider rejects malformed response without retry", async () => {
  let calls = 0;
  const provider = new CloudflareWorkersAIProvider({ run: async () => { calls++; return { response: "not json" }; } });
  await assert.rejects(provider.generate(packet, "code"), /invalid or ungrounded/);
  assert.equal(calls, 1);
});

test("provider rejects oversized source before inference", async () => {
  let calls = 0;
  const provider = new CloudflareWorkersAIProvider({ run: async () => { calls++; return { response: output() }; } });
  await assert.rejects(provider.generate(packet, "x".repeat(12001)), /evidence is invalid/);
  assert.equal(calls, 0);
});

test("REST provider sends one authenticated request to the fixed account and model", async () => {
  const account = "a".repeat(32);
  const token = "cloudflare-test-token-" + "x".repeat(32);
  let calls = 0;
  const provider = new CloudflareWorkersAIRestProvider(account, token, async (url, options) => {
    calls++;
    assert.equal(url, `https://api.cloudflare.com/client/v4/accounts/${account}/ai/run/${WORKERS_AI_MODEL}`);
    assert.equal(options.redirect, "manual");
    assert.equal(options.headers.Authorization, `Bearer ${token}`);
    const request = JSON.parse(options.body);
    assert.equal(request.max_tokens, 1024);
    assert.equal(request.stream, false);
    return Response.json({ success: true, result: { response: output() }, errors: [], messages: [] });
  });
  assert.equal((await provider.generate(packet, "def run(): pass")).execution_role.confidence, .6);
  assert.equal(calls, 1);
});

test("REST provider rejects failed or malformed envelopes without retry", async () => {
  const account = "a".repeat(32);
  const token = "cloudflare-test-token-" + "x".repeat(32);
  for (const response of [new Response("private", { status: 429 }), Response.json({ success: false, result: null })]) {
    let calls = 0;
    const provider = new CloudflareWorkersAIRestProvider(account, token, async () => { calls++; return response; });
    await assert.rejects(provider.generate(packet, "code"), /Workers AI/);
    assert.equal(calls, 1);
  }
});
