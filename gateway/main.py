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

import os, uuid, hashlib, mimetypes
from pathlib import Path
from typing import Optional
import httpx, aiofiles
import redis.asyncio as aioredis
import asyncpg
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks, Query
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

ALLOWED_MIME_PREFIXES = ("text/", "image/", "audio/", "video/", "application/pdf")
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB

redis_client: aioredis.Redis = None
db_pool: asyncpg.Pool = None


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global redis_client, db_pool
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    db_pool = await asyncpg.create_pool(POSTGRES_URL, min_size=2, max_size=10)
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
    async with httpx.AsyncClient(timeout=3) as client:
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
                raise HTTPException(413, "File too large (max 200 MB)")
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
    provider: str = "groq"   # groq | ollama
    model: Optional[str] = None
    top_k: int = 5


@app.post("/api/query")
async def query(req: QueryRequest):
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{QUERY_URL}/api/query", json=req.dict())
            r.raise_for_status()
            return r.json()
    except httpx.TimeoutException:
        raise HTTPException(504, "Query service timed out")
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text)


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