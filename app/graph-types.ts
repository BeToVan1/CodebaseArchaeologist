import type { InventoryCoverage } from "./analysis-status";

export type EvidenceStatement = {
  text: string;
  classification: "fact" | "heuristic" | "interpretation";
  confidence: number;
  provenance: string;
};
export type GraphNode = {
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
export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  kind: "imports" | "contains" | "calls" | "extends" | "may-dispatch-to" | "depends-on" | "reads" | "writes";
  confidence?: number;
  classification?: "fact" | "heuristic" | "interpretation";
  resolution_method?: string;
  evidence?: { path?: string; line?: number; column?: number; expression?: string };
};
export type RepositoryMetadata = {
  name: string;
  url?: string;
  pinned_url?: string;
  source: "github" | "local";
};
export type SnapshotMetadata = { commit_sha: string };
export type UnresolvedStep = {
  source_id: string;
  reason: string;
  evidence: { path?: string; line?: number; column?: number; expression?: string };
};
export type ExecutionFlow = {
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
export type RiskFinding = {
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
  remediation: {
    classification: "heuristic";
    confidence: number;
    provenance: string;
    why_it_matters: string;
    actions: Array<{
      id: string;
      title: string;
      description: string;
      priority: number;
      effort: "small" | "medium" | "large";
      classification: "heuristic";
      confidence: number;
      evidence_refs: string[];
    }>;
    validation_steps: string[];
  };
};
export type ArchitecturePattern = {
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
export type Graph = {
  project_discovery?: {
    version: "1";
    scope: "root-pyproject-only";
    status: "missing" | "skipped" | "unreadable" | "invalid" | "parsed";
    path: "pyproject.toml";
    sha256: string | null;
    declarations: { key: string[]; value: string | string[]; classification: "fact"; confidence: 1; provenance: string }[];
    warnings: string[];
    limitations: string[];
  };
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
  coverage?: InventoryCoverage;
  analysis?: {
    tier: "inventory" | "deep";
    engine: string;
    limitations: string[];
  };
};
export type Claim = {
  id?: string;
  classification: "fact" | "heuristic" | "interpretation";
  text: string;
  confidence: number;
  provenance: string;
  evidence_refs?: string[];
};
