"use client";

import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./graph.css";
import { useEffect, useMemo, useState } from "react";

type EvidenceStatement = {
  text: string;
  classification: "fact" | "heuristic" | "interpretation";
  confidence: number;
  provenance: string;
};
type GraphNode = {
  id: string;
  kind: "file" | "class" | "function" | "method";
  path: string;
  name?: string;
  qualified_name?: string;
  start_line?: number;
  definition_line?: number;
  end_line?: number;
  parent_id?: string;
  decorators?: string[];
  bases?: string[];
  is_async?: boolean;
  docstring?: string;
  size_bytes?: number;
  source?: string;
  source_truncated?: boolean;
  source_error?: string;
  framework?: string;
  architectural_role?: string;
  entrypoint?: {
    framework: string;
    kind: string;
    method?: string;
    route_path?: string | null;
    label: string;
  };
  entrypoint_evidence?: { path?: string; line?: number; column?: number; expression?: string };
  sqlalchemy?: {
    kind: "declarative-base" | "abstract-model" | "model";
    table_name?: string | null;
    table_expression?: string | null;
    is_abstract: boolean;
    columns: { name: string; line: number; annotation: string }[];
    relationships: { name: string; line: number; annotation: string }[];
  };
  evidence_packet?: {
    version: string;
    node_id: string;
    source_range: { path: string; start_line: number; end_line: number };
    summary: EvidenceStatement;
    execution_role: EvidenceStatement;
    structural_rationale: EvidenceStatement;
    related_edge_ids: string[];
    flow_ids: string[];
    finding_ids: string[];
    pattern_ids: string[];
    claims: Claim[];
  };
};
type GraphEdge = {
  id: string;
  source: string;
  target: string;
  kind: "imports" | "contains" | "calls" | "extends" | "may-dispatch-to" | "depends-on" | "reads" | "writes";
  confidence?: number;
  resolution_method?: string;
  evidence?: { path?: string; line?: number; column?: number; expression?: string };
};
type RepositoryMetadata = {
  name: string;
  url?: string;
  pinned_url?: string;
  source: "github" | "local";
};
type SnapshotMetadata = { commit_sha: string };
type UnresolvedStep = {
  source_id: string;
  reason: string;
  evidence: { path?: string; line?: number; column?: number; expression?: string };
};
type ExecutionFlow = {
  id: string;
  entrypoint_id: string;
  label: string;
  framework: string;
  ordered_node_ids: string[];
  ordered_edge_ids: string[];
  confidence: number;
  completeness: "complete" | "partial";
  unresolved_steps: UnresolvedStep[];
};
type RiskFinding = {
  id: string;
  rule_id: "large-symbol" | "high-fan-in" | "high-fan-out" | "import-cycle";
  node_id: string;
  related_node_ids: string[];
  title: string;
  severity: "low" | "medium" | "high";
  classification: "fact" | "heuristic" | "interpretation";
  confidence: number;
  summary: string;
  provenance: string;
  evidence: { path: string; line: number; end_line?: number; expression?: string };
  metrics: Record<string, number>;
};
type ArchitecturePattern = {
  id: string;
  pattern_id: "layered-architecture" | "fastapi-boundary" | "dependency-injection" | "data-mapper" | "repository-boundary" | "unit-of-work";
  title: string;
  classification: "fact" | "heuristic";
  confidence: number;
  summary: string;
  provenance: string;
  node_ids: string[];
  edge_ids: string[];
  evidence_refs: string[];
  metrics: Record<string, number>;
};
type Graph = {
  schema_version: string;
  repository?: RepositoryMetadata;
  snapshot?: SnapshotMetadata;
  source_url?: string;
  repo_root?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  flows?: ExecutionFlow[];
  findings?: RiskFinding[];
  patterns?: ArchitecturePattern[];
};
type Claim = {
  id?: string;
  classification: "fact" | "heuristic" | "interpretation";
  text: string;
  confidence: number;
  provenance: string;
  evidence_refs?: string[];
};
type Explanation = {
  summary: string;
  role: string;
  rationale: string;
  claims: Claim[];
  grounding?: { summary: EvidenceStatement; role: EvidenceStatement; rationale: EvidenceStatement };
};
type AIInterpretationSection = {
  text: string;
  classification: "interpretation";
  confidence: number;
  provenance: string;
  evidence_refs: string[];
};
type AIInterpretation = {
  model: string;
  classification: "interpretation";
  what_it_does: AIInterpretationSection;
  execution_role: AIInterpretationSection;
  structural_rationale: AIInterpretationSection;
  uncertainties: string[];
};

const filename = (path: string) => path.split("/").at(-1) ?? path;
const folder = (path: string) => path.split("/").slice(0, -1).join("/") || "repository root";
const MAX_SOURCE_CHARACTERS = 200_000;
const ANALYZER_API_URL = process.env.NEXT_PUBLIC_ANALYZER_API_URL ?? "http://127.0.0.1:8000";
const LOCAL_ANALYZER_ENABLED = process.env.NODE_ENV === "development" || Boolean(process.env.NEXT_PUBLIC_ANALYZER_API_URL);

const layerFor = (path: string) => {
  if (path.startsWith("tests/")) return { key: "tests", label: "Tests", order: 0 };
  if (path.includes("/entrypoints/") || path.endsWith("bootstrap.py") || path.endsWith("views.py")) return { key: "entrypoints", label: "Entry points", order: 1 };
  if (path.includes("/service_layer/")) return { key: "services", label: "Application", order: 2 };
  if (path.includes("/domain/")) return { key: "domain", label: "Domain", order: 3 };
  if (path.includes("/adapters/")) return { key: "adapters", label: "Infrastructure", order: 4 };
  return { key: "support", label: "Support", order: 5 };
};

