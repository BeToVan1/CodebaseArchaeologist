import type { Graph, GraphNode } from "./graph-types";
import { testEvidenceForSelection } from "./test-proximity";

export function TestEvidence({ graph, selected, imported, onOpen }: {
  graph: Graph; selected: GraphNode; imported: boolean; onOpen: (node: GraphNode) => void;
}) {
  const report = graph.test_proximity;
  const evidence = testEvidenceForSelection(graph, selected);
  return <section className="test-evidence" aria-label="Test proximity evidence">
    <h3>Test evidence</h3>
    {imported && <p>Imported evidence · unverified. References are checked for consistency, not independently verified against the repository.</p>}
    {!report ? <p>{graph.analysis?.tier === "inventory" ? "Test proximity was not analyzed in this inventory report." : "This report does not include test-proximity data."}</p> : <>
      <p>Path-based heuristics, not test coverage. Calls and imports do not prove assertions, execution, or passing tests.</p>
      {report.links_truncated && <p role="status">This report omits some links because of output limits.</p>}
      {!evidence.length && <p>No recorded incoming test evidence for this selection. This does not mean the code is untested.</p>}
      {(["symbol-call", "module-import"] as const).map(signal => {
        const matches = evidence.filter(item => item.link.signal === signal);
        if (!matches.length) return null;
        return <details key={signal} open><summary>{signal === "symbol-call" ? "Recorded calls to selected code" : "Imports of containing module"} ({matches.length})</summary>
          {signal === "module-import" && <p>Module-level context only; not evidence for every symbol in this file.</p>}
          {matches.slice(0, 50).map(({link, source, target, edge}) => <article key={link.edge_id}>
            <button type="button" onClick={() => onOpen(source)}>Open {source.path}{source.name ? ` · ${source.name}` : ""}</button>
            <p>Line {edge.evidence?.line} → {target.qualified_name ?? target.path}</p>
            <small>Heuristic · 60% score, not coverage · {edge.resolution_method ?? "recorded static edge"}</small>
          </article>)}
          {matches.length > 50 && <p>Showing the first 50 links. Download the JSON report for all retained links.</p>}
        </details>;
      })}
      <details><summary>Method and limitations</summary><p>{report.provenance}</p><ul>{report.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul></details>
    </>}
  </section>;
}
