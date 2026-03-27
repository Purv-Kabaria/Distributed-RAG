-- Distributed RAG - PostgreSQL Schema
-- Each service owns its own tables (Decoupled Data principle)

-- ── Gateway / Auth tables ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS api_keys (
    id          SERIAL PRIMARY KEY,
    key_hash    TEXT UNIQUE NOT NULL,
    label       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Ingestion / Document tables ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename      TEXT NOT NULL,
    original_name TEXT NOT NULL,
    file_type     TEXT NOT NULL,   -- text | image | audio | video | pdf
    file_size     BIGINT,
    status        TEXT NOT NULL DEFAULT 'queued',
                  -- queued | extracting | chunking | embedding | done | failed
    error_msg     TEXT,
    chunk_count   INT DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    content     TEXT NOT NULL,
    token_count INT,
    extra       JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Embedding Worker tables ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS embedding_jobs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id    UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'pending', -- pending | running | done | failed
    attempts    INT DEFAULT 0,
    error_msg   TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Query Service tables ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS query_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question        TEXT NOT NULL,
    answer          TEXT,
    model_used      TEXT,
    provider        TEXT,   -- groq | ollama | gemini
    chunks_used     INT DEFAULT 0,
    duration_ms     INT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_documents_status   ON documents(status);
CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_emb_jobs_status    ON embedding_jobs(status);
CREATE INDEX IF NOT EXISTS idx_emb_jobs_chunk     ON embedding_jobs(chunk_id);
