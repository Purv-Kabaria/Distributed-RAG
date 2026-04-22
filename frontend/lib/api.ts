const isBrowser = typeof window !== "undefined";

// If in browser, use the IP the user typed in the URL bar. 
// If rendering on the server inside Docker, use the internal Docker network name.
const BASE = isBrowser 
  ? `http://${window.location.hostname}:8000` 
  : "http://gateway:8000";
const DEFAULT_TIMEOUT_MS = 25000;
const DEFAULT_RETRIES = 1;

export class ApiError extends Error {
  status?: number;
  code?: string;
  details?: unknown;
  constructor(message: string, status?: number, code?: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export interface Document {
  id: string;
  original_name: string;
  file_type: string;
  status: string;
  chunk_count: number;
  chunks_indexed?: number;
  embedding_total?: number;
  embedding_done?: number;
  embedding_failed?: number;
  indexing_progress_pct?: number;
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

export interface ConnectedDevice {
  client_id: string;
  name: string;
  ip: string;
  ua: string;
  last_seen: number;
}

export interface OverviewStats {
  active_devices: number;
  devices: ConnectedDevice[];
  documents_total: number;
  documents_ingested: number;
  chunks_total: number;
  embeddings_done: number;
  embeddings_failed: number;
}

export interface WebsiteScrapeResult {
  doc_id: string;
  status: string;
  pages_scraped: number;
  source_url: string;
}

export interface ModelProvider {
  provider: string;
  models: string[];
  available: boolean;
  reason?: string;
}

export interface OllamaModelsResponse {
  models: Array<{ name: string }>;
  available: boolean;
  reason?: string;
}

function normalizeError(message: string) {
  if (message.includes("Failed to fetch")) return "Network error. Check server availability and URL.";
  if (message.includes("timed out")) return "Request timed out. Please try again.";
  if (message.length > 240) return message.slice(0, 240) + "...";
  return message;
}

async function parseError(res: Response): Promise<ApiError> {
  const raw = await res.text().catch(() => "");
  try {
    const data = raw ? JSON.parse(raw) : null;
    const msg = data?.detail || data?.message || `HTTP ${res.status}`;
    return new ApiError(normalizeError(String(msg)), res.status, data?.code, data);
  } catch {
    return new ApiError(normalizeError(raw || `HTTP ${res.status}`), res.status);
  }
}

async function req<T>(
  path: string,
  opts?: RequestInit & { timeoutMs?: number; retries?: number }
): Promise<T> {
  const timeoutMs = opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const retries = opts?.retries ?? DEFAULT_RETRIES;
  let lastError: unknown = null;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(`${BASE}${path}`, {
        ...opts,
        signal: ctrl.signal,
        headers: { "Content-Type": "application/json", ...(opts?.headers ?? {}) },
      });
      if (!res.ok) throw await parseError(res);
      return res.json();
    } catch (err: unknown) {
      lastError = err;
      const isAbort = err instanceof DOMException && err.name === "AbortError";
      if (isAbort) {
        lastError = new ApiError("Request timed out");
      }
      if (attempt === retries) break;
      await new Promise((r) => setTimeout(r, 300 * (attempt + 1)));
    } finally {
      clearTimeout(timer);
    }
  }
  if (lastError instanceof ApiError) throw lastError;
  const msg = lastError instanceof Error ? lastError.message : "Unknown request error";
  throw new ApiError(normalizeError(msg));
}

export async function uploadDocument(file: File): Promise<{ doc_id: string; status: string }> {
  const form = new FormData();
  form.append("file", file);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 60000);
  try {
    const res = await fetch(`${BASE}/api/documents/upload`, { method: "POST", body: form, signal: ctrl.signal });
    if (!res.ok) throw await parseError(res);
    return res.json();
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("Upload timed out");
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
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
  top_k = 5,
  gemini_api_key?: string
): Promise<QueryResult> {
  return req<QueryResult>("/api/query", {
    method: "POST",
    body: JSON.stringify({ question, provider, model, top_k, gemini_api_key }),
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

export async function sendClientHeartbeat(client_id: string, name: string) {
  return req<{ ok: boolean; last_seen: number }>("/api/client/heartbeat", {
    method: "POST",
    body: JSON.stringify({ client_id, name }),
  });
}

export async function getOverviewStats(): Promise<OverviewStats> {
  return req<OverviewStats>("/api/stats/overview");
}

export async function scrapeWebsite(
  url: string,
  max_pages = 25,
  single_page_only = false
): Promise<WebsiteScrapeResult> {
  return req<WebsiteScrapeResult>("/api/websites/scrape", {
    method: "POST",
    body: JSON.stringify({ url, max_pages, single_page_only }),
  });
}

export async function getModels(): Promise<{ providers: ModelProvider[] }> {
  return fetch(`${BASE}/api/models`)
    .then(async (r) => {
      if (!r.ok) throw await parseError(r);
      return r.json();
    })
    .catch(() => ({ providers: [] }));
}

export async function getOllamaInstalledModels(): Promise<OllamaModelsResponse> {
  return req<OllamaModelsResponse>("/api/ollama/models", { retries: 0, timeoutMs: 10000 });
}