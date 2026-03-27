"use client";

import { useState, useEffect } from "react";
import { Activity, Database, Layers, Cpu, Inbox } from "lucide-react";
import { getSystemStatus, getQueueStats, SystemStatus, QueueStats } from "@/lib/api";

function Dot({ ok }: { ok: boolean }) {
  return (
    <span className={`inline-block h-2 w-2 rounded-full ${ok ? "bg-[var(--color-success)]" : "bg-[var(--color-danger)]"}`} />
  );
}

export default function SystemMonitor() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [queue, setQueue] = useState<QueueStats | null>(null);

  useEffect(() => {
    const refresh = async () => {
      try { setStatus(await getSystemStatus()); } catch {}
      try { setQueue(await getQueueStats()); } catch {}
    };
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  const services = [
    { label: "Gateway",   icon: <Activity size={13} />,  ok: true /* self */ },
    { label: "Ingestion", icon: <Layers size={13} />,    ok: status?.ingestion?.status === "ok" },
    { label: "Query",     icon: <Cpu size={13} />,       ok: status?.query?.status === "ok" },
    { label: "Redis",     icon: <Database size={13} />,  ok: status?.redis?.status === "ok" },
  ];

  return (
    <div className="rounded-[var(--radius-card)] bg-[var(--color-bg-surface)] border border-[var(--color-bg-border)] p-4 space-y-4">
      <h3 className="text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)]">
        System Status
      </h3>

      <div className="grid grid-cols-2 gap-2">
        {services.map((s) => (
          <div key={s.label} className="flex items-center gap-2 text-xs">
            <span className="text-[var(--color-text-muted)]">{s.icon}</span>
            <span className="text-[var(--color-text-secondary)]">{s.label}</span>
            <Dot ok={s.ok} />
          </div>
        ))}
      </div>

      {queue && (
        <div className="border-t border-[var(--color-bg-border)] pt-3 space-y-1.5">
          <h4 className="text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)]">Queues</h4>
          <div className="flex items-center justify-between text-xs">
            <span className="flex items-center gap-1.5 text-[var(--color-text-secondary)]">
              <Inbox size={11} /> Ingestion
            </span>
            <span className={`font-mono font-medium ${queue.ingestion_queue > 0 ? "text-[var(--color-warning)]" : "text-[var(--color-text-muted)]"}`}>
              {queue.ingestion_queue}
            </span>
          </div>
          <div className="flex items-center justify-between text-xs">
            <span className="flex items-center gap-1.5 text-[var(--color-text-secondary)]">
              <Inbox size={11} /> Embedding
            </span>
            <span className={`font-mono font-medium ${queue.embedding_queue > 0 ? "text-[var(--color-warning)]" : "text-[var(--color-text-muted)]"}`}>
              {queue.embedding_queue}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
