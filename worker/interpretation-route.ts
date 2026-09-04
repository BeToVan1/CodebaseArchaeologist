import { readBoundedBody } from "./github-analyzer.ts";
import { networkKey } from "./deep-limits.ts";
import { deepConfigured, withDeadline, type DeepEnv } from "./deep-proxy.ts";
import { CloudflareWorkersAIProvider, WORKERS_AI_MODEL, type WorkersAI } from "./interpretation-provider.ts";

export const EVIDENCE_ENDPOINT = "https://codebase-archaeologist.duckdns.org/api/evidence/prepare";
const REPORT_ID = /^[A-Za-z0-9_-]{43}$/;

export interface HostedInterpretationEnv extends DeepEnv {
  ARCHAEOLOGIST_INTERPRETATION_ENABLED?: string;
  AI?: WorkersAI;
}

function reply(detail: string, status: number) {
  return Response.json({ detail }, { status, headers: {
    "Cache-Control": "no-store", ...(status === 405 ? { Allow: "POST" } : {}),
  } });
}
function object(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
function exactKeys(value: Record<string, unknown>, expected: string[]) {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  return actual.length === wanted.length && actual.every((key, index) => key === wanted[index]);
}
export function interpretationConfigured(env: HostedInterpretationEnv): boolean {
  return env.ARCHAEOLOGIST_INTERPRETATION_ENABLED === "true"
    && deepConfigured(env) && object(env.AI) && typeof env.AI.run === "function";
}

export async function handleInterpretationRequest(
  request: Request, env: HostedInterpretationEnv, fetcher: typeof fetch = fetch,
): Promise<Response> {
  if (request.method !== "POST") return reply("Method not allowed.", 405);
  if (request.headers.get("origin") !== new URL(request.url).origin
      || request.headers.get("sec-fetch-site") === "cross-site")
    return reply("Interpretation must be requested from this website.", 403);
  if (request.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/json")
    return reply("Send a JSON request.", 415);
  if (!interpretationConfigured(env))
    return reply("AI interpretation is not enabled. Deterministic evidence remains available.", 503);
  const clientIp = request.headers.get("cf-connecting-ip");
  if (!clientIp || clientIp.length > 45 || !/^[0-9a-fA-F:.]+$/.test(clientIp))
    return reply("Interpretation cannot verify the request network.", 503);

  const controller = new AbortController();
  const signal = AbortSignal.any([request.signal, controller.signal]);
  const timer = setTimeout(() => controller.abort(new DOMException("Timed out", "TimeoutError")), 25_000);
  let providerStarted = false;
  let phase: "input" | "evidence" | "provider" = "input";
  let inputSignal: AbortSignal | undefined;
  try {
    inputSignal = AbortSignal.any([signal, AbortSignal.timeout(5_000)]);
    const incoming = await withDeadline(inputSignal, () => readBoundedBody(request.body, 2048, inputSignal));
    if (incoming.truncated) return reply("Request exceeds the 2 KiB limit.", 413);
    let body: unknown;
    try { body = JSON.parse(incoming.text); } catch { return reply("Request must be valid JSON.", 400); }
    if (!object(body) || !exactKeys(body, ["reportId", "nodeId"])
        || typeof body.reportId !== "string" || !REPORT_ID.test(body.reportId)
        || typeof body.nodeId !== "string" || body.nodeId.length < 1 || body.nodeId.length > 1000)
      return reply("A valid report and selected symbol are required.", 400);

    phase = "evidence";
    const clientKey = await withDeadline(signal, () => networkKey(clientIp, env.ARCHAEOLOGIST_SERVICE_TOKEN!));
    signal.throwIfAborted();
    const upstream = await withDeadline(signal, () => fetcher(EVIDENCE_ENDPOINT, {
      method: "POST", redirect: "manual", signal,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${env.ARCHAEOLOGIST_SERVICE_TOKEN}`,
        "X-Archaeologist-Client-Key": clientKey,
      },
      body: JSON.stringify({ reportId: body.reportId, nodeId: body.nodeId }),
    }));
    if (!upstream.ok) {
      await upstream.body?.cancel();
      if (upstream.status === 404) return reply("Analysis evidence expired or is unavailable. Run a new deep analysis.", 404);
      if (upstream.status === 422) return reply("The selected symbol cannot be interpreted from trusted evidence.", 422);
      if (upstream.status === 429) return reply("Interpretation is temporarily busy. Try again shortly.", 429);
      return reply("Trusted interpretation evidence is temporarily unavailable.", 503);
    }
    const preparedBody = await withDeadline(signal, () => readBoundedBody(upstream.body, 80 * 1024, signal));
    if (preparedBody.truncated) return reply("Trusted interpretation evidence exceeds the hosted limit.", 413);
    let prepared: unknown;
    try { prepared = JSON.parse(preparedBody.text); } catch { throw new Error("Invalid evidence response"); }
    if (!object(prepared) || !exactKeys(prepared, ["commitSha", "evidencePacket", "sourceExcerpt"])
        || typeof prepared.commitSha !== "string" || !/^[a-f0-9]{40}$/.test(prepared.commitSha)
        || !object(prepared.evidencePacket) || prepared.evidencePacket.node_id !== body.nodeId
        || typeof prepared.sourceExcerpt !== "string" || prepared.sourceExcerpt.length < 1
        || prepared.sourceExcerpt.length > 12_000)
      throw new Error("Invalid evidence response");
    const evidencePacket = prepared.evidencePacket as Record<string, unknown>;
    const sourceExcerpt = prepared.sourceExcerpt as string;

    providerStarted = true;
    phase = "provider";
    const generated = await withDeadline(signal, () =>
      new CloudflareWorkersAIProvider(env.AI!).generate(evidencePacket, sourceExcerpt));
    const section = (value: typeof generated.what_it_does) => ({
      ...value, classification: "interpretation" as const,
      provenance: `Cloudflare Workers AI ${WORKERS_AI_MODEL} interpretation of server-retained evidence`,
    });
    return Response.json({
      model: WORKERS_AI_MODEL,
      classification: "interpretation",
      commitSha: prepared.commitSha,
      nodeId: body.nodeId,
      what_it_does: section(generated.what_it_does),
      execution_role: section(generated.execution_role),
      structural_rationale: section(generated.structural_rationale),
      uncertainties: generated.uncertainties,
    }, { headers: { "Cache-Control": "no-store" } });
  } catch {
    if (request.signal.aborted) return reply("Interpretation cancelled.", 499);
    if (signal.aborted) return reply("Interpretation timed out.", 504);
    if (phase === "input" && inputSignal?.aborted) return reply("Request body timed out.", 408);
    return reply(providerStarted
      ? "AI interpretation is temporarily unavailable; deterministic evidence is unchanged."
      : "Trusted interpretation evidence is invalid or unavailable.", providerStarted ? 502 : 503);
  } finally {
    clearTimeout(timer);
    controller.abort();
  }
}
