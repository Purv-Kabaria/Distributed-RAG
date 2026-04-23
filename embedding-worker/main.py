"""
Component 3: Embedding Worker  ← COMPUTE-INTENSIVE DISTRIBUTED COMPONENT
───────────────────────────────────────────────────────────────────────────
Responsibilities:
  • Consume 'embedding:queue' from Redis (supports multiple worker instances)
  • Call Google Gemini Embedding 2 (gemini-embedding-2-preview)
    - Text chunks → embed as text
    - Images      → embed natively as image (multimodal)
    - Audio       → embed natively as audio (multimodal)
    - Video       → embed natively as video (multimodal)
  • Push resulting vectors to the Vector Store service
  • Update embedding job status in Postgres
  • This is the horizontally-scalable compute bottleneck
"""

import os, json, asyncio, logging, base64, time
from pathlib import Path
import redis.asyncio as aioredis
import asyncpg
import httpx
from google import genai
from google.genai import types
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s [embedding] %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Distributed RAG – Embedding Worker", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

REDIS_URL         = os.getenv("REDIS_URL", "redis://localhost:6379")
POSTGRES_URL      = os.getenv("POSTGRES_URL")
VECTOR_STORE_URL  = os.getenv("VECTOR_STORE_URL", "http://vector-store:8003")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
UPLOAD_DIR        = Path(os.getenv("UPLOAD_DIR", "/uploads"))
CONCURRENCY       = int(os.getenv("WORKER_CONCURRENCY", "2"))
QUERY_EMBED_CONCURRENCY = int(os.getenv("QUERY_EMBED_CONCURRENCY", "8"))
QUEUE_POP_TIMEOUT_SEC = int(os.getenv("EMBEDDING_QUEUE_POP_TIMEOUT_SEC", "2"))
EMBED_HTTP_RETRIES = int(os.getenv("EMBED_HTTP_RETRIES", "3"))
EMBED_RETRY_BACKOFF_SEC = float(os.getenv("EMBED_RETRY_BACKOFF_SEC", "1.2"))

# Gemini Embedding 2 - first natively multimodal embedding model
EMBEDDING_MODEL   = "gemini-embedding-2-preview"
EMBEDDING_DIM     = 3072   # default; can be reduced with MRL

redis_client: aioredis.Redis = None
db_pool: asyncpg.Pool = None
gemini_client: genai.Client = None
query_embed_semaphore: asyncio.Semaphore | None = None


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global redis_client, db_pool, gemini_client, query_embed_semaphore
    redis_client  = aioredis.from_url(REDIS_URL, decode_responses=False)
    db_pool       = await asyncpg.create_pool(POSTGRES_URL, min_size=2, max_size=10)
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    query_embed_semaphore = asyncio.Semaphore(max(1, QUERY_EMBED_CONCURRENCY))
    # Start N concurrent workers
    for i in range(CONCURRENCY):
        asyncio.create_task(worker_loop(worker_id=i))
    log.info(f"Embedding worker started with {CONCURRENCY} concurrent tasks ✓")


@app.on_event("shutdown")
async def shutdown():
    await redis_client.close()
    await db_pool.close()


@app.get("/health")
async def health():
    return {"status": "ok", "component": "embedding-worker", "model": EMBEDDING_MODEL}


@app.post("/api/embed/text")
async def embed_text_endpoint(body: dict):
    """Synchronous embed endpoint for the query service."""
    text = body.get("text", "")
    task = body.get("task_type", "RETRIEVAL_QUERY")
    async with query_embed_semaphore:
        vec = await embed_text(text, task_type=task)
    return {"embedding": vec}


# ── Gemini Embedding Helpers ──────────────────────────────────────────────────

async def embed_text(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    """Embed plain text using Gemini Embedding 2."""
    loop = asyncio.get_event_loop()

    def _call():
        result = gemini_client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
            config=types.EmbedContentConfig(task_type=task_type),
        )
        return result.embeddings[0].values

    last_exc = None
    for attempt in range(EMBED_HTTP_RETRIES + 1):
        try:
            return await loop.run_in_executor(None, _call)
        except Exception as e:
            last_exc = e
            if attempt >= EMBED_HTTP_RETRIES:
                break
            await asyncio.sleep(EMBED_RETRY_BACKOFF_SEC * (attempt + 1))
    raise last_exc


async def embed_multimodal(file_path: Path, file_type: str) -> list[float]:
    """
    Embed image/audio/video using Gemini Embedding 2 multimodal API.
    Files are read as bytes and passed as Part objects.
    """
    loop = asyncio.get_event_loop()
    file_bytes = file_path.read_bytes()

    # Map our type to MIME
    mime_map = {
        "image": _guess_image_mime(file_path),
        "audio": _guess_audio_mime(file_path),
        "video": _guess_video_mime(file_path),
    }
    mime = mime_map.get(file_type, "application/octet-stream")

    def _call():
        part = types.Part.from_bytes(data=file_bytes, mime_type=mime)
        result = gemini_client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=types.Content(parts=[part]),
        )
        return result.embeddings[0].values

    last_exc = None
    for attempt in range(EMBED_HTTP_RETRIES + 1):
        try:
            return await loop.run_in_executor(None, _call)
        except Exception as e:
            last_exc = e
            if attempt >= EMBED_HTTP_RETRIES:
                break
            await asyncio.sleep(EMBED_RETRY_BACKOFF_SEC * (attempt + 1))
    raise last_exc


