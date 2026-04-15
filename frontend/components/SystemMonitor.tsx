"use client";

import { useState, useEffect } from "react";
import { Activity, Database, Layers, Cpu, Inbox } from "lucide-react";
import {
  getSystemStatus,
  getQueueStats,
  getOverviewStats,
  sendClientHeartbeat,
  SystemStatus,
  QueueStats,
  OverviewStats,
} from "@/lib/api";

function Dot({ ok }: { ok: boolean }) {
  return (
    <span className={`inline-block h-2 w-2 rounded-full ${ok ? "bg-[var(--color-success)]" : "bg-[var(--color-danger)]"}`} />
  );
}

export default function SystemMonitor() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [queue, setQueue] = useState<QueueStats | null>(null);
  const [overview, setOverview] = useState<OverviewStats | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  useEffect(() => {
    const deviceIdKey = "dc.client.id.v1";
    let clientId = window.localStorage.getItem(deviceIdKey);
    if (!clientId) {
      clientId = crypto.randomUUID();
      window.localStorage.setItem(deviceIdKey, clientId);
    }
    const name = `${window.navigator.platform || "Device"} · ${window.location.hostname}`;
    const refresh = async () => {
      let err: string | null = null;
      try { await sendClientHeartbeat(clientId!, name); } catch (e: unknown) {
        err = e instanceof Error ? e.message : "Heartbeat failed";
      }
      try { setStatus(await getSystemStatus()); } catch (e: unknown) {
        err = e instanceof Error ? e.message : "Status unavailable";
      }
      try { setQueue(await getQueueStats()); } catch (e: unknown) {
        err = e instanceof Error ? e.message : "Queue stats unavailable";
      }
      try { setOverview(await getOverviewStats()); } catch (e: unknown) {
        err = e instanceof Error ? e.message : "Overview unavailable";
      }
      setLastError(err);
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
    <div className="rounded-[var(--radius-card)] glass-panel p-4 space-y-4">
      <h3 className="text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)]">
        System Status
      </h3>

      <div className="grid grid-cols-2 gap-2">
        {services.map((s) => (
          <div key={s.label} className="flex items-center gap-2 text-xs rounded-md px-2 py-1 bg-[var(--color-bg-elevated)]/60 border border-[var(--color-bg-border)]/70">
            <span className="text-[var(--color-text-muted)]">{s.icon}</span>
            <span className="text-[var(--color-text-secondary)]">{s.label}</span>
            <Dot ok={s.ok} />
          </div>
        ))}
      </div>
      {lastError && (
        <div className="rounded-lg border border-red-900/40 bg-[#1e0808] px-3 py-2 text-xs text-[var(--color-danger)]">
          {lastError}
        </div>
      )}

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
      {overview && (
        <div className="border-t border-[var(--color-bg-border)] pt-3 space-y-2">
          <h4 className="text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)]">Live Metrics</h4>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="rounded bg-[var(--color-bg-elevated)]/80 px-2 py-1.5 text-[var(--color-text-secondary)] border border-[var(--color-bg-border)]/70">
              Devices: <span className="font-mono text-[var(--color-text-primary)]">{overview.active_devices}</span>
            </div>
            <div className="rounded bg-[var(--color-bg-elevated)]/80 px-2 py-1.5 text-[var(--color-text-secondary)] border border-[var(--color-bg-border)]/70">
              Docs ingested: <span className="font-mono text-[var(--color-text-primary)]">{overview.documents_ingested}</span>
            </div>
            <div className="rounded bg-[var(--color-bg-elevated)]/80 px-2 py-1.5 text-[var(--color-text-secondary)] border border-[var(--color-bg-border)]/70">
              Docs total: <span className="font-mono text-[var(--color-text-primary)]">{overview.documents_total}</span>
            </div>
            <div className="rounded bg-[var(--color-bg-elevated)]/80 px-2 py-1.5 text-[var(--color-text-secondary)] border border-[var(--color-bg-border)]/70">
              Chunks: <span className="font-mono text-[var(--color-text-primary)]">{overview.chunks_total}</span>
            </div>
            <div className="rounded bg-[var(--color-bg-elevated)]/80 px-2 py-1.5 text-[var(--color-text-secondary)] border border-[var(--color-bg-border)]/70">
              Embeddings done: <span className="font-mono text-[var(--color-text-primary)]">{overview.embeddings_done}</span>
            </div>
            <div className="rounded bg-[var(--color-bg-elevated)]/80 px-2 py-1.5 text-[var(--color-text-secondary)] border border-[var(--color-bg-border)]/70">
              Embeddings failed: <span className="font-mono text-[var(--color-text-primary)]">{overview.embeddings_failed}</span>
            </div>
          </div>
          <div className="space-y-1">
            {overview.devices.slice(0, 5).map((d) => (
              <div key={d.client_id} className="flex items-center justify-between text-xs text-[var(--color-text-secondary)]">
                <span className="truncate">{d.name}</span>
                <span className="font-mono text-[var(--color-text-muted)]">{d.ip || "n/a"}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
