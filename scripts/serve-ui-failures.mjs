// Local browser acceptance fixture. Never deploy or use as a real analyzer.
// First start the existing production build on 127.0.0.1:3001.
// Then run this with Node 24 and open http://127.0.0.1:3002.
// Submit https://github.com/fixture/busy (or quota, outage, limit, timeout).
import { createServer } from "node:http";
import { pathToFileURL } from "node:url";
import { handleDeepRequest } from "../worker/deep-proxy.ts";

const origin = "http://127.0.0.1:3002";
const scenarios = new Map([
  ["https://github.com/fixture/busy", [429, {}]],
  ["https://github.com/fixture/quota", [429, { "X-Archaeologist-Limit": "quota" }]],
  ["https://github.com/fixture/outage", [503, {}]],
  ["https://github.com/fixture/limit", [413, {}]],
  ["https://github.com/fixture/timeout", [504, {}]],
]);

export async function fixtureResponse(request, fetchBuild = fetch) {
  const url = new URL(request.url);
  if (url.origin !== origin) return new Response("Loopback fixture only", { status: 403 });
  if (url.pathname === "/api/analysis-capabilities") return Response.json({ deep: true });
  if (url.pathname === "/api/analyze/deep") {
    // The real proxy validates input and formats errors; its only upstream is a stub.
    const headers = new Headers(request.headers);
    headers.set("CF-Connecting-IP", "192.0.2.1");
    return handleDeepRequest(new Request(request, { headers }), {
      ARCHAEOLOGIST_DEEP_ENABLED: "true",
      ARCHAEOLOGIST_SERVICE_TOKEN: "synthetic-local-fixture-token-not-a-real-secret",
    }, async (_endpoint, options) => {
      const scenario = scenarios.get(JSON.parse(options.body).repositoryUrl);
      if (!scenario) return new Response(null, { status: 400 });
      const [status, responseHeaders] = scenario;
      return new Response("Synthetic upstream detail must not reach the UI", { status, headers: responseHeaders });
    });
  }
  // Block every other API and all writes: no inventory, interpretation, or live jobs.
  if (url.pathname.startsWith("/api/") || !["GET", "HEAD"].includes(request.method)) {
    return new Response("Disabled in failure fixture", { status: 403 });
  }
  // Fixed loopback destination; never follow redirects to remote services.
  return fetchBuild("http://127.0.0.1:3001" + url.pathname + url.search, {
    method: request.method, redirect: "error", signal: AbortSignal.timeout(10000),
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const server = createServer(async (req, res) => {
    try {
      if (req.headers.host !== "127.0.0.1:3002") {
        res.writeHead(403).end("Loopback fixture only"); return;
      }
      const chunks = [];
      let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 2048) { res.writeHead(413).end("Fixture input too large"); return; }
        chunks.push(chunk);
      }
      const request = new Request(new URL(req.url, origin), {
        method: req.method, headers: req.headers,
        ...(!["GET", "HEAD"].includes(req.method) ? { body: Buffer.concat(chunks) } : {}),
      });
      const response = await fixtureResponse(request);
      const headers = Object.fromEntries(response.headers);
      // Fetch decodes compressed build assets before forwarding.
      delete headers["content-encoding"]; delete headers["content-length"];
      headers["cache-control"] = "no-store";
      res.writeHead(response.status, headers);
      res.end(Buffer.from(await response.arrayBuffer()));
    } catch {
      if (!res.headersSent) res.writeHead(502);
      res.end("Local fixture failed; check the build server.");
    }
  });
  server.requestTimeout = 10000;
  server.listen(3002, "127.0.0.1", () => console.log(`LOCAL FAILURE FIXTURE: ${origin} (no live analysis)`));
}
