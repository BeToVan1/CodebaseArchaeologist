import { readBoundedBody } from "./github-analyzer.ts";
import { networkKey } from "./deep-limits.ts";
import { deepConfigured, withDeadline, type DeepEnv } from "./deep-proxy.ts";
import { WORKERS_AI_MODEL } from "./interpretation-provider.ts";

export const INTERPRETATION_ENDPOINT = "https://codebase-archaeologist.duckdns.org/api/interpret/quota-v1";
const REPORT_ID = /^[A-Za-z0-9_-]{43}$/;
const PROVENANCE = `Cloudflare Workers AI ${WORKERS_AI_MODEL} interpretation of server-retained evidence`;

export interface HostedInterpretationEnv extends DeepEnv { ARCHAEOLOGIST_INTERPRETATION_ENABLED?: string }

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
function validSection(value: unknown): boolean {
  return object(value) && exactKeys(value, ["text", "confidence", "evidence_refs", "classification", "provenance"])
    && typeof value.text === "string" && value.text.length >= 1 && value.text.length <= 1200
    && typeof value.confidence === "number" && Number.isFinite(value.confidence)
    && value.confidence >= 0 && value.confidence <= 0.85
    && value.classification === "interpretation" && value.provenance === PROVENANCE
    && Array.isArray(value.evidence_refs) && value.evidence_refs.length >= 1 && value.evidence_refs.length <= 10
    && value.evidence_refs.every(ref => typeof ref === "string" && ref.length >= 1 && ref.length <= 1000);
}
function validInterpretation(value: unknown, nodeId: string): value is Record<string, unknown> {
  if (!object(value) || !exactKeys(value, [
    "model", "classification", "commitSha", "nodeId", "what_it_does",
    "execution_role", "structural_rationale", "uncertainties",
  ])) return false;
  return value.model === WORKERS_AI_MODEL && value.classification === "interpretation"
    && typeof value.commitSha === "string" && /^[a-f0-9]{40}$/.test(value.commitSha)
    && value.nodeId === nodeId
    && validSection(value.what_it_does) && validSection(value.execution_role) && validSection(value.structural_rationale)
    && Array.isArray(value.uncertainties) && value.uncertainties.length <= 5
    && value.uncertainties.every(item => typeof item === "string" && item.length >= 1 && item.length <= 500);
}

export function interpretationConfigured(env: HostedInterpretationEnv): boolean {
  return env.ARCHAEOLOGIST_INTERPRETATION_ENABLED === "true" && deepConfigured(env);
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
  const timer = setTimeout(() => controller.abort(new DOMException("Timed out", "TimeoutError")), 30_000);
  let phase: "input" | "upstream" = "input";
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

    phase = "upstream";
    const clientKey = await withDeadline(signal, () => networkKey(clientIp, env.ARCHAEOLOGIST_SERVICE_TOKEN!));
    const upstream = await withDeadline(signal, () => fetcher(INTERPRETATION_ENDPOINT, {
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
      if (upstream.status === 429) return reply("The AI service is temporarily busy or its free allowance is unavailable.", 429);
      if (upstream.status === 502) return reply("AI interpretation is temporarily unavailable; deterministic evidence is unchanged.", 502);
      return reply("Trusted interpretation is temporarily unavailable.", 503);
    }
    const contents = await withDeadline(signal, () => readBoundedBody(upstream.body, 64 * 1024, signal));
    if (contents.truncated) return reply("AI interpretation exceeded the hosted output limit.", 502);
    let result: unknown;
    try { result = JSON.parse(contents.text); } catch { return reply("AI interpretation returned an invalid response.", 502); }
    if (!validInterpretation(result, body.nodeId)) return reply("AI interpretation returned an invalid response.", 502);
    return Response.json(result, { headers: { "Cache-Control": "no-store" } });
  } catch {
    if (request.signal.aborted) return reply("Interpretation cancelled.", 499);
    if (signal.aborted) return reply("Interpretation timed out.", 504);
    if (phase === "input" && inputSignal?.aborted) return reply("Request body timed out.", 408);
    return reply("Trusted interpretation is temporarily unavailable.", 503);
  } finally {
    clearTimeout(timer);
    controller.abort();
  }
}
