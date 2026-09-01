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

type GraphNode = {
  id: string;
  kind: "file";
  path: string;
  size_bytes?: number;
  source?: string;
  source_truncated?: boolean;
  source_error?: string;
};
type GraphEdge = { id: string; source: string; target: string; kind: "imports" };
type RepositoryMetadata = {
  name: string;
  url?: string;
  source: "github" | "local";
};
type Graph = {
  schema_version: string;
  repository?: RepositoryMetadata;
  source_url?: string;
  repo_root?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
};
type Claim = {
  classification: "fact" | "heuristic" | "interpretation";
  text: string;
  confidence: number;
  provenance: string;
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

function explainNode(node: GraphNode, incoming: GraphEdge[], outgoing: GraphEdge[]): { summary: string; role: string; rationale: string; claims: Claim[] } {
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
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
    if (!isRecord(node) || typeof node.id !== "string" || node.kind !== "file" || typeof node.path !== "string") {
      throw new Error(`Invalid graph: node ${index + 1} must have string id/path fields and kind "file".`);
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
    nodeIds.add(node.id);
  }
  for (const [index, edge] of edges.entries()) {
    if (!isRecord(edge) || typeof edge.id !== "string" || typeof edge.source !== "string" || typeof edge.target !== "string" || edge.kind !== "imports") {
      throw new Error(`Invalid graph: edge ${index + 1} must have id, source, target, and kind "imports".`);
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
    .filter((node) => !node.path.startsWith("tests/"))
    .sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0))[0]?.id
    ?? graph.nodes[0]?.id
    ?? null;
}

export default function Home() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<"production" | "all">("production");
  const [error, setError] = useState<string | null>(null);
  const [submittedUrl, setSubmittedUrl] = useState("https://github.com/cosmicpython/code");
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

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
      })
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load graph data"));
  }, []);

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
      (scope === "all" || !node.path.startsWith("tests/"))
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
  const incoming = graph?.edges.filter((edge) => edge.target === selectedId) ?? [];
  const outgoing = graph?.edges.filter((edge) => edge.source === selectedId) ?? [];
  const explanation = selected ? explainNode(selected, incoming, outgoing) : null;
  const nodePath = (id: string) => graph?.nodes.find((node) => node.id === id)?.path ?? id;
  const displayedSource = selected?.source?.slice(0, MAX_SOURCE_CHARACTERS);
  const sourceLines = displayedSource?.split("\n") ?? [];
  const sourceIsTruncated = Boolean(
    selected?.source_truncated || (selected?.source && selected.source.length > MAX_SOURCE_CHARACTERS),
  );
  const repositoryName = graph?.repository?.name
    ?? (graph?.source_url ? githubName(graph.source_url) : graph?.repo_root?.split(/[\\/]/).filter(Boolean).at(-1))
    ?? "Analyzed repository";
  const repositorySource = graph?.repository?.source ?? (graph?.source_url ? "github" : "local");
  const repositoryUrl = graph?.repository?.url ?? graph?.source_url;
  const riskCount = graph?.nodes.filter((node) => node.source_error).length ?? 0;

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="mark" aria-hidden="true">A</div>
          <div><div className="product-name">Archaeologist</div><div className="repo-name">{repositoryName}</div></div>
        </div>
        <div className="snapshot"><span className={`status-dot${isAnalyzing ? " busy" : ""}`} />{isAnalyzing ? "Analyzing repository" : graph ? "Graph ready" : error ? "Graph unavailable" : "Loading graph"}<code>{graph ? `${graph.nodes.length} files · ${graph.edges.length} imports` : "Please wait"}</code></div>
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
          {graph && <div className="origin"><span>{repositorySource === "github" ? "GitHub" : "Local directory"}</span>{repositoryUrl ? <a href={repositoryUrl} target="_blank" rel="noreferrer">Open repository ↗</a> : <strong>{repositoryName}</strong>}</div>}
          <label className="search"><span aria-hidden="true">⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter files" aria-label="Filter files" /></label>
          <div className="scope-switch" aria-label="Graph scope">
            <button className={scope === "production" ? "active" : ""} onClick={() => setScope("production")}>Production</button>
            <button className={scope === "all" ? "active" : ""} onClick={() => setScope("all")}>All files</button>
          </div>
          <nav aria-label="Graph summary">
            <div className="nav-item active"><span>Files</span><strong>{graph?.nodes.length ?? 0}</strong></div>
            <div className="nav-item"><span>Dependencies</span><strong>{graph?.edges.length ?? 0}</strong></div>
            <div className={riskCount ? "nav-item warning" : "nav-item muted"}><span>Read warnings</span><strong>{riskCount}</strong></div>
          </nav>
          <div className="contract"><span>Graph contract</span><code>schema v{graph?.schema_version ?? "0.1"}</code></div>
        </aside>

        <section className="canvas" aria-label="Repository dependency graph">
          <div className="canvas-head"><div><div className="eyebrow">Architecture map</div><h2>Python imports</h2><p className="relationship-help">A → B means file A imports file B.</p></div><div className="legend"><span /> Selected <i /> Imports →</div></div>
          <div className="layer-guide" aria-hidden="true">
            {(scope === "all" ? ["Tests"] : []).concat(["Entry points", "Application", "Domain", "Infrastructure", "Support"]).map((layer) => <span key={layer}>{layer}</span>)}
          </div>
          <div className="graph-surface">
            {error ? <div className="state-card error-state"><strong>Graph could not be loaded</strong><p>{error}</p></div> : !graph ? (
              <div className="state-card loading-state"><span aria-hidden="true" /><strong>Loading repository graph</strong><p>Validating nodes and dependencies…</p></div>
            ) : graph.nodes.length === 0 ? (
              <div className="state-card"><strong>No Python files found</strong><p>This repository does not contain any analyzable .py files.</p></div>
            ) : (
              <ReactFlow
                key={`${scope}:${flowNodes.length}:${query}`}
                nodes={flowNodes}
                edges={flowEdges}
                onNodeClick={(_, node) => setSelectedId(node.id)}
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
            {!error && graph && graph.nodes.length > 0 && !visibleGraphNodes.length && <div className="empty overlay">No files match “{query}”.</div>}
          </div>
        </section>

        <aside className="detail" aria-live="polite">
          <div className="detail-top"><span className="pill">FILE</span><span className="connection-count">{incoming.length + outgoing.length} connections</span></div>
          {selected ? <>
            <div className="detail-icon">PY</div><h2>{filename(selected.path)}</h2><p className="full-path">{selected.path}</p>
            <div className="divider" />
            <dl><div><dt>Kind</dt><dd>Python file</dd></div><div><dt>Size</dt><dd>{formatBytes(selected.size_bytes)}</dd></div><div><dt>Node ID</dt><dd><code>{selected.id}</code></dd></div><div><dt>Folder</dt><dd>{folder(selected.path)}</dd></div></dl>
            {explanation && <section className="explanation-section">
              <div className="section-heading"><h3>Understanding</h3><span className="analysis-label">Static analysis</span></div>
              <div className="explanation-block"><h4>What it does</h4><p>{explanation.summary}</p></div>
              <div className="explanation-block"><h4>Execution role</h4><p>{explanation.role}</p></div>
              <div className="explanation-block"><h4>Why it is structured here</h4><p>{explanation.rationale}</p></div>
              <div className="claims"><h4>Claims and evidence</h4>{explanation.claims.map((claim) => <article className={`claim ${claim.classification}`} key={claim.classification}>
                <div><span>{claim.classification}</span><strong>{Math.round(claim.confidence * 100)}% confidence</strong></div>
                <p>{claim.text}</p><small>Provenance: {claim.provenance}</small>
              </article>)}</div>
            </section>}
            <section className="source-section">
              <div className="section-heading"><h3>Source</h3>{sourceIsTruncated && <span>Truncated</span>}</div>
              {displayedSource !== undefined ? (
                <pre className="code-viewer" aria-label={`Source code for ${selected.path}`} tabIndex={0}>
                  <code>{sourceLines.map((line, index) => (
                    <span className="code-line" key={index}>
                      <span className="line-number" aria-hidden="true">{index + 1}</span>
                      <span className="line-content">{line || " "}</span>
                    </span>
                  ))}</code>
                </pre>
              ) : selected.source_error ? (
                <div className="source-unavailable source-warning" role="alert"><strong>Analyzer warning</strong><p>{selected.source_error}</p></div>
              ) : (
                <div className="source-unavailable"><strong>Source unavailable</strong><p>The analyzer did not include this file’s contents.</p></div>
              )}
              {sourceIsTruncated && <p className="truncation-note">Only the first 200 KB are shown.</p>}
            </section>
            <section className="connections"><h3>Imports</h3>
              <div className="connection-group"><span>Outgoing</span>{outgoing.length ? outgoing.map((edge) => <button key={edge.id} onClick={() => setSelectedId(edge.target)}>→ {nodePath(edge.target)}</button>) : <p>None in current graph</p>}</div>
              <div className="connection-group"><span>Incoming</span>{incoming.length ? incoming.map((edge) => <button key={edge.id} onClick={() => setSelectedId(edge.source)}>← {nodePath(edge.source)}</button>) : <p>None in current graph</p>}</div>
            </section>
          </> : <p>Select a node to inspect it.</p>}
        </aside>
      </section>
    </main>
  );
}

