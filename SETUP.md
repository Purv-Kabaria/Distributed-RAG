# Distributed RAG Core - Setup and Operations

This document is a code-accurate setup and operations guide for the current repository.

---

## 1) System at a Glance

This project implements a distributed RAG pipeline with five server-side application components:

1. `gateway` (API entry + upload + query proxy + ollama proxy)
2. `ingestion-worker` (extraction/OCR/STT/vision chunk production)
3. `embedding-worker` (Gemini embeddings + vector upsert orchestration)
4. `vector-store` (sole Qdrant access service)
5. `query-service` (retrieve + prompt build + LLM call)

Shared infrastructure services:

- `redis` (queue + cache)
- `postgres` (metadata)
- `qdrant` (vector DB)
- `frontend` (client UI; does not count toward 5 components)

---

## 2) Lab Mandate Compliance

### 5-Component rule

- Distinct server components: `gateway`, `ingestion-worker`, `embedding-worker`, `vector-store`, `query-service`.
- Each runs in its own container with healthchecks in `docker-compose.yml`.

### Functional decomposition

- `gateway`: upload/list/delete/status and query proxy.
- `ingestion-worker`: extract/chunk from files and enqueue embedding jobs.
- `embedding-worker`: embed chunks and track embedding job progress.
- `vector-store`: upsert/search/delete vectors in Qdrant.
- `query-service`: query-time embedding, retrieval, prompting, and LLM response.

### Inter-component dependency

End-to-end upload + query requires chained service interaction:

- Upload path: frontend -> gateway -> Redis `ingestion:queue` -> ingestion-worker -> Redis `embedding:queue` -> embedding-worker -> vector-store -> Qdrant.
- Query path: frontend -> gateway -> query-service -> embedding-worker (`/api/embed/text`) + vector-store (`/api/vectors/search`) -> Ollama or Groq.

### No fat clients

- Frontend is only UI and API caller.
- Processing, storage, retrieval, and generation occur server-side.

### Decoupled data

- Qdrant is only accessed via `vector-store`.
- Postgres holds metadata (`documents`, `chunks`, `embedding_jobs`, `query_logs`) and is used by multiple services for pipeline state.
- Redis is used for asynchronous queue decoupling and search cache.

---

## 3) Runtime Architecture Details

### `gateway` (`gateway/main.py`)

Responsibilities:

- Accept uploads (`/api/documents/upload`) with MIME + size validation.
- Insert initial `documents` row with status `queued`.
- Push ingestion jobs into Redis `ingestion:queue`.
- List/status/delete documents.
- Proxy queries to `query-service`.
- Aggregate system health (`/api/system/status`).
- Expose queue depths (`/api/stats/queue`).
- Proxy Ollama model management and test APIs (`/api/ollama/*`).

Important notes:

- Upload size cap: 200 MB.
- Supported MIME buckets: text, image, audio, video, pdf.
- On document delete, gateway also calls vector-store delete by `doc_id`.

### `ingestion-worker` (`ingestion-worker/main.py`)

Responsibilities:

- BRPOP `ingestion:queue`.
- Extract and normalize content by file type.
- Save chunks in Postgres `chunks`.
- Update `documents` statuses.
- LPUSH embedding jobs to `embedding:queue`.

File-type behavior:

- `text`: `read_text`.
- `pdf`: PyMuPDF text extraction.
- `image`: Tesseract OCR + visual caption backend(s).
- `audio`: faster-whisper transcription with timestamps.
- `video`: optional visual frame captions + audio transcription with timestamps.

Vision backends:

- Ollama (`OLLAMA_URL`, `OLLAMA_VISION_MODEL`)
- Gemini fallback (`GEMINI_API_KEY`, `GEMINI_VISION_MODEL`)
- Selection by `VISION_ORDER`:
  - `ollama_then_gemini`
  - `gemini_only`
  - `ollama_only`

Chunk metadata:

- `chunks.extra` JSONB stores fields such as:
  - `time_start_sec`
  - `time_end_sec`
  - `chunk_kind` (`transcript`, `visual_frame`, `image`)

Startup migration safeguard:

- Worker runs `ALTER TABLE chunks ADD COLUMN IF NOT EXISTS extra JSONB ...` at startup for old volumes.

### `embedding-worker` (`embedding-worker/main.py`)

Responsibilities:

- BRPOP `embedding:queue`.
- Create/update `embedding_jobs`.
- Embed content via Gemini embedding model.
- Upsert vectors through `vector-store`.
- Mark `documents` as `done` when all jobs complete, otherwise `failed`.

Embedding logic:

- Query embedding endpoint (`/api/embed/text`) defaults to `RETRIEVAL_QUERY`.
- Document chunks embed as text with `RETRIEVAL_DOCUMENT`.
- Native multimodal embed path exists but current flow primarily embeds extracted text/transcript/caption content.

