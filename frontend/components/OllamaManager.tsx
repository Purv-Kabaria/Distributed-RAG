"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import {
  Download, Trash2, CheckCircle2, AlertCircle, Loader2,
  RefreshCw, Play, Server, Package, Zap, ChevronRight
} from "lucide-react";

const BASE =
  process.env.NEXT_PUBLIC_GATEWAY_URL ||
  (typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "http://gateway:8000");

const RECOMMENDED_MODELS = [
  { name: "llama3.2",         size: "2.0 GB", desc: "Meta Llama 3.2 3B — fast, great for most tasks" },
  { name: "llama3.2:1b",     size: "1.3 GB", desc: "Llama 3.2 1B — minimal RAM, very fast" },
  { name: "mistral",          size: "4.1 GB", desc: "Mistral 7B — strong reasoning" },
  { name: "gemma2:2b",        size: "1.6 GB", desc: "Google Gemma 2 2B — efficient & accurate" },
  { name: "phi3:mini",        size: "2.2 GB", desc: "Microsoft Phi-3 Mini — compact & capable" },
  { name: "qwen2.5:3b",       size: "1.9 GB", desc: "Alibaba Qwen 2.5 3B — multilingual" },
  { name: "deepseek-r1:1.5b", size: "1.1 GB", desc: "DeepSeek R1 1.5B — reasoning focused" },
  { name: "llama3.1:8b",     size: "4.7 GB", desc: "Meta Llama 3.1 8B — best quality mid-size" },
];

interface OllamaModel {
  name: string;
  size: number;
  modified_at?: string;
  details?: { parameter_size?: string };
}

interface PullProgress {
  status: string;
  digest?: string;
  total?: number;
  completed?: number;
  error?: string;
}

interface DownloadState {
  model: string;
  status: "pulling" | "done" | "error";
  message: string;
  percent: number;
  error?: string;
}

