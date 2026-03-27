# Distributed RAG Core

A fully distributed Retrieval-Augmented Generation system built as a **5-component distributed architecture** for the Distributed Computing Lab.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser (Next.js 15 + Tailwind CSS v4)          [NOT a component]  │
└───────────────────────┬─────────────────────────────────────────────┘
                        │ REST / HTTP
┌───────────────────────▼─────────────────────────────────────────────┐
│  Component 1: API Gateway                              :8000         │
│  • Single client entry point                                         │
│  • File upload handler + status polling                              │
│  • Proxy to Query Service + Ollama (pull / test / delete models)    │
│  • Rate limiting via Redis                                           │
└────────┬─────────────────────────────────────┬────────────────────  │
         │ Redis LPUSH                          │ HTTP proxy           │
         │ ingestion:queue                      │                      │
┌────────▼─────────────┐              ┌─────────▼──────────────────┐  │
│  Component 2:        │              │  Component 5:               │  │
│  Ingestion Worker    │              │  Query Service     :8004    │  │
│  :8001               │              │  • Embed question (Gemini)  │  │
│  • BRPOP from Redis  │              │  • Retrieve top-K chunks    │  │
│  • Extract content   │              │  • Call Groq or Ollama LLM  │  │
│    PDF/text → read   │              │  • Return answer + sources  │  │
│    Image → Tesseract │              └────────────┬────────────────┘  │
│    Audio/video →     │                           │                    │
│    Whisper + ffmpeg  │                    ┌──────┘                    │
│  • Chunk text        │                    │ search                    │
│  • LPUSH             │                    │                           │
│    embedding:queue   │                    │                           │
└────────┬─────────────┘                    │                           │
         │ Redis LPUSH                      │                           │
         │ embedding:queue                  │                           │
┌────────▼──────────────────────┐  ┌────────▼────────────────────────┐ │
│  Component 3:                 │  │  Component 4:                   │ │
│  Embedding Worker    :8002    │  │  Vector Store Service  :8003    │ │
│  ★ COMPUTE-INTENSIVE ★        │──▶│  • Sole owner of Qdrant DB      │ │
│  • BRPOP from Redis           │  │  • Upsert vectors               │ │
│  • Gemini Embedding 2         │  │  • Cosine similarity search     │ │
│    Text → text embedding      │  │  • Redis caching (hot queries)  │ │
│    Image/audio/video →        │  └────────────────────────────────┘ │
│    multimodal embedding       │                                      │
│  • Stores chunk text in       │                                      │
│    Qdrant payload (for LLM)   │                                      │
│  • Horizontally scalable      │                                      │
└───────────────────────────────┘                                      │
                                                                       │
┌──────────────────────────────────────────────────────────────────── ┘
│  Infrastructure (shared, not counted as components)                  │
│  Redis 7  ·  PostgreSQL 16  ·  Qdrant 1.9                           │
└─────────────────────────────────────────────────────────────────────┘
```

### How the 5 Components Satisfy the Mandate

| Rule | How it's met |
|------|-------------|
| **5 distinct server-side components** | Gateway, Ingestion, Embedding, Vector Store, Query Service |
| **Each in its own isolated container** | 5 separate Docker services with individual Dockerfiles |
| **Network communication (not function calls)** | REST/HTTP between services; Redis for async queue messaging |
| **Functional Decomposition** | Each service has a unique role — not 5 copies of the same code |
| **Inter-Component Dependency** | A single upload touches all 5 components sequentially |
| **No Fat Clients** | Frontend is client-only; all logic is server-side |
| **Decoupled Data** | Gateway/Ingestion/Embedding/Query each own their own Postgres tables; Vector Store alone talks to Qdrant |

---

## Prerequisites

Install these on **every Windows machine** that will run the system:

1. **Docker Desktop for Windows**
   - Download: https://www.docker.com/products/docker-desktop/
   - During install, enable **WSL 2 backend** (recommended) or Hyper-V
   - After install, open Docker Desktop and wait until the whale icon stops animating
   - Stable access to **Docker Hub** is required (frontend base image `node:22-alpine` and other pulls). Timeouts or TLS errors usually mean network, VPN, or firewall issues.

2. **Git for Windows** (to clone the project)
   - Download: https://git-scm.com/download/win

3. **A terminal** — use **PowerShell 7** or the built-in Windows Terminal

### Compute and APIs

- **Gemini API** is used for all embeddings (document chunks and user questions). An internet connection is required for embedding unless you change the stack.
- **Ingestion Worker** runs **locally in the container**: Tesseract OCR for images, **faster-whisper** (CPU) for audio/video after **ffmpeg** extracts audio from video. No GPU is required, but the first audio/video job may download Whisper weights and use noticeable CPU and disk.
- **LLM answers** use **Groq** (cloud) or **Ollama** (host), same as before.

---

## API Keys

You need **at least one** of these:

### Gemini API Key (REQUIRED — for embeddings)

1. Go to https://aistudio.google.com/app/apikey
2. Click **Create API key**
3. Copy the key

### Groq API Key (recommended — for LLM queries, free tier)

1. Go to https://console.groq.com
2. Sign up and go to **API Keys**
3. Create a new key and copy it

### Ollama (optional — local LLM, no GPU needed for small models)

- Install from https://ollama.com/download/windows
- Run: `ollama pull llama3.2` (pulls a ~2 GB model)
- It runs on `http://localhost:11434` by default
- The UI can talk to Ollama **through the gateway** (Ollama tab) so the browser never needs direct access to port 11434.