### `vector-store` (`vector-store/main.py`)

Responsibilities:

- Own Qdrant collection lifecycle.
- Upsert vectors (`/api/vectors/upsert`).
- Search vectors (`/api/vectors/search`) with optional `doc_filter`.
- Delete vectors by document (`/api/vectors/{doc_id}`).
- Cache search responses in Redis.

Payload fields preserved in search hits:

- `chunk_id`, `doc_id`, `text`, `score`
- Optional `time_start_sec`, `time_end_sec`, `chunk_kind`, `file_type`

### `query-service` (`query-service/main.py`)

Responsibilities:

- Accept query requests (`/api/query`).
- Embed question via embedding-worker `/api/embed/text`.
- Retrieve top-K context via vector-store `/api/vectors/search`.
- Build prompt with source score and media-time hints.
- Call selected LLM provider:
  - `groq` (cloud)
  - `ollama` (local or remote LAN host)
- Insert query logs into `query_logs`.

Prompt behavior:

- Instructs model to use only provided context.
- Instructs timestamp use for "when was X said/shown" style questions.

### Frontend (`frontend/*`)

Tabs:

- Query
- Documents
- Ollama
- System

Main API wiring:

- Most app APIs use `frontend/lib/api.ts`.
- Ollama management UI uses `frontend/components/Ollamamanager.tsx`.

---

## 4) Data Model and State Flow

Schema source: `shared/init.sql`

### Core tables

- `documents`: upload record + pipeline status + errors + chunk_count.
- `chunks`: chunk text + token_count + `extra` metadata JSONB.
- `embedding_jobs`: per chunk embedding status/attempts/errors.
- `query_logs`: query audit with provider/model/timing/chunk usage.
- `api_keys`: present in schema, currently unused at runtime.

### Status lifecycle

`queued` -> `extracting` -> `chunking` -> `embedding` -> `done`

Failure path:

- Any unrecoverable stage error sets status `failed` with `error_msg`.

---

## 5) Queues, Endpoints, and Contracts

### Redis queues

- `ingestion:queue` (producer: gateway, consumer: ingestion-worker)
- `embedding:queue` (producer: ingestion-worker, consumer: embedding-worker)

### High-value HTTP endpoints

Gateway:

- `POST /api/documents/upload`
- `GET /api/documents`
- `GET /api/documents/{doc_id}/status`
- `DELETE /api/documents/{doc_id}`
- `POST /api/query`
- `GET /api/query/history`
- `GET /api/stats/queue`
- `GET /api/system/status`
- `GET/POST/DELETE /api/ollama/*`

Ingestion worker:

- `GET /health`

Embedding worker:

- `GET /health`
- `POST /api/embed/text`

Vector store:

- `GET /health`
- `POST /api/vectors/upsert`
- `POST /api/vectors/search`
- `DELETE /api/vectors/{doc_id}`

Query service:

- `GET /health`
- `GET /api/models`
- `POST /api/query`

---

## 6) Prerequisites (Windows)

Install on machines that run services:

1. Docker Desktop (WSL2 backend recommended)
2. Git for Windows
3. PowerShell 7 or Windows Terminal

If using Ollama:

- Install Ollama on chosen host machine.
- Pull models as needed, for example:
  - `ollama pull llama3.2`
  - `ollama pull llava`

---

## 7) Environment Configuration

Create `.env`:

```powershell
Copy-Item .env.example .env
```

Set required keys:

```env
GEMINI_API_KEY=...
```

Optional:

```env
GROQ_API_KEY=...
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_VISION_MODEL=llava
GEMINI_VISION_MODEL=gemini-2.0-flash
VISION_ORDER=ollama_then_gemini
WHISPER_MODEL_SIZE=small
VIDEO_VISUAL_MAX_FRAMES=6
VIDEO_VISUAL_INTERVAL_SEC=45
WORKER_CONCURRENCY=2
SECRET_KEY=distributed-rag-secret-2025
```

---

## 8) Docker Compose Notes (Important)

Current `docker-compose.yml` includes machine-specific host IP values in some service env vars (for example `10.123.252.181` in multiple places).

Before team rollout, ensure these are correct for your environment:

- `POSTGRES_URL` and `REDIS_URL` entries in services should be reachable from those containers.
- `VECTOR_STORE_URL` in `embedding-worker` should target a reachable vector-store endpoint.
- `NEXT_PUBLIC_GATEWAY_URL` in `frontend` currently points to a fixed host IP.

Recommended for portability:

- Prefer internal service DNS names (`postgres`, `redis`, `vector-store`) for container-to-container communication.
- Use LAN host IP only where browser clients or cross-machine hosts require it.

---

## 9) Build and Run

