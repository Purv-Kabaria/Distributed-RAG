"""
Component 1: API Gateway
─────────────────────────
Responsibilities:
  • Accept file uploads from the frontend
  • Route /query requests to the Query Service
  • Serve document & job status (polling)
  • Rate limiting via Redis
  • Single entry point for all client traffic
"""

import os, uuid, hashlib, mimetypes, re
from pathlib import Path
from typing import Optional
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag
from html.parser import HTMLParser
import httpx, aiofiles
import redis.asyncio as aioredis
import asyncpg
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import json, logging, time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [gateway] %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Distributed RAG – API Gateway", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

REDIS_URL          = os.getenv("REDIS_URL", "redis://localhost:6379")
POSTGRES_URL       = os.getenv("POSTGRES_URL")
INGESTION_URL      = os.getenv("INGESTION_SERVICE_URL", "http://ingestion-worker:8001")
QUERY_URL          = os.getenv("QUERY_SERVICE_URL", "http://query-service:8004")
UPLOAD_DIR         = Path(os.getenv("UPLOAD_DIR", "/uploads"))
DB_POOL_MIN        = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX        = int(os.getenv("DB_POOL_MAX", "10"))
QUERY_TIMEOUT_SEC  = float(os.getenv("GATEWAY_QUERY_TIMEOUT_SEC", "120"))
HTTP_RETRIES       = int(os.getenv("GATEWAY_HTTP_RETRIES", "1"))
HEALTH_TIMEOUT_SEC = float(os.getenv("GATEWAY_HEALTH_TIMEOUT_SEC", "3"))
SCRAPE_MAX_PAGES_DEFAULT = int(os.getenv("SCRAPE_MAX_PAGES_DEFAULT", "25"))
SCRAPE_MAX_PAGES_HARD_LIMIT = int(os.getenv("SCRAPE_MAX_PAGES_HARD_LIMIT", "100"))
MAX_FILE_SIZE      = int(os.getenv("MAX_FILE_SIZE_MB", "200")) * 1024 * 1024

ALLOWED_MIME_PREFIXES = ("text/", "image/", "audio/", "video/", "application/pdf")

redis_client: aioredis.Redis = None
db_pool: asyncpg.Pool = None
DEVICE_TTL_SEC = 120


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global redis_client, db_pool
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL is required")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    db_pool = await asyncpg.create_pool(POSTGRES_URL, min_size=DB_POOL_MIN, max_size=DB_POOL_MAX)
    log.info("Gateway started ✓")


@app.on_event("shutdown")
async def shutdown():
    await redis_client.close()
    await db_pool.close()


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "component": "gateway"}


@app.get("/api/system/status")
async def system_status():
    """Aggregate health of all backend services."""
    services = {
        "ingestion": f"{INGESTION_URL}/health",
        "query":     f"{QUERY_URL}/health",
    }
    results = {}
    async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_SEC) as client:
        for name, url in services.items():
            try:
                r = await client.get(url)
                results[name] = r.json()
            except Exception as e:
                results[name] = {"status": "unreachable", "error": str(e)}
    # Redis ping
    try:
        await redis_client.ping()
        results["redis"] = {"status": "ok"}
    except Exception:
        results["redis"] = {"status": "unreachable"}
    return results


# ── Upload ────────────────────────────────────────────────────────────────────

