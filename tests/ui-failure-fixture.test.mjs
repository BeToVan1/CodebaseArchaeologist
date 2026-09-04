import assert from "node:assert/strict";
import test from "node:test";
import { fixtureResponse } from "../scripts/serve-ui-failures.mjs";

const origin = "http://127.0.0.1:3002";
const forbidden = () => assert.fail("No build or external fetch allowed");
test("local UI fixture formats failures through the real proxy without network", async () => {
  for (const [scenario, status, message] of [["busy", 429, /busy/], ["quota", 429, /allowance/], ["outage", 503, /unavailable/], ["limit", 413, /limits/], ["timeout", 504, /time limit/]]) {
    const response = await fixtureResponse(new Request(origin + "/api/analyze/deep", {
      method: "POST", headers: { Origin: origin, "Content-Type": "application/json" },
      body: JSON.stringify({ repositoryUrl: "https://github.com/fixture/" + scenario }),
    }), forbidden);
    assert.equal(response.status, status);
    assert.match((await response.json()).detail, message);
  }
});
test("local UI fixture blocks other API calls, writes and foreign origins", async () => {
  for (const request of [new Request(origin + "/api/analyze"), new Request(origin + "/api/interpret"), new Request(origin + "/", { method: "POST" }), new Request("https://example.com/")]) {
    assert.equal((await fixtureResponse(request, forbidden)).status, 403);
  }
});
test("local UI fixture forwards assets only to a fixed loopback build without redirects", async () => {
  await fixtureResponse(new Request(origin + "/assets/app.js"), async (url, options) => {
    assert.equal(url, "http://127.0.0.1:3001/assets/app.js");
    assert.equal(options.redirect, "error");
    return new Response("fixture");
  });
});