function formatBytes(bytes: number) {
  if (!bytes) return "—";
  const gb = bytes / 1_073_741_824;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${(bytes / 1_048_576).toFixed(0)} MB`;
}

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

export default function OllamaManager({
  onModelReady,
}: {
  onModelReady?: (model: string) => void;
}) {
  const [available, setAvailable] = useState<boolean | null>(null);
  const [models, setModels] = useState<OllamaModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [downloads, setDownloads] = useState<Record<string, DownloadState>>({});
  const [deleting, setDeleting] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, "ok" | "fail">>({});
  const [customModel, setCustomModel] = useState("");
  const abortRefs = useRef<Record<string, AbortController>>({});

  const loadModels = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${BASE}/api/ollama/models`);
      const data = await res.json();
      setAvailable(data.available);
      setModels(data.models || []);
    } catch {
      setAvailable(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadModels();
  }, [loadModels]);

  const installedNames = new Set(models.map((m) => m.name));

  async function pullModel(modelName: string) {
    if (downloads[modelName]?.status === "pulling") return;

    const ctrl = new AbortController();
    abortRefs.current[modelName] = ctrl;

    setDownloads((prev) => ({
      ...prev,
      [modelName]: { model: modelName, status: "pulling", message: "Starting download…", percent: 0 },
    }));

    try {
      const res = await fetch(`${BASE}/api/ollama/pull`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: modelName }),
        signal: ctrl.signal,
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (!res.body) throw new Error("No response body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const p: PullProgress = JSON.parse(line);
            if (p.error) throw new Error(p.error);

            const percent =
              p.total && p.completed
                ? Math.round((p.completed / p.total) * 100)
                : 0;

            setDownloads((prev) => ({
              ...prev,
              [modelName]: {
                model: modelName,
                status: "pulling",
                message: p.status || "Downloading…",
                percent,
              },
            }));
          } catch (parseErr) {
            if ((parseErr as Error).message !== "Unexpected end of JSON input") {
              throw parseErr;
            }
          }
        }
      }

      setDownloads((prev) => ({
        ...prev,
        [modelName]: { model: modelName, status: "done", message: "Download complete!", percent: 100 },
      }));
      await loadModels();
      onModelReady?.(modelName);

      // Auto-clear success after 4s
      setTimeout(() => {
        setDownloads((prev) => {
          const n = { ...prev };
          delete n[modelName];
          return n;
        });
      }, 4000);
    } catch (e: unknown) {
      if ((e as Error).name === "AbortError") {
        setDownloads((prev) => {
          const n = { ...prev };
          delete n[modelName];
          return n;
        });
        return;
      }
      const msg = e instanceof Error ? e.message : "Download failed";
      setDownloads((prev) => ({
        ...prev,
        [modelName]: { model: modelName, status: "error", message: msg, percent: 0, error: msg },
      }));
    }
  }

  async function deleteModel(modelName: string) {
    setDeleting(modelName);
    try {
      await fetch(`${BASE}/api/ollama/models/${encodeURIComponent(modelName)}`, {
        method: "DELETE",
      });
      await loadModels();
    } catch {}
    finally { setDeleting(null); }
  }

  async function testModel(modelName: string) {
    setTesting(modelName);
    try {
      const res = await fetch(`${BASE}/api/ollama/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: modelName }),
      });
      const data = await res.json();
      setTestResults((prev) => ({ ...prev, [modelName]: data.ok ? "ok" : "fail" }));
    } catch {
      setTestResults((prev) => ({ ...prev, [modelName]: "fail" }));
    } finally {
      setTesting(null);
    }
  }

  // ── Unavailable state ──────────────────────────────────────────────────────
  if (!loading && available === false) {
    return (
      <div className="rounded-[var(--radius-card)] bg-[var(--color-bg-surface)] border border-[var(--color-bg-border)] p-5 space-y-4">
        <div className="flex items-center gap-2">
          <Server size={15} className="text-[var(--color-text-muted)]" />
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Ollama — Not Connected</h3>
        </div>
        <div className="rounded-lg bg-[#1e0808] border border-red-900/40 px-4 py-3 text-xs text-[var(--color-danger)] space-y-1">
          <p className="font-semibold">Ollama is not reachable.</p>
          <p className="text-red-400/80">Make sure you have set <code className="bg-black/30 px-1 rounded">OLLAMA_URL=http://host.docker.internal:11434</code> in your <code className="bg-black/30 px-1 rounded">.env</code> file and restarted Docker Compose.</p>
        </div>
        <div className="text-xs text-[var(--color-text-muted)] space-y-1">
          <p className="font-medium text-[var(--color-text-secondary)]">Setup steps:</p>
          <ol className="list-decimal list-inside space-y-0.5 pl-1">
            <li>Download Ollama from <span className="text-[var(--color-brand-hover)]">ollama.com/download/windows</span></li>
            <li>Run <code className="bg-[var(--color-bg-elevated)] px-1 rounded">ollama serve</code> in a terminal</li>
            <li>Add <code className="bg-[var(--color-bg-elevated)] px-1 rounded">OLLAMA_URL=http://host.docker.internal:11434</code> to your .env</li>
            <li>Run <code className="bg-[var(--color-bg-elevated)] px-1 rounded">docker compose up --build</code></li>
          </ol>
        </div>
        <button onClick={loadModels} className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors">
          <RefreshCw size={12} /> Retry connection
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* ── Installed models ── */}
      <div className="rounded-[var(--radius-card)] bg-[var(--color-bg-surface)] border border-[var(--color-bg-border)] p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Package size={14} className="text-[var(--color-text-muted)]" />
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Installed Models</h3>
            <span className="text-xs text-[var(--color-text-muted)] bg-[var(--color-bg-elevated)] px-1.5 py-0.5 rounded-full">
              {models.length}
            </span>
          </div>
          <button onClick={loadModels} disabled={loading}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors disabled:opacity-40">
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          </button>
        </div>

        {loading ? (
          <div className="space-y-2">
            {[...Array(2)].map((_, i) => <div key={i} className="h-12 rounded-lg skeleton" />)}
          </div>
        ) : models.length === 0 ? (
          <p className="text-xs text-[var(--color-text-muted)] text-center py-4">
            No models installed yet. Pull one below.
          </p>
        ) : (
          <div className="space-y-2">
            {models.map((m) => (
              <div key={m.name}
                className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] group">
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-[var(--color-text-primary)] font-mono">{m.name}</p>
                  <p className="text-xs text-[var(--color-text-muted)]">
                    {formatBytes(m.size)}
                    {m.details?.parameter_size && ` · ${m.details.parameter_size}`}
                    {m.modified_at && ` · ${timeAgo(m.modified_at)}`}
                  </p>
                </div>

                {/* Test result badge */}
                {testResults[m.name] === "ok" && (
                  <span className="text-xs text-[var(--color-success)] flex items-center gap-1">
                    <CheckCircle2 size={11} /> Working
                  </span>
                )}
                {testResults[m.name] === "fail" && (
                  <span className="text-xs text-[var(--color-danger)] flex items-center gap-1">
                    <AlertCircle size={11} /> Failed
                  </span>
                )}

                {/* Test button */}
                <button
                  onClick={() => testModel(m.name)}
                  disabled={testing === m.name}
                  title="Test model"
                  className="opacity-0 group-hover:opacity-100 text-[var(--color-text-muted)] hover:text-[var(--color-brand-hover)] transition-all disabled:opacity-30"
                >
                  {testing === m.name
                    ? <Loader2 size={13} className="animate-spin" />
                    : <Play size={13} />}
                </button>

                {/* Delete button */}
                <button
                  onClick={() => deleteModel(m.name)}
                  disabled={deleting === m.name}
                  title="Delete model"
                  className="opacity-0 group-hover:opacity-100 text-[var(--color-text-muted)] hover:text-[var(--color-danger)] transition-all disabled:opacity-30"
                >
                  {deleting === m.name
                    ? <Loader2 size={13} className="animate-spin" />
                    : <Trash2 size={13} />}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Download progress cards ── */}
      {Object.values(downloads).length > 0 && (
        <div className="space-y-2">
          {Object.values(downloads).map((dl) => (
            <div key={dl.model}
              className="rounded-lg bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] px-4 py-3 space-y-2 animate-slide-up">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold font-mono text-[var(--color-text-primary)]">{dl.model}</span>
                <span className="text-xs text-[var(--color-text-muted)]">
                  {dl.status === "pulling" && `${dl.percent}%`}
                  {dl.status === "done" && <CheckCircle2 size={13} className="text-[var(--color-success)]" />}
                  {dl.status === "error" && <AlertCircle size={13} className="text-[var(--color-danger)]" />}
                </span>
              </div>

              {/* Progress bar */}
              {dl.status === "pulling" && (
                <div className="h-1.5 rounded-full bg-[var(--color-bg-border)] overflow-hidden">
                  <div
                    className="h-full rounded-full bg-[var(--color-brand)] transition-all duration-300"
                    style={{ width: `${dl.percent || 2}%` }}
                  />
                </div>
              )}

              <p className={`text-xs ${dl.status === "error" ? "text-[var(--color-danger)]" : "text-[var(--color-text-muted)]"}`}>
                {dl.message}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* ── Recommended models ── */}
      <div className="rounded-[var(--radius-card)] bg-[var(--color-bg-surface)] border border-[var(--color-bg-border)] p-5">
        <div className="flex items-center gap-2 mb-4">
          <Zap size={14} className="text-[var(--color-text-muted)]" />
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Recommended Models</h3>
        </div>

        <div className="space-y-1.5">
          {RECOMMENDED_MODELS.map((rm) => {
            const installed = installedNames.has(rm.name);
            const dl = downloads[rm.name];
            const isPulling = dl?.status === "pulling";

            return (
              <div key={rm.name}
                className="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-[var(--color-bg-border)] hover:border-[var(--color-brand)]/40 transition-colors"
                style={{ background: installed ? "rgba(34,197,94,0.04)" : "var(--color-bg-elevated)" }}>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold font-mono text-[var(--color-text-primary)]">{rm.name}</span>
                    <span className="text-xs text-[var(--color-text-muted)] bg-[var(--color-bg-border)] px-1.5 py-0.5 rounded">
                      {rm.size}
                    </span>
                    {installed && (
                      <span className="text-xs text-[var(--color-success)] flex items-center gap-0.5">
                        <CheckCircle2 size={10} /> installed
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-[var(--color-text-muted)] mt-0.5">{rm.desc}</p>
                </div>

                {!installed && !isPulling && (
                  <button
                    onClick={() => pullModel(rm.name)}
                    className="shrink-0 flex items-center gap-1 text-xs font-medium text-[var(--color-brand-hover)] hover:text-white bg-[var(--color-brand-dim)] hover:bg-[var(--color-brand)] px-2.5 py-1.5 rounded-lg transition-all"
                  >
                    <Download size={11} /> Pull
                  </button>
                )}
                {isPulling && (
                  <span className="text-xs text-[var(--color-text-muted)] flex items-center gap-1 shrink-0">
                    <Loader2 size={11} className="animate-spin" /> {dl.percent}%
                  </span>
                )}
                {installed && !isPulling && (
                  <button
                    onClick={() => { onModelReady?.(rm.name); }}
                    className="shrink-0 flex items-center gap-1 text-xs text-[var(--color-success)] hover:text-white bg-green-900/20 hover:bg-green-800/40 px-2.5 py-1.5 rounded-lg transition-all"
                  >
                    Use <ChevronRight size={11} />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Custom model pull ── */}
      <div className="rounded-[var(--radius-card)] bg-[var(--color-bg-surface)] border border-[var(--color-bg-border)] p-5">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)] mb-3">Pull Custom Model</h3>
        <div className="flex gap-2">
          <input
            value={customModel}
            onChange={(e) => setCustomModel(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && customModel.trim() && pullModel(customModel.trim())}
            placeholder="e.g. codellama:7b, mixtral, llava…"
            className="flex-1 bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] text-xs rounded-lg px-3 py-2 focus:outline-none focus:border-[var(--color-brand)] transition-colors"
          />
          <button
            onClick={() => customModel.trim() && pullModel(customModel.trim())}
            disabled={!customModel.trim() || downloads[customModel.trim()]?.status === "pulling"}
            className="bg-[var(--color-brand)] hover:bg-[var(--color-brand-hover)] disabled:opacity-40 text-white text-xs font-medium rounded-lg px-3 py-2 flex items-center gap-1.5 transition-colors"
          >
            <Download size={12} /> Pull
          </button>
        </div>
        <p className="text-xs text-[var(--color-text-muted)] mt-2">
          Any model name from <span className="text-[var(--color-brand-hover)]">ollama.com/library</span>
        </p>
      </div>
    </div>
  );
}