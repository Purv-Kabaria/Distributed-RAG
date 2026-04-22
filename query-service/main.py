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

import os, logging, time, re, math
from typing import Optional
import asyncpg
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [query] %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Distributed RAG – Query Service", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

POSTGRES_URL       = os.getenv("POSTGRES_URL")
VECTOR_STORE_URL   = os.getenv("VECTOR_STORE_URL", "http://vector-store:8003")
EMBEDDING_URL      = os.getenv("EMBEDDING_SERVICE_URL", "http://embedding-worker:8002")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")
OLLAMA_URL         = os.getenv("OLLAMA_URL", "")
GROQ_DEFAULT_MODEL = os.getenv("GROQ_DEFAULT_MODEL", "llama-3.3-70b-versatile")
GEMINI_DEFAULT_MODEL = os.getenv("GEMINI_DEFAULT_MODEL", "gemini-2.5-flash")
OLLAMA_DEFAULT_MODEL = os.getenv("OLLAMA_DEFAULT_MODEL", "gemma3:4b")
QUESTION_MAX_CHARS = int(os.getenv("QUESTION_MAX_CHARS", "6000"))
TOP_K_MIN = int(os.getenv("TOP_K_MIN", "1"))
TOP_K_MAX = int(os.getenv("TOP_K_MAX", "20"))
LANG_ENFORCE_RETRY = int(os.getenv("LANG_ENFORCE_RETRY", "1"))
LANG_ENFORCE_ANSWER_MAX_CHARS = int(os.getenv("LANG_ENFORCE_ANSWER_MAX_CHARS", "4000"))
EMBED_TIMEOUT_SEC = float(os.getenv("EMBED_TIMEOUT_SEC", "30"))
VECTOR_TIMEOUT_SEC = float(os.getenv("VECTOR_TIMEOUT_SEC", "20"))
GROQ_TIMEOUT_SEC = float(os.getenv("GROQ_TIMEOUT_SEC", "60"))
GEMINI_TIMEOUT_SEC = float(os.getenv("GEMINI_TIMEOUT_SEC", "60"))
OLLAMA_TIMEOUT_SEC = float(os.getenv("OLLAMA_TIMEOUT_SEC", "120"))
HTTP_RETRIES = int(os.getenv("QUERY_HTTP_RETRIES", "2"))
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
RETRIEVAL_CANDIDATE_MULTIPLIER = int(os.getenv("RETRIEVAL_CANDIDATE_MULTIPLIER", "4"))
RETRIEVAL_CANDIDATE_MAX = int(os.getenv("RETRIEVAL_CANDIDATE_MAX", "60"))
RERANK_SEMANTIC_WEIGHT = float(os.getenv("RERANK_SEMANTIC_WEIGHT", "0.72"))
RERANK_LEXICAL_WEIGHT = float(os.getenv("RERANK_LEXICAL_WEIGHT", "0.28"))
RERANK_RECENCY_WEIGHT = float(os.getenv("RERANK_RECENCY_WEIGHT", "0.04"))
CONTEXT_CHARS_PER_CHUNK = int(os.getenv("CONTEXT_CHARS_PER_CHUNK", "2200"))
MIN_TOP_SCORE_TO_ANSWER = float(os.getenv("MIN_TOP_SCORE_TO_ANSWER", "0.22"))
MIN_AVG_SCORE_TO_ANSWER = float(os.getenv("MIN_AVG_SCORE_TO_ANSWER", "0.16"))
MIN_POST_ANSWER_GROUNDING = float(os.getenv("MIN_POST_ANSWER_GROUNDING", "0.08"))
MULTI_QUERY_ENABLED = int(os.getenv("MULTI_QUERY_ENABLED", "1"))
MULTI_QUERY_MAX = int(os.getenv("MULTI_QUERY_MAX", "4"))
RRF_K = int(os.getenv("RRF_K", "60"))

db_pool: asyncpg.Pool = None


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    global db_pool
    if not POSTGRES_URL:
        raise RuntimeError("POSTGRES_URL is required")
    db_pool = await asyncpg.create_pool(POSTGRES_URL, min_size=DB_POOL_MIN, max_size=DB_POOL_MAX)
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
    providers.append({
        "provider": "gemini",
        "models": [
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-2.5-pro",
        ],
        "available": True,
        "reason": "" if GEMINI_API_KEY else "Provide key in Query tab or set GEMINI_API_KEY in .env",
    })

    return {"providers": providers}