@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...)):
    # Validate mime
    content_type = file.content_type or mimetypes.guess_type(file.filename)[0] or ""
    if not any(content_type.startswith(p) for p in ALLOWED_MIME_PREFIXES):
        raise HTTPException(400, f"Unsupported file type: {content_type}")

    doc_id   = str(uuid.uuid4())
    ext      = Path(file.filename).suffix
    saved_name = f"{doc_id}{ext}"
    dest     = UPLOAD_DIR / saved_name

    # Stream to disk
    size = 0
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"File too large (max {MAX_FILE_SIZE // (1024 * 1024)} MB)")
            await f.write(chunk)

    # Determine type bucket
    if content_type.startswith("image/"):
        file_type = "image"
    elif content_type.startswith("audio/"):
        file_type = "audio"
    elif content_type.startswith("video/"):
        file_type = "video"
    elif content_type == "application/pdf":
        file_type = "pdf"
    else:
        file_type = "text"

    # Register in DB
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO documents (id, filename, original_name, file_type, file_size, status)
               VALUES ($1,$2,$3,$4,$5,'queued')""",
            doc_id, saved_name, file.filename, file_type, size
        )

    # Push ingestion job to ingestion-worker via Redis queue
    job = {
        "doc_id":    doc_id,
        "filename":  saved_name,
        "file_type": file_type,
        "file_path": str(dest),
    }
    await redis_client.lpush("ingestion:queue", json.dumps(job))

    log.info(f"Uploaded doc={doc_id} type={file_type} size={size}")
    return {"doc_id": doc_id, "status": "queued", "filename": file.filename}


# ── Document status ───────────────────────────────────────────────────────────

@app.get("/api/documents/{doc_id}/status")
async def doc_status(doc_id: str):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id,original_name,file_type,status,error_msg,chunk_count,created_at FROM documents WHERE id=$1",
            doc_id
        )
    if not row:
        raise HTTPException(404, "Document not found")
    return dict(row)


@app.get("/api/documents")
async def list_documents(limit: int = Query(50, le=200)):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id,original_name,file_type,status,chunk_count,created_at FROM documents ORDER BY created_at DESC LIMIT $1",
            limit
        )
    return [dict(r) for r in rows]


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT filename FROM documents WHERE id=$1", doc_id)
        if not row:
            raise HTTPException(404, "Document not found")
        # Remove file
        fpath = UPLOAD_DIR / row["filename"]
        fpath.unlink(missing_ok=True)
        await conn.execute("DELETE FROM documents WHERE id=$1", doc_id)
    # Also notify vector store to delete vectors
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.delete(f"http://vector-store:8003/api/vectors/{doc_id}")
    except Exception:
        pass
    return {"deleted": doc_id}


# ── Query (proxy to Query Service) ───────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str
    provider: str = "groq"   # groq | ollama | gemini
    model: Optional[str] = None
    top_k: int = 5
    gemini_api_key: Optional[str] = None


class WebsiteScrapeRequest(BaseModel):
    url: str
    max_pages: int = SCRAPE_MAX_PAGES_DEFAULT
    single_page_only: bool = False


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.links = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in ("script", "style", "noscript"):
            self._skip_depth += 1
        if t == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in ("script", "style", "noscript") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            d = data.strip()
            if d:
                self.text_parts.append(d)

    def get_text(self):
        return " ".join(self.text_parts)


def normalize_url(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    parsed = urlparse(s)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    clean = parsed._replace(fragment="")
    return clean.geturl()


def same_host(a: str, b: str) -> bool:
    return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()


def clean_text(txt: str) -> str:
    return re.sub(r"\s+", " ", txt or "").strip()


async def scrape_site(seed_url: str, max_pages: int, single_page_only: bool = False) -> tuple[str, int]:
    visited = set()
    q = deque([seed_url])
    docs = []
    pages = 0
    max_pages = max(1, min(max_pages, SCRAPE_MAX_PAGES_HARD_LIMIT))
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        while q and pages < max_pages:
            current = q.popleft()
            if current in visited:
                continue
            visited.add(current)
            try:
                r = await client.get(current, headers={"User-Agent": "DistributedRAGBot/1.0"})
            except Exception:
                continue
            if r.status_code >= 400:
                continue
            ctype = (r.headers.get("content-type") or "").lower()
            if "text/html" not in ctype and "application/xhtml+xml" not in ctype:
                continue
            parser = TextExtractor()
            try:
                parser.feed(r.text)
            except Exception:
                continue
            text = clean_text(parser.get_text())
            if not text:
                continue
            pages += 1
            docs.append(f"URL: {current}\n\n{text}")
            if not single_page_only:
                for href in parser.links:
                    nxt, _ = urldefrag(urljoin(current, href))
                    if not nxt.startswith(("http://", "https://")): 
                        continue
                    if same_host(seed_url, nxt) and nxt not in visited:
                        q.append(nxt)
    return "\n\n" + ("\n\n".join(docs)), pages


@app.post("/api/query")
async def query(req: QueryRequest):
    last_exc = None
    for _ in range(HTTP_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=QUERY_TIMEOUT_SEC) as client:
                r = await client.post(f"{QUERY_URL}/api/query", json=req.dict())
                r.raise_for_status()
                return r.json()
        except httpx.TimeoutException as e:
            last_exc = e
        except httpx.HTTPStatusError as e:
            raise HTTPException(e.response.status_code, e.response.text)
        except Exception as e:
            last_exc = e
    if isinstance(last_exc, httpx.TimeoutException):
        raise HTTPException(504, "Query service timed out")
    raise HTTPException(503, f"Query service unavailable: {last_exc}")


@app.post("/api/websites/scrape")
async def scrape_website(req: WebsiteScrapeRequest):
    base = normalize_url(req.url)
    if not base:
        raise HTTPException(400, "Invalid URL")
    effective_max_pages = 1 if req.single_page_only else req.max_pages
    content, pages = await scrape_site(base, effective_max_pages, req.single_page_only)
    if pages == 0 or not content.strip():
        raise HTTPException(400, "No scrapeable HTML content found")

    doc_id = str(uuid.uuid4())
    saved_name = f"{doc_id}.txt"
    dest = UPLOAD_DIR / saved_name
    payload = content.strip()
    async with aiofiles.open(dest, "w", encoding="utf-8") as f:
        await f.write(payload)
    size = dest.stat().st_size

    title = f"website_{urlparse(base).netloc}_{pages}pages.txt"
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO documents (id, filename, original_name, file_type, file_size, status)
               VALUES ($1,$2,$3,$4,$5,'queued')""",
            doc_id, saved_name, title, "text", size
        )

    job = {
        "doc_id": doc_id,
        "filename": saved_name,
        "file_type": "text",
        "file_path": str(dest),
    }
    await redis_client.lpush("ingestion:queue", json.dumps(job))
    return {"doc_id": doc_id, "status": "queued", "pages_scraped": pages, "source_url": base}