function layeredPositions(nodes: GraphNode[]) {
  const rows = new Map<string, number>();
  return new Map(nodes.map((node) => {
    const layer = layerFor(node.path);
    const row = rows.get(layer.key) ?? 0;
    rows.set(layer.key, row + 1);
    return [node.id, { x: layer.order * 310, y: row * 118 }];
  }));
}

function formatBytes(bytes: number | undefined) {
  if (bytes === undefined) return "Unknown";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function symbolsIn(source: string | undefined) {
  if (!source) return [];
  return [...source.matchAll(/^(?:async\s+)?(?:class|def)\s+([A-Za-z_]\w*)/gm)].map((match) => match[1]).slice(0, 5);
}

function explainNode(node: GraphNode, incoming: GraphEdge[], outgoing: GraphEdge[]): Explanation {
  const layer = layerFor(node.path);
  const symbols = symbolsIn(node.source);
  const roles: Record<string, string> = {
    entrypoints: "Boundary code that starts or receives an execution flow and delegates work inward.",
    services: "Application orchestration that coordinates use cases across domain and infrastructure code.",
    domain: "Core business behavior and vocabulary, kept separate from delivery and persistence concerns.",
    adapters: "Infrastructure integration that translates between the application and external systems.",
    tests: "Verification code that exercises production behavior and documents expected outcomes.",
    support: "Supporting configuration or package setup used by multiple architectural layers.",
  };
  const rationales: Record<string, string> = {
    entrypoints: "Keeping boundary concerns here prevents HTTP, messaging, or startup details from leaking into business rules.",
    services: "A service layer provides one place to coordinate a use case without coupling domain objects to infrastructure.",
    domain: "This placement suggests the project is protecting business logic from framework and database dependencies.",
    adapters: "The adapter boundary makes external technology replaceable behind application-facing interfaces.",
    tests: "The test hierarchy mirrors the type of confidence each test provides: unit, integration, or end-to-end.",
    support: "Cross-cutting setup is separated so feature modules can remain focused on their primary responsibility.",
  };
  const summary = symbols.length
    ? `Defines ${symbols.join(", ")}${symbols.length === 5 ? ", and other symbols" : ""}.`
    : node.source_error ? "The file could not be read, so behavior could not be determined." : "Contains package setup or module-level behavior with no top-level class or function definitions.";
  return {
    summary,
    role: roles[layer.key],
    rationale: rationales[layer.key],
    claims: [
      {
        classification: "fact",
        text: `${outgoing.length} internal import${outgoing.length === 1 ? "" : "s"} out; ${incoming.length} internal importer${incoming.length === 1 ? "" : "s"} in.`,
        confidence: 1,
        provenance: "Python AST import statements and resolved repository paths",
      },
      {
        classification: "heuristic",
        text: `Likely belongs to the ${layer.label.toLowerCase()} layer.`,
        confidence: 0.9,
        provenance: `Path convention: ${node.path}`,
      },
      {
        classification: "interpretation",
        text: rationales[layer.key],
        confidence: 0.72,
        provenance: "Layered Python architecture pattern inferred from path and dependency direction",
      },
    ],
  };
}

function explainSymbol(symbol: GraphNode, incoming: GraphEdge[], outgoing: GraphEdge[]): Explanation {
  const packet = symbol.evidence_packet;
  if (packet) {
    return {
      summary: packet.summary.text,
      role: packet.execution_role.text,
      rationale: packet.structural_rationale.text,
      claims: packet.claims,
      grounding: {
        summary: packet.summary,
        role: packet.execution_role,
        rationale: packet.structural_rationale,
      },
    };
  }
  const range = `lines ${symbol.start_line}–${symbol.end_line}`;
  const asyncPrefix = symbol.is_async ? "async " : "";
  const decorators = symbol.decorators?.length ? ` It is decorated by ${symbol.decorators.join(", ")}.` : "";
  const roles = {
    class: "Groups related state and behavior behind a named Python type.",
    method: "Implements behavior owned by its containing class.",
    function: "Implements a callable unit of module or nested behavior.",
    file: "Contains Python source.",
  };
  const model = symbol.sqlalchemy?.kind === "model" ? symbol.sqlalchemy : null;
  const role = model
    ? `Maps application state to${model.table_name ? ` the ${model.table_name} table` : " a SQLAlchemy persistence model"}.`
    : roles[symbol.kind];
  const rationale = model
    ? "The model keeps database mapping concerns explicit while repository and service code can refer to a stable application type."
    : "Its nesting and decorators are reported directly from the Python syntax tree; architectural intent requires additional evidence.";
  const modelClaims: Claim[] = model ? [{
    classification: "fact",
    text: `${model.columns.length} mapped column${model.columns.length === 1 ? "" : "s"} and ${model.relationships.length} ORM relationship${model.relationships.length === 1 ? "" : "s"} were declared on this class.`,
    confidence: 1,
    provenance: `SQLAlchemy Mapped annotations in ${symbol.path}`,
  }] : [];
  return {
    summary: `Defines the ${asyncPrefix}${symbol.kind} ${symbol.qualified_name} at ${range}.${decorators}`,
    role,
    rationale,
    claims: [
      {
        classification: "fact",
        text: `${symbol.qualified_name} occupies ${range}.`,
        confidence: 1,
        provenance: `Python AST source range in ${symbol.path}`,
      },
      {
        classification: "fact",
        text: `${outgoing.length} outgoing and ${incoming.length} incoming symbol relationship${incoming.length + outgoing.length === 1 ? "" : "s"} were resolved.`,
        confidence: 1,
        provenance: "Python AST call, inheritance, dependency, and persistence resolution",
      },
      ...modelClaims,
      {
        classification: "interpretation",
        text: role,
        confidence: 0.7,
        provenance: `Python symbol kind: ${symbol.kind}`,
      },
    ],
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
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

function isAIInterpretationSection(value: unknown): value is AIInterpretationSection {
  return isRecord(value)
    && typeof value.text === "string"
    && value.classification === "interpretation"
    && typeof value.confidence === "number"
    && value.confidence >= 0
    && value.confidence <= 0.85
    && typeof value.provenance === "string"
    && Array.isArray(value.evidence_refs)
    && value.evidence_refs.length > 0
    && value.evidence_refs.every((reference) => typeof reference === "string");
}

function validateAIInterpretation(value: unknown): AIInterpretation {
  if (
    !isRecord(value)
    || typeof value.model !== "string"
    || value.classification !== "interpretation"
    || !isAIInterpretationSection(value.what_it_does)
    || !isAIInterpretationSection(value.execution_role)
    || !isAIInterpretationSection(value.structural_rationale)
    || !Array.isArray(value.uncertainties)
    || !value.uncertainties.every((uncertainty) => typeof uncertainty === "string")
  ) {
    throw new Error("The analyzer returned an invalid AI interpretation.");
  }
  return value as AIInterpretation;
}

function validateGraph(value: unknown): Graph {
  if (!isRecord(value) || typeof value.schema_version !== "string") {
    throw new Error("Invalid graph: schema_version must be a string.");
  }
  if (!Array.isArray(value.nodes) || !Array.isArray(value.edges)) {
    throw new Error("Invalid graph: nodes and edges must be arrays.");
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

function githubName(url: string) {
  const match = url.match(/^https:\/\/github\.com\/([^/]+)\/([^/#?]+?)(?:\.git)?\/?$/i);
  return match ? `${match[1]}/${match[2]}` : "GitHub repository";
}

function primaryNodeId(graph: Graph) {
  const degree = new Map<string, number>();
  graph.edges.forEach((edge) => {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  });
  return graph.nodes
    .filter((node) => node.kind === "file" && !node.path.startsWith("tests/"))
    .sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0))[0]?.id
    ?? graph.nodes[0]?.id
    ?? null;
}

export default function Home() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedSymbolId, setSelectedSymbolId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<"production" | "all">("production");
  const [mapMode, setMapMode] = useState<"architecture" | "patterns" | "flows" | "risks">("architecture");
  const [selectedFlowId, setSelectedFlowId] = useState<string | null>(null);
  const [selectedRiskId, setSelectedRiskId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submittedUrl, setSubmittedUrl] = useState("https://github.com/cosmicpython/code");
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [aiInterpretation, setAIInterpretation] = useState<AIInterpretation | null>(null);
  const [isInterpreting, setIsInterpreting] = useState(false);
  const [interpretationError, setInterpretationError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/graph.json")
      .then((response) => {
        if (!response.ok) throw new Error(`Graph request failed (${response.status})`);
        return response.json();
      })
      .then((data: unknown) => {
        const validated = validateGraph(data);
        setGraph(validated);
        setSelectedId(primaryNodeId(validated));
        setSelectedSymbolId(null);
        setSelectedFlowId(validated.flows?.[0]?.id ?? null);
        setSelectedRiskId(validated.findings?.[0]?.id ?? null);
      })
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load graph data"));
  }, []);

  useEffect(() => {
    setAIInterpretation(null);
    setInterpretationError(null);
    setIsInterpreting(false);
  }, [graph, selectedSymbolId]);

  async function analyzeSubmittedRepository(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsAnalyzing(true);
    setAnalysisError(null);
    try {
      const response = await fetch(`${ANALYZER_API_URL}/api/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repositoryUrl: submittedUrl }),
      });
      const data: unknown = await response.json();
      if (!response.ok) {
        const detail = isRecord(data) && typeof data.detail === "string" ? data.detail : `Analysis failed (${response.status})`;
        throw new Error(detail);
      }
      const validated = validateGraph(data);
      setGraph(validated);
      setSelectedId(primaryNodeId(validated));
      setSelectedSymbolId(null);
      setSelectedFlowId(validated.flows?.[0]?.id ?? null);
      setSelectedRiskId(validated.findings?.[0]?.id ?? null);
      setMapMode("architecture");
      setQuery("");
      setScope("production");
    } catch (cause: unknown) {
      setAnalysisError(cause instanceof Error ? cause.message : "Repository analysis failed");
    } finally {
      setIsAnalyzing(false);
    }
  }

  const visibleGraphNodes = useMemo(
    () => graph?.nodes.filter((node) =>
      node.kind === "file"
      && (scope === "all" || !node.path.startsWith("tests/"))
      && node.path.toLowerCase().includes(query.toLowerCase()),
    ) ?? [],
    [graph, query, scope],
  );
  const visibleIds = useMemo(() => new Set(visibleGraphNodes.map((node) => node.id)), [visibleGraphNodes]);
  const positions = useMemo(() => layeredPositions(visibleGraphNodes), [visibleGraphNodes]);

  useEffect(() => {
    if (selectedId && !visibleIds.has(selectedId)) setSelectedId(visibleGraphNodes[0]?.id ?? null);
  }, [selectedId, visibleGraphNodes, visibleIds]);
  const flowNodes = useMemo<Node[]>(
    () => visibleGraphNodes.map((node, index) => ({
      id: node.id,
      position: positions.get(node.id) ?? { x: 0, y: index * 118 },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      data: { label: <div className="node-label"><strong>{filename(node.path)}</strong><small>{folder(node.path)}</small></div>, path: node.path },
      className: selectedId === node.id ? "flow-node selected" : "flow-node",
      ariaLabel: `Open ${node.path}`,
    })),
    [positions, selectedId, visibleGraphNodes],
  );
  const flowEdges = useMemo<Edge[]>(
    () => (graph?.edges ?? []).filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)).map((edge) => ({
      ...edge,
      type: "smoothstep",
      animated: false,
      markerEnd: { type: MarkerType.ArrowClosed },
      className: selectedId && (edge.source === selectedId || edge.target === selectedId) ? "relationship active" : "relationship",
      style: selectedId && (edge.source === selectedId || edge.target === selectedId)
        ? { stroke: "#315b3d", strokeWidth: 2.4, opacity: 1 }
        : { stroke: "#8e9b91", strokeWidth: 1.2, opacity: selectedId ? 0.16 : 0.48 },
    })),
    [graph, selectedId, visibleIds],
  );
  const selected = graph?.nodes.find((node) => node.id === selectedId) ?? null;
  const incoming = graph?.edges.filter((edge) => edge.kind === "imports" && edge.target === selectedId) ?? [];
  const outgoing = graph?.edges.filter((edge) => edge.kind === "imports" && edge.source === selectedId) ?? [];
  const selectedSymbol = graph?.nodes.find((node) => node.id === selectedSymbolId && node.kind !== "file") ?? null;
  const symbolIncoming = graph?.edges.filter((edge) => ["calls", "extends", "may-dispatch-to", "depends-on", "reads", "writes"].includes(edge.kind) && edge.target === selectedSymbolId) ?? [];
  const symbolOutgoing = graph?.edges.filter((edge) => ["calls", "extends", "may-dispatch-to", "depends-on", "reads", "writes"].includes(edge.kind) && edge.source === selectedSymbolId) ?? [];
  const symbolsForSelected = graph?.nodes
    .filter((node) => node.kind !== "file" && node.path === selected?.path)
    .sort((a, b) => Number(a.start_line) - Number(b.start_line)) ?? [];
  const explanation = selectedSymbol ? explainSymbol(selectedSymbol, symbolIncoming, symbolOutgoing) : selected ? explainNode(selected, incoming, outgoing) : null;
  const nodePath = (id: string) => graph?.nodes.find((node) => node.id === id)?.path ?? id;
  const nodeName = (id: string) => graph?.nodes.find((node) => node.id === id)?.qualified_name ?? nodePath(id);
  const selectFileNode = (id: string) => { setSelectedId(id); setSelectedSymbolId(null); };
  const selectSymbolNode = (id: string) => {
    const symbol = graph?.nodes.find((node) => node.id === id && node.kind !== "file");
    if (!symbol) return;
    setSelectedId(`file:${symbol.path}`);
    setSelectedSymbolId(id);
  };
  const relationshipLabel = (edge: GraphEdge) => `${edge.kind.replaceAll("-", " ")} · ${Math.round((edge.confidence ?? 1) * 100)}%`;
  const flows = graph?.flows ?? [];
  const selectedFlow = flows.find((flow) => flow.id === selectedFlowId) ?? flows[0] ?? null;
  const flowEdge = (index: number) => graph?.edges.find((edge) => edge.id === selectedFlow?.ordered_edge_ids[index]);
  const allFindings = graph?.findings ?? [];
  const patterns = graph?.patterns ?? [];
  const findings = allFindings.filter((finding) =>
    (scope === "all" || !finding.evidence.path.startsWith("tests/"))
    && finding.evidence.path.toLowerCase().includes(query.toLowerCase()),
  );
  const selectedRisk = findings.find((finding) => finding.id === selectedRiskId) ?? findings[0] ?? null;
  const selectRisk = (finding: RiskFinding) => {
    setSelectedRiskId(finding.id);
    const node = graph?.nodes.find((candidate) => candidate.id === finding.node_id);
    if (!node) return;
    if (node.kind === "file") selectFileNode(node.id);
    else selectSymbolNode(node.id);
  };
  const selectedNodeFindings = findings.filter((finding) => {
    const selectedNodeId = selectedSymbolId ?? selectedId;
    return selectedNodeId !== null
      && (finding.node_id === selectedNodeId || finding.related_node_ids.includes(selectedNodeId));
  });
  const displayedSource = selected?.source?.slice(0, MAX_SOURCE_CHARACTERS);
  const fileSourceLines = displayedSource?.split("\n") ?? [];
  const sourceStartLine = selectedSymbol?.start_line ?? 1;
  const sourceEndLine = selectedSymbol?.end_line ?? fileSourceLines.length;
  const sourceLines = selectedSymbol
    ? fileSourceLines.slice(sourceStartLine - 1, sourceEndLine)
    : fileSourceLines;

  async function interpretSelectedSymbol() {
    if (!selectedSymbol?.evidence_packet) return;
    setIsInterpreting(true);
    setInterpretationError(null);
    try {
      const response = await fetch(`${ANALYZER_API_URL}/api/interpret`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          evidencePacket: selectedSymbol.evidence_packet,
          sourceExcerpt: sourceLines.join("\n").slice(0, 12_000),
        }),
      });
      const data: unknown = await response.json();
      if (!response.ok) {
        const detail = isRecord(data) && typeof data.detail === "string"
          ? data.detail
          : `AI interpretation failed (${response.status})`;
        throw new Error(detail);
      }
      setAIInterpretation(validateAIInterpretation(data));
    } catch (cause: unknown) {
      setAIInterpretation(null);
      setInterpretationError(cause instanceof Error ? cause.message : "AI interpretation failed");
    } finally {
      setIsInterpreting(false);
    }
  }
  const selectedRangeAvailable = !selectedSymbol || sourceStartLine <= fileSourceLines.length;
  const sourceIsTruncated = Boolean(
    selected?.source_truncated || (selected?.source && selected.source.length > MAX_SOURCE_CHARACTERS),
  );
  const repositoryName = graph?.repository?.name
    ?? (graph?.source_url ? githubName(graph.source_url) : graph?.repo_root?.split(/[\\/]/).filter(Boolean).at(-1))
    ?? "Analyzed repository";
  const repositorySource = graph?.repository?.source ?? (graph?.source_url ? "github" : "local");
  const repositoryUrl = graph?.repository?.url ?? graph?.source_url;
  const pinnedRepositoryUrl = graph?.repository?.pinned_url ?? repositoryUrl;
  const commitSha = graph?.snapshot?.commit_sha;
  const selectedSourceUrl = repositorySource === "github" && repositoryUrl && commitSha && selected
    ? `${repositoryUrl.replace(/\.git\/?$/, "")}/blob/${commitSha}/${selected.path}${selectedSymbol ? `#L${selectedSymbol.start_line}-L${selectedSymbol.end_line}` : ""}`
    : undefined;
  const fileCount = graph?.nodes.filter((node) => node.kind === "file").length ?? 0;
  const symbolCount = graph?.nodes.filter((node) => node.kind !== "file").length ?? 0;
  const relationshipCount = graph?.edges.filter((edge) => ["calls", "extends", "may-dispatch-to", "depends-on", "reads", "writes"].includes(edge.kind)).length ?? 0;
  const riskCount = findings.length;
  const highRiskCount = findings.filter((finding) => finding.severity === "high").length;
  const factPatternCount = patterns.filter((pattern) => pattern.classification === "fact").length;
  const canvasCopy = {
    architecture: ["Architecture map", "Python imports", "A → B means file A imports file B."],
    patterns: ["Pattern detection", "Architectural patterns", "Every detected pattern reports its classification, confidence, provenance, metrics, and exact graph evidence."],
    flows: ["Flow discovery", "Representative execution paths", "Paths begin at proven framework entrypoints and preserve uncertain or unresolved steps."],
    risks: ["Risk analysis", "Evidence-backed findings", "Deterministic heuristics identify structural hotspots; each finding links to exact source evidence."],
  }[mapMode];

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="mark" aria-hidden="true">A</div>
          <div><div className="product-name">Archaeologist</div><div className="repo-name">{repositoryName}</div></div>
        </div>
        <div className="snapshot"><span className={`status-dot${isAnalyzing ? " busy" : ""}`} />{isAnalyzing ? "Analyzing repository" : graph ? "Graph ready" : error ? "Graph unavailable" : "Loading graph"}<code>{graph ? `${fileCount} files · ${symbolCount} symbols` : "Please wait"}</code></div>
      </header>

      <section className="workspace">
        <aside className="rail">
          <div className="eyebrow">Repository</div><h1>Dependency map</h1>
          <p className="rail-copy">Explore files, source, and internal imports from the analyzed repository.</p>
          {LOCAL_ANALYZER_ENABLED && <form className="repository-form" onSubmit={analyzeSubmittedRepository}>
            <label htmlFor="repository-url">Public GitHub URL</label>
            <input id="repository-url" type="url" value={submittedUrl} onChange={(event) => setSubmittedUrl(event.target.value)} required pattern="https://github\.com/.+/.+" disabled={isAnalyzing} />
            <button type="submit" disabled={isAnalyzing}>{isAnalyzing ? "Analyzing…" : "Analyze repository"}</button>
            <small>Local analyzer · public Python repositories only</small>
            {analysisError && <p className="analysis-error" role="alert">{analysisError}</p>}
          </form>}
          {graph && <div className="origin"><span>{repositorySource === "github" ? "Pinned GitHub snapshot" : "Local directory"}</span>{pinnedRepositoryUrl ? <a href={pinnedRepositoryUrl} target="_blank" rel="noreferrer">{commitSha ? `${commitSha.slice(0, 12)} ↗` : "Open repository ↗"}</a> : <strong>{repositoryName}</strong>}</div>}
          <label className="search"><span aria-hidden="true">⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter files" aria-label="Filter files" /></label>
          <div className="scope-switch" aria-label="Graph scope">
            <button className={scope === "production" ? "active" : ""} onClick={() => setScope("production")}>Production</button>
            <button className={scope === "all" ? "active" : ""} onClick={() => setScope("all")}>All files</button>
          </div>
          <nav aria-label="Graph summary">
            <button className={`nav-item${mapMode === "architecture" ? " active" : ""}`} onClick={() => setMapMode("architecture")}><span>Architecture map</span><strong>{fileCount}</strong></button>
            <button className={`nav-item${mapMode === "patterns" ? " active" : patterns.length ? "" : " muted"}`} onClick={() => setMapMode("patterns")}><span>Patterns</span><strong>{patterns.length}</strong></button>
            <button className={`nav-item${mapMode === "flows" ? " active" : ""}`} onClick={() => setMapMode("flows")}><span>Execution flows</span><strong>{flows.length}</strong></button>
            <button className={`nav-item${mapMode === "risks" ? " active warning" : riskCount ? " warning" : " muted"}`} onClick={() => setMapMode("risks")}><span>Risk findings</span><strong>{riskCount}</strong></button>
            <div className="nav-item"><span>Symbols</span><strong>{symbolCount}</strong></div>
            <div className="nav-item"><span>Dependencies</span><strong>{graph?.edges.filter((edge) => edge.kind === "imports").length ?? 0}</strong></div>
            <div className="nav-item"><span>Symbol relationships</span><strong>{relationshipCount}</strong></div>
          </nav>
          <div className="contract"><span>Graph contract</span><code>schema v{graph?.schema_version ?? "0.1"}</code></div>
        </aside>

        <section className="canvas" aria-label="Repository dependency graph">
          <div className="canvas-head"><div><div className="eyebrow">{canvasCopy[0]}</div><h2>{canvasCopy[1]}</h2><p className="relationship-help">{canvasCopy[2]}</p></div>{mapMode === "architecture" ? <div className="legend"><span /> Selected <i /> Imports →</div> : mapMode === "flows" ? <div className="legend flow-legend"><span /> Proven <i /> Candidate</div> : mapMode === "patterns" ? <div className="pattern-summary"><strong>{factPatternCount}</strong> facts · <strong>{patterns.length - factPatternCount}</strong> heuristics</div> : <div className="risk-summary"><strong>{highRiskCount}</strong> high · <strong>{riskCount - highRiskCount}</strong> medium/low</div>}</div>
          {mapMode === "architecture" && <div className="layer-guide" aria-hidden="true">
            {(scope === "all" ? ["Tests"] : []).concat(["Entry points", "Application", "Domain", "Infrastructure", "Support"]).map((layer) => <span key={layer}>{layer}</span>)}
          </div>}
          <div className={`graph-surface${mapMode !== "architecture" ? " flow-surface" : ""}`}>
            {error ? <div className="state-card error-state"><strong>Graph could not be loaded</strong><p>{error}</p></div> : !graph ? (
              <div className="state-card loading-state"><span aria-hidden="true" /><strong>Loading repository graph</strong><p>Validating nodes and dependencies…</p></div>
            ) : graph.nodes.length === 0 ? (
              <div className="state-card"><strong>No Python files found</strong><p>This repository does not contain any analyzable .py files.</p></div>
            ) : mapMode === "patterns" ? patterns.length ? (
              <div className="pattern-browser">
                <div className="pattern-list-heading"><div><span>Detected structure</span><h3>{patterns.length} architectural pattern{patterns.length === 1 ? "" : "s"}</h3></div><p>Facts come from proven syntax and framework relationships. Heuristics combine naming, placement, and dependency direction.</p></div>
                <div className="pattern-list" aria-label="Architecture patterns">
                  {patterns.map((pattern) => <article className={`pattern-card ${pattern.classification}`} key={pattern.id}>
                    <div className="pattern-card-top"><span>{pattern.classification}</span><strong>{Math.round(pattern.confidence * 100)}% confidence</strong></div>
                    <h3>{pattern.title}</h3><p>{pattern.summary}</p>
                    <dl>{Object.entries(pattern.metrics).map(([metric, value]) => <div key={metric}><dt>{metric.replaceAll("_", " ")}</dt><dd>{value}</dd></div>)}</dl>
                    <small>{pattern.evidence_refs.length} evidence reference{pattern.evidence_refs.length === 1 ? "" : "s"} · {pattern.provenance}</small>
                    <button type="button" onClick={() => { const nodeId = pattern.node_ids[0]; const node = graph.nodes.find((candidate) => candidate.id === nodeId); if (node?.kind === "file") selectFileNode(nodeId); else selectSymbolNode(nodeId); }}>Open representative evidence</button>
                  </article>)}
                </div>
              </div>
            ) : (
              <div className="state-card"><strong>No supported patterns detected</strong><p>The analyzer did not find enough static evidence for a currently supported architecture pattern.</p></div>
            ) : mapMode === "risks" ? findings.length ? (
              <div className="risk-browser">
                <div className="risk-list-heading"><div><span>Ranked findings</span><h3>{findings.length} structural hotspot{findings.length === 1 ? "" : "s"}</h3></div><p>Severity ranks urgency; confidence reports how strongly the static evidence supports the heuristic.</p></div>
                <div className="risk-list" aria-label="Risk findings">
                  {findings.map((finding) => <button className={finding.id === selectedRisk?.id ? `active ${finding.severity}` : finding.severity} key={finding.id} onClick={() => selectRisk(finding)}>
                    <div><span className={`risk-severity ${finding.severity}`}>{finding.severity}</span><span className="risk-classification">{finding.classification} · {Math.round(finding.confidence * 100)}%</span></div>
                    <strong>{finding.title}</strong>
                    <p>{finding.summary}</p>
                    <small>{finding.evidence.path}:{finding.evidence.line}{finding.evidence.end_line ? `–${finding.evidence.end_line}` : ""} · {finding.provenance}</small>
                  </button>)}
                </div>
              </div>
            ) : (
              <div className="state-card"><strong>No structural risks detected</strong><p>No analyzed symbol crossed the bounded size or relationship thresholds, and no circular import component was found.</p></div>
            ) : mapMode === "flows" ? flows.length && selectedFlow ? (
              <div className="flow-browser">
                <div className="flow-catalog" aria-label="Representative execution flows">
                  <div className="flow-catalog-heading"><strong>Entrypoint paths</strong><span>{flows.length}</span></div>
                  {flows.map((flow) => <button className={flow.id === selectedFlow.id ? "active" : ""} key={flow.id} onClick={() => { setSelectedFlowId(flow.id); selectSymbolNode(flow.entrypoint_id); }}>
                    <strong>{flow.label}</strong>
                    <small>{nodePath(flow.entrypoint_id)} · {flow.ordered_node_ids.length} steps</small>
                    <span className={`flow-status ${flow.completeness}`}>{flow.completeness}</span>
                  </button>)}
                </div>
                <article className="flow-inspector">
                  <header><div><span>{selectedFlow.framework} entrypoint</span><h3>{selectedFlow.label}</h3></div><strong>{Math.round(selectedFlow.confidence * 100)}% confidence</strong></header>
                  <ol className="flow-path">
                    {selectedFlow.ordered_node_ids.map((nodeId, index) => {
                      const edge = index ? flowEdge(index - 1) : undefined;
                      return <li key={`${selectedFlow.id}:${nodeId}:${index}`}>
                        {edge && <span className={`flow-edge-kind ${edge.kind === "may-dispatch-to" ? "candidate" : ["reads", "writes"].includes(edge.kind) ? "persistence" : ""}`}>{edge.kind.replaceAll("-", " ")} · {Math.round((edge.confidence ?? 1) * 100)}%</span>}
                        <button onClick={() => selectSymbolNode(nodeId)}>
                          <strong>{nodeName(nodeId)}</strong><small>{nodePath(nodeId)}{edge?.evidence?.line ? ` · evidence line ${edge.evidence.line}` : ""}</small>
                        </button>
                      </li>;
                    })}
                  </ol>
                  <section className="flow-gaps">
                    <div className="section-heading"><h3>Unresolved steps</h3><span>{selectedFlow.unresolved_steps.length}</span></div>
                    {selectedFlow.unresolved_steps.length ? <><ul>{selectedFlow.unresolved_steps.slice(0, 12).map((step, index) => <li key={`${step.source_id}:${step.evidence.line}:${index}`}><strong>{step.evidence.expression ?? "Dynamic expression"}</strong><span>{step.reason.replaceAll("-", " ")}{step.evidence.line ? ` · ${step.evidence.path}:${step.evidence.line}` : ""}</span></li>)}</ul>{selectedFlow.unresolved_steps.length > 12 && <p className="flow-more-gaps">Showing 12 of {selectedFlow.unresolved_steps.length} unresolved calls on this path.</p>}</> : <p>No unresolved calls occur on this path.</p>}
                  </section>
                </article>
              </div>
            ) : (
              <div className="state-card"><strong>No execution flows recognized</strong><p>The analyzer has not found a supported framework entrypoint. This slice currently recognizes routes registered on FastAPI and APIRouter instances.</p></div>
            ) : (
              <ReactFlow
                key={`${scope}:${flowNodes.length}:${query}`}
                nodes={flowNodes}
                edges={flowEdges}
                onNodeClick={(_, node) => { setSelectedId(node.id); setSelectedSymbolId(null); }}
                fitView
                fitViewOptions={{ padding: 0.24 }}
                minZoom={0.35}
                maxZoom={1.8}
                nodesDraggable
                nodesConnectable={false}
                selectionOnDrag={false}
              >
                <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#cbd3c9" />
                <Controls showInteractive={false} />
              </ReactFlow>
            )}
            {mapMode === "architecture" && !error && graph && graph.nodes.length > 0 && !visibleGraphNodes.length && <div className="empty overlay">No files match “{query}”.</div>}
          </div>
        </section>

        <aside className="detail" aria-live="polite">
          <div className="detail-top"><span className="pill">{selectedSymbol ? selectedSymbol.kind : "FILE"}</span><span className="connection-count">{selectedSymbol ? symbolIncoming.length + symbolOutgoing.length : incoming.length + outgoing.length} connections</span></div>
          {selected ? <>
            <div className="detail-icon">PY</div><h2>{filename(selected.path)}</h2><p className="full-path">{selected.path}</p>
            <div className="divider" />
            <dl><div><dt>Kind</dt><dd>Python file</dd></div><div><dt>Size</dt><dd>{formatBytes(selected.size_bytes)}</dd></div><div><dt>Node ID</dt><dd><code>{selected.id}</code></dd></div><div><dt>Folder</dt><dd>{folder(selected.path)}</dd></div></dl>
            <section className="symbols-section">
              <div className="section-heading"><h3>Symbols</h3><span>{symbolsForSelected.length}</span></div>
              {symbolsForSelected.length ? <div className="symbol-list">
                <button className={!selectedSymbol ? "active" : ""} onClick={() => setSelectedSymbolId(null)}><strong>Whole file</strong><small>All source lines</small></button>
                {symbolsForSelected.map((symbol) => <button className={selectedSymbolId === symbol.id ? "active" : ""} key={symbol.id} onClick={() => setSelectedSymbolId(symbol.id)}>
                  <strong>{symbol.name}</strong><small>{symbol.kind} · L{symbol.start_line}–{symbol.end_line}</small>
                </button>)}
              </div> : <p className="no-symbols">No class or function definitions found.</p>}
            </section>
            {selectedNodeFindings.length > 0 && <section className="node-risks">
              <div className="section-heading"><h3>Risk evidence</h3><span>{selectedNodeFindings.length}</span></div>
              {selectedNodeFindings.map((finding) => <article className={`node-risk ${finding.severity}`} key={finding.id}>
                <div><span>{finding.severity}</span><strong>{finding.title}</strong></div>
                <p>{finding.summary}</p>
                <small>{finding.classification} · {Math.round(finding.confidence * 100)}% confidence · {finding.provenance}</small>
              </article>)}
            </section>}
            {explanation && <section className="explanation-section">
              <div className="section-heading"><h3>Understanding</h3><span className="analysis-label">Static analysis</span></div>
              <div className="explanation-block"><h4>What it does</h4><p>{explanation.summary}</p>{explanation.grounding && <small className={`grounding-label ${explanation.grounding.summary.classification}`}>{explanation.grounding.summary.classification} · {Math.round(explanation.grounding.summary.confidence * 100)}% · {explanation.grounding.summary.provenance}</small>}</div>
              <div className="explanation-block"><h4>Execution role</h4><p>{explanation.role}</p>{explanation.grounding && <small className={`grounding-label ${explanation.grounding.role.classification}`}>{explanation.grounding.role.classification} · {Math.round(explanation.grounding.role.confidence * 100)}% · {explanation.grounding.role.provenance}</small>}</div>
              <div className="explanation-block"><h4>Why it is structured here</h4><p>{explanation.rationale}</p>{explanation.grounding && <small className={`grounding-label ${explanation.grounding.rationale.classification}`}>{explanation.grounding.rationale.classification} · {Math.round(explanation.grounding.rationale.confidence * 100)}% · {explanation.grounding.rationale.provenance}</small>}</div>
              <div className="claims"><h4>Claims and evidence</h4>{explanation.claims.map((claim, index) => <article className={`claim ${claim.classification}`} key={`${claim.classification}:${index}`}>
                <div><span>{claim.classification}</span><strong>{Math.round(claim.confidence * 100)}% confidence</strong></div>
                <p>{claim.text}</p><small>Provenance: {claim.provenance}{claim.evidence_refs?.length ? ` · ${claim.evidence_refs.length} evidence reference${claim.evidence_refs.length === 1 ? "" : "s"}` : ""}</small>
              </article>)}</div>
            </section>}
            {selectedSymbol?.evidence_packet && LOCAL_ANALYZER_ENABLED && <section className="ai-interpretation-section">
              <div className="section-heading"><h3>AI interpretation</h3><span className="interpretation-label">Optional</span></div>
              <p className="ai-intro">Generate a deeper explanation from this symbol’s evidence packet and visible source. Static facts above remain unchanged.</p>
              {!aiInterpretation && <button className="interpret-button" type="button" onClick={interpretSelectedSymbol} disabled={isInterpreting}>
                {isInterpreting ? "Interpreting evidence…" : "Generate grounded interpretation"}
              </button>}
              {interpretationError && <div className="interpretation-error" role="alert"><strong>AI interpretation unavailable</strong><p>{interpretationError}</p></div>}
              {aiInterpretation && <div className="ai-result">
                <div className="ai-result-meta"><span>Interpretation</span><strong>{aiInterpretation.model}</strong></div>
                {([
                  ["What it does", aiInterpretation.what_it_does],
                  ["Execution role", aiInterpretation.execution_role],
                  ["Why it may be structured this way", aiInterpretation.structural_rationale],
                ] as [string, AIInterpretationSection][]).map(([label, section]) => <article key={label}>
                  <h4>{label}</h4><p>{section.text}</p>
                  <small>{Math.round(section.confidence * 100)}% confidence · {section.evidence_refs.join(", ")}</small>
                </article>)}
                {aiInterpretation.uncertainties.length > 0 && <div className="ai-uncertainties"><h4>Uncertainties</h4><ul>{aiInterpretation.uncertainties.map((uncertainty) => <li key={uncertainty}>{uncertainty}</li>)}</ul></div>}
                <button className="regenerate-button" type="button" onClick={interpretSelectedSymbol} disabled={isInterpreting}>{isInterpreting ? "Interpreting…" : "Regenerate"}</button>
              </div>}
            </section>}
            {selectedSymbol?.sqlalchemy && <section className="model-metadata">
              <div className="section-heading"><h3>SQLAlchemy mapping</h3><span>{selectedSymbol.sqlalchemy.kind.replaceAll("-", " ")}</span></div>
              {(selectedSymbol.sqlalchemy.table_name || selectedSymbol.sqlalchemy.table_expression) && <p className="model-table"><strong>Table</strong><code>{selectedSymbol.sqlalchemy.table_name ?? selectedSymbol.sqlalchemy.table_expression}</code></p>}
              <div className="model-members"><div><strong>Mapped columns</strong>{selectedSymbol.sqlalchemy.columns.length ? <ul>{selectedSymbol.sqlalchemy.columns.map((column) => <li key={`${column.name}:${column.line}`}><code>{column.name}</code><span>{column.annotation} · line {column.line}</span></li>)}</ul> : <p>None declared directly</p>}</div>
              <div><strong>Relationships</strong>{selectedSymbol.sqlalchemy.relationships.length ? <ul>{selectedSymbol.sqlalchemy.relationships.map((relationship) => <li key={`${relationship.name}:${relationship.line}`}><code>{relationship.name}</code><span>{relationship.annotation} · line {relationship.line}</span></li>)}</ul> : <p>None declared directly</p>}</div></div>
            </section>}
            {selectedSymbol && <section className="connections symbol-relationships"><h3>Symbol relationships</h3>
              <div className="connection-group"><span>Outgoing</span>{symbolOutgoing.length ? symbolOutgoing.map((edge) => <button key={edge.id} onClick={() => selectSymbolNode(edge.target)}><strong>→ {nodeName(edge.target)}</strong><small>{relationshipLabel(edge)}{edge.evidence?.line ? ` · line ${edge.evidence.line}` : ""}</small></button>) : <p>None resolved</p>}</div>
              <div className="connection-group"><span>Incoming</span>{symbolIncoming.length ? symbolIncoming.map((edge) => <button key={edge.id} onClick={() => selectSymbolNode(edge.source)}><strong>← {nodeName(edge.source)}</strong><small>{relationshipLabel(edge)}{edge.evidence?.line ? ` · line ${edge.evidence.line}` : ""}</small></button>) : <p>None resolved</p>}</div>
            </section>}
            <section className="source-section">
              <div className="section-heading"><h3>{selectedSymbol ? selectedSymbol.qualified_name : "Source"}</h3>{selectedSymbol ? <span>L{selectedSymbol.start_line}–{selectedSymbol.end_line}</span> : sourceIsTruncated && <span>Truncated</span>}</div>
              {selectedSourceUrl && <a className="source-link" href={selectedSourceUrl} target="_blank" rel="noreferrer">Open {selectedSymbol ? "this symbol" : "this file"} at the analyzed commit ↗</a>}
              {displayedSource !== undefined && selectedRangeAvailable ? (
                <pre className="code-viewer" aria-label={`Source code for ${selected.path}`} tabIndex={0}>
                  <code>{sourceLines.map((line, index) => (
                    <span className="code-line" key={index}>
                      <span className="line-number" aria-hidden="true">{sourceStartLine + index}</span>
                      <span className="line-content">{line || " "}</span>
                    </span>
                  ))}</code>
                </pre>
              ) : displayedSource !== undefined ? (
                <div className="source-unavailable source-warning" role="alert"><strong>Source range unavailable</strong><p>This symbol begins after the analyzer's 200 KB source capture limit.</p></div>
              ) : selected.source_error ? (
                <div className="source-unavailable source-warning" role="alert"><strong>Analyzer warning</strong><p>{selected.source_error}</p></div>
              ) : (
                <div className="source-unavailable"><strong>Source unavailable</strong><p>The analyzer did not include this file’s contents.</p></div>
              )}
              {sourceIsTruncated && <p className="truncation-note">Only the first 200 KB are shown.</p>}
            </section>
            <section className="connections"><h3>Imports</h3>
              <div className="connection-group"><span>Outgoing</span>{outgoing.length ? outgoing.map((edge) => <button key={edge.id} onClick={() => selectFileNode(edge.target)}>→ {nodePath(edge.target)}</button>) : <p>None in current graph</p>}</div>
              <div className="connection-group"><span>Incoming</span>{incoming.length ? incoming.map((edge) => <button key={edge.id} onClick={() => selectFileNode(edge.source)}>← {nodePath(edge.source)}</button>) : <p>None in current graph</p>}</div>
            </section>
          </> : <p>Select a node to inspect it.</p>}
        </aside>
      </section>
    </main>
  );
}






