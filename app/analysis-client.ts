import { MAX_REPORT_BYTES, validateReport } from "./graph-report.ts";

export type AnalysisMode = "inventory" | "deep" | "local";
export type EvidenceReference = { reportId: string; expiresAt: number };
export type AnalysisResult = { graph: ReturnType<typeof validateReport>; evidenceReference: EvidenceReference | null };
const REPORT_ID_HEADER = "X-Archaeologist-Report-Id";
const REPORT_TTL_HEADER = "X-Archaeologist-Report-TTL";
export function analysisEndpoint(mode: AnalysisMode, localUrl = "") {
  if (mode === "deep") return "/api/analyze/deep";
  if (mode === "local") {
    if (!localUrl) throw new Error("Local analyzer is not configured.");
    return `${localUrl}/api/analyze`;
  }
  return "/api/analyze";
}
export async function submitAnalysisResult(mode: AnalysisMode, repositoryUrl: string, localUrl: string, signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<AnalysisResult> {
  const response = await fetcher(analysisEndpoint(mode, localUrl), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repositoryUrl }), signal,
  });
  const limit = response.ok ? MAX_REPORT_BYTES : 4096;
  const reader = response.body?.getReader();
  const chunks: Uint8Array[] = [];
  let bytes = 0;
  try {
    if (reader) while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > limit) throw new Error("Analysis response exceeds the browser limit.");
      chunks.push(value);
    }
  } finally { await reader?.cancel().catch(() => {}); reader?.releaseLock(); }
  const buffer = new Uint8Array(bytes);
  let offset = 0;
  for (const chunk of chunks) { buffer.set(chunk, offset); offset += chunk.byteLength; }
  let data;
  try { data = JSON.parse(new TextDecoder().decode(buffer)); }
  catch { throw new Error(`Analysis returned an unreadable response (${response.status}).`); }
  if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `Analysis failed (${response.status}).`);
  const graph = validateReport(data);
  if (mode === "deep" && graph.analysis?.tier !== "deep") throw new Error("The server did not return deep analysis. Your current map is unchanged.");
  let evidenceReference: EvidenceReference | null = null;
  if (mode === "deep") {
    const reportId = response.headers.get(REPORT_ID_HEADER) ?? "";
    const ttl = Number(response.headers.get(REPORT_TTL_HEADER));
    if (!/^[A-Za-z0-9_-]{43}$/.test(reportId) || !Number.isSafeInteger(ttl) || ttl < 1 || ttl > 900)
      throw new Error("Deep analysis did not provide a trusted evidence reference. Your current map is unchanged.");
    evidenceReference = { reportId, expiresAt: Date.now() + ttl * 1000 };
  }
  return { graph, evidenceReference };
}

/** Backward-compatible graph-only API for callers that do not need hosted interpretation. */
export async function submitAnalysis(mode: AnalysisMode, repositoryUrl: string, localUrl: string, signal: AbortSignal, fetcher: typeof fetch = fetch) {
  return (await submitAnalysisResult(mode, repositoryUrl, localUrl, signal, fetcher)).graph;
}