# ── Embed helper ──────────────────────────────────────────────────────────────

async def embed_query(question: str) -> list[float]:
    last_exc = None
    for _ in range(HTTP_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=EMBED_TIMEOUT_SEC) as client:
                r = await client.post(f"{EMBEDDING_URL}/api/embed/text", json={"text": question})
                r.raise_for_status()
                return r.json()["embedding"]
        except Exception as e:
            last_exc = e
    raise last_exc


# ── Retrieve helper ───────────────────────────────────────────────────────────

async def retrieve_context(vec: list[float], top_k: int) -> list[dict]:
    last_exc = None
    for _ in range(HTTP_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=VECTOR_TIMEOUT_SEC) as client:
                r = await client.post(
                    f"{VECTOR_STORE_URL}/api/vectors/search",
                    json={"query_vector": vec, "top_k": top_k}
                )
                r.raise_for_status()
                return r.json()["hits"]
        except Exception as e:
            last_exc = e
    raise last_exc


def build_search_queries(question: str) -> list[str]:
    q = question.strip()
    if not q:
        return []
    variants = [q]
    lower = normalize_text(q)
    temporal = re.sub(
        r"\b(when|time|timestamp|what\s+time|at\s+what\s+time|earlier|later|before|after|first|last)\b",
        "",
        lower,
    )
    temporal = re.sub(r"\s+", " ", temporal).strip(" ?.")
    if temporal and temporal != lower:
        variants.append(temporal)
    focus = re.sub(
        r"\b(please|could you|can you|tell me|explain|summarize|describe)\b",
        "",
        lower,
    )
    focus = re.sub(r"\s+", " ", focus).strip(" ?.")
    if focus and focus != lower and focus not in variants:
        variants.append(focus)
    if quote_required(q):
        variants.append(lower.replace("exact quote", "").replace("verbatim", "").strip())
    out = []
    seen = set()
    for v in variants:
        if v and v not in seen:
            out.append(v)
            seen.add(v)
    return out[:max(1, MULTI_QUERY_MAX)]


def rrf_fuse(rank_lists: list[list[dict]], k: int = 60) -> list[dict]:
    scored: dict[str, dict] = {}
    for lst in rank_lists:
        for rank, item in enumerate(lst, start=1):
            cid = str(item.get("chunk_id") or f"row-{rank}")
            s = 1.0 / (k + rank)
            if cid not in scored:
                scored[cid] = {
                    "rrf": 0.0,
                    "best_score": float(item.get("score") or 0.0),
                    "item": item,
                }
            scored[cid]["rrf"] += s
            if float(item.get("score") or 0.0) > scored[cid]["best_score"]:
                scored[cid]["best_score"] = float(item.get("score") or 0.0)
                scored[cid]["item"] = item
    fused = []
    for rec in scored.values():
        it = dict(rec["item"])
        it["score"] = max(float(it.get("score") or 0.0), rec["best_score"])
        it["_rrf_score"] = rec["rrf"]
        fused.append(it)
    fused.sort(key=lambda x: (float(x.get("_rrf_score", 0.0)), float(x.get("score", 0.0))), reverse=True)
    return fused


async def retrieve_context_multi_query(question: str, top_k: int, candidate_k: int) -> list[dict]:
    queries = build_search_queries(question) if MULTI_QUERY_ENABLED else [question]
    if not queries:
        return []
    rank_lists: list[list[dict]] = []
    for q in queries:
        vec = await embed_query(q)
        hits = await retrieve_context(vec, candidate_k)
        rank_lists.append(hits[:candidate_k])
    return rrf_fuse(rank_lists, k=RRF_K)


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def tokenize(s: str) -> set[str]:
    text = normalize_text(s)
    words = re.findall(r"[a-z0-9']+", text)
    return {w for w in words if len(w) > 1}


def lexical_overlap(question: str, chunk_text: str) -> float:
    q = tokenize(question)
    c = tokenize(chunk_text)
    if not q or not c:
        return 0.0
    inter = len(q & c)
    if inter == 0:
        return 0.0
    return inter / max(1, len(q))


def answer_grounding_overlap(answer: str, chunks: list[dict]) -> float:
    a = tokenize(answer)
    if not a:
        return 0.0
    union = set()
    for c in chunks:
        union |= tokenize(c.get("text", ""))
    if not union:
        return 0.0
    return len(a & union) / max(1, len(a))


