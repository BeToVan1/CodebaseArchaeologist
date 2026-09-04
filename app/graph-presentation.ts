import type { Claim, EvidenceStatement, GraphEdge, GraphNode } from "./graph-types.ts";
import type { NodeChange } from "@xyflow/react";
import { isTestPath } from "./test-proximity.ts";

export function pathMatchesScope(path: string, scope: "production" | "all", query: string): boolean {
  return (scope === "all" || !isTestPath(path)) && path.toLowerCase().includes(query.toLowerCase());
}

export function revealedNodeSelection(nodes: GraphNode[], id: string, scope: "production" | "all", query: string) {
  const target = nodes.find(node => node.id === id);
  if (!target) return null;
  const file = target.kind === "file" ? target : nodes.find(node => node.kind === "file" && node.path === target.path);
  if (!file) return null;
  return {
    fileId: file.id, symbolId: target.kind === "file" ? null : target.id,
    scope: isTestPath(file.path) ? "all" as const : scope,
    query: file.path.toLowerCase().includes(query.toLowerCase()) ? query : "",
  };
}

export function changedFileSelection(changes: NodeChange[], visibleIds: Set<string>): string | null {
  for (let index = changes.length - 1; index >= 0; index--) {
    const change = changes[index];
    if (change.type === "select" && change.selected && visibleIds.has(change.id)) return change.id;
  }
  return null;
}

export type Explanation = {
  summary: string; role: string; rationale: string; claims: Claim[];
  grounding?: { summary: EvidenceStatement; role: EvidenceStatement; rationale: EvidenceStatement };
};

export function selectionMetadata(file: GraphNode, symbol: GraphNode | null) {
  const node = symbol ?? file;
  return { kind: symbol ? `Python ${symbol.kind}` : "Python file", id: node.id,
    name: symbol?.qualified_name ?? symbol?.name ?? file.path,
    range: symbol ? `${symbol.start_line}–${symbol.end_line}` : null };
}

// Evidence belongs to the edge's call/read site, not its destination symbol.
export function evidenceLocation(edge: GraphEdge | undefined): string {
  if (!edge?.evidence?.path) return "Evidence location unavailable";
  return `${edge.evidence.path}${edge.evidence.line ? `:${edge.evidence.line}` : " (line unavailable)"}`;
}

export type ReportOrigin = "example" | "analysis" | "imported";
export function currentReportLabel(origin: ReportOrigin, tier: string): string {
  return `${origin === "example" ? "Bundled example" : origin === "imported" ? "Imported report" : "Analysis result"} · ${tier} report`;
}

export function explainFile(node: GraphNode, symbols: GraphNode[], incoming: GraphEdge[], outgoing: GraphEdge[], layer: {key: string; label: string}): Explanation {
  const members = symbols.filter(symbol => symbol.path === node.path && symbol.kind !== "file");
  const models = members.filter(symbol => symbol.sqlalchemy?.kind === "model");
  const routes = members.filter(symbol => symbol.entrypoint?.framework === "fastapi");
  const facts = [
    ...(models.length ? [`Contains ${models.length} SQLAlchemy model${models.length === 1 ? "" : "s"}: ${models.map(model => model.qualified_name ?? model.name).join(", ")}.`] : []),
    ...(routes.length ? [`Contains ${routes.length} recognized FastAPI route${routes.length === 1 ? "" : "s"}: ${routes.map(route => route.entrypoint!.label).join(", ")}.`] : []),
  ];
  const summary: EvidenceStatement = {
    text: members.length ? `Contains ${members.length} recorded Python symbol${members.length === 1 ? "" : "s"}. Select a symbol for its exact definition and evidence.`
      : node.source_error ? "Source could not be read; behavior cannot be established."
        : "No symbol definitions were recorded. Inspect the source; this does not establish the file's purpose.",
    classification: "fact", confidence: 1, provenance: "Recorded AST symbols and source-read status for this file",
  };
  const roles: Record<string, string> = {
    entrypoints: "Its path suggests delivery or startup boundary code.",
    services: "Its path suggests application use-case orchestration.",
    domain: "Its path suggests domain behavior or business vocabulary.",
    adapters: "Its path suggests infrastructure integration.",
    tests: "Its path suggests verification code.",
  };
  const role: EvidenceStatement = facts.length ? {
    text: facts.join(" "), classification: "fact", confidence: 1,
    provenance: "Recognized framework metadata on this file's AST symbols",
  } : {
    text: roles[layer.key] ?? "The file's execution role is not established. Its path alone does not identify configuration, infrastructure, or business logic.",
    classification: "heuristic", confidence: roles[layer.key] ? 0.6 : 0,
    provenance: roles[layer.key] ? `Path convention only: ${node.path}; not runtime evidence` : "No recognized framework role or architectural path convention",
  };
  const rationale: EvidenceStatement = {
    text: "The author's reason for this file's placement is not established by these static facts. Inspect its symbols, relationships and project documentation before drawing an architectural conclusion.",
    classification: "interpretation", confidence: 0, provenance: "Intent not established; no author rationale inferred",
  };
  const refs = [node.id, ...members.map(symbol => symbol.id)];
  return {summary: summary.text, role: role.text, rationale: rationale.text,
    grounding: {summary, role, rationale}, claims: [
      {...summary, evidence_refs: refs},
      {...role, evidence_refs: facts.length ? [node.id, ...models.map(model => model.id), ...routes.map(route => route.id)] : [node.id]},
      {text: `${outgoing.length} internal imports out; ${incoming.length} internal importers in.`,
        classification: "fact", confidence: 1, provenance: "Resolved AST import edges",
        evidence_refs: [node.id, ...incoming.map(edge => edge.id), ...outgoing.map(edge => edge.id)]},
    ]};
}
