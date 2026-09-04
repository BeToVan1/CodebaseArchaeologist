import type { Graph } from "./graph-types";

const statusMessages = {
  missing: "No root pyproject.toml was found in the analyzed snapshot. This does not mean the project has no dependencies.",
  skipped: "The root pyproject.toml was skipped by the analyzer's safety limits.",
  unreadable: "The analyzer could not read the root pyproject.toml.",
  invalid: "The root pyproject.toml could not be parsed as UTF-8 TOML.",
  parsed: "Recorded literal declarations from the root pyproject.toml. These are not installed versions or verified runtime behavior.",
};

export function ProjectDetails({ graph, imported = false }: { graph: Graph | null; imported?: boolean }) {
  if (!graph) return null;
  const metadata = graph.project_discovery;
  return <details className="project-details">
    <summary>Project details</summary>
    {imported && <p className="project-details-trust">Imported metadata · unverified. Values, hashes and confidence are supplied by the report, not independently checked by this site.</p>}
    {!metadata ? <p>{graph.analysis?.tier === "inventory"
      ? "Project declarations were not analyzed in this inventory report."
      : "This report does not include project metadata. Open a report produced by an analyzer with project discovery support."}</p> : <>
      <p>{statusMessages[metadata.status]}</p>
      {metadata.status === "parsed" && <>
        <p>{metadata.declarations.length} recorded {metadata.declarations.length === 1 ? "declaration" : "declarations"}. Omitted or dynamic fields remain unresolved; an absent field is not proof of no dependencies.</p>
        <p>Fact classification and 100% confidence refer only to recording the literal text, not its correctness. Script targets are declarations, not proven execution flows.</p>
        <div className="project-declarations">{metadata.declarations.map((declaration, index) => <details key={index}>
          <summary><code>{declaration.key.join(" → ")}</code>{Array.isArray(declaration.value) && <span> ({declaration.value.length} items)</span>}</summary>
          {Array.isArray(declaration.value)
            ? declaration.value.length ? <ul>{declaration.value.map((value, item) => <li key={item}><code>{value}</code></li>)}</ul> : <p>Explicitly declared empty list.</p>
            : <p><code>{declaration.value}</code></p>}
          <small>{declaration.provenance}</small>
        </details>)}</div>
      </>}
      {metadata.warnings.length > 0 && <section aria-label="Project discovery warnings"><h2>Unresolved or omitted</h2><ul>{metadata.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></section>}
      <details className="project-source"><summary>Source and limitations</summary>
        <p>Source: <code>{metadata.path}</code> · root manifest only. Exact declaration line spans are not recorded.</p>
        {metadata.sha256 ? <p>Reported SHA-256: <code>{metadata.sha256}</code></p> : <p>No source hash recorded.</p>}
        <ul>{metadata.limitations.map((limitation, index) => <li key={index}>{limitation}</li>)}</ul>
      </details>
    </>}
  </details>;
}
