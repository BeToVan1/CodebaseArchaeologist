import { MAX_REPORT_BYTES, validateReport } from '../app/graph-report.ts';

type Schema = true | 'numbers' | { [key: string]: Schema } | readonly [Schema];
const fields = (names: string): Record<string, Schema> => Object.fromEntries(names.split(' ').map(key => [key, true]));
const evidence = fields('path line end_line column expression');
const statement = fields('text classification confidence provenance');
const claim = { ...statement, id: true, evidence_refs: [true] } as const;
const member = fields('name line annotation');
const schema: Schema = {
  ...fields('schema_version source_url repo_root'),
  repository: fields('name url pinned_url source'), snapshot: fields('commit_sha'),
  analysis: { ...fields('tier engine'), limitations: [true] },
  coverage: fields('python_files_total_found python_files_analyzed python_files_truncated github_tree_truncated source_failures source_truncations unmatched_imports'),
  nodes: [{ ...fields('id kind path name qualified_name parent_id start_line end_line definition_line is_async docstring size_bytes source source_truncated source_error framework architectural_role'),
    decorators: [true], bases: [true], entrypoint: { ...fields('framework kind method route_path label router_prefix'), router_prefix_evidence: evidence, local_inclusions: [{ ...fields('parent path'), evidence }] }, entrypoint_evidence: evidence,
    sqlalchemy: { ...fields('kind table_name table_expression is_abstract'), columns: [member], relationships: [member] },
    evidence_packet: { ...fields('version node_id'), source_range: fields('path start_line end_line'),
      summary: statement, execution_role: statement, structural_rationale: statement,
      related_edge_ids: [true], flow_ids: [true], finding_ids: [true], pattern_ids: [true], claims: [claim] } }],
  edges: [{ ...fields('id source target kind confidence classification resolution_method'), evidence }],
  flows: [{ ...fields('id entrypoint_id label framework confidence completeness'), ordered_node_ids: [true], ordered_edge_ids: [true],
    unresolved_steps: [{ ...fields('source_id reason'), evidence }] }],
  findings: [{ ...fields('id rule_id node_id title severity classification confidence summary provenance'), related_node_ids: [true], evidence, metrics: 'numbers',
    remediation: { ...fields('classification confidence provenance why_it_matters'), validation_steps: [true],
      actions: [{ ...fields('id title description priority effort classification confidence'), evidence_refs: [true] }] } }],
  patterns: [{ ...fields('id pattern_id title classification confidence summary provenance'), node_ids: [true], edge_ids: [true], evidence_refs: [true], metrics: 'numbers' }],
  test_proximity: { ...fields('version scope test_files_identified candidate_links links_truncated provenance'), limitations: [true],
    links: [fields('signal source_node_id target_node_id edge_id classification confidence')] },
  project_discovery: { ...fields('version scope status path sha256'), warnings: [true], limitations: [true],
    declarations: [{ ...fields('value classification confidence provenance'), key: [true] }] },
};

function project(value: unknown, spec: Schema): unknown {
  if (value === undefined || value === null) return value;
  if (spec === true) {
    // Scalar leaves may contain string lists (project declaration values), never objects.
    if (['string', 'boolean', 'number'].includes(typeof value)) return value;
    if (Array.isArray(value) && value.every(v => typeof v === 'string')) return [...value];
    throw new Error('Invalid saved report.');
  }
  if (Array.isArray(spec)) {
    if (!Array.isArray(value)) throw new Error('Invalid saved report.');
    return value.map(v => project(v, spec[0]));
  }
  if (typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid saved report.');
  if (spec === 'numbers') {
    // Extension metric names are data, never capabilities; values are finite numbers only.
    return Object.fromEntries(Object.entries(value).filter(([key, v]) =>
      !['__proto__', 'constructor', 'prototype'].includes(key) && typeof v === 'number' && Number.isFinite(v)));
  }
  return Object.fromEntries(Object.entries(spec).filter(([key]) => Object.hasOwn(value, key))
    .map(([key, child]) => [key, project((value as Record<string, unknown>)[key], child)]));
}

/** Bounded JSON only; no credentials/network. Output remains uploaded-unverified.
 * Not a secret detector: source/text fields are intentionally preserved verbatim.
 * Future HTTP routes must enforce streaming limits before constructing this string.
 */
export function serializeSavedReport(contents: string): string {
  if (typeof contents !== 'string' || new TextEncoder().encode(contents).byteLength > MAX_REPORT_BYTES)
    throw new Error('Saved report exceeds the 10 MiB limit.');
  try {
    const value = JSON.parse(contents);
    validateReport(value);
    const clean = project(value, schema);
    validateReport(clean);
    const result = JSON.stringify(clean);
    if (new TextEncoder().encode(result).byteLength > MAX_REPORT_BYTES) throw new Error();
    return result;
  } catch {
    throw new Error('Invalid saved report.');
  }
}
