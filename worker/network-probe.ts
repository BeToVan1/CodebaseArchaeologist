// Short-lived rollout diagnostic. Never returns an IP, token, or admission key.
// It does not contact Oracle, consume quota, or enable deep analysis.
export interface NetworkProbeEnv {
  ARCHAEOLOGIST_NETWORK_PROBE_UNTIL?: string;
  ARCHAEOLOGIST_SERVICE_TOKEN?: string;
}

export async function handleNetworkProbe(request: Request, env: NetworkProbeEnv, now = Date.now()): Promise<Response> {
  const headers = { "Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow" };
  const until = Date.parse(env.ARCHAEOLOGIST_NETWORK_PROBE_UNTIL ?? "");
  const token = env.ARCHAEOLOGIST_SERVICE_TOKEN;
  if (!Number.isFinite(until) || until <= now || until > now + 3_600_000
      || !token || !/^[\x21-\x7e]{32,256}$/.test(token)) {
    return Response.json({ detail: "Not found." }, { status: 404, headers });
  }
  if (request.method !== "GET") return Response.json({ detail: "Method not allowed." }, { status: 405, headers: { ...headers, Allow: "GET" } });
  const ip = request.headers.get("cf-connecting-ip");
  if (!ip || ip.length > 45 || !/^[0-9a-fA-F:.]+$/.test(ip)) {
    return Response.json({ available: false }, { headers });
  }
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", encoder.encode(token), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const digest = await crypto.subtle.sign("HMAC", key, encoder.encode(`archaeologist-rollout-probe:${until}:${ip}`));
  const proof = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
  return Response.json({ available: true, proof, knownSharedWorkerAddress: ip.toLowerCase() === "2a06:98c0:3600::103" }, { headers });
}
