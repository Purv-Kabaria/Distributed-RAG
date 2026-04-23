# Distributed RAG Core - Setup and Operations

This document is a code-accurate setup and operations guide for the current repository.

---

## 1) System Overview

This project is a distributed RAG system with five backend components:

1. `gateway` - API entrypoint, uploads, website scraping, query proxy, ollama proxy, metrics.
2. `ingestion-worker` - extraction/chunking for text/pdf/image/audio/video.
3. `embedding-worker` - Gemini embeddings + vector upsert orchestration.
4. `vector-store` - sole Qdrant access service.
5. `query-service` - query-time retrieve + prompt + LLM response.

Shared infrastructure:

- `redis` - async queues + cache + client heartbeat state.
- `postgres` - metadata state.
- `qdrant` - vector database.
- `frontend` - UI only (not counted as one of the five components).

---

## 2) End-to-End Data Flow

### Upload flow

`frontend -> gateway -> ingestion:queue -> ingestion-worker -> embedding:queue -> embedding-worker -> vector-store -> qdrant`

### Website scrape flow

`frontend (Scrape tab) -> gateway /api/websites/scrape -> generated text doc -> ingestion:queue -> ... same embedding pipeline`

### Query flow

`frontend -> gateway /api/query -> query-service -> embedding-worker (/api/embed/text) + vector-store (/api/vectors/search) -> groq/ollama/gemini`

---

## 3) Runtime Responsibilities

### `gateway`

- Upload/list/status/delete documents.
- Query proxy.
- Ollama model list/pull/delete/test proxy.
- Website scraping endpoint (`/api/websites/scrape`).
- Aggregated health/queue/overview metrics.
- Client heartbeat intake for multi-device visibility.
- Returns per-document chunking/indexing progress counters in document list/status APIs.

### `ingestion-worker`

- Consumes `ingestion:queue`.
- Extracts content by file type:
  - text, pdf
  - image (OCR + vision caption)
  - audio/video (Whisper timestamps + optional vision frames)
- Whisper model is preloaded into the Docker image and loaded locally in container runtime (`WHISPER_LOCAL_ONLY=1` by default).
- Stores chunks in Postgres.
- Pushes embedding jobs to `embedding:queue`.
- Supports concurrent consumers (`INGESTION_CONCURRENCY`).
- Retries jobs when file path is temporarily not accessible (`INGESTION_FILE_RETRY_*`).

### `embedding-worker`

- Consumes `embedding:queue`.
- Embeds chunks with Gemini embedding model.
- Upserts vectors through `vector-store`.
- Tracks `embedding_jobs` and document completion.
- Provides query-time embedding endpoint (`/api/embed/text`).
- Separates query-time embed concurrency (`QUERY_EMBED_CONCURRENCY`) from background worker concurrency (`WORKER_CONCURRENCY`).

### `vector-store`

- Owns Qdrant collection lifecycle and search/upsert/delete APIs.
- Caches searches in Redis.
- Uses deterministic point IDs derived from `chunk_id` digest.

### `query-service`

- Validates query inputs (`QUESTION_MAX_CHARS`, `TOP_K_MIN/MAX`).
- Embeds query text via embedding-worker.
- Retrieves context via vector-store.
- Calls Groq, Ollama, or Gemini.
- Logs query metadata to Postgres.
- Prompt enforces English output, strict grounding, Markdown output, and no direct source citations in responses.

---

## 4) Frontend Capabilities

Tabs:

- Query
- Documents
- Scrape
- Ollama
- System

Notable behaviors:

- Query supports persistent chat, new chat, markdown answer rendering.
- Ollama model selection in Query is dropdown-only from installed models.
- Gemini is available as a Query provider with optional per-request API key override (falls back to `GEMINI_API_KEY` from env).
- System shows queue depth, service health, active device count, ingestion/embedding metrics.
- Documents tab shows live per-document chunking and indexing progress.
- Scrape tab ingests same-domain website content into the existing pipeline.

