import type { ExecutionFlow, GraphNode, UnresolvedStep } from "./graph-types";

/** Select from the installed report only; never synthesize missing paths. */
export function entrypointFlows(flows: ExecutionFlow[], requested: string | null) {
  const entrypointIds = [...new Set(flows.map((flow) => flow.entrypoint_id))];
  const entrypointId = requested && entrypointIds.includes(requested) ? requested : entrypointIds[0] ?? null;
  return { entrypointIds, entrypointId, paths: flows.filter((flow) => flow.entrypoint_id === entrypointId) };
}

/** Consistency check only: imported evidence is still unverified. */
export function flowEvidenceTarget(nodes: GraphNode[], sourceId: string, evidence: UnresolvedStep["evidence"]) {
  const node = nodes.find((candidate) => candidate.id === sourceId);
  const line = evidence.line;
  if (!node || evidence.path !== node.path || typeof line !== "number" || !Number.isSafeInteger(line) || line < 1) return null;
  if (!nodes.some((candidate) => candidate.kind === "file" && candidate.path === node.path)) return null;
  if (node.kind !== "file" && (typeof node.start_line !== "number" || typeof node.end_line !== "number"
      || !Number.isSafeInteger(node.start_line) || !Number.isSafeInteger(node.end_line)
      || node.start_line < 1 || line < node.start_line || line > node.end_line)) return null;
  return { nodeId: node.id, path: node.path, line };
}
