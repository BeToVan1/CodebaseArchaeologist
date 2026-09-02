import { isRecord, validateGraph } from "./graph-validation.ts";
import type { Graph } from "./graph-types";

export const MAX_REPORT_BYTES = 10 * 1024 * 1024;
export const MAX_REPORT_NODES = 10_000;
export const MAX_REPORT_EDGES = 30_000;
export const MAX_REPORT_FILES = 500;

type Check = (value: unknown) => boolean;
const text: Check = (v) => typeof v === "string";
const number: Check = (v) => typeof v === "number" && Number.isFinite(v);
const line: Check = (v) => Number.isSafeInteger(v) && Number(v) >= 1;
const confidence: Check = (v) => number(v) && Number(v) >= 0 && Number(v) <= 1;
const boolean: Check = (v) => typeof v === "boolean";
const choice = (...values: string[]): Check => (v) => typeof v === "string" && values.includes(v);
const optional = (check: Check): Check => (v) => v === undefined || check(v);
const nullable = (check: Check): Check => (v) => v === null || check(v);
const list = (check: Check): Check => (v) => Array.isArray(v) && v.every(check);
const shape = (fields: Record<string, Check>): Check => (v) => isRecord(v) && Object.entries(fields).every(([key, check]) => check(v[key]));
const strings = list(text);
const classification = choice("fact", "heuristic", "interpretation");
const statementFields = { text, classification, confidence, provenance: text };
const statement = shape(statementFields);
const evidence = shape({ path: optional(text), line: optional(line), end_line: optional(line), column: optional(number), expression: optional(text) });
const metrics: Check = (v) => isRecord(v) && Object.values(v).every(number);
const claim = shape({ ...statementFields, id: optional(text), evidence_refs: strings });
const action = shape({ id: text, title: text, description: text, priority: line, effort: choice("small", "medium", "large"), classification: choice("heuristic"), confidence, evidence_refs: strings });
const remediation = shape({ classification: choice("heuristic"), confidence, provenance: text, why_it_matters: text, actions: list(action), validation_steps: strings });
const member = shape({ name: text, line, annotation: text });
const packet = shape({ version: text, node_id: text, source_range: shape({ path: text, start_line: line, end_line: line }), summary: statement, execution_role: statement, structural_rationale: statement,
  related_edge_ids: strings, flow_ids: strings, finding_ids: strings, pattern_ids: strings, claims: list(claim) });
const nodeDetails = shape({ name: optional(text), qualified_name: optional(text), parent_id: optional(text), decorators: optional(strings), bases: optional(strings), docstring: optional(text),
  start_line: optional(line), end_line: optional(line), definition_line: optional(line), is_async: optional(boolean), source_truncated: optional(boolean), framework: optional(text), architectural_role: optional(text),
  entrypoint: optional(shape({ framework: text, kind: text, method: optional(text), route_path: optional(nullable(text)), label: text })), entrypoint_evidence: optional(evidence),
  sqlalchemy: optional(shape({ kind: choice("declarative-base", "abstract-model", "model"), table_name: optional(nullable(text)), table_expression: optional(nullable(text)), is_abstract: boolean, columns: list(member), relationships: list(member) })),
  evidence_packet: optional(packet) });

function requireValid(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`Invalid report: ${message}`);
}

