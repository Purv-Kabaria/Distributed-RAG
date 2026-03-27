const BASE = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8000";

export interface Document {
  id: string;
  original_name: string;
  file_type: string;
  status: string;
  chunk_count: number;
  error_msg?: string;
  created_at: string;
}

export interface SourceChunk {
  chunk_id: string;
  doc_id: string;
  text: string;
  score: number;
  time_start_sec?: number;
  time_end_sec?: number;
  chunk_kind?: string;
  file_type?: string;
}

export interface QueryResult {
  question: string;
  answer: string;
  chunks: SourceChunk[];
  model: string;
  provider: string;
  duration_ms: number;
}

export interface SystemStatus {
  ingestion: { status: string };
  query: { status: string };
  redis: { status: string };
}

export interface QueueStats {
  ingestion_queue: number;
  embedding_queue: number;
}

export interface ModelProvider {
  provider: string;
  models: string[];
  available: boolean;
  reason?: string;
}

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts?.headers ?? {}) },
  });
  if (!res.ok) {
    const msg = await res.text().catch(() => res.statusText);
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return res.json();
}

// ── Documents ─────────────────────────────────────────────────────────────────

export async function uploadDocument(file: File): Promise<{ doc_id: string; status: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/api/documents/upload`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getDocuments(): Promise<Document[]> {
  return req<Document[]>("/api/documents");
}

export async function getDocumentStatus(id: string): Promise<Document> {
  return req<Document>(`/api/documents/${id}/status`);
}

export async function deleteDocument(id: string): Promise<void> {
  await req(`/api/documents/${id}`, { method: "DELETE" });
}

// ── Query ─────────────────────────────────────────────────────────────────────

export async function queryRAG(
  question: string,
  provider: string,
  model: string,
  top_k = 5
): Promise<QueryResult> {
  return req<QueryResult>("/api/query", {
    method: "POST",
    body: JSON.stringify({ question, provider, model, top_k }),
  });
}

export async function getQueryHistory() {
  return req<QueryResult[]>("/api/query/history");
}

// ── System ────────────────────────────────────────────────────────────────────

export async function getSystemStatus(): Promise<SystemStatus> {
  return req<SystemStatus>("/api/system/status");
}

export async function getQueueStats(): Promise<QueueStats> {
  return req<QueueStats>("/api/stats/queue");
}

export async function getModels(): Promise<{ providers: ModelProvider[] }> {
  return req<{ providers: ModelProvider[] }>("http://query-service:8004/api/models").catch(() =>
    fetch(`${BASE.replace("8000", "8004")}/api/models`)
      .then((r) => r.json())
      .catch(() => ({ providers: [] }))
  );
}
