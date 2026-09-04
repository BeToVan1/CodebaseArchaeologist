import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { submitAnalysis } from "../app/analysis-client.ts";
import { handleDeepRequest } from "../worker/deep-proxy.ts";

const graph = JSON.parse(await readFile(new URL("../public/graph.json", import.meta.url), "utf8"));
const graphResponse = value => Response.json(value, { headers: {
  "X-Archaeologist-Report-Id": "R".repeat(43), "X-Archaeologist-Report-TTL": "900",
} });
const secret = "offline-only-test-token-" + "x".repeat(40);
// In-memory transport: no network, credentials, quota ledger or repository jobs.
function transport(upstream) {
  return async (url, options) => {
    assert.equal(url, "/api/analyze/deep");
    assert.equal(options.headers.Authorization, undefined);
    return handleDeepRequest(new Request("https://site.test" + url, {
      ...options,
      headers: { ...options.headers, Origin: "https://site.test", "CF-Connecting-IP": "192.0.2.1" },
    }), { ARCHAEOLOGIST_DEEP_ENABLED: "true", ARCHAEOLOGIST_SERVICE_TOKEN: secret }, upstream);
  };
}

for (const [name, status, headers, message] of [
  ["busy", 429, {}, /worker is busy/i],
  ["quota", 429, { "X-Archaeologist-Limit": "quota" }, /allowance reached/i],
  ["outage", 503, {}, /temporarily unavailable/i],
  ["authorization failure", 401, {}, /temporarily unavailable/i],
  ["repository limit", 413, {}, /exceeds hosted limits/i],
  ["deadline", 504, {}, /exceeded its time limit/i],
]) {
  test(`client/proxy ${name} failure is sanitized, not retried, and permits later success`, async () => {
    let calls = 0;
    const fetcher = transport(async () => {
      calls++;
      return calls === 1 ? new Response(secret, { status, headers }) : graphResponse(graph);
    });
    await assert.rejects(submitAnalysis("deep", graph.repository.url, "", new AbortController().signal, fetcher), (error) => {
      assert.match(error.message, message);
      assert.ok(!error.message.includes(secret));
      return true;
    });
    assert.equal(calls, 1, "No automatic retry or inventory fallback");
    assert.deepEqual(await submitAnalysis("deep", graph.repository.url, "", new AbortController().signal, fetcher), graph);
    assert.equal(calls, 2, "A separate explicit request succeeds");
  });
}

test("client/proxy reject malformed, wrong-repository and wrong-tier success responses", async () => {
  for (const value of [{}, { ...graph, repository: { ...graph.repository, url: "https://github.com/wrong/repo" } }, { ...graph, analysis: { ...graph.analysis, tier: "inventory" } }]) {
    let calls = 0;
    await assert.rejects(submitAnalysis("deep", graph.repository.url, "", new AbortController().signal,
      transport(async () => { calls++; return graphResponse(value); })), /invalid or unavailable report/i);
    assert.equal(calls, 1);
  }
});

test("cancellation while reading a stalled upstream body closes the stream without retry", { timeout: 5000 }, async () => {
  const controller = new AbortController();
  let beganReading;
  const reading = new Promise((resolve) => { beganReading = resolve; });
  let cancelled = false;
  let calls = 0;
  const pending = submitAnalysis("deep", graph.repository.url, "", controller.signal, transport(async () => {
    calls++;
    return new Response(new ReadableStream({
      pull() { beganReading(); },
      cancel() { cancelled = true; },
    }));
  }));
  const rejected = assert.rejects(pending, /cancelled/i);
  await reading;
  controller.abort();
  await rejected;
  assert.equal(cancelled, true);
  assert.equal(calls, 1);
});