```powershell
docker compose up --build -d
```

Check services:

```powershell
docker compose ps
docker compose logs -f
```

Health examples:

- `http://localhost:8000/health` (gateway)
- `http://localhost:3000` (frontend)

---

## 10) Multi-Device Windows Connectivity (Exact Steps)

Use this topology:

- Machine A: Docker host (full stack)
- Machine B: Ollama host (optional, often GPU-capable)
- Machine C..N: browser-only clients

### Step A: discover IPs

On each machine:

```powershell
ipconfig
```

Use the active LAN/Wi-Fi IPv4 (not WSL adapter addresses).

### Step B: firewall rules

On Docker host (Admin PowerShell):

```powershell
New-NetFirewallRule -DisplayName "RAG Frontend 3000" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow
New-NetFirewallRule -DisplayName "RAG Gateway 8000"  -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

On Ollama host (if separate):

```powershell
New-NetFirewallRule -DisplayName "Ollama 11434" -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow
```

### Step C: start Ollama for LAN (if separate host)

On Ollama host:

```powershell
$env:OLLAMA_HOST="0.0.0.0:11434"
ollama serve
```

Set Docker host `.env`:

```env
OLLAMA_URL=http://<ollama-host-ip>:11434
```

### Step D: validate connectivity

From client machine:

```powershell
Test-NetConnection <docker-host-ip> -Port 3000
Test-NetConnection <docker-host-ip> -Port 8000
```

From Docker host to Ollama host (if separate):

```powershell
Invoke-WebRequest http://<ollama-host-ip>:11434/api/tags
```

### Step E: client access URL

- Open `http://<docker-host-ip>:3000`

---

## 11) Feature Deep Dive: Media Time and Visual Understanding

### Audio/video "exact time" Q&A

- Whisper transcription stores timestamped lines in chunk content.
- Chunk metadata preserves numeric ranges in seconds.
- Query prompt instructs model to cite timestamps.
- UI surfaces source time ranges when available.

### Image/video visual understanding

- Image flow combines:
  - OCR text
  - vision description (Ollama and/or Gemini fallback)
- Video flow combines:
  - frame-based visual captions
  - speech transcription

If a backend is unavailable:

- `VISION_ORDER` controls fallback behavior.
- Placeholder chunks are still generated to keep pipeline continuity.

---

## 12) Known Pitfalls and Current Behavior

1. `init.sql` runs only on first Postgres volume creation.
   - Mitigation: ingestion startup migration for `chunks.extra`.

2. Some compose env values are host-IP hardcoded.
   - This can break when moved to a new network.

3. Frontend API handling differs by module:
   - General API client and Ollama manager may use different base URL strategies.
   - Ensure `NEXT_PUBLIC_GATEWAY_URL` matches your deployment plan.

4. Gateway file header mentions rate limiting, but active implementation is queue/state proxying and upload handling.

5. Query provider support is `groq` and `ollama`; if provider backend is down, query returns an HTTP error (503 or propagated status).

---

## 13) Troubleshooting

### Upload stays `failed`

- Check:
  - `docker compose logs -f gateway ingestion-worker`
- Common causes:
  - unsupported MIME
  - extraction failure with empty text where fallback is not applicable
  - DB schema mismatch in old volumes

### Embedding fails

- Check:
  - `docker compose logs -f embedding-worker`
- Validate:
  - `GEMINI_API_KEY`
  - outbound internet to Gemini API

### Query fails

- Check:
  - `docker compose logs -f query-service gateway`
- Validate provider path:
  - Groq key set for `groq`
  - Ollama reachable for `ollama`

### LAN clients cannot connect

- Verify host IP and firewall rules.
- Confirm same subnet and non-guest isolated Wi-Fi.
- Test ports via `Test-NetConnection`.

---

## 14) File Guide

- `docker-compose.yml`: service wiring, healthchecks, ports, envs.
- `shared/init.sql`: base schema.
- `gateway/main.py`: upload/status/query proxy/ollama proxy.
- `ingestion-worker/main.py`: extraction, OCR/STT/vision, chunking, enqueue.
- `embedding-worker/main.py`: embeddings, job tracking, vector upsert calls.
- `vector-store/main.py`: Qdrant access and search cache.
- `query-service/main.py`: RAG query orchestration.
- `frontend/lib/api.ts`: main API client calls.
- `frontend/components/Ollamamanager.tsx`: model management UI calls.

---

## 15) Quick Verification Checklist

1. `docker compose ps` shows all services healthy.
2. Upload text/pdf/image/audio/video from UI.
3. Observe status transitions to `done`.
4. Ask "when was X said" on audio/video and verify timestamped sources.
5. Confirm visual description chunks appear for image/video.
6. Connect a second device to `http://<docker-host-ip>:3000` and repeat query.

