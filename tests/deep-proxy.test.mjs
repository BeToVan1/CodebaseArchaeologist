import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createHmac } from "node:crypto";
import { deepConfigured, DEEP_ENDPOINT, handleDeepRequest, withDeadline } from "../worker/deep-proxy.ts";
import { networkKey } from "../worker/deep-limits.ts";
import { analysisEndpoint, submitAnalysis } from "../app/analysis-client.ts";
import { readBoundedBody } from "../worker/github-analyzer.ts";

const graph = JSON.parse(await readFile(new URL("../public/graph.json", import.meta.url), "utf8"));
const token = "private-service-token-" + "x".repeat(40);
const environment = () => ({ ARCHAEOLOGIST_DEEP_ENABLED: "true", ARCHAEOLOGIST_SERVICE_TOKEN: token });
const evidenceHeaders = { "X-Archaeologist-Report-Id": "R".repeat(43), "X-Archaeologist-Report-TTL": "900" };
const graphResponse = value => Response.json(value, { headers: evidenceHeaders });
const request = (options = {}) => new Request("https://site.test/api/analyze/deep", {
  method: "POST", headers: { "Content-Type": "application/json", Origin: "https://site.test", "CF-Connecting-IP": "192.0.2.1", ...options.headers },
  body: JSON.stringify({ repositoryUrl: graph.repository.url }), ...Object.fromEntries(Object.entries(options).filter(([key]) => key !== "headers")),
});
const forbidden = () => { assert.fail("Upstream must not be called"); };

test("deep mode fails closed without enable flag or secret", async () => {
  for (const env of [{}, { ...environment(), ARCHAEOLOGIST_DEEP_ENABLED: "false" }, { ...environment(), ARCHAEOLOGIST_SERVICE_TOKEN: "short" }]) {
    assert.equal(deepConfigured(env), false);
    assert.equal((await handleDeepRequest(request(), env, forbidden)).status, 503);
  }
});
test("rejects wrong methods, origins, formats and untrusted network identifiers", async () => {
  assert.equal((await handleDeepRequest(new Request("https://site.test/api/analyze/deep"), environment(), forbidden)).status, 405);
  for (const [headers, status] of [[{ Origin: "https://other.test" }, 403], [{ Origin: "" }, 403], [{ "Sec-Fetch-Site": "cross-site" }, 403], [{ "Content-Type": "text/plain" }, 415], [{ "CF-Connecting-IP": "", "X-Forwarded-For": "192.0.2.2" }, 503]]) {
    assert.equal((await handleDeepRequest(request({ headers }), environment(), forbidden)).status, status);
  }
});
test("invalid or oversized JSON never consumes quota or reaches upstream", async () => {
  const env = environment();
  for (const [body, status] of [["{", 400], [JSON.stringify({ repositoryUrl: "http://127.0.0.1" }), 400], ["x".repeat(2049), 413]]) {
    assert.equal((await handleDeepRequest(request({ body }), env, forbidden)).status, status);
  }
});
test("forwards only canonical URL and server token to fixed HTTPS endpoint", async () => {
  let calls = 0;
  const response = await handleDeepRequest(request({ body: JSON.stringify({ repositoryUrl: graph.repository.url + ".git", token: "attacker", endpoint: "https://evil.test" }), headers: { Authorization: "Bearer attacker", Cookie: "private=cookie", "X-Archaeologist-Client-Key": "a".repeat(64), "X-Forwarded-For": "192.0.2.99" } }), environment(), async (url, options) => {
    calls++;
    assert.equal(url, DEEP_ENDPOINT);
    assert.equal(options.redirect, "manual");
    assert.ok(url.endsWith("/api/analyze/quota-v1"));
    assert.deepEqual(options.headers, { "Content-Type": "application/json", Authorization: `Bearer ${token}`, "X-Archaeologist-Client-Key": await networkKey("192.0.2.1", token) });
    assert.deepEqual(JSON.parse(options.body), { repositoryUrl: graph.repository.url });
    return graphResponse(graph);
  });
  assert.equal(calls, 1); assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(response.headers.get("x-archaeologist-report-id"), "R".repeat(43));
  assert.deepEqual(await response.json(), graph);
});
test("Oracle quota denial is distinct from busy and sanitized", async () => {
  const response = await handleDeepRequest(request(), environment(), async () => new Response(token, {
    status: 429, headers: { "X-Archaeologist-Limit": "quota", "Retry-After": "attacker" },
  }));
  assert.equal(response.status, 429);
  assert.equal(response.headers.get("retry-after"), "3600");
  const detail = await response.text();
  assert.ok(detail.includes("allowance reached") && !detail.includes(token));
  const busy = await handleDeepRequest(request(), environment(), async () => new Response(null, { status: 429 }));
  assert.equal(busy.headers.get("retry-after"), "5");
});

