import type { Graph, GraphEdge, EvidenceStatement, ExecutionFlow } from "./graph-types";

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isEvidenceStatement(value: unknown): value is EvidenceStatement {
  return isRecord(value)
    && typeof value.text === "string"
    && ["fact", "heuristic", "interpretation"].includes(String(value.classification))
    && typeof value.confidence === "number"
    && value.confidence >= 0
    && value.confidence <= 1
    && typeof value.provenance === "string";
}

export function validateGraph(value: unknown): Graph {
  if (!isRecord(value) || typeof value.schema_version !== "string") {
    throw new Error("Invalid graph: schema_version must be a string.");
  }
  if (!Array.isArray(value.nodes) || !Array.isArray(value.edges)) {
    throw new Error("Invalid graph: nodes and edges must be arrays.");
  }
  if (value.project_discovery !== undefined) {
    const p = value.project_discovery;
    const text = (v: unknown): v is string => typeof v === "string" && v.length > 0 && v.length <= 1024 && !/[\u0000-\u001f]/.test(v);
    const list = (v: unknown, max: number) => Array.isArray(v) && v.length <= max && v.every(text);
    if (!isRecord(p) || p.version !== "1" || p.scope !== "root-pyproject-only" || p.path !== "pyproject.toml"
      || !["missing", "skipped", "unreadable", "invalid", "parsed"].includes(String(p.status))
      || !(p.sha256 === null || typeof p.sha256 === "string" && /^[a-f0-9]{64}$/.test(p.sha256))
      || (["parsed", "invalid"].includes(String(p.status)) && p.sha256 === null)
      || !list(p.warnings, 12) || !list(p.limitations, 12)
      || !Array.isArray(p.declarations) || p.declarations.length > 128
      || (p.status !== "parsed" && p.declarations.length !== 0)
      || !p.declarations.every(d => isRecord(d) && list(d.key, 3) && (d.key as string[]).length >= 2
        && (text(d.value) || list(d.value, 128)) && d.classification === "fact" && d.confidence === 1 && text(d.provenance))
      || new TextEncoder().encode(JSON.stringify(p)).length > 128 * 1024) {
      throw new Error("Invalid graph: project discovery metadata is malformed or exceeds limits.");
    }
  }

  const nodes = value.nodes as unknown[];
  const edges = value.edges as unknown[];
  const nodeIds = new Set<string>();
  for (const [index, node] of nodes.entries()) {
    if (!isRecord(node) || typeof node.id !== "string" || !["file", "class", "function", "method"].includes(String(node.kind)) || typeof node.path !== "string") {
      throw new Error(`Invalid graph: node ${index + 1} has invalid id, path, or kind fields.`);
    }
    if (nodeIds.has(node.id)) throw new Error(`Invalid graph: duplicate node id "${node.id}".`);
    if (node.source !== undefined && typeof node.source !== "string") {
      throw new Error(`Invalid graph: source for "${node.id}" must be a string.`);
    }
    if (node.size_bytes !== undefined && (typeof node.size_bytes !== "number" || node.size_bytes < 0)) {
      throw new Error(`Invalid graph: size_bytes for "${node.id}" must be a non-negative number.`);
    }
    if (node.source_error !== undefined && typeof node.source_error !== "string") {
      throw new Error(`Invalid graph: source_error for "${node.id}" must be a string.`);
    }
    if (node.kind !== "file" && (
      typeof node.name !== "string"
      || typeof node.qualified_name !== "string"
      || !Number.isInteger(node.start_line)
      || !Number.isInteger(node.end_line)
      || Number(node.start_line) < 1
      || Number(node.end_line) < Number(node.start_line)
    )) {
      throw new Error(`Invalid graph: symbol "${node.id}" must include its name and exact source range.`);
    }
    if (node.evidence_packet !== undefined) {
      const packet = node.evidence_packet;
      if (
        !isRecord(packet)
        || packet.node_id !== node.id
        || !isRecord(packet.source_range)
        || packet.source_range.path !== node.path
        || !isEvidenceStatement(packet.summary)
        || !isEvidenceStatement(packet.execution_role)
        || !isEvidenceStatement(packet.structural_rationale)
        || !Array.isArray(packet.related_edge_ids)
        || !Array.isArray(packet.flow_ids)
        || !Array.isArray(packet.finding_ids)
        || !Array.isArray(packet.pattern_ids)
        || !Array.isArray(packet.claims)
      ) {
        throw new Error(`Invalid graph: symbol "${node.id}" has an invalid evidence packet.`);
      }
    }
    nodeIds.add(node.id);
  }
  for (const [index, edge] of edges.entries()) {
    if (!isRecord(edge) || typeof edge.id !== "string" || typeof edge.source !== "string" || typeof edge.target !== "string" || !["imports", "contains", "calls", "extends", "may-dispatch-to", "depends-on", "reads", "writes"].includes(String(edge.kind))) {
      throw new Error(`Invalid graph: edge ${index + 1} has invalid id, endpoints, or kind.`);
    }
    if (edge.confidence !== undefined && (typeof edge.confidence !== "number" || edge.confidence < 0 || edge.confidence > 1)) {
      throw new Error(`Invalid graph: edge "${edge.id}" has invalid confidence.`);
    }
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      throw new Error(`Invalid graph: edge "${edge.id}" references a missing node.`);
    }
  }
  if (value.repository !== undefined) {
    const repository = value.repository;
    if (!isRecord(repository) || typeof repository.name !== "string" || !["github", "local"].includes(String(repository.source))) {
      throw new Error("Invalid graph: repository metadata must include name and source.");
    }
  }
  if (value.snapshot !== undefined) {
    const snapshot = value.snapshot;
    if (!isRecord(snapshot) || typeof snapshot.commit_sha !== "string" || !/^[0-9a-f]{40}$/i.test(snapshot.commit_sha)) {
      throw new Error("Invalid graph: snapshot metadata must include a full commit SHA.");
    }
  }
  if (value.analysis !== undefined) {
    const analysis = value.analysis;
    if (
      !isRecord(analysis)
      || !["inventory", "deep"].includes(String(analysis.tier))
      || typeof analysis.engine !== "string"
      || !Array.isArray(analysis.limitations)
      || !analysis.limitations.every((item) => typeof item === "string")
    ) {
      throw new Error("Invalid graph: analysis metadata must identify its tier and limitations.");
    }
  }
  if (value.coverage !== undefined) {
    if (!isRecord(value.coverage)) throw new Error("Invalid graph: coverage must be an object.");
    for (const key of ["python_files_total_found", "python_files_analyzed", "source_failures", "source_truncations", "unmatched_imports"]) {
      const count = value.coverage[key];
      if (count !== undefined && (!Number.isInteger(count) || Number(count) < 0)) throw new Error(`Invalid graph: coverage ${key} must be a non-negative integer.`);
    }
    for (const key of ["python_files_truncated", "github_tree_truncated"]) {
      if (value.coverage[key] !== undefined && typeof value.coverage[key] !== "boolean") throw new Error(`Invalid graph: coverage ${key} must be a boolean.`);
    }
  }
  if (value.flows !== undefined) {
    if (!Array.isArray(value.flows)) throw new Error("Invalid graph: flows must be an array.");
    const edgeIds = new Set((edges as GraphEdge[]).map((edge) => edge.id));
    const edgeById = new Map((edges as GraphEdge[]).map((edge) => [edge.id, edge]));
    for (const [index, flow] of value.flows.entries()) {
      if (
        !isRecord(flow)
        || typeof flow.id !== "string"
        || typeof flow.entrypoint_id !== "string"
        || !nodeIds.has(flow.entrypoint_id)
        || typeof flow.label !== "string"
        || typeof flow.framework !== "string"
        || !Array.isArray(flow.ordered_node_ids)
        || !Array.isArray(flow.ordered_edge_ids)
        || !flow.ordered_node_ids.length
        || !flow.ordered_node_ids.every((id) => typeof id === "string" && nodeIds.has(id))
        || !flow.ordered_edge_ids.every((id) => typeof id === "string" && edgeIds.has(id))
        || flow.ordered_edge_ids.length !== flow.ordered_node_ids.length - 1
        || typeof flow.confidence !== "number"
        || flow.confidence < 0
        || flow.confidence > 1
        || !["complete", "partial"].includes(String(flow.completeness))
        || !Array.isArray(flow.unresolved_steps)
        || !flow.unresolved_steps.every((step) =>
          isRecord(step)
          && typeof step.source_id === "string"
          && nodeIds.has(step.source_id)
          && typeof step.reason === "string"
          && isRecord(step.evidence),
        )
      ) {
        throw new Error(`Invalid graph: flow ${index + 1} has an invalid path or metadata.`);
      }
      const typedFlow = flow as unknown as ExecutionFlow;
      if (!typedFlow.ordered_edge_ids.every((edgeId, edgeIndex) => {
        const edge = edgeById.get(edgeId);
        return edge?.source === typedFlow.ordered_node_ids[edgeIndex]
          && edge.target === typedFlow.ordered_node_ids[edgeIndex + 1];
      })) {
        throw new Error(`Invalid graph: flow ${index + 1} contains a disconnected edge.`);
      }
    }
  }
  if (value.findings !== undefined) {
    if (!Array.isArray(value.findings)) throw new Error("Invalid graph: findings must be an array.");
    for (const [index, finding] of value.findings.entries()) {
      if (
        !isRecord(finding)
        || typeof finding.id !== "string"
        || typeof finding.rule_id !== "string"
        || typeof finding.node_id !== "string"
        || !nodeIds.has(finding.node_id)
        || !Array.isArray(finding.related_node_ids)
        || !finding.related_node_ids.every((id) => typeof id === "string" && nodeIds.has(id))
        || typeof finding.title !== "string"
        || !["low", "medium", "high"].includes(String(finding.severity))
        || !["fact", "heuristic", "interpretation"].includes(String(finding.classification))
        || typeof finding.confidence !== "number"
        || finding.confidence < 0
        || finding.confidence > 1
        || typeof finding.summary !== "string"
        || typeof finding.provenance !== "string"
        || !isRecord(finding.evidence)
        || typeof finding.evidence.path !== "string"
        || !Number.isInteger(finding.evidence.line)
        || !isRecord(finding.metrics)
      ) {
        throw new Error(`Invalid graph: finding ${index + 1} has invalid evidence or classification.`);
      }
    }
  }
  if (value.patterns !== undefined) {
    if (!Array.isArray(value.patterns)) throw new Error("Invalid graph: patterns must be an array.");
    const edgeIds = new Set((edges as GraphEdge[]).map((edge) => edge.id));
    for (const [index, pattern] of value.patterns.entries()) {
      if (
        !isRecord(pattern)
        || typeof pattern.id !== "string"
        || typeof pattern.pattern_id !== "string"
        || typeof pattern.title !== "string"
        || !["fact", "heuristic"].includes(String(pattern.classification))
        || typeof pattern.confidence !== "number"
        || pattern.confidence < 0
        || pattern.confidence > 1
        || typeof pattern.summary !== "string"
        || typeof pattern.provenance !== "string"
        || !Array.isArray(pattern.node_ids)
        || !pattern.node_ids.length
        || !pattern.node_ids.every((id) => typeof id === "string" && nodeIds.has(id))
        || !Array.isArray(pattern.edge_ids)
        || !pattern.edge_ids.every((id) => typeof id === "string" && edgeIds.has(id))
        || !Array.isArray(pattern.evidence_refs)
        || !pattern.evidence_refs.length
        || !isRecord(pattern.metrics)
      ) {
        throw new Error(`Invalid graph: architecture pattern ${index + 1} has invalid evidence or classification.`);
      }
    }
  }
  return value as Graph;
}
