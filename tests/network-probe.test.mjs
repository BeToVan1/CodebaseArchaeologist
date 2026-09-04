import assert from "node:assert/strict";
import test from "node:test";
import { handleNetworkProbe } from "../worker/network-probe.ts";
import { networkKey } from "../worker/deep-limits.ts";

const now = Date.parse("2026-09-03T12:00:00Z");
const token = "test-only-service-token-" + "z".repeat(40);
const env = { ARCHAEOLOGIST_SERVICE_TOKEN: token, ARCHAEOLOGIST_NETWORK_PROBE_UNTIL: new Date(now + 1800000).toISOString() };
const request = (headers = {}, method = "GET") => new Request("https://site.test/api/network-probe", { method, headers: { "CF-Connecting-IP": "192.0.2.1", ...headers } });

test("network probe is disabled by default, expires, and rejects distant expiry or invalid secret", async () => {
  for (const change of [{ ARCHAEOLOGIST_NETWORK_PROBE_UNTIL: undefined }, { ARCHAEOLOGIST_NETWORK_PROBE_UNTIL: "invalid" }, { ARCHAEOLOGIST_NETWORK_PROBE_UNTIL: new Date(now).toISOString() }, { ARCHAEOLOGIST_NETWORK_PROBE_UNTIL: new Date(now + 3600001).toISOString() }, { ARCHAEOLOGIST_SERVICE_TOKEN: "short" }]) {
    assert.equal((await handleNetworkProbe(request(), { ...env, ...change }, now)).status, 404);
  }
  assert.equal((await handleNetworkProbe(request({}, "POST"), env, now)).status, 405);
});

test("probe returns only a temporary domain-separated proof and ignores client identity headers", async () => {
  const result = await handleNetworkProbe(request(), env, now);
  assert.equal(result.headers.get("cache-control"), "no-store");
  const body = await result.json();
  assert.deepEqual(Object.keys(body).sort(), ["available", "knownSharedWorkerAddress", "proof"]);
  assert.match(body.proof, /^[a-f0-9]{64}$/);
  assert.notEqual(body.proof, await networkKey("192.0.2.1", token));
  assert.equal(JSON.stringify(body).includes(token), false);
  assert.equal(JSON.stringify(body).includes("192.0.2.1"), false);
  const spoofed = await (await handleNetworkProbe(request({ "X-Forwarded-For": "192.0.2.9", "X-Real-IP": "192.0.2.8", "X-Archaeologist-Client-Key": "a".repeat(64) }), env, now)).json();
  assert.deepEqual(spoofed, body);
  const different = await (await handleNetworkProbe(request({ "CF-Connecting-IP": "192.0.2.2" }), env, now)).json();
  assert.notEqual(different.proof, body.proof);
  const next = await (await handleNetworkProbe(request(), { ...env, ARCHAEOLOGIST_NETWORK_PROBE_UNTIL: new Date(now + 600000).toISOString() }, now)).json();
  assert.notEqual(next.proof, body.proof);
});

test("probe identifies absent and known shared Worker headers without reporting raw addresses", async () => {
  assert.deepEqual(await (await handleNetworkProbe(request({ "CF-Connecting-IP": "" }), env, now)).json(), { available: false });
  const shared = await (await handleNetworkProbe(request({ "CF-Connecting-IP": "2a06:98c0:3600::103" }), env, now)).json();
  assert.equal(shared.knownSharedWorkerAddress, true);
});