@app.get("/api/query/history")
async def query_history(limit: int = Query(20, le=100)):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id,question,answer,model_used,provider,chunks_used,duration_ms,created_at FROM query_logs ORDER BY created_at DESC LIMIT $1",
            limit
        )
    return [dict(r) for r in rows]


# ── Queue stats (for monitoring UI) ──────────────────────────────────────────

@app.get("/api/stats/queue")
async def queue_stats():
    ing_len = await redis_client.llen("ingestion:queue")
    emb_len = await redis_client.llen("embedding:queue")
    return {
        "ingestion_queue": ing_len,
        "embedding_queue": emb_len,
    }


@app.post("/api/client/heartbeat")
async def client_heartbeat(body: dict, request: Request):
    client_id = (body.get("client_id") or "").strip()
    if not client_id:
        raise HTTPException(400, "client_id is required")
    now = int(time.time())
    entry = {
        "client_id": client_id,
        "name": (body.get("name") or "").strip() or "Unknown device",
        "ip": request.client.host if request.client else "",
        "ua": request.headers.get("user-agent", ""),
        "last_seen": now,
    }
    await redis_client.hset("clients:last_seen", client_id, str(now))
    await redis_client.hset("clients:meta", client_id, json.dumps(entry))
    return {"ok": True, "last_seen": now}


@app.get("/api/stats/overview")
async def stats_overview():
    now = int(time.time())
    raw_seen = await redis_client.hgetall("clients:last_seen")
    active_ids = []
    for client_id, seen in raw_seen.items():
        try:
            if now - int(seen) <= DEVICE_TTL_SEC:
                active_ids.append(client_id)
        except Exception:
            continue
    metas = await redis_client.hmget("clients:meta", active_ids) if active_ids else []
    devices = []
    for raw in metas or []:
        if not raw:
            continue
        try:
            devices.append(json.loads(raw))
        except Exception:
            continue
    async with db_pool.acquire() as conn:
        docs_total = await conn.fetchval("SELECT COUNT(*) FROM documents")
        docs_ingested = await conn.fetchval("SELECT COUNT(*) FROM documents WHERE status='done'")
        chunks_total = await conn.fetchval("SELECT COUNT(*) FROM chunks")
        embeddings_done = await conn.fetchval("SELECT COUNT(*) FROM embedding_jobs WHERE status='done'")
        embeddings_failed = await conn.fetchval("SELECT COUNT(*) FROM embedding_jobs WHERE status='failed'")
    return {
        "active_devices": len(devices),
        "devices": devices,
        "documents_total": docs_total,
        "documents_ingested": docs_ingested,
        "chunks_total": chunks_total,
        "embeddings_done": embeddings_done,
        "embeddings_failed": embeddings_failed,
    }


# ── Ollama Proxy (so browser can reach Ollama on host through gateway) ────────

OLLAMA_URL = os.getenv("OLLAMA_URL", "")


@app.get("/api/ollama/models")
async def ollama_list_models():
    if not OLLAMA_URL:
        return {"models": [], "available": False, "reason": "OLLAMA_URL not set in .env"}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            r.raise_for_status()
            return {"models": r.json().get("models", []), "available": True}
    except Exception as e:
        return {"models": [], "available": False, "reason": str(e)}


@app.post("/api/ollama/pull")
async def ollama_pull_model(body: dict):
    """
    Stream Ollama pull progress back to the client as newline-delimited JSON.
    """
    if not OLLAMA_URL:
        raise HTTPException(400, "OLLAMA_URL not set in .env")
    model = body.get("model", "")
    if not model:
        raise HTTPException(400, "model name required")

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_URL}/api/pull",
                    json={"name": model, "stream": True},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            yield line + "\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.delete("/api/ollama/models/{model_name:path}")
async def ollama_delete_model(model_name: str):
    if not OLLAMA_URL:
        raise HTTPException(400, "OLLAMA_URL not set in .env")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.request(
                "DELETE",
                f"{OLLAMA_URL}/api/delete",
                json={"name": model_name},
            )
            r.raise_for_status()
            return {"deleted": model_name}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/ollama/test")
async def ollama_test_model(body: dict):
    """Quick generation test to verify a model actually works."""
    if not OLLAMA_URL:
        raise HTTPException(400, "OLLAMA_URL not set in .env")
    model = body.get("model", "")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": model, "prompt": "Reply with exactly: OK", "stream": False},
            )
            r.raise_for_status()
            return {"ok": True, "response": r.json().get("response", "")}
    except Exception as e:
        raise HTTPException(503, str(e))