---

## 5) Data Model

Schema source: `shared/init.sql`

Core tables:

- `documents`
- `chunks` (`extra` JSONB for timestamps/chunk kinds)
- `embedding_jobs`
- `query_logs`
- `api_keys` (present, currently unused)

Document lifecycle:

`queued -> extracting -> chunking -> embedding -> done`  
failure path -> `failed` with `error_msg`

---

## 6) Prerequisites (Windows)

Install:

1. Docker Desktop (WSL2 backend recommended)
2. Git for Windows
3. PowerShell 7 / Windows Terminal

If using Ollama:

- Install Ollama on local or separate LAN machine.
- Pull required models, e.g.:
  - `ollama pull llama3.2`
  - `ollama pull llava`

---

## 7) Environment Configuration

Create `.env`:

```powershell
Copy-Item .env.example .env
```

Required:

```env
GEMINI_API_KEY=...
```

Common optional:

```env
PUBLIC_HOST=192.168.1.50
GROQ_API_KEY=...
OLLAMA_URL=http://host.docker.internal:11434
```

Key tunables:

- Gateway/query/vector robustness and timeouts (`*_TIMEOUT_SEC`, retries, pool sizes).
- Query limits (`QUESTION_MAX_CHARS`, `TOP_K_MIN/MAX`).
- Worker scaling:
  - `INGESTION_CONCURRENCY`
  - `WORKER_CONCURRENCY`
  - `QUERY_EMBED_CONCURRENCY`
- Worker file accessibility retry:
  - `INGESTION_FILE_RETRY_MAX`
  - `INGESTION_FILE_RETRY_DELAY_SEC`

- Whisper local model controls:
  - `WHISPER_MODEL_SIZE`
  - `WHISPER_MODEL_PATH`
  - `WHISPER_DOWNLOAD_ROOT`
  - `WHISPER_LOCAL_ONLY`

See `.env.example` for full list and defaults.

---

## 8) Main Stack Run

Start everything:

```powershell
docker compose up -d --build
```

Check status:

```powershell
docker compose ps
docker compose logs -f
```

Health URLs:

- `http://localhost:8000/health` (gateway)
- `http://localhost:3000` (frontend)

---

## 9) Multi-Device Access (LAN)

Recommended topology:

- Machine A: main Docker stack host.
- Machine B (optional): Ollama host.
- Machine C..N: browser clients.

### A) Discover IP

```powershell
ipconfig
```

Use active LAN/Wi-Fi IPv4.

### B) Open firewall ports

On main host:

```powershell
New-NetFirewallRule -DisplayName "RAG Frontend 3000" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow
New-NetFirewallRule -DisplayName "RAG Gateway 8000"  -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

If remote workers are used against central infra, also allow:

- `5432` (Postgres)
- `6379` (Redis)
- `8003` (vector-store)

If Ollama is separate host:

```powershell
New-NetFirewallRule -DisplayName "Ollama 11434" -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow
```

### C) Client access

Open from any device:

`http://<docker-host-ip>:3000`

---

## 10) Remote Worker Mode (`docker-compose.worker.yml`)

Use this file when running extra ingestion/embedding workers on another machine.

### Critical requirements

1. `PUBLIC_HOST` must point to central stack host IP.
2. Ingestion workers must access the same uploads path:
   - set `SHARED_UPLOADS_DIR` to a shared/network path mirrored at `/uploads`.
3. Workers are intended for ingestion + embedding only.
4. Remote ingestion worker images preload Whisper model at build time using `WHISPER_MODEL_SIZE`.

### Run remote workers

```powershell
docker compose -f docker-compose.worker.yml up -d --build
```

### Worker-only tuning

