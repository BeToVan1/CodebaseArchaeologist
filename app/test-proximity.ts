import type { Graph, GraphNode } from "./graph-types";

export function isTestPath(path: string): boolean {
  const parts = path.split("/");
  const name = parts.pop() ?? "";
  return name.endsWith(".py") && (parts.some(part => part === "test" || part === "tests")
    || name.startsWith("test_") || name.endsWith("_test.py") || name === "tests.py" || name === "conftest.py");
}

export function validateTestProximity(value: unknown, graph: Graph): void {
  if (value === undefined) return;
  const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
  const text = (v: unknown) => typeof v === "string" && v.length > 0 && v.length <= 2048;
  const fail = () => { throw new Error("Invalid graph: test proximity metadata or evidence references are invalid."); };
  if (!record(value) || value.version !== "1" || value.scope !== "recorded-direct-edges"
    || !Number.isSafeInteger(value.test_files_identified) || Number(value.test_files_identified) < 0
    || !Number.isSafeInteger(value.candidate_links) || Number(value.candidate_links) < 0
    || typeof value.links_truncated !== "boolean" || !text(value.provenance)
    || !Array.isArray(value.limitations) || !value.limitations.length || value.limitations.length > 12 || !value.limitations.every(text)
    || !Array.isArray(value.links) || value.links.length > 1000
    || new TextEncoder().encode(JSON.stringify(value)).length > 300 * 1024) return fail();
  const nodes = new Map(graph.nodes.map(node => [node.id, node]));
  const edges = new Map(graph.edges.map(edge => [edge.id, edge]));
  if (nodes.size !== graph.nodes.length || edges.size !== graph.edges.length) return fail();
  const eligible = graph.edges.filter(edge => {
    const source = nodes.get(edge.source), target = nodes.get(edge.target);
    return source && target && isTestPath(source.path) && !isTestPath(target.path)
      && Number.isFinite(edge.confidence) && Number(edge.confidence) >= 0.9 && Number(edge.confidence) <= 1
      && ((edge.kind === "imports" && source.kind === "file" && target.kind === "file")
        || (edge.kind === "calls" && source.kind !== "file" && target.kind !== "file"));
  });
  if (value.test_files_identified !== graph.nodes.filter(node => node.kind === "file" && isTestPath(node.path)).length
    || value.candidate_links !== eligible.length || value.links_truncated !== (eligible.length > value.links.length)) return fail();
  const eligibleIds = new Set(eligible.map(edge => edge.id));
  const seen = new Set<string>();
  for (const link of value.links) {
    if (!record(link) || typeof link.edge_id !== "string" || !eligibleIds.has(link.edge_id) || seen.has(link.edge_id)) return fail();
    seen.add(link.edge_id);
    const edge = edges.get(link.edge_id)!;
    const source = nodes.get(edge.source)!;
    if (link.source_node_id !== edge.source || link.target_node_id !== edge.target
      || link.signal !== (edge.kind === "calls" ? "symbol-call" : "module-import")
      || link.classification !== "heuristic" || link.confidence !== 0.6
      || !Number.isSafeInteger(edge.evidence?.line) || Number(edge.evidence?.line) < 1
      || (edge.evidence?.path !== undefined && edge.evidence.path !== source.path)) return fail();
  }
}

export function testEvidenceForSelection(graph: Graph, selected: GraphNode) {
  const nodes = new Map(graph.nodes.map(node => [node.id, node]));
  const edges = new Map(graph.edges.map(edge => [edge.id, edge]));
  return (graph.test_proximity?.links ?? []).filter(link => {
    const target = nodes.get(link.target_node_id);
    return link.signal === "module-import" || selected.kind === "file"
      ? target?.path === selected.path : target?.id === selected.id;
  }).map(link => ({ link, source: nodes.get(link.source_node_id)!, target: nodes.get(link.target_node_id)!, edge: edges.get(link.edge_id)! }));
}
