"use client";

import { useState } from "react";
import { Network, HardDrive, MessageSquare, LayoutDashboard, Server } from "lucide-react";
import UploadPanel from "@/components/UploadPanel";
import DocumentList from "@/components/DocumentList";
import QueryPanel from "@/components/QueryPanel";
import SystemMonitor from "@/components/SystemMonitor";
import ArchDiagram from "@/components/ArchDiagram";
import OllamaManager from "@/components/OllamaManager";

type Tab = "query" | "docs" | "ollama" | "arch";

export default function HomePage() {
  const [activeTab, setActiveTab] = useState<Tab>("query");
  const [refreshSignal, setRefreshSignal] = useState(0);
  // When user clicks "Use" on an installed model, pre-select it in the query panel
  const [selectedOllamaModel, setSelectedOllamaModel] = useState<string>("");

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "query",  label: "Query",     icon: <MessageSquare size={14} /> },
    { id: "docs",   label: "Documents", icon: <HardDrive size={14} /> },
    { id: "ollama", label: "Ollama",    icon: <Server size={14} /> },
    { id: "arch",   label: "System",    icon: <LayoutDashboard size={14} /> },
  ];

  function handleModelReady(model: string) {
    setSelectedOllamaModel(model);
    setActiveTab("query");
  }

  return (
    <div className="flex flex-col h-screen bg-[var(--color-bg-base)]">
      {/* ── Header ── */}
      <header className="flex items-center justify-between px-6 py-3 border-b border-[var(--color-bg-border)] bg-[var(--color-bg-surface)] shrink-0">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-[var(--color-brand)] flex items-center justify-center">
            <Network size={15} className="text-white" />
          </div>
          <div>
            <h1 className="text-sm font-bold text-[var(--color-text-primary)] leading-none">
              Distributed RAG Core
            </h1>
            <p className="text-xs text-[var(--color-text-muted)] leading-none mt-0.5">
              5-component distributed system · Gemini Embedding 2
            </p>
          </div>
        </div>

        <nav className="flex gap-1 bg-[var(--color-bg-elevated)] rounded-xl p-1 border border-[var(--color-bg-border)]">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                activeTab === t.id
                  ? "bg-[var(--color-brand)] text-white shadow"
                  : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
              }`}
            >
              {t.icon}{t.label}
            </button>
          ))}
        </nav>
      </header>

      {/* ── Body ── */}
      <div className="flex-1 min-h-0 overflow-hidden">

        {/* Query Tab */}
        {activeTab === "query" && (
          <div className="h-full grid grid-cols-[320px_1fr] divide-x divide-[var(--color-bg-border)]">
            {/* Left: upload + monitor */}
            <div className="flex flex-col h-full overflow-hidden bg-[var(--color-bg-surface)]">
              <div className="p-4 border-b border-[var(--color-bg-border)]">
                <h2 className="text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)] mb-3">
                  Upload Documents
                </h2>
                <UploadPanel onUploaded={() => setRefreshSignal((s) => s + 1)} />
              </div>
              <div className="flex-1 overflow-y-auto p-4 space-y-4">
                <SystemMonitor />
              </div>
            </div>

            {/* Right: chat */}
            <div className="flex flex-col h-full bg-[var(--color-bg-base)]">
              <QueryPanel preselectedOllamaModel={selectedOllamaModel} />
            </div>
          </div>
        )}

        {/* Documents Tab */}
        {activeTab === "docs" && (
          <div className="h-full overflow-y-auto p-6">
            <div className="max-w-3xl mx-auto space-y-6">
              <div className="rounded-[var(--radius-card)] bg-[var(--color-bg-surface)] border border-[var(--color-bg-border)] p-5">
                <h2 className="text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)] mb-4">
                  Upload
                </h2>
                <UploadPanel onUploaded={() => setRefreshSignal((s) => s + 1)} />
              </div>
              <div className="rounded-[var(--radius-card)] bg-[var(--color-bg-surface)] border border-[var(--color-bg-border)] p-5">
                <h2 className="text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)] mb-4">
                  Document Library
                </h2>
                <DocumentList refreshSignal={refreshSignal} />
              </div>
            </div>
          </div>
        )}

        {/* Ollama Tab */}
        {activeTab === "ollama" && (
          <div className="h-full overflow-y-auto p-6">
            <div className="max-w-2xl mx-auto space-y-4">
              <div>
                <h2 className="text-base font-bold text-[var(--color-text-primary)]">Ollama Model Manager</h2>
                <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
                  Download, test, and manage local LLM models. No GPU required for smaller models.
                </p>
              </div>
              <OllamaManager onModelReady={handleModelReady} />
            </div>
          </div>
        )}

        {/* System Tab */}
        {activeTab === "arch" && (
          <div className="h-full overflow-y-auto p-6">
            <div className="max-w-3xl mx-auto space-y-6">
              <ArchDiagram />
              <SystemMonitor />

              <div className="rounded-[var(--radius-card)] bg-[var(--color-bg-surface)] border border-[var(--color-bg-border)] p-5 space-y-4">
                <h3 className="text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)]">
                  Component Reference
                </h3>
                {[
                  { n: "1. API Gateway",        port: ":8000", color: "#8b5cf6", desc: "Single client entry point. Routes uploads, proxies queries, rate limits, serves status. Also proxies Ollama API so the browser never needs direct access." },
                  { n: "2. Ingestion Worker",   port: ":8001", color: "#0ea5e9", desc: "Consumes Redis ingestion queue. Extracts text (PDF/text), passes multimodal files as-is, chunks text, pushes embedding jobs." },
                  { n: "3. Embedding Worker",   port: ":8002", color: "#f59e0b", desc: "COMPUTE-INTENSIVE. Calls Gemini Embedding 2 (text + multimodal image/audio/video). Horizontally scalable via WORKER_CONCURRENCY." },
                  { n: "4. Vector Store",       port: ":8003", color: "#22c55e", desc: "Sole owner of Qdrant. Upsert vectors, cosine similarity search, Redis caching for hot queries." },
                  { n: "5. Query Service",      port: ":8004", color: "#ec4899", desc: "RAG pipeline: embed query → retrieve chunks → call Groq API or Ollama LLM → return answer + sources." },
                ].map((c) => (
                  <div key={c.n} className="flex gap-3">
                    <div className="w-1 rounded-full shrink-0" style={{ background: c.color }} />
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold text-[var(--color-text-primary)]">{c.n}</span>
                        <code className="text-xs font-mono text-[var(--color-text-muted)] bg-[var(--color-bg-elevated)] px-1.5 py-0.5 rounded">{c.port}</code>
                      </div>
                      <p className="text-xs text-[var(--color-text-secondary)] mt-0.5 leading-relaxed">{c.desc}</p>
                    </div>
                  </div>
                ))}
              </div>

              <div className="rounded-[var(--radius-card)] bg-[var(--color-bg-surface)] border border-[var(--color-bg-border)] p-5">
                <h3 className="text-xs font-semibold uppercase tracking-widest text-[var(--color-text-muted)] mb-3">Data Flow</h3>
                <div className="space-y-2">
                  {[
                    "① Upload → Gateway saves file → Redis LPUSH ingestion:queue",
                    "② Ingestion Worker BRPOP → extract/chunk → Redis LPUSH embedding:queue",
                    "③ Embedding Worker BRPOP → Gemini Embedding 2 API → Vector Store upsert → Qdrant",
                    "④ Query → Gateway → Query Service → Embedding Worker (embed question) → Vector Store (cosine search) → Groq/Ollama LLM → response",
                  ].map((step) => (
                    <p key={step} className="text-xs font-mono text-[var(--color-text-secondary)] leading-relaxed">{step}</p>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}