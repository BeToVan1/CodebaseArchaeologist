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
type Graph = { schema_version: string; nodes: GraphNode[]; edges: GraphEdge[] };

const filename = (path: string) => path.split("/").at(-1) ?? path;
const folder = (path: string) => path.split("/").slice(0, -1).join("/") || "repository root";
const positionFor = (index: number) => ({ x: (index % 2) * 330, y: Math.floor(index / 2) * 150 });
const MAX_SOURCE_CHARACTERS = 200_000;

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
      .then((data: Graph) => {
        setGraph({ ...data, edges: data.edges ?? [] });
        setSelectedId(data.nodes[0]?.id ?? null);
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

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="mark" aria-hidden="true">A</div>
          <div><div className="product-name">Archaeologist</div><div className="repo-name">cosmicpython/code · fixture</div></div>
        </div>
        <div className="snapshot"><span className="status-dot" />Graph ready<code>{graph?.nodes.length ?? 0} files · {graph?.edges.length ?? 0} imports</code></div>
      </header>

      <section className="workspace">
        <aside className="rail">
          <div className="eyebrow">Repository</div><h1>Dependency map</h1>
          <p className="rail-copy">Explore files and the imports connecting them. This fixture is ready to be replaced by analyzer output.</p>
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
            {error ? <div className="empty">{error}</div> : (
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
            {!error && graph && !visibleGraphNodes.length && <div className="empty overlay">No files match “{query}”.</div>}
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
            <div className="next-step"><span>Fixture data</span><p>This source is representative fixture content. Person A’s analyzer can replace it through the optional source field.</p></div>
          </> : <p>Select a node to inspect it.</p>}
        </aside>
      </section>
    </main>
  );
}