---

## Setup

### Step 1 — Clone the project

```powershell
git clone <your-repo-url> distributed-rag
cd distributed-rag
```

Use your actual repository path if the folder is named differently (for example `DC`).

### Step 2 — Create your `.env` file

```powershell
Copy-Item .env.example .env
```

Open `.env` in Notepad (or VS Code) and fill in your keys:

```
GEMINI_API_KEY=your_gemini_api_key_here
GROQ_API_KEY=your_groq_api_key_here
```

If you are using Ollama on the host machine, also set:

```
OLLAMA_URL=http://host.docker.internal:11434
```

Optional tuning (see `.env.example`):

- `WORKER_CONCURRENCY` — concurrent embedding tasks inside each embedding-worker container (default `2`).
- `SECRET_KEY` — gateway secret (change in production).

To change the **Whisper model size** used for audio/video transcription inside ingestion, add an environment entry to the `ingestion-worker` service in `docker-compose.yml`, for example `- WHISPER_MODEL_SIZE=base`. Allowed values match faster-whisper: `tiny`, `base`, `small`, `medium`, `large`, etc. Default in code is `small`.

### Step 3 — Database init file

Postgres loads schema from `shared/init.sql` on **first** container init only. If you change that file after volumes already exist, either run migrations manually or reset with `docker compose down -v` (destructive).

### Step 4 — Build and start everything

```powershell
docker compose up --build
```

**First build** often takes **10–20+ minutes** on a slow link: base images, Python dependencies (including ONNX runtime and Whisper stack in **ingestion-worker**), and the Next.js frontend install. **Subsequent** builds are much faster when layers are cached.

If the build fails with `TLS handshake timeout` or similar while fetching `node:22-alpine`, retry when the network is stable, or run `docker pull node:22-alpine` first.

Wait until services are healthy. Example log lines:

```
gateway-1           | ... [gateway] Gateway started ✓
ingestion-worker-1  | ... [ingestion] Ingestion worker started ✓
embedding-worker-1  | ... [embedding] Embedding worker started with 2 concurrent tasks ✓
vector-store-1      | ... [vector-store] Vector Store started ✓
query-service-1     | ... [query] Query service started ✓
frontend-1          | ✓ Ready ...
```

### Step 5 — Open the app

Open your browser: **http://localhost:3000**

---

## Docker services (reference)

`docker compose` runs **nine** services: **Redis**, **Postgres**, **Qdrant**, the **five** application components above, and the **frontend**. Only **3000** (frontend) and **8000** (gateway) are published to the host by default.

---

## Using the App

### Upload a Document

1. Open the **Query** tab (default).
2. Drag and drop or browse in the left panel.
3. Supported types include plain text, **PDF**, and common **image**, **audio**, and **video** MIME types (as allowed by the gateway).
4. Pipeline: `queued → extracting → chunking → embedding → done`.
5. Images are run through **OCR**; audio and video are **transcribed** before chunking. Empty extraction falls back to placeholder chunks and weaker RAG for that file.

### Ask Questions

1. After at least one document is `done`, type a question in the chat (right panel).
2. Choose **Groq** or **Ollama** and a model.
3. Send. Answers include retrieved chunk text (from Qdrant payloads) as context for the LLM.

### Document Library

- **Documents** tab: list, status, chunk counts, delete (also removes vectors for that document).

### Ollama Tab

- Lists models via the gateway, **pull** with streamed progress, **test** a model, **delete** models. Requires `OLLAMA_URL` set and Ollama running on the host.

### System Tab

- Service health, queue depths, architecture notes, and data-flow summary.

---

## Running on Multiple Machines (LAN)

### Host machine (runs Docker)

1. Complete setup above.
2. LAN IP: `ipconfig` → IPv4 of the active adapter (e.g. `192.168.1.100`).
3. Windows Firewall (run PowerShell **as Administrator**):

```powershell
New-NetFirewallRule -DisplayName "RAG Frontend" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow
New-NetFirewallRule -DisplayName "RAG Gateway"  -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

### Client machines

- Browser only: `http://<host-lan-ip>:3000`
- The frontend is built with `NEXT_PUBLIC_GATEWAY_URL=http://localhost:8000`. For **pure LAN clients**, that URL must match how **their** browser reaches the gateway. If you need LAN access from other PCs, rebuild the frontend with a build arg / env pointing to `http://<host-lan-ip>:8000` or serve behind a reverse proxy.

---

## Scaling the Embedding Worker

```powershell
docker compose up --scale embedding-worker=4
```

