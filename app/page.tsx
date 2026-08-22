"use client";

import { useEffect, useMemo, useState } from "react";

type GraphNode = { id: string; kind: "file"; path: string };
type Graph = { schema_version: string; nodes: GraphNode[]; edges: unknown[] };
const filename = (path: string) => path.split("/").at(-1) ?? path;
const folder = (path: string) => path.split("/").slice(0, -1).join("/") || "repository root";

export default function Home() {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    fetch("/graph.json").then((response) => response.json()).then((data: Graph) => {
      setGraph(data);
      setSelectedId(data.nodes[0]?.id ?? null);
    });
  }, []);

  const visibleNodes = useMemo(() => graph?.nodes.filter((node) =>
    node.path.toLowerCase().includes(query.toLowerCase())) ?? [], [graph, query]);
  const selected = graph?.nodes.find((node) => node.id === selectedId) ?? null;

  return <main className="shell">
    <header className="topbar">
      <div className="brand"><div className="mark" aria-hidden="true">A</div><div><div className="product-name">Archaeologist</div><div className="repo-name">cosmicpython/code</div></div></div>
      <div className="snapshot"><span className="status-dot" />Snapshot ready<code>main · 8f21c4a</code></div>
    </header>
    <section className="workspace">
      <aside className="rail">
        <div className="eyebrow">Repository</div><h1>File map</h1><p className="rail-copy">Explore the first deterministic output from the analyzer.</p>
        <label className="search"><span aria-hidden="true">⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter files" aria-label="Filter files" /></label>
        <nav aria-label="Explorer sections"><button className="nav-item active"><span>Files</span><strong>{graph?.nodes.length ?? 0}</strong></button><button className="nav-item" disabled><span>Dependencies</span><strong>—</strong></button><button className="nav-item" disabled><span>Risks</span><strong>—</strong></button></nav>
        <div className="contract"><span>Graph contract</span><code>schema v{graph?.schema_version ?? "0.1"}</code></div>
      </aside>
      <section className="canvas" aria-label="Repository file graph">
        <div className="canvas-head"><div><div className="eyebrow">Architecture map</div><h2>Repository files</h2></div><div className="legend"><span /> Python file</div></div>
        <div className="node-grid">{visibleNodes.map((node, index) => <button className={`file-node ${selectedId === node.id ? "selected" : ""}`} key={node.id} onClick={() => setSelectedId(node.id)} style={{ "--delay": `${index * 45}ms` } as React.CSSProperties}><div className="file-icon">PY</div><div className="node-text"><strong>{filename(node.path)}</strong><span>{folder(node.path)}</span></div><span className="node-arrow">→</span></button>)}</div>
        {!visibleNodes.length && <div className="empty">No files match “{query}”.</div>}<div className="canvas-note">Dependency lines will appear when Person A adds edges to the graph.</div>
      </section>
      <aside className="detail" aria-live="polite"><div className="detail-top"><span className="pill">FILE</span><button className="more" aria-label="More actions">•••</button></div>{selected ? <><div className="detail-icon">PY</div><h2>{filename(selected.path)}</h2><p className="full-path">{selected.path}</p><div className="divider" /><dl><div><dt>Kind</dt><dd>Python file</dd></div><div><dt>Node ID</dt><dd><code>{selected.id}</code></dd></div><div><dt>Folder</dt><dd>{folder(selected.path)}</dd></div></dl><div className="next-step"><span>Next enrichment</span><p>Symbols, imports, and explanations will populate this panel.</p></div></> : <p>Select a node to inspect it.</p>}</aside>
    </section>
  </main>;
}
