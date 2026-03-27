"""
Component 5: Query Service
───────────────────────────
Responsibilities:
  • Accept user questions
  • Embed the question using Gemini Embedding 2
  • Retrieve top-K similar chunks from Vector Store
  • Call LLM (Groq API or Ollama) with context
  • Stream or return the final answer
  • Log queries to Postgres
"""

import os, logging, time, json
from typing import Optional, AsyncGenerator
import asyncpg
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [query] %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Distributed RAG – Query Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

REDIS_URL          = os.getenv("REDIS_URL", "redis://localhost:6379")
POSTGRES_URL       = os.getenv("POSTGRES_URL")
VECTOR_STORE_URL   = os.getenv("VECTOR_STORE_URL", "http://vector-store:8003")
EMBEDDING_URL      = os.getenv("EMBEDDING_SERVICE_URL", "http://embedding-worker:8002")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")
OLLAMA_URL         = os.getenv("OLLAMA_URL", "")

db_pool: asyncpg.Pool = None

GROQ_DEFAULT_MODEL   = "llama-3.3-70b-versatile"
OLLAMA_DEFAULT_MODEL = "gemma3:4b"


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global db_pool
    db_pool = await asyncpg.create_pool(POSTGRES_URL, min_size=2, max_size=10)
    log.info("Query service started ✓")


@app.on_event("shutdown")
async def shutdown():
    await db_pool.close()


@app.get("/health")
async def health():
    return {"status": "ok", "component": "query-service"}


# ── Models endpoint ───────────────────────────────────────────────────────────

@app.get("/api/models")
async def list_models():
    """Return available LLM providers & models."""
    providers = []

    # Groq
    if GROQ_API_KEY:
        providers.append({
            "provider": "groq",
            "models": [
                "llama-3.3-70b-versatile",
                "llama-3.1-8b-instant",
                "mixtral-8x7b-32768",
                "gemma2-9b-it",
            ],
            "available": True,
        })
    else:
        providers.append({"provider": "groq", "models": [], "available": False,
                          "reason": "GROQ_API_KEY not set"})

    # Ollama
    if OLLAMA_URL:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{OLLAMA_URL}/api/tags")
                models = [m["name"] for m in r.json().get("models", [])]
            providers.append({"provider": "ollama", "models": models, "available": True})
        except Exception as e:
            providers.append({"provider": "ollama", "models": [], "available": False,
                              "reason": str(e)})
    else:
        providers.append({"provider": "ollama", "models": [], "available": False,
                          "reason": "OLLAMA_URL not set"})

    return {"providers": providers}


# ── Embed helper ──────────────────────────────────────────────────────────────

async def embed_query(question: str) -> list[float]:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{EMBEDDING_URL}/api/embed/text", json={"text": question})
        r.raise_for_status()
        return r.json()["embedding"]


# ── Retrieve helper ───────────────────────────────────────────────────────────

async def retrieve_context(vec: list[float], top_k: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{VECTOR_STORE_URL}/api/vectors/search",
            json={"query_vector": vec, "top_k": top_k}
        )
        r.raise_for_status()
        return r.json()["hits"]


# ── LLM Callers ───────────────────────────────────────────────────────────────

def _time_hint(c: dict) -> str:
    t0 = c.get("time_start_sec")
    t1 = c.get("time_end_sec")
    if t0 is None or t1 is None:
        return ""
    return f" | media_time≈{float(t0):.1f}s–{float(t1):.1f}s"


def build_prompt(question: str, chunks: list[dict]) -> str:
    blocks = []
    for i, c in enumerate(chunks):
        th = _time_hint(c)
        blocks.append(f"[Source {i + 1}{th} | score={c['score']:.3f}]\n{c['text']}")
    context = "\n\n---\n\n".join(blocks)
    return f"""You are a helpful assistant. Answer using ONLY the context below.
For audio or video transcripts, lines look like [MM:SS.mm–MM:SS.mm] speech. Cite those timestamps (or the media_time range in seconds) when the user asks when something was said or shown.
For image or video visual descriptions, use the described content as facts about what appears.

If the context does not contain enough information, say so clearly.

CONTEXT:
{context}

QUESTION: {question}

ANSWER:"""


async def call_groq(prompt: str, model: str) -> str:
    if not GROQ_API_KEY:
        raise HTTPException(400, "GROQ_API_KEY not configured")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": model or GROQ_DEFAULT_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 1024,
            }
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def call_ollama(prompt: str, model: str) -> str:
    if not OLLAMA_URL:
        raise HTTPException(400, "OLLAMA_URL not configured")
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model or OLLAMA_DEFAULT_MODEL,
                  "prompt": prompt,
                  "stream": False}
        )
        r.raise_for_status()
        return r.json().get("response", "")


# ── Main Query Endpoint ───────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question:  str
    provider:  str = "groq"   # groq | ollama
    model:     Optional[str] = None
    top_k:     int = 5


@app.post("/api/query")
async def query(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(400, "Question cannot be empty")

    t0 = time.time()

    # 1. Embed the question
    try:
        query_vec = await embed_query(req.question)
    except Exception as e:
        raise HTTPException(503, f"Embedding service error: {e}")

    # 2. Retrieve context
    try:
        chunks = await retrieve_context(query_vec, req.top_k)
    except Exception as e:
        raise HTTPException(503, f"Vector store error: {e}")

    if not chunks:
        return {
            "question": req.question,
            "answer":   "No relevant documents found. Please upload some documents first.",
            "chunks":   [],
            "model":    None,
            "provider": req.provider,
            "duration_ms": round((time.time() - t0) * 1000),
        }

    # 3. Build prompt & call LLM
    prompt = build_prompt(req.question, chunks)
    model  = req.model

    try:
        if req.provider == "groq":
            answer = await call_groq(prompt, model or GROQ_DEFAULT_MODEL)
            used_model = model or GROQ_DEFAULT_MODEL
        elif req.provider == "ollama":
            answer = await call_ollama(prompt, model or OLLAMA_DEFAULT_MODEL)
            used_model = model or OLLAMA_DEFAULT_MODEL
        else:
            raise HTTPException(400, f"Unknown provider: {req.provider}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"LLM error ({req.provider}): {e}")

    duration_ms = round((time.time() - t0) * 1000)

    # 4. Log to Postgres
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO query_logs (question,answer,model_used,provider,chunks_used,duration_ms)
                   VALUES ($1,$2,$3,$4,$5,$6)""",
                req.question, answer, used_model, req.provider, len(chunks), duration_ms
            )
    except Exception as e:
        log.warning(f"Failed to log query: {e}")

    return {
        "question":    req.question,
        "answer":      answer,
        "chunks":      chunks,
        "model":       used_model,
        "provider":    req.provider,
        "duration_ms": duration_ms,
    }
