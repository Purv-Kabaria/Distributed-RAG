"use client";

import { useState, useEffect, useRef } from "react";
import { Send, Loader2, ChevronDown, Zap, Server, Clock, BookOpen } from "lucide-react";
import { queryRAG, QueryResult } from "@/lib/api";

const GROQ_MODELS = [
  "llama-3.3-70b-versatile",
  "llama-3.1-8b-instant",
  "mixtral-8x7b-32768",
  "gemma2-9b-it",
];

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  result?: QueryResult;
}

export default function QueryPanel({
  preselectedOllamaModel,
}: {
  preselectedOllamaModel?: string;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [provider, setProvider] = useState<"groq" | "ollama">("groq");
  const [model, setModel] = useState("llama-3.3-70b-versatile");
  const [topK, setTopK] = useState(5);
  const [error, setError] = useState<string | null>(null);
  const [ollamaModel, setOllamaModel] = useState("gemma3:4b");
  const bottomRef = useRef<HTMLDivElement>(null);

  // When parent passes a selected Ollama model, switch to it
  useEffect(() => {
    if (preselectedOllamaModel) {
      setProvider("ollama");
      setOllamaModel(preselectedOllamaModel);
    }
  }, [preselectedOllamaModel]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const q = input.trim();
    if (!q || loading) return;
    setInput("");
    setError(null);

    const userMsg: Message = { id: Math.random().toString(36).slice(2), role: "user", content: q };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      const activeModel = provider === "groq" ? model : ollamaModel;
      const result = await queryRAG(q, provider, activeModel, topK);
      const assistantMsg: Message = {
        id: Math.random().toString(36).slice(2),
        role: "assistant",
        content: result.answer,
        result,
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Query failed";
      setError(msg);
      setMessages((prev) => prev.filter((m) => m.id !== userMsg.id));
      setInput(q);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Settings bar */}
      <div className="flex flex-wrap gap-2 p-3 border-b border-[var(--color-bg-border)] bg-[var(--color-bg-surface)]">
        {/* Provider toggle */}
        <div className="flex rounded-lg overflow-hidden border border-[var(--color-bg-border)] text-xs">
          {(["groq", "ollama"] as const).map((p) => (
            <button
              key={p}
              onClick={() => setProvider(p)}
              className={`px-3 py-1.5 font-medium transition-colors ${
                provider === p
                  ? "bg-[var(--color-brand)] text-white"
                  : "bg-[var(--color-bg-elevated)] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
              }`}
            >
              {p === "groq"
                ? <><Zap size={11} className="inline mr-1" />Groq</>
                : <><Server size={11} className="inline mr-1" />Ollama</>}
            </button>
          ))}
        </div>

        {/* Model selector */}
        {provider === "groq" ? (
          <div className="relative">
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="appearance-none text-xs bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] text-[var(--color-text-secondary)] rounded-lg px-3 py-1.5 pr-7 focus:outline-none focus:border-[var(--color-brand)] cursor-pointer"
            >
              {GROQ_MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            <ChevronDown size={11} className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-[var(--color-text-muted)]" />
          </div>
        ) : (
          <div className="flex items-center gap-1.5">
            <input
              value={ollamaModel}
              onChange={(e) => setOllamaModel(e.target.value)}
              placeholder="e.g. gemma3:4b"
              className="text-xs bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] text-[var(--color-text-secondary)] rounded-lg px-3 py-1.5 focus:outline-none focus:border-[var(--color-brand)] w-36"
            />
            <span className="text-xs text-[var(--color-text-muted)]">
              (manage in <span className="text-[var(--color-brand-hover)]">Ollama tab</span>)
            </span>
          </div>
        )}

        {/* Top-K */}
        <div className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
          <BookOpen size={12} />
          <span>Top</span>
          <select
            value={topK}
            onChange={(e) => setTopK(Number(e.target.value))}
            className="appearance-none bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] text-[var(--color-text-secondary)] rounded px-2 py-1 focus:outline-none focus:border-[var(--color-brand)] cursor-pointer"
          >
            {[3, 5, 8, 10].map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 min-h-0">
        {messages.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center h-full text-center text-[var(--color-text-muted)] space-y-2">
            <Send size={32} className="opacity-20" />
            <p className="text-sm">Ask anything about your uploaded documents.</p>
            <p className="text-xs opacity-60">Upload docs on the left, then query here.</p>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"} animate-slide-up`}>
            {msg.role === "user" ? (
              <div className="max-w-[80%] bg-[var(--color-brand)] text-white rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm">
                {msg.content}
              </div>
            ) : (
              <div className="max-w-[92%] space-y-2">
                <div className="bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] rounded-2xl rounded-tl-sm px-4 py-3 text-sm text-[var(--color-text-primary)] leading-relaxed whitespace-pre-wrap">
                  {msg.content}
                </div>
                {msg.result && (
                  <div className="flex flex-wrap gap-2 text-xs text-[var(--color-text-muted)]">
                    <span className="flex items-center gap-1"><Clock size={10} />{msg.result.duration_ms}ms</span>
                    <span>·</span>
                    <span>{msg.result.provider} / {msg.result.model}</span>
                    <span>·</span>
                    <span>{msg.result.chunks.length} chunks used</span>
                  </div>
                )}
                {msg.result && msg.result.chunks.length > 0 && (
                  <details className="group">
                    <summary className="cursor-pointer text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] list-none flex items-center gap-1">
                      <ChevronDown size={11} className="group-open:rotate-180 transition-transform" />
                      View {msg.result.chunks.length} source chunk{msg.result.chunks.length !== 1 ? "s" : ""}
                    </summary>
                    <div className="mt-2 space-y-2">
                      {msg.result.chunks.map((c, i) => (
                        <div key={c.chunk_id} className="rounded-lg bg-[var(--color-bg-surface)] border border-[var(--color-bg-border)] px-3 py-2">
                          <div className="flex flex-wrap justify-between gap-1 mb-1">
                            <span className="text-xs text-[var(--color-text-muted)]">Source {i + 1}</span>
                            <div className="flex flex-wrap items-center gap-2">
                              {c.time_start_sec != null && c.time_end_sec != null && (
                                <span className="text-xs text-[var(--color-brand-hover)] tabular-nums">
                                  {c.time_start_sec.toFixed(1)}s–{c.time_end_sec.toFixed(1)}s
                                </span>
                              )}
                              {c.chunk_kind && (
                                <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">{c.chunk_kind}</span>
                              )}
                              <span className="text-xs text-[var(--color-brand-hover)]">score {c.score.toFixed(3)}</span>
                            </div>
                          </div>
                          <p className="text-xs text-[var(--color-text-secondary)] line-clamp-3 font-mono">{c.text}</p>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div className="flex justify-start animate-slide-up">
            <div className="bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] rounded-2xl rounded-tl-sm px-4 py-3">
              <Loader2 size={16} className="animate-spin text-[var(--color-brand-hover)]" />
            </div>
          </div>
        )}

        {error && (
          <div className="text-xs text-[var(--color-danger)] bg-[#1e0808] border border-red-900/40 rounded-lg px-3 py-2 animate-slide-up">
            {error}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="p-3 border-t border-[var(--color-bg-border)]">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask a question about your documents…"
            disabled={loading}
            className="flex-1 bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] text-sm rounded-xl px-4 py-2.5 focus:outline-none focus:border-[var(--color-brand)] focus:shadow-[var(--shadow-glow)] transition-all disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="bg-[var(--color-brand)] hover:bg-[var(--color-brand-hover)] disabled:opacity-40 text-white rounded-xl px-4 py-2.5 transition-colors flex items-center gap-2"
          >
            {loading ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
          </button>
        </div>
      </form>
    </div>
  );
}