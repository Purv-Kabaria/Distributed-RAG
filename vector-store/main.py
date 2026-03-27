"""
Component 4: Vector Store Service
───────────────────────────────────
Responsibilities:
  • Own and manage the Qdrant vector database
  • Upsert vectors from the Embedding Worker
  • Similarity search for the Query Service
  • Manage collections (create, info, delete)
  • Cache hot query results in Redis
  
This service is the only component that speaks to Qdrant,
enforcing the Decoupled Data principle.
"""

import os, logging, hashlib, json, time
from typing import Optional
import redis.asyncio as aioredis
import asyncpg
import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter,
    FieldCondition, MatchValue, SearchRequest
)
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [vector-store] %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Distributed RAG – Vector Store", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

QDRANT_URL   = os.getenv("QDRANT_URL", "http://qdrant:6333")
POSTGRES_URL = os.getenv("POSTGRES_URL")
REDIS_URL    = os.getenv("REDIS_URL", "redis://localhost:6379")

COLLECTION   = "rag_chunks"
VECTOR_DIM   = 3072   # Gemini Embedding 2 default
CACHE_TTL    = 60     # seconds

qdrant: AsyncQdrantClient = None
redis_client: aioredis.Redis = None
db_pool: asyncpg.Pool = None


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global qdrant, redis_client, db_pool
    qdrant       = AsyncQdrantClient(url=QDRANT_URL)
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    db_pool      = await asyncpg.create_pool(POSTGRES_URL, min_size=2, max_size=10)
    await ensure_collection()
    log.info("Vector Store started ✓")


@app.on_event("shutdown")
async def shutdown():
    await qdrant.close()
    await redis_client.close()
    await db_pool.close()


@app.get("/health")
async def health():
    try:
        info = await qdrant.get_collection(COLLECTION)
        return {"status": "ok", "component": "vector-store",
                "vectors": info.points_count}
    except Exception:
        return {"status": "ok", "component": "vector-store", "vectors": 0}


# ── Collection Management ─────────────────────────────────────────────────────

async def ensure_collection():
    try:
        await qdrant.get_collection(COLLECTION)
        log.info(f"Collection '{COLLECTION}' already exists")
    except Exception:
        await qdrant.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        log.info(f"Created collection '{COLLECTION}' dim={VECTOR_DIM}")


@app.get("/api/collections/info")
async def collection_info():
    info = await qdrant.get_collection(COLLECTION)
    return {
        "name":    COLLECTION,
        "vectors": info.points_count,
        "dim":     VECTOR_DIM,
    }


# ── Upsert ────────────────────────────────────────────────────────────────────

class UpsertRequest(BaseModel):
    doc_id:    str
    chunk_id:  str
    embedding: list[float]
    text:      str
    metadata:  dict = {}


@app.post("/api/vectors/upsert")
async def upsert_vector(req: UpsertRequest):
    # Use chunk_id as deterministic integer ID (hash → int)
    point_id = abs(hash(req.chunk_id)) % (2**63)
    point = PointStruct(
        id=point_id,
        vector=req.embedding,
        payload={
            "doc_id":   req.doc_id,
            "chunk_id": req.chunk_id,
            "text":     req.text,
            **req.metadata,
        }
    )
    await qdrant.upsert(collection_name=COLLECTION, points=[point])
    log.debug(f"Upserted chunk={req.chunk_id} point_id={point_id}")
    return {"status": "ok", "point_id": point_id}


# ── Search ────────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query_vector: list[float]
    top_k: int = 5
    doc_filter: Optional[list[str]] = None   # filter by doc IDs


@app.post("/api/vectors/search")
async def search_vectors(req: SearchRequest):
    # Cache key from vector fingerprint
    fp  = hashlib.md5(str(req.query_vector[:8]).encode()).hexdigest()
    key = f"vec:search:{fp}:{req.top_k}"
    cached = await redis_client.get(key)
    if cached:
        return json.loads(cached)

    query_filter = None
    if req.doc_filter:
        from qdrant_client.models import Filter, FieldCondition, MatchAny
        query_filter = Filter(
            must=[FieldCondition(key="doc_id", match=MatchAny(any=req.doc_filter))]
        )

    results = await qdrant.search(
        collection_name=COLLECTION,
        query_vector=req.query_vector,
        limit=req.top_k,
        query_filter=query_filter,
        with_payload=True,
    )

    hits = []
    for r in results:
        p = r.payload or {}
        hit = {
            "chunk_id": p.get("chunk_id"),
            "doc_id": p.get("doc_id"),
            "text": p.get("text", ""),
            "score": r.score,
        }
        for k in ("time_start_sec", "time_end_sec", "chunk_kind", "file_type"):
            if k in p and p[k] is not None:
                hit[k] = p[k]
        hits.append(hit)

    resp = {"hits": hits}
    await redis_client.setex(key, CACHE_TTL, json.dumps(resp))
    return resp


# ── Delete ────────────────────────────────────────────────────────────────────

@app.delete("/api/vectors/{doc_id}")
async def delete_document_vectors(doc_id: str):
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    await qdrant.delete(
        collection_name=COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
        )
    )
    return {"deleted": doc_id}


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def stats():
    info = await qdrant.get_collection(COLLECTION)
    return {
        "total_vectors": info.points_count,
        "collection":    COLLECTION,
        "dim":           VECTOR_DIM,
    }
