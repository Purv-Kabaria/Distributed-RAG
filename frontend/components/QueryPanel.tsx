"use client";

import { useState, useEffect, useRef } from "react";
import { Send, Loader2, ChevronDown, Zap, Server, Clock, BookOpen } from "lucide-react";
import { getOllamaInstalledModels, queryRAG, QueryResult } from "@/lib/api";

const GROQ_MODELS = [
  "llama-3.3-70b-versatile",
  "llama-3.1-8b-instant",
  "mixtral-8x7b-32768",
  "gemma2-9b-it",
];
const GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro"];

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  result?: QueryResult;
}

const CHAT_STORAGE_KEY = "dc.chat.history.v1";
const CHAT_PERSIST_KEY = "dc.chat.persist.v1";
const DEFAULT_OLLAMA_MODEL = "gemma3:4b";

function escapeHtml(input: string): string {
  return input
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderMarkdown(input: string): string {
  const raw = (input ?? "").replace(/\r\n/g, "\n");
  const escaped = escapeHtml(raw);
  const codeBlocks: string[] = [];
  let body = escaped.replace(/```([\s\S]*?)```/g, (_, code) => {
    const token = `@@CODE_BLOCK_${codeBlocks.length}@@`;
    codeBlocks.push(`<pre><code>${String(code).trim()}</code></pre>`);
    return token;
  });

  const inline = (text: string) =>
    text
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>");

  const blocks = body.split(/\n{2,}/);
  const rendered = blocks.map((block) => {
    const trimmed = block.trim();
    if (!trimmed) return "";
    if (/^@@CODE_BLOCK_\d+@@$/.test(trimmed)) return trimmed;
    const lines = trimmed.split("\n");
    if (lines.every((line) => /^\s*[-*]\s+.+$/.test(line))) {
      const items = lines.map((line) => `<li>${inline(line.replace(/^\s*[-*]\s+/, ""))}</li>`).join("");
      return `<ul>${items}</ul>`;
    }
    if (lines.every((line) => /^\s*\d+\.\s+.+$/.test(line))) {
      const items = lines.map((line) => `<li>${inline(line.replace(/^\s*\d+\.\s+/, ""))}</li>`).join("");
      return `<ol>${items}</ol>`;
    }
    if (lines.length === 1) {
      const hm = lines[0].match(/^(#{1,6})\s+(.+)$/);
      if (hm) {
        const lvl = hm[1].length;
        return `<h${lvl}>${inline(hm[2])}</h${lvl}>`;
      }
    }
    return `<p>${lines.map((line) => inline(line)).join("<br/>")}</p>`;
  });

  body = rendered.join("");
  codeBlocks.forEach((code, idx) => {
    body = body.replace(`@@CODE_BLOCK_${idx}@@`, code);
  });
  return body;
}

export default function QueryPanel({
  preselectedOllamaModel,
}: {
  preselectedOllamaModel?: string;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [keepHistory, setKeepHistory] = useState(true);
  const [provider, setProvider] = useState<"groq" | "ollama" | "gemini">("groq");
  const [model, setModel] = useState("llama-3.3-70b-versatile");
  const [geminiModel, setGeminiModel] = useState("gemini-2.5-flash");
  const [geminiApiKey, setGeminiApiKey] = useState("");
  const [topK, setTopK] = useState(5);
  const [error, setError] = useState<string | null>(null);
  const [lastFailedPrompt, setLastFailedPrompt] = useState<string | null>(null);
  const [ollamaModel, setOllamaModel] = useState("");
  const [ollamaModels, setOllamaModels] = useState<string[]>([]);
  const [ollamaLoading, setOllamaLoading] = useState(false);
  const [ollamaUnavailableReason, setOllamaUnavailableReason] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // When parent passes a selected Ollama model, switch to it
  useEffect(() => {
    if (preselectedOllamaModel) {
      setProvider("ollama");
      setOllamaModel(preselectedOllamaModel);
    }
  }, [preselectedOllamaModel]);

  useEffect(() => {
    let active = true;
    const loadOllamaModels = async () => {
      setOllamaLoading(true);
      try {
        const data = await getOllamaInstalledModels();
        if (!active) return;
        if (!data.available) {
          setOllamaModels([]);
          setOllamaUnavailableReason(data.reason || "Ollama is not reachable");
          return;
        }
        const names = (data.models || [])
          .map((m) => m?.name)
          .filter((v): v is string => typeof v === "string" && v.length > 0);
        setOllamaModels(names);
        setOllamaUnavailableReason(null);
        if (names.length > 0) {
          setOllamaModel((prev) => {
            if (names.includes(prev)) return prev;
            if (names.includes(DEFAULT_OLLAMA_MODEL)) return DEFAULT_OLLAMA_MODEL;
            return names[0];
          });
        } else {
          setOllamaModel("");
        }
      } catch (e: unknown) {
        if (!active) return;
        const msg = e instanceof Error ? e.message : "Failed to load Ollama models";
        setOllamaModels([]);
        setOllamaUnavailableReason(msg);
      } finally {
        if (active) setOllamaLoading(false);
      }
    };
    loadOllamaModels();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    try {
      const persistRaw = window.localStorage.getItem(CHAT_PERSIST_KEY);
      if (persistRaw === "0") {
        setKeepHistory(false);
        return;
      }
      const raw = window.localStorage.getItem(CHAT_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Message[];
      if (!Array.isArray(parsed)) return;
      setMessages(parsed.slice(-200));
    } catch {}
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(CHAT_PERSIST_KEY, keepHistory ? "1" : "0");
      if (keepHistory) {
        window.localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(messages.slice(-200)));
      }
    } catch {}
  }, [messages, keepHistory]);

  function startNewChat() {
    setMessages([]);
    setError(null);
    if (typeof window !== "undefined") {
      try {
        window.localStorage.removeItem(CHAT_STORAGE_KEY);
      } catch {}
    }
  }

  function toggleKeepHistory(value: boolean) {
    setKeepHistory(value);
    if (!value && typeof window !== "undefined") {
      try {
        window.localStorage.removeItem(CHAT_STORAGE_KEY);
      } catch {}
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const q = input.trim();
    if (!q || loading) return;
    if (provider === "ollama" && !ollamaModel) {
      setError("No installed Ollama model available. Install one in the Ollama tab.");
      return;
    }
    setInput("");
    setError(null);

    const userMsg: Message = { id: Math.random().toString(36).slice(2), role: "user", content: q };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      const activeModel = provider === "groq" ? model : provider === "gemini" ? geminiModel : ollamaModel;
      const result = await queryRAG(q, provider, activeModel, topK, provider === "gemini" ? geminiApiKey.trim() : undefined);
      const assistantMsg: Message = {
        id: Math.random().toString(36).slice(2),
        role: "assistant",
        content: result.answer,
        result,
      };
      setMessages((prev) => [...prev, assistantMsg]);
      setLastFailedPrompt(null);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Query failed";
      setError(msg);
      setLastFailedPrompt(q);
      setMessages((prev) => prev.filter((m) => m.id !== userMsg.id));
      setInput(q);
    } finally {
      setLoading(false);
    }
  }

  async function retryLastFailed() {
    if (!lastFailedPrompt || loading) return;
    const q = lastFailedPrompt.trim();
    if (!q) return;
    if (provider === "ollama" && !ollamaModel) {
      setError("No installed Ollama model available. Install one in the Ollama tab.");
      return;
    }
    setInput("");
    setError(null);
    const userMsg: Message = { id: Math.random().toString(36).slice(2), role: "user", content: q };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);
    try {
      const activeModel = provider === "groq" ? model : provider === "gemini" ? geminiModel : ollamaModel;
      const result = await queryRAG(q, provider, activeModel, topK, provider === "gemini" ? geminiApiKey.trim() : undefined);
      const assistantMsg: Message = {
        id: Math.random().toString(36).slice(2),
        role: "assistant",
        content: result.answer,
        result,
      };
      setMessages((prev) => [...prev, assistantMsg]);
      setLastFailedPrompt(null);
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
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      {/* Settings bar */}
      <div className="flex flex-wrap gap-2 p-3 border-b border-[var(--color-bg-border)] bg-[var(--color-bg-surface)]/60 backdrop-blur-sm">
        {/* Provider toggle */}
        <div className="flex rounded-lg overflow-hidden border border-[var(--color-bg-border)] text-xs">
          {(["groq", "gemini", "ollama"] as const).map((p) => (
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
                : p === "gemini"
                  ? <><Zap size={11} className="inline mr-1" />Gemini</>
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
        ) : provider === "gemini" ? (
          <div className="flex items-center gap-2">
            <div className="relative">
              <select
                value={geminiModel}
                onChange={(e) => setGeminiModel(e.target.value)}
                className="appearance-none text-xs bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] text-[var(--color-text-secondary)] rounded-lg px-3 py-1.5 pr-7 focus:outline-none focus:border-[var(--color-brand)] cursor-pointer min-w-40"
              >
                {GEMINI_MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
              <ChevronDown size={11} className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-[var(--color-text-muted)]" />
            </div>
            <input
              type="password"
              value={geminiApiKey}
              onChange={(e) => setGeminiApiKey(e.target.value)}
              placeholder="Gemini API key (optional)"
              className="text-xs bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] text-[var(--color-text-secondary)] placeholder-[var(--color-text-muted)] rounded-lg px-3 py-1.5 focus:outline-none focus:border-[var(--color-brand)] min-w-56"
            />
            <span className="text-xs text-[var(--color-text-muted)]">
              Uses this key if provided; otherwise uses .env key
            </span>
          </div>
        ) : (
          <div className="flex items-center gap-1.5">
            <div className="relative">
              <select
                value={ollamaModel}
                onChange={(e) => setOllamaModel(e.target.value)}
                disabled={ollamaLoading || ollamaModels.length === 0}
                className="appearance-none text-xs bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] text-[var(--color-text-secondary)] rounded-lg px-3 py-1.5 pr-7 focus:outline-none focus:border-[var(--color-brand)] cursor-pointer disabled:opacity-40 min-w-44"
              >
                {ollamaLoading && <option>Loading models...</option>}
                {!ollamaLoading && ollamaModels.length === 0 && <option>No installed models</option>}
                {ollamaModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              <ChevronDown size={11} className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none text-[var(--color-text-muted)]" />
            </div>
            <span className="text-xs text-[var(--color-text-muted)]">
              {ollamaUnavailableReason
                ? ollamaUnavailableReason
                : "(installed models only, manage in Ollama tab)"}
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
        <button
          type="button"
          onClick={startNewChat}
          className="text-xs bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] text-[var(--color-text-secondary)] rounded px-2.5 py-1 hover:text-[var(--color-text-primary)] hover:border-[var(--color-brand)]/40"
        >
          New Chat
        </button>
        <label className="text-xs text-[var(--color-text-muted)] flex items-center gap-1.5 px-1">
          <input
            type="checkbox"
            checked={keepHistory}
            onChange={(e) => toggleKeepHistory(e.target.checked)}
            className="accent-[var(--color-brand)]"
          />
          Keep history
        </label>
      </div>

      {/* Messages */}
      <div className="flex-1 min-h-0 h-0 overflow-y-auto p-4 space-y-4">
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
              <div className="max-w-[80%] bg-gradient-to-br from-[var(--color-brand)] to-[#4f46e5] text-white rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm shadow-[var(--shadow-glow)]">
                {msg.content}
              </div>
            ) : (
              <div className="max-w-[92%] space-y-2">
                <div className="bg-[var(--color-bg-elevated)]/85 border border-[var(--color-bg-border)] rounded-2xl rounded-tl-sm px-4 py-3 text-sm text-[var(--color-text-primary)] leading-relaxed whitespace-pre-wrap shadow-[var(--shadow-soft)]">
                  <div
                    className="[&>p]:mb-3 [&>p:last-child]:mb-0 [&>ul]:mb-3 [&>ol]:mb-3 [&>ul]:list-disc [&>ol]:list-decimal [&>ul]:pl-5 [&>ol]:pl-5 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-[#10131a] [&_pre]:border [&_pre]:border-[var(--color-bg-border)] [&_pre]:p-3 [&_pre]:my-3 [&_code]:font-mono [&_code]:text-[0.92em] [&_a]:text-[var(--color-brand-hover)] [&_a]:underline"
                    dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                  />
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
          <div className="space-y-2 animate-slide-up">
            <div className="text-xs text-[var(--color-danger)] bg-[#1e0808] border border-red-900/40 rounded-lg px-3 py-2">
              {error}
            </div>
            {lastFailedPrompt && (
              <button
                type="button"
                onClick={retryLastFailed}
                disabled={loading}
                className="text-xs text-[var(--color-brand-hover)] hover:text-white disabled:opacity-40"
              >
                Retry last question
              </button>
            )}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="p-3 border-t border-[var(--color-bg-border)] bg-[var(--color-bg-surface)]/35">
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
            disabled={loading || !input.trim() || (provider === "ollama" && !ollamaModel)}
            className="bg-[var(--color-brand)] hover:bg-[var(--color-brand-hover)] disabled:opacity-40 text-white rounded-xl px-4 py-2.5 transition-colors flex items-center gap-2"
          >
            {loading ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
          </button>
        </div>
        <div className="mt-1 text-[11px] text-[var(--color-text-muted)] text-right">{input.length}/6000</div>
      </form>
    </div>
  );
}