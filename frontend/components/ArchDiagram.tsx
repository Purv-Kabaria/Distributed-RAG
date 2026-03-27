"use client";

export default function ArchDiagram() {
  const nodes = [
    { id: "client",    label: "Browser",          sub: "Next.js 15",         x: 50,  y: 10,  color: "#6366f1" },
    { id: "gateway",   label: "API Gateway",       sub: ":8000",              x: 50,  y: 33,  color: "#8b5cf6" },
    { id: "ingest",    label: "Ingestion Worker",  sub: ":8001 · Redis BRPOP",x: 15,  y: 57,  color: "#0ea5e9" },
    { id: "embed",     label: "Embedding Worker",  sub: ":8002 · Gemini Emb2",x: 50,  y: 57,  color: "#f59e0b" },
    { id: "vector",    label: "Vector Store",      sub: ":8003 · Qdrant",     x: 85,  y: 57,  color: "#22c55e" },
    { id: "query",     label: "Query Service",     sub: ":8004 · Groq/Ollama",x: 50,  y: 80,  color: "#ec4899" },
    { id: "infra",     label: "Infrastructure",    sub: "Redis · Postgres",   x: 50,  y: 96,  color: "#475569" },
  ];

  const edges = [
    { from: "client",  to: "gateway", label: "REST" },
    { from: "gateway", to: "ingest",  label: "Redis LPUSH" },
    { from: "gateway", to: "query",   label: "proxy" },
    { from: "ingest",  to: "embed",   label: "Redis LPUSH" },
    { from: "embed",   to: "vector",  label: "upsert vec" },
    { from: "query",   to: "embed",   label: "embed query" },
    { from: "query",   to: "vector",  label: "search" },
    { from: "gateway", to: "infra",   label: "PG" },
    { from: "ingest",  to: "infra",   label: "PG" },
    { from: "embed",   to: "infra",   label: "PG" },
    { from: "vector",  to: "infra",   label: "PG+Redis" },
    { from: "query",   to: "infra",   label: "PG" },
  ];

  const W = 600, H = 360;
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const px = (pct: number) => (pct / 100) * W;
  const py = (pct: number) => (pct / 100) * H;

  return (
    <div className="rounded-[var(--radius-card)] bg-[var(--color-bg-surface)] border border-[var(--color-bg-border)] p-4">
      <h3 className="text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)] mb-3">
        Architecture · 5-Component Distributed System
      </h3>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 320 }}>
        <defs>
          <marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
            <path d="M0,0 L0,6 L6,3 z" fill="#475569" />
          </marker>
        </defs>

        {/* Edges */}
        {edges.map((e, i) => {
          const s = byId[e.from], t = byId[e.to];
          const x1 = px(s.x), y1 = py(s.y) + 14;
          const x2 = px(t.x), y2 = py(t.y) - 4;
          const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
          return (
            <g key={i}>
              <line x1={x1} y1={y1} x2={x2} y2={y2}
                stroke="#2a2d38" strokeWidth="1.5" markerEnd="url(#arrow)" />
              <text x={mx} y={my - 3} textAnchor="middle"
                fontSize="8" fill="#475569" fontFamily="JetBrains Mono, monospace">
                {e.label}
              </text>
            </g>
          );
        })}

        {/* Nodes */}
        {nodes.map((n) => (
          <g key={n.id} transform={`translate(${px(n.x)},${py(n.y)})`}>
            <rect x={-60} y={-14} width={120} height={28}
              rx={6} fill="#1e2028" stroke={n.color} strokeWidth="1.5" />
            <text y={-3} textAnchor="middle" fontSize="9" fontWeight="600"
              fill="#f1f5f9" fontFamily="Inter, sans-serif">
              {n.label}
            </text>
            <text y={9} textAnchor="middle" fontSize="7.5"
              fill="#64748b" fontFamily="JetBrains Mono, monospace">
              {n.sub}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}