def temporal_question(question: str) -> bool:
    q = normalize_text(question)
    patterns = [
        "when ", "what time", "timestamp", "timecode", "at what point",
        "earlier", "later", "before", "after", "first", "last",
    ]
    return any(p in q for p in patterns)


def quote_required(question: str) -> bool:
    q = normalize_text(question)
    patterns = [
        "exact words", "exact quote", "verbatim", "word for word", "quote exactly",
    ]
    return any(p in q for p in patterns)


def rerank_chunks(question: str, chunks: list[dict], top_k: int) -> list[dict]:
    if not chunks:
        return []
    wants_temporal = temporal_question(question)
    scored = []
    for idx, c in enumerate(chunks):
        sem = float(c.get("score") or 0.0)
        lex = lexical_overlap(question, c.get("text", ""))
        kind = str(c.get("chunk_kind") or "")
        temporal_bonus = 0.0
        if wants_temporal and kind in ("transcript", "visual_frame"):
            temporal_bonus += 0.08
        if wants_temporal and c.get("time_start_sec") is not None:
            temporal_bonus += 0.04
        recency = 1.0 / (1.0 + math.log1p(idx + 1))
        total = (
            RERANK_SEMANTIC_WEIGHT * sem
            + RERANK_LEXICAL_WEIGHT * lex
            + RERANK_RECENCY_WEIGHT * recency
            + temporal_bonus
        )
        scored.append((total, idx, c))
    scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
    out = [row[2] for row in scored[:max(1, top_k)]]
    return out


def retrieval_confident_enough(chunks: list[dict]) -> bool:
    if not chunks:
        return False
    scores = [float(c.get("score") or 0.0) for c in chunks]
    top = max(scores)
    avg = sum(scores) / len(scores)
    return top >= MIN_TOP_SCORE_TO_ANSWER and avg >= MIN_AVG_SCORE_TO_ANSWER


# ── LLM Callers ───────────────────────────────────────────────────────────────

def _time_hint(c: dict) -> str:
    t0 = c.get("time_start_sec")
    t1 = c.get("time_end_sec")
    if t0 is None or t1 is None:
        return ""
    return f" | media_time≈{float(t0):.1f}s–{float(t1):.1f}s"


def build_prompt(question: str, chunks: list[dict]) -> str:
    prefers_exact_quote = quote_required(question)
    blocks = []
    for i, c in enumerate(chunks):
        th = _time_hint(c)
        kind = c.get("chunk_kind") or "generic"
        file_type = c.get("file_type") or "unknown"
        content = (c.get("text") or "")[:CONTEXT_CHARS_PER_CHUNK]
        blocks.append(f"[Context Chunk {i + 1}{th} | score={c['score']:.3f} | kind={kind} | type={file_type}]\n{content}")
    context = "\n\n---\n\n".join(blocks)
    quote_rule = (
        "If the user explicitly asks for an exact quote or verbatim text, provide exact wording from context."
        if prefers_exact_quote
        else "For audio/video transcript content, do not merely quote raw lines; prefer concise paraphrased explanations in your own words while preserving meaning."
    )
    return f"""[SYSTEM ROLE]
You are a rigorous, retrieval-grounded analyst. Your job is to deliver the most accurate possible answer using ONLY the provided CONTEXT.

[TOP PRIORITIES]
Priority 1: factual correctness from context.
Priority 2: explicit uncertainty over speculation.
Priority 3: clear, concise Markdown output.

[HARD CONSTRAINTS]
1) English only output. Translate internally when needed.
2) Use only provided context; never inject external knowledge.
3) Never invent entities, numbers, timestamps, relationships, or causes.
4) If evidence is missing, weak, ambiguous, or contradictory, output exactly:
I don't know based on the provided context.
5) Never cite or reference sources in any form for any modality (text, docs, audio, image, video).
   Forbidden: source/chunk labels, bracket citations, document names, URLs, file paths, metadata references, or phrases implying direct source attribution.
6) Output valid Markdown only.

[TASK ROUTING]
Classify the question intent before answering:
- factual lookup
- synthesis across multiple chunks
- comparison/difference
- chronology/timeline
- procedural/how-to
- exact-quote request

[EVIDENCE FUSION POLICY]
- Merge overlapping facts from multiple chunks into one coherent answer.
- Prefer high-confidence, specific, and mutually consistent evidence.
- If two claims conflict and cannot be reconciled safely, abstain with the exact fallback sentence.
- Prefer precision over breadth; omit unsupported details.

[MEDIA INTERPRETATION POLICY]
- For transcripts: treat spoken lines and timing markers as factual evidence.
- For visual frames/images: treat described visual content as evidence without extrapolation.
- If user asks "when", include timestamp/media_time only when present.
- If timing is requested but absent, explicitly say timing is unavailable in context.
- {quote_rule}

[ANSWER QUALITY POLICY]
- Lead with the direct answer first.
- For complex questions, follow with compact supporting bullets.
- Avoid repeating the question.
- Avoid generic disclaimers and filler text.
- Keep tone neutral, technical, and decisive when evidence is strong.
- If user asks for steps/checklists/tables, provide them in Markdown when context supports it.

[SAFETY AGAINST HALLUCINATION]
Before finalizing, self-check silently:
- Is every factual claim grounded in context?
- Did I avoid all source/citation references?
- Did I avoid unsupported inference leaps?
- If unsure on any major claim, use the exact fallback sentence.

[USER QUESTION]
{question}

[CONTEXT]
{context}

[OUTPUT FORMAT]
- Markdown only
- Start with direct answer (1-5 sentences)
- Add concise bullets/sections only when useful
- Do not enforce fixed number of bullets or sections
"""