test("upstream errors and redirects are sanitized and never retried", async () => {
  for (const [status, expected] of [[404, 502], [503, 503], [301, 502], [302, 502], [307, 502], [401, 503], [403, 503], [413, 413], [429, 429], [500, 502], [504, 504]]) {
    let calls = 0;
    const response = await handleDeepRequest(request(), environment(), async () => { calls++; return new Response(token, { status, headers: { Location: "https://evil.test", "Set-Cookie": token } }); });
    assert.equal(calls, 1); assert.equal(response.status, expected);
    assert.equal(response.headers.get("set-cookie"), null);
    assert.ok(!(await response.text()).includes(token));
  }
});
test("invalid graph or wrong repository cannot replace the map", async () => {
  for (const value of [{}, { ...graph, nodes: [] }, { ...graph, analysis: { ...graph.analysis, tier: "inventory" } }, { ...graph, snapshot: undefined }, { ...graph, repository: { ...graph.repository, url: "https://github.com/wrong/repo" } }]) {
    assert.equal((await handleDeepRequest(request(), environment(), async () => graphResponse(value))).status, 502);
  }
  assert.equal((await handleDeepRequest(request(), environment(), async () => Response.json(graph))).status, 502);
});
test("oversized upstream output is rejected", async () => {
  const response = await handleDeepRequest(request(), environment(), async () => new Response("x".repeat(10 * 1024 * 1024 + 1)));
  assert.equal(response.status, 413);
});
test("client cancellation aborts the upstream request", async () => {
  const controller = new AbortController(); let signal;
  const response = await handleDeepRequest(request({ signal: controller.signal }), environment(), async (_, options) => {
    signal = options.signal; controller.abort();
    return new Promise(() => {});
  });
  assert.equal(response.status, 499); assert.equal(signal.aborted, true);
});
test("bounded stream reader cancels stalled input on abort", async () => {
  const controller = new AbortController(); let cancelled = false;
  const stream = new ReadableStream({ cancel() { cancelled = true; } });
  const pending = readBoundedBody(stream, 20, controller.signal);
  controller.abort();
  await assert.rejects(pending);
  assert.equal(cancelled, true);
});
test("deadline rejects even when upstream fails to honor abort", async () => {
  const controller = new AbortController();
  const pending = withDeadline(controller.signal, () => new Promise(() => {}));
  controller.abort(new DOMException("Timed out", "TimeoutError"));
  await assert.rejects(pending, { name: "TimeoutError" });
});
test("browser uses same-origin deep route even when a local API URL exists", async () => {
  assert.equal(analysisEndpoint("deep", "http://localhost:8000"), "/api/analyze/deep");
  assert.equal(analysisEndpoint("inventory", "http://localhost:8000"), "/api/analyze");
  let browserOptions;
  const result = await submitAnalysis("deep", graph.repository.url, "http://localhost:8000", new AbortController().signal, async (url, options) => {
    browserOptions = options;
    return handleDeepRequest(new Request("https://site.test" + url, { ...options, headers: { ...options.headers, Origin: "https://site.test", "CF-Connecting-IP": "192.0.2.1" } }), environment(), async () => graphResponse(graph));
  });
  assert.equal(browserOptions.headers.Authorization, undefined);
  assert.deepEqual(result, graph);
});
test("browser surfaces busy response instead of installing an error as graph", async () => {
  await assert.rejects(submitAnalysis("deep", graph.repository.url, "", new AbortController().signal,
    async () => Response.json({ detail: "Worker busy" }, { status: 429 })), /Worker busy/);
});

test("network keys match the HMAC contract and contain neither IP nor token", async () => {
  const key = await networkKey("192.0.2.1", token);
  assert.equal(key, createHmac("sha256", token).update("deep-analysis-network:192.0.2.1").digest("hex"));
  assert.match(key, /^[a-f0-9]{64}$/);
  assert.notEqual(key, await networkKey("192.0.2.2", token));
  assert.notEqual(key, await networkKey("192.0.2.1", "other-token"));
});

test("Sites build declares no database and old Oracle endpoint is never retried", async () => {
  const hosting = JSON.parse(await readFile(new URL("../.openai/hosting.json", import.meta.url), "utf8"));
  assert.equal(hosting.d1, null);
  assert.equal(deepConfigured(environment()), true);
  let calls = 0;
  const result = await handleDeepRequest(request(), environment(), async (url) => {
    calls++;
    assert.ok(url.endsWith("/api/analyze/quota-v1"));
    return new Response(null, {status: 404});
  });
  assert.equal(result.status, 502);
  assert.equal(calls, 1);
});