- `INGESTION_CONCURRENCY`
- `WORKER_CONCURRENCY`
- `QUERY_EMBED_CONCURRENCY`
- `INGESTION_QUEUE_POP_TIMEOUT_SEC`
- `EMBEDDING_QUEUE_POP_TIMEOUT_SEC`
- `WHISPER_MODEL_SIZE`
- `WHISPER_MODEL_PATH`
- `WHISPER_DOWNLOAD_ROOT`
- `WHISPER_LOCAL_ONLY`

---

## 11) High-Value Endpoints

Gateway:

- `POST /api/documents/upload`
- `GET /api/documents`
- `GET /api/documents/{doc_id}/status`
- `DELETE /api/documents/{doc_id}`
- `POST /api/websites/scrape`
- `POST /api/query`
- `GET /api/query/history`
- `GET /api/system/status`
- `GET /api/stats/queue`
- `GET /api/stats/overview`
- `POST /api/client/heartbeat`
- `GET /api/ollama/models`
- `POST /api/ollama/pull`
- `DELETE /api/ollama/models/{model_name}`
- `POST /api/ollama/test`

Embedding worker:

- `POST /api/embed/text`

Vector store:

- `POST /api/vectors/upsert`
- `POST /api/vectors/search`
- `DELETE /api/vectors/{doc_id}`

Query service:

- `GET /api/models`
- `POST /api/query`

---

## 12) Maintenance Scripts

### Clear full runtime state

`clear_database.py`:

```powershell
python clear_database.py --yes
```

Clears Postgres runtime tables, Redis queues/cache/client state, Qdrant vectors, and uploaded files.

### Clear vector database only

`clear_vector_db.py`:

```powershell
python clear_vector_db.py --yes
```

Deletes Qdrant collection and recreates it by default.

---

## 13) Troubleshooting

### Ingestion worker fails with file not found

- In remote worker mode, verify `SHARED_UPLOADS_DIR` is correctly mapped.
- Check retry envs: `INGESTION_FILE_RETRY_MAX`, `INGESTION_FILE_RETRY_DELAY_SEC`.

### Embedding backlog grows

- Increase `WORKER_CONCURRENCY`.
- Increase worker instances (main and/or remote worker compose).

### Query latency spikes under load

- Increase `QUERY_EMBED_CONCURRENCY`.
- Tune query service timeouts/retries.

### Ollama not available in Query tab dropdown

- Verify `OLLAMA_URL`.
- Verify models exist via Ollama tab.
- Check gateway logs for `/api/ollama/models`.

### LAN devices cannot connect

- Verify `PUBLIC_HOST`.
- Validate firewall and subnet routing.
- Test ports from client:

```powershell
Test-NetConnection <docker-host-ip> -Port 3000
Test-NetConnection <docker-host-ip> -Port 8000
```

---

## 14) File Guide

- `docker-compose.yml` - full stack orchestration
- `docker-compose.worker.yml` - remote ingestion/embedding workers
- `.env.example` - complete env defaults/tuning
- `shared/init.sql` - base schema
- `gateway/main.py` - API ingress, scrape, metrics, queue producers
- `ingestion-worker/main.py` - extraction/chunking + embedding queue producer
- `embedding-worker/main.py` - background embeddings + query-time embeddings
- `vector-store/main.py` - Qdrant access service
- `query-service/main.py` - retrieval + LLM orchestration
- `frontend/lib/api.ts` - frontend API client and error handling
- `clear_database.py` - full runtime reset script
- `clear_vector_db.py` - vector-only reset script

---

## 15) Verification Checklist

1. `docker compose ps` shows healthy services.
2. Upload text/pdf/image/audio/video and watch status reach `done`.
3. Scrape a website in Scrape tab and verify new document appears.
4. Query with Groq, Ollama, and Gemini models.
5. Confirm Ollama model selector shows installed models only.
6. Open app from a second device and confirm metrics show active devices.
7. While ingesting, confirm each document shows chunking/indexing progress in Documents tab.
8. If remote workers enabled, confirm chunks/embeddings continue flowing and are queryable.

