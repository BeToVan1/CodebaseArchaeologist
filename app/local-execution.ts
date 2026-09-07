import type { GraphNode, Claim } from "./graph-types";

/** Presentation only: imported claims are not authenticated by their IDs. */
export function localExecutionClaims(node: GraphNode | null | undefined): Claim[] {
  if (!node || node.kind === "file") return [];
  const prefix = `claim:${node.id}:local-execution:`;
  return (node.evidence_packet?.claims ?? []).filter(claim =>
    claim.classification === "fact" && claim.id?.startsWith(prefix)
    && /^\d+$/.test(claim.id.slice(prefix.length))
    && claim.evidence_refs?.includes(node.id));
}