Each replica consumes from the same Redis queue.

---

## Stopping the System

```powershell
docker compose down
```

```powershell
docker compose down -v
```

The second command removes named volumes (Postgres, Redis, Qdrant, uploads) for a full reset.

---

## Troubleshooting

### Docker build: `node:22-alpine` TLS handshake timeout

Transient Docker Hub or network issue. Retry, switch networks, disable VPN briefly, or configure a registry mirror in Docker Desktop. Pre-pull: `docker pull node:22-alpine`.

### Ingestion worker exits or restarts on import errors

Check `docker compose logs ingestion-worker`. The service depends on `requests` (for faster-whisper) and other packages in `ingestion-worker/requirements.txt`. Rebuild: `docker compose build --no-cache ingestion-worker`.

### Docker Desktop won't start

- Enable virtualization in BIOS
- WSL 2: `wsl --install` (Admin PowerShell), then reboot

### Port already in use

Change the **left** side of `ports` in `docker-compose.yml` (e.g. `"3001:3000"`).

### Embedding stuck or documents never reach `done`

- Verify `GEMINI_API_KEY` in `.env`
- `docker compose logs embedding-worker`
- Rate limits: batch uploads may queue; embedding worker retries on failure

### Groq errors

- `GROQ_API_KEY` in `.env`
- `docker compose logs query-service`

### Ollama errors

- Ollama running: `ollama serve` (if not a service)
- Model pulled: `ollama pull llama3.2`
- `.env`: `OLLAMA_URL=http://host.docker.internal:11434`

### Poor answers from image uploads

- OCR quality depends on image clarity and Tesseract; scanned PDFs may still be better as PDF text extraction when PyMuPDF finds text.
- Ensure ingestion completed without errors.

### Audio/video very slow or high CPU

- Use a smaller Whisper model via `WHISPER_MODEL_SIZE` on `ingestion-worker` (e.g. `tiny` or `base`).

### Frontend blank page

- Wait for Next.js startup
- `docker compose logs frontend`

### View logs

```powershell
docker compose logs -f
docker compose logs -f ingestion-worker
docker compose logs -f embedding-worker
```

---

## Project File Structure

```
distributed-rag/   (or your repo folder name)
├── docker-compose.yml
├── SETUP.md
├── .env.example
├── .env
│
├── shared/
│   └── init.sql
│
├── gateway/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
├── ingestion-worker/
│   ├── Dockerfile          # curl, ffmpeg, tesseract-ocr
│   ├── requirements.txt    # PyMuPDF, Pillow, pytesseract, faster-whisper, requests, …
│   └── main.py
│
├── embedding-worker/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
├── vector-store/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
├── query-service/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
│
└── frontend/
    ├── Dockerfile
    ├── package.json
    ├── next.config.ts
    ├── postcss.config.mjs
    ├── tsconfig.json
    ├── app/
    │   ├── globals.css
    │   ├── layout.tsx
    │   └── page.tsx
    ├── components/
    │   ├── UploadPanel.tsx
    │   ├── DocumentList.tsx
    │   ├── QueryPanel.tsx
    │   ├── SystemMonitor.tsx
    │   ├── StatusBadge.tsx
    │   ├── ArchDiagram.tsx
    │   └── OllamaManager.tsx
    └── lib/
        └── api.ts
```

---

## Technology Choices (Exam-Ready Justification)

| Technology | Reason |
|-----------|--------|
| **Redis** | Async queue (`LPUSH`/`BRPOP`) between ingestion and embedding; optional caching in vector store. |
| **Qdrant** | Vector search with cosine similarity; only the Vector Store service talks to it. |
| **PostgreSQL** | Documents, chunks, embedding jobs, query logs; ACID metadata. |
| **Tesseract + Pillow** | Deterministic OCR for images in the ingestion worker so LLM context is real text. |
| **faster-whisper + ffmpeg** | Local speech-to-text for audio; video audio extracted then transcribed. |
| **Gemini Embedding 2** (`gemini-embedding-2-preview`) | Multimodal embeddings for chunks; query embeddings use `RETRIEVAL_QUERY` task type where applicable. |
| **Groq API** | Fast hosted LLM inference without local GPU. |
| **Ollama** | Local LLM; gateway proxies API for the browser. |
| **FastAPI** | Async Python services. |
| **Next.js 15** | App Router frontend; Docker build uses standalone output where configured. |
| **Tailwind CSS v4** | CSS-first theming. |

---

## Distributed Computing Concepts Demonstrated

1. **Message Queue Pattern** — Redis decouples producers from consumers.

2. **Worker Pool / Fan-Out** — Multiple embedding-worker replicas share one queue.

3. **Service Decomposition** — Single responsibility per service; network-only coupling.

4. **Decoupled Data** — Qdrant only via Vector Store; Postgres tables scoped by concern.

5. **Eventual Consistency** — Document status moves from `queued` toward `done` asynchronously.

6. **Fault Isolation** — Gateway and query path can stay up while workers restart; queued Redis work remains.
