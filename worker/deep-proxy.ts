import { canonicalGithubUrl, MAX_REPORT_BYTES, validateReport } from "../app/graph-report.ts";
import { readBoundedBody } from "./github-analyzer.ts";
import { networkKey } from "./deep-limits.ts";

// Credentials can only be sent to the owner-approved backend, never a request URL.
// Versioned route prevents dispatch to the older, unmetered Oracle service.
export const DEEP_ENDPOINT = "https://codebase-archaeologist.duckdns.org/api/analyze/quota-v1";
export const REPORT_ID_HEADER = "X-Archaeologist-Report-Id";
export const REPORT_TTL_HEADER = "X-Archaeologist-Report-TTL";
export interface DeepEnv {
  ARCHAEOLOGIST_DEEP_ENABLED?: string;
  ARCHAEOLOGIST_SERVICE_TOKEN?: string;
}
export function deepConfigured(env: DeepEnv): boolean {
  return env.ARCHAEOLOGIST_DEEP_ENABLED === "true"
    && typeof env.ARCHAEOLOGIST_SERVICE_TOKEN === "string"
    && /^[\x21-\x7e]{32,256}$/.test(env.ARCHAEOLOGIST_SERVICE_TOKEN);
}
function reply(detail: string, status: number, retry?: number) {
  return Response.json({ detail }, { status, headers: {
    "Cache-Control": "no-store", ...(retry ? { "Retry-After": String(retry) } : {}),
    ...(status === 405 ? { Allow: "POST" } : {}),
  } });
}
// Race the entire fetch + body operation, not just response headers. Cancel the
// stream when timing out or when the client disconnects; don't retry analysis.
export async function withDeadline<T>(signal: AbortSignal, work: () => Promise<T>): Promise<T> {
  signal.throwIfAborted();
  let abort: () => void = () => {};
  try {
    return await Promise.race([work(), new Promise<never>((_, reject) => {
      abort = () => reject(signal.reason);
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) abort();
    })]);
  } finally { signal.removeEventListener("abort", abort); }
}
export async function handleDeepRequest(request: Request, env: DeepEnv, fetcher: typeof fetch = fetch): Promise<Response> {
  if (request.method !== "POST") return reply("Method not allowed.", 405);
  if (request.headers.get("origin") !== new URL(request.url).origin || request.headers.get("sec-fetch-site") === "cross-site") {
    return reply("Deep analysis must be requested from this website.", 403);
  }
  if (request.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/json") return reply("Send a JSON request.", 415);
  if (!deepConfigured(env)) return reply("Hosted deep analysis is not configured. Use inventory mode or open a local report.", 503);
  // Only the platform-supplied connecting IP is considered. Never accept XFF,
  // client IDs from JSON, or user-provided quota keys. This is NOT user identity.
  const clientIp = request.headers.get("cf-connecting-ip");
  if (!clientIp || clientIp.length > 45 || !/^[0-9a-fA-F:.]+$/.test(clientIp)) return reply("Deep analysis cannot verify the request network. Use inventory mode.", 503);
  const controller = new AbortController();
  const signal = AbortSignal.any([request.signal, controller.signal]);
  const timer = setTimeout(() => controller.abort(new DOMException("Timed out", "TimeoutError")), 70_000);
  let phase: "input" | "limits" | "upstream" = "input";
  const inputSignal = AbortSignal.any([signal, AbortSignal.timeout(5_000)]);
  try {
    const incoming = await withDeadline(inputSignal, () => readBoundedBody(request.body, 2048, inputSignal));
    if (incoming.truncated) return reply("Request exceeds the 2 KiB limit.", 413);
    let body: unknown;
    try { body = JSON.parse(incoming.text); } catch { return reply("Request must be valid JSON.", 400); }
    const value = body && typeof body === "object" && !Array.isArray(body) ? (body as Record<string, unknown>).repositoryUrl : null;
    const canonical = typeof value === "string" && value.length <= 300 ? canonicalGithubUrl(value.trim()) : undefined;
    if (!canonical) return reply("Enter a public GitHub repository URL.", 400);
    phase = "limits";
    const clientKey = await withDeadline(signal, () => networkKey(clientIp, env.ARCHAEOLOGIST_SERVICE_TOKEN!));
    signal.throwIfAborted();
    phase = "upstream";
    return await withDeadline(signal, async () => {
      const response = await fetcher(DEEP_ENDPOINT, { method: "POST", redirect: "manual", signal,
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${env.ARCHAEOLOGIST_SERVICE_TOKEN}`, "X-Archaeologist-Client-Key": clientKey },
        body: JSON.stringify({ repositoryUrl: canonical }) });
      if (!response.ok) {
        await response.body?.cancel();
        if (response.status === 429) return response.headers.get("x-archaeologist-limit") === "quota"
          ? reply("Deep-analysis allowance reached (3 per network / 10 minutes, 30 total / hour). Try later or use inventory mode.", 429, 3600)
          : reply("The deep-analysis worker is busy. Retry shortly; your current map is unchanged.", 429, 5);
        if (response.status === 413) return reply("Repository or report exceeds hosted limits. Use a smaller repository or the local analyzer.", 413);
        if (response.status === 504) return reply("Deep analysis exceeded its time limit. Try a smaller repository or use the local analyzer.", 504);
        if ([401, 403, 503].includes(response.status)) return reply("Hosted deep analysis is temporarily unavailable. Use inventory mode.", 503);
        return reply("The deep-analysis service could not complete this request. Check the repository URL or use inventory mode.", 502);
      }
      const contents = await readBoundedBody(response.body, MAX_REPORT_BYTES, signal);
      if (contents.truncated) return reply("Report exceeds the 10 MiB browser limit. Use the local analyzer.", 413);
      const graph = validateReport(JSON.parse(contents.text));
      if (graph.analysis?.tier !== "deep" || graph.repository?.source !== "github"
          || canonicalGithubUrl(graph.repository?.url ?? "")?.toLowerCase() !== canonical.toLowerCase()
          || !graph.snapshot?.commit_sha || graph.repository.pinned_url !== `${graph.repository.url}/tree/${graph.snapshot.commit_sha}`) {
        throw new Error("Unexpected report identity or tier");
      }
      const reportId = response.headers.get(REPORT_ID_HEADER) ?? "";
      const reportTtl = response.headers.get(REPORT_TTL_HEADER) ?? "";
      if (!/^[A-Za-z0-9_-]{43}$/.test(reportId) || reportTtl !== "900")
        throw new Error("Missing trusted evidence reference");
      return Response.json(graph, { headers: {
        "Cache-Control": "no-store", [REPORT_ID_HEADER]: reportId, [REPORT_TTL_HEADER]: reportTtl,
      } });
    });
  } catch {
    if (request.signal.aborted) return reply("Analysis cancelled.", 499);
    if (signal.aborted) return reply("Deep analysis timed out. Try a smaller repository or use inventory mode.", 504);
    if (phase === "input" && inputSignal.aborted) return reply("Request body timed out.", 408);
    return reply(phase === "input" ? "Request body could not be read." : phase === "limits" ? "Deep-analysis usage limits are unavailable. Please try inventory mode." : "The deep-analysis service returned an invalid or unavailable report.", phase === "input" ? 400 : phase === "limits" ? 503 : 502);
  } finally {
    clearTimeout(timer);
    controller.abort();
  }
}
