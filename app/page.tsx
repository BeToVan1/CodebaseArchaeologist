"use client";

import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
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
  source?: string;
  source_truncated?: boolean;
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

const filename = (path: string) => path.split("/").at(-1) ?? path;
const folder = (path: string) => path.split("/").slice(0, -1).join("/") || "repository root";
const positionFor = (index: number) => ({ x: (index % 2) * 330, y: Math.floor(index / 2) * 150 });
const MAX_SOURCE_CHARACTERS = 200_000;

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

export default function Home() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/graph.json")
      .then((response) => {
        if (!response.ok) throw new Error(`Graph request failed (${response.status})`);
        return response.json();
      })
      .then((data: unknown) => {
        const validated = validateGraph(data);
        setGraph(validated);
        setSelectedId(validated.nodes[0]?.id ?? null);
      })
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Could not load graph data"));
  }, []);

  const visibleGraphNodes = useMemo(
    () => graph?.nodes.filter((node) => node.path.toLowerCase().includes(query.toLowerCase())) ?? [],
    [graph, query],
  );
  const visibleIds = useMemo(() => new Set(visibleGraphNodes.map((node) => node.id)), [visibleGraphNodes]);
  const flowNodes = useMemo<Node[]>(
    () => visibleGraphNodes.map((node, index) => ({
      id: node.id,
      position: positionFor(index),
      data: { label: filename(node.path), path: node.path },
      className: selectedId === node.id ? "flow-node selected" : "flow-node",
    })),
    [selectedId, visibleGraphNodes],
  );
  const flowEdges = useMemo<Edge[]>(
    () => (graph?.edges ?? []).filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)).map((edge) => ({
      ...edge,
      type: "smoothstep",
      animated: true,
      markerEnd: { type: MarkerType.ArrowClosed },
      style: { stroke: "#315b3d", strokeWidth: 1.6 },
    })),
    [graph, visibleIds],
  );
  const selected = graph?.nodes.find((node) => node.id === selectedId) ?? null;
  const incoming = graph?.edges.filter((edge) => edge.target === selectedId) ?? [];
  const outgoing = graph?.edges.filter((edge) => edge.source === selectedId) ?? [];
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

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="mark" aria-hidden="true">A</div>
          <div><div className="product-name">Archaeologist</div><div className="repo-name">{repositoryName}</div></div>
        </div>
        <div className="snapshot"><span className="status-dot" />{graph ? "Graph ready" : error ? "Graph unavailable" : "Loading graph"}<code>{graph ? `${graph.nodes.length} files · ${graph.edges.length} imports` : "Please wait"}</code></div>
      </header>

      <section className="workspace">
        <aside className="rail">
          <div className="eyebrow">Repository</div><h1>Dependency map</h1>
          <p className="rail-copy">Explore files, source, and internal imports from the analyzed repository.</p>
          {graph && <div className="origin"><span>{repositorySource === "github" ? "GitHub" : "Local directory"}</span>{repositoryUrl ? <a href={repositoryUrl} target="_blank" rel="noreferrer">Open repository ↗</a> : <strong>{repositoryName}</strong>}</div>}
          <label className="search"><span aria-hidden="true">⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter files" aria-label="Filter files" /></label>
          <nav aria-label="Graph summary">
            <div className="nav-item active"><span>Files</span><strong>{graph?.nodes.length ?? 0}</strong></div>
            <div className="nav-item"><span>Dependencies</span><strong>{graph?.edges.length ?? 0}</strong></div>
            <div className="nav-item muted"><span>Risks</span><strong>—</strong></div>
          </nav>
          <div className="contract"><span>Graph contract</span><code>schema v{graph?.schema_version ?? "0.1"}</code></div>
        </aside>

        <section className="canvas" aria-label="Repository dependency graph">
          <div className="canvas-head"><div><div className="eyebrow">Architecture map</div><h2>Python imports</h2></div><div className="legend"><span /> File <i /> Import</div></div>
          <div className="graph-surface">
            {error ? <div className="state-card error-state"><strong>Graph could not be loaded</strong><p>{error}</p></div> : !graph ? (
              <div className="state-card loading-state"><span aria-hidden="true" /><strong>Loading repository graph</strong><p>Validating nodes and dependencies…</p></div>
            ) : graph.nodes.length === 0 ? (
              <div className="state-card"><strong>No Python files found</strong><p>This repository does not contain any analyzable .py files.</p></div>
            ) : (
              <ReactFlow
                key={`${flowNodes.length}:${query}`}
                nodes={flowNodes}
                edges={flowEdges}
                onNodeClick={(_, node) => setSelectedId(node.id)}
                fitView
                fitViewOptions={{ padding: 0.24 }}
                minZoom={0.35}
                maxZoom={1.8}
                nodesDraggable
                nodesConnectable={false}
              >
                <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#cbd3c9" />
                <MiniMap pannable zoomable nodeColor="#315b3d" maskColor="rgba(244,246,241,.76)" />
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
            <dl><div><dt>Kind</dt><dd>Python file</dd></div><div><dt>Node ID</dt><dd><code>{selected.id}</code></dd></div><div><dt>Folder</dt><dd>{folder(selected.path)}</dd></div></dl>
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
              ) : (
                <div className="source-unavailable"><strong>Source unavailable</strong><p>The analyzer has not included this file’s contents yet.</p></div>
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