def likely_non_english(text: str) -> bool:
    # Conservative detection for common non-Latin scripts.
    s = text or ""
    return bool(re.search(r"[\u0400-\u04FF\u0370-\u03FF\u4E00-\u9FFF\u0600-\u06FF\u0900-\u097F\u0B80-\u0BFF]", s))


def normalize_answer_markdown(answer: str) -> str:
    out = (answer or "").strip()
    out = re.sub(r"\[(?:source|context\s*chunk)\s+\d+\]", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\[(?:\d+|chunk\s*\d+|doc(?:ument)?\s*\d+)\]", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\b(?:according to|based on|from)\s+(?:the\s+)?(?:source|sources|context|chunk|chunks|document|documents)\b[:\-\s]*", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\b(?:source|sources|context|chunk|chunks)\s*[:\-]\s*", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


async def call_groq(prompt: str, model: str) -> str:
    if not GROQ_API_KEY:
        raise HTTPException(400, "GROQ_API_KEY not configured")
    last_exc = None
    for _ in range(HTTP_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=GROQ_TIMEOUT_SEC) as client:
                system_msg = (
                    "You must respond in English only. If the provided context or question contains any other language, translate it to English before answering. "
                    "Output must be valid Markdown only. Never cite or reference sources, chunks, documents, URLs, or context labels in any form."
                )
                r = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                             "Content-Type": "application/json"},
                    json={
                        "model": model or GROQ_DEFAULT_MODEL,
                        "messages": [
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 1024,
                    }
                )
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            last_exc = e
    raise last_exc


async def call_ollama(prompt: str, model: str) -> str:
    if not OLLAMA_URL:
        raise HTTPException(400, "OLLAMA_URL not configured")
    last_exc = None
    enforced_prompt = (
        "You must respond in English only. If the provided context or question contains any other language, translate it to English. "
        "Output must be valid Markdown only. Never cite or reference sources, chunks, documents, URLs, or context labels in any form.\n\n"
        + prompt
    )
    for _ in range(HTTP_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT_SEC) as client:
                r = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={"model": model or OLLAMA_DEFAULT_MODEL,
                          "prompt": enforced_prompt,
                          "stream": False}
                )
                r.raise_for_status()
                return r.json().get("response", "")
        except Exception as e:
            last_exc = e
    raise last_exc


async def call_gemini(prompt: str, model: str, api_key_override: Optional[str] = None) -> str:
    key = (api_key_override or "").strip() or GEMINI_API_KEY
    if not key:
        raise HTTPException(400, "Gemini API key not configured. Provide one in Query tab or set GEMINI_API_KEY in .env")
    last_exc = None
    for _ in range(HTTP_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT_SEC) as client:
                enforced_prompt = (
                    "Respond in English only. Output valid Markdown only. "
                    "Never cite or reference sources, chunks, documents, URLs, or context labels in any form.\n\n"
                    + prompt
                )
                r = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model or GEMINI_DEFAULT_MODEL}:generateContent",
                    params={"key": key},
                    json={
                        "contents": [{"parts": [{"text": enforced_prompt}]}],
                        "generationConfig": {
                            "temperature": 0.2,
                            "maxOutputTokens": 1024,
                        },
                    },
                )
                r.raise_for_status()
                data = r.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    return ""
                parts = (candidates[0].get("content") or {}).get("parts") or []
                text_parts = [p.get("text", "") for p in parts if isinstance(p, dict)]
                return "".join(text_parts).strip()
        except Exception as e:
            last_exc = e
    raise last_exc