def _guess_image_mime(p: Path) -> str:
    ext = p.suffix.lower()
    return {"jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/jpeg")


def _guess_audio_mime(p: Path) -> str:
    ext = p.suffix.lower()
    return {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
            ".m4a": "audio/mp4", ".flac": "audio/flac"}.get(ext, "audio/mpeg")


def _guess_video_mime(p: Path) -> str:
    ext = p.suffix.lower()
    return {".mp4": "video/mp4", ".mov": "video/quicktime",
            ".avi": "video/x-msvideo", ".mkv": "video/x-matroska"}.get(ext, "video/mp4")


def _is_placeholder_chunk(content: str) -> bool:
    s = (content or "").strip()
    if len(s) < 8 or not s.startswith("[") or not s.endswith("]"):
        return False
    return " file:" in s


# ── DB helpers ────────────────────────────────────────────────────────────────

async def update_job(chunk_id: str, status: str, error: str = None):
    async with db_pool.acquire() as conn:
        await conn.execute(
            """UPDATE embedding_jobs
               SET status=$1, error_msg=$2, attempts=attempts+1, updated_at=NOW()
               WHERE chunk_id=$3""",
            status, error, chunk_id
        )


async def insert_job(doc_id: str, chunk_id: str):
    async with db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO embedding_jobs (chunk_id, document_id, status)
               VALUES ($1,$2,'running')
               ON CONFLICT DO NOTHING""",
            chunk_id, doc_id
        )


async def check_all_done(doc_id: str):
    """If all embedding jobs for the doc are done, mark document as done."""
    async with db_pool.acquire() as conn:
        pending = await conn.fetchval(
            "SELECT COUNT(*) FROM embedding_jobs WHERE document_id=$1 AND status != 'done'",
            doc_id
        )
        if pending == 0:
            await conn.execute(
                "UPDATE documents SET status='done', updated_at=NOW() WHERE id=$1",
                doc_id
            )
            log.info(f"doc={doc_id} fully embedded ✓")


# ── Core Processing ───────────────────────────────────────────────────────────

async def process_job(job: dict):
    doc_id       = job["doc_id"]
    chunk_id     = job["chunk_id"]
    content      = job["content"]
    is_multimodal = job.get("is_multimodal", False)
    file_type    = job.get("file_type", "text")
    file_path_str = job.get("file_path")

    await insert_job(doc_id, chunk_id)

    try:
        t0 = time.time()

        if (
            is_multimodal
            and file_path_str
            and _is_placeholder_chunk(content)
            and file_type == "image"
        ):
            fp = Path(file_path_str)
            vec = await embed_multimodal(fp, file_type)
            description = content
        else:
            vec = await embed_text(content, task_type="RETRIEVAL_DOCUMENT")
            description = content

        elapsed = round((time.time() - t0) * 1000)
        log.info(f"Embedded chunk={chunk_id} in {elapsed}ms dim={len(vec)}")

        vm = job.get("vector_metadata") or {}
        meta = {
            "doc_id": doc_id,
            "chunk_id": chunk_id,
            "file_type": file_type,
        }
        for k, v in vm.items():
            if v is not None:
                meta[k] = v

        payload = {
            "doc_id": doc_id,
            "chunk_id": chunk_id,
            "embedding": vec,
            "text": description,
            "metadata": meta,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{VECTOR_STORE_URL}/api/vectors/upsert", json=payload)
            r.raise_for_status()

        await update_job(chunk_id, "done")
        await check_all_done(doc_id)

    except Exception as e:
        log.exception(f"Embedding failed chunk={chunk_id}: {e}")
        await update_job(chunk_id, "failed", str(e))
        # Mark document failed
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE documents SET status='failed', error_msg=$1, updated_at=NOW() WHERE id=$2",
                str(e), doc_id
            )


# ── Worker loop ───────────────────────────────────────────────────────────────

async def worker_loop(worker_id: int = 0):
    log.info(f"Embedding worker-{worker_id} listening on embedding:queue")
    while True:
        try:
            item = await redis_client.brpop(b"embedding:queue", timeout=QUEUE_POP_TIMEOUT_SEC)
            if item:
                _, raw = item
                job = json.loads(raw.decode())
                await process_job(job)
        except Exception as e:
            log.exception(f"Worker-{worker_id} error: {e}")
            await asyncio.sleep(2)