/** Only the current export contract is accepted. Validation is not attestation. */
export function validateReport(value: unknown): Graph {
  requireValid(isRecord(value) && value.schema_version === "1.1", "expected schema v1.1. Regenerate the report with the current analyzer.");
  requireValid(Array.isArray(value.nodes) && value.nodes.length <= MAX_REPORT_NODES, "node limit exceeded or nodes missing.");
  requireValid(Array.isArray(value.edges) && value.edges.length <= MAX_REPORT_EDGES, "edge limit exceeded or edges missing.");
  requireValid(isRecord(value.analysis) && ["inventory", "deep"].includes(String(value.analysis.tier)), "analysis tier is required.");
  requireValid([value.flows, value.findings, value.patterns].every(Array.isArray), "flows, findings and patterns must be arrays.");
  requireValid(optional(text)(value.source_url) && optional(text)(value.repo_root), "invalid repository description.");
  const graph = validateGraph(value);
  requireValid(graph.nodes.filter((node) => node.kind === "file").length <= MAX_REPORT_FILES, "at most 500 files can be opened in the browser.");
  const collections = [graph.nodes, graph.edges, graph.flows!, graph.findings!, graph.patterns!];
  const allIds = new Set<string>();
  for (const collection of collections) for (const item of collection) {
    requireValid(item.id.length > 0 && !allIds.has(item.id), "IDs must be nonempty and unique.");
    allIds.add(item.id);
  }
  const nodeIds = new Set(graph.nodes.map((node) => node.id));
  const edgeIds = new Set(graph.edges.map((edge) => edge.id));
  const flowIds = new Set(graph.flows!.map((flow) => flow.id));
  const findingIds = new Set(graph.findings!.map((finding) => finding.id));
  const patternIds = new Set(graph.patterns!.map((pattern) => pattern.id));
  const refs = (ids: string[], known = allIds) => ids.every((id) => known.has(id));
  const filePaths = new Set(graph.nodes.filter((node) => node.kind === "file").map((node) => node.path));
  for (const node of graph.nodes) {
    requireValid(nodeDetails(node), "invalid nested symbol metadata or evidence packet.");
    requireValid(node.path && !node.path.includes("\\") && !node.path.split("/").some((part) => !part || part === "." || part === ".."), "paths must be repository-relative.");
    requireValid(node.kind === "file" || filePaths.has(node.path), "symbol references a missing file.");
    requireValid(!node.parent_id || nodeIds.has(node.parent_id), "symbol references a missing parent.");
    const p = node.evidence_packet;
    if (p) {
      requireValid(p.source_range.start_line === node.start_line && p.source_range.end_line === node.end_line, "evidence source range differs from its symbol.");
      requireValid(refs(p.related_edge_ids, edgeIds) && refs(p.flow_ids, flowIds) && refs(p.finding_ids, findingIds) && refs(p.pattern_ids, patternIds), "evidence packet references missing records.");
      requireValid(p.claims.every((c) => c.evidence_refs!.length > 0 && refs(c.evidence_refs!)), "claims must reference evidence in this report.");
    }
  }
  for (const edge of graph.edges) requireValid(shape({ confidence: optional(confidence), classification: optional(classification), resolution_method: optional(text), evidence: optional(evidence) })(edge), "invalid relationship evidence.");
  for (const flow of graph.flows!) requireValid(flow.unresolved_steps.every((step) => evidence(step.evidence)), "invalid unresolved flow evidence.");
  for (const finding of graph.findings!) {
    requireValid(metrics(finding.metrics) && evidence(finding.evidence) && remediation(finding.remediation), "invalid risk metrics or remediation.");
    requireValid(finding.remediation.confidence <= finding.confidence && finding.remediation.actions.every((a) => a.confidence <= finding.confidence && a.evidence_refs.length > 0 && refs(a.evidence_refs)), "remediation exceeds its evidence confidence or references missing evidence.");
  }
  for (const pattern of graph.patterns!) requireValid(metrics(pattern.metrics) && strings(pattern.evidence_refs) && refs(pattern.evidence_refs), "invalid pattern metrics or evidence references.");
  if (graph.analysis!.tier === "inventory") requireValid(graph.nodes.every((n) => n.kind === "file" && !n.evidence_packet && !n.sqlalchemy && !n.entrypoint) && graph.edges.every((e) => e.kind === "imports" && e.classification === "heuristic") && !graph.flows!.length && !graph.findings!.length && !graph.patterns!.length, "inventory reports cannot claim deep analysis.");

  const repository = graph.repository;
  requireValid(!repository || shape({ url: optional(text), pinned_url: optional(text) })(repository), "invalid repository links.");
  const repositoryUrl = repository?.url ?? graph.source_url;
  if (repositoryUrl) {
    const canonical = canonicalGithubUrl(repositoryUrl);
    requireValid(canonical, "repository links must identify a public GitHub repository.");
    requireValid(!graph.source_url || canonicalGithubUrl(graph.source_url) === canonical, "repository URLs disagree.");
    requireValid(!repository?.pinned_url || repository.pinned_url === `${canonical}/tree/${graph.snapshot?.commit_sha}`, "snapshot link does not match the repository and commit.");
  } else requireValid(!repository?.pinned_url, "snapshot link requires a repository URL.");
  return graph;
}

export function canonicalGithubUrl(value: string): string | undefined {
  const match = /^https:\/\/github\.com\/([A-Za-z0-9][A-Za-z0-9-]{0,38})\/([A-Za-z0-9._-]+?)(?:\.git)?\/?$/i.exec(value);
  return match && ![".", ".."].includes(match[2]) ? `https://github.com/${match[1]}/${match[2]}` : undefined;
}

export async function readReportFile(file: Pick<File, "size" | "text">): Promise<Graph> {
  if (file.size > MAX_REPORT_BYTES) throw new Error("Report exceeds the 10 MiB limit. Analyze a smaller repository or directory.");
  const contents = await file.text();
  if (new TextEncoder().encode(contents).byteLength > MAX_REPORT_BYTES) throw new Error("Report exceeds the 10 MiB limit.");
  let value: unknown;
  try { value = JSON.parse(contents); } catch { throw new Error("Report is not valid JSON. Choose the graph.json produced by the analyzer."); }
  return validateReport(value);
}

export function serializeReport(graph: Graph): string {
  validateReport(graph);
  const contents = JSON.stringify(graph);
  if (new TextEncoder().encode(contents).byteLength > MAX_REPORT_BYTES) throw new Error("This graph exceeds the 10 MiB portable report limit.");
  return contents;
}

export function reportFilename(graph: Graph): string {
  const name = (graph.repository?.name ?? "local-repository").replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 80);
  return `${name}-${graph.snapshot?.commit_sha.slice(0, 12) ?? "local"}.graph.json`;
}