# ── Main Query Endpoint ───────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question:  str
    provider:  str = "groq"   # groq | ollama | gemini
    model:     Optional[str] = None
    top_k:     int = 5
    gemini_api_key: Optional[str] = None


@app.post("/api/query")
async def query(req: QueryRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Question cannot be empty")
    if len(question) > QUESTION_MAX_CHARS:
        raise HTTPException(400, f"Question too long (max {QUESTION_MAX_CHARS} chars)")
    if req.top_k < TOP_K_MIN or req.top_k > TOP_K_MAX:
        raise HTTPException(400, f"top_k must be between {TOP_K_MIN} and {TOP_K_MAX}")

    t0 = time.time()

    # 1. Retrieve (multi-query + fusion) and rerank context
    try:
        candidate_k = min(RETRIEVAL_CANDIDATE_MAX, max(req.top_k, req.top_k * RETRIEVAL_CANDIDATE_MULTIPLIER))
        retrieved = await retrieve_context_multi_query(question, req.top_k, candidate_k)
        chunks = rerank_chunks(question, retrieved, req.top_k)
    except Exception as e:
        raise HTTPException(503, f"Retrieval pipeline error: {e}")

    if not chunks:
        return {
            "question": question,
            "answer":   "No relevant documents found. Please upload some documents first.",
            "chunks":   [],
            "model":    None,
            "provider": req.provider,
            "duration_ms": round((time.time() - t0) * 1000),
        }

    if not retrieval_confident_enough(chunks):
        duration_ms = round((time.time() - t0) * 1000)
        return {
            "question": question,
            "answer": "I don't know based on the provided context.",
            "chunks": chunks,
            "model": None,
            "provider": req.provider,
            "duration_ms": duration_ms,
        }

    # 3. Build prompt & call LLM
    prompt = build_prompt(question, chunks)
    model  = req.model

    try:
        if req.provider == "groq":
            answer = await call_groq(prompt, model or GROQ_DEFAULT_MODEL)
            used_model = model or GROQ_DEFAULT_MODEL
        elif req.provider == "ollama":
            answer = await call_ollama(prompt, model or OLLAMA_DEFAULT_MODEL)
            used_model = model or OLLAMA_DEFAULT_MODEL
        elif req.provider == "gemini":
            answer = await call_gemini(prompt, model or GEMINI_DEFAULT_MODEL, req.gemini_api_key)
            used_model = model or GEMINI_DEFAULT_MODEL
        else:
            raise HTTPException(400, f"Unknown provider: {req.provider}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"LLM error ({req.provider}): {e}")

    if LANG_ENFORCE_RETRY > 0 and likely_non_english(answer):
        try:
            retry_prompt = (
                prompt
                + "\n\nPrevious output may include other languages. Translate it to English only. "
                + f"Output only English text.\n\nPrevious output:\n{answer[:LANG_ENFORCE_ANSWER_MAX_CHARS]}"
            )
            if req.provider == "groq":
                answer = await call_groq(retry_prompt, model or GROQ_DEFAULT_MODEL)
            elif req.provider == "ollama":
                answer = await call_ollama(retry_prompt, model or OLLAMA_DEFAULT_MODEL)
            elif req.provider == "gemini":
                answer = await call_gemini(retry_prompt, model or GEMINI_DEFAULT_MODEL, req.gemini_api_key)
        except Exception:
            pass

    answer = normalize_answer_markdown(answer)

    if likely_non_english(answer):
        raise HTTPException(
            502,
            "Language enforcement failed: model returned non-English output after retry.",
        )

    grounding = answer_grounding_overlap(answer, chunks)
    if grounding < MIN_POST_ANSWER_GROUNDING:
        answer = "I don't know based on the provided context."

    duration_ms = round((time.time() - t0) * 1000)

    # 4. Log to Postgres
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO query_logs (question,answer,model_used,provider,chunks_used,duration_ms)
                   VALUES ($1,$2,$3,$4,$5,$6)""",
                question, answer, used_model, req.provider, len(chunks), duration_ms
            )
    except Exception as e:
        log.warning(f"Failed to log query: {e}")

    return {
        "question":    question,
        "answer":      answer,
        "chunks":      chunks,
        "model":       used_model,
        "provider":    req.provider,
        "duration_ms": duration_ms,
    }
