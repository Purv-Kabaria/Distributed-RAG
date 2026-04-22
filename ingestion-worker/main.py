import os, json, asyncio, logging, uuid, subprocess, tempfile, base64
from pathlib import Path
from typing import Any

import httpx
import redis.asyncio as aioredis
import asyncpg
from google import genai
from google.genai import types
from PIL import Image
import pytesseract
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ingestion] %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Distributed RAG – Ingestion Worker", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
POSTGRES_URL = os.getenv("POSTGRES_URL")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/uploads"))
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")
WHISPER_MODEL_PATH = os.getenv("WHISPER_MODEL_PATH", "")
WHISPER_DOWNLOAD_ROOT = os.getenv("WHISPER_DOWNLOAD_ROOT", "/models/whisper")
WHISPER_LOCAL_ONLY = os.getenv("WHISPER_LOCAL_ONLY", "1").strip().lower() in ("1", "true", "yes", "on")
OLLAMA_URL = (os.getenv("OLLAMA_URL") or "").rstrip("/")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llava")
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-2.0-flash")
VISION_ORDER = os.getenv("VISION_ORDER", "ollama_then_gemini").strip().lower()
VIDEO_VISUAL_MAX_FRAMES = int(os.getenv("VIDEO_VISUAL_MAX_FRAMES", "6"))
VIDEO_VISUAL_INTERVAL_SEC = float(os.getenv("VIDEO_VISUAL_INTERVAL_SEC", "45"))
WORKER_CONCURRENCY = int(os.getenv("INGESTION_CONCURRENCY", "2"))
QUEUE_POP_TIMEOUT_SEC = int(os.getenv("INGESTION_QUEUE_POP_TIMEOUT_SEC", "2"))
FILE_RETRY_MAX = int(os.getenv("INGESTION_FILE_RETRY_MAX", "6"))
FILE_RETRY_DELAY_SEC = float(os.getenv("INGESTION_FILE_RETRY_DELAY_SEC", "1.5"))

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

redis_client: aioredis.Redis = None
db_pool: asyncpg.Pool = None
whisper_model: Any = None
gemini_client: Any = None


@app.on_event("startup")
async def startup():
    global redis_client, db_pool, gemini_client
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    db_pool = await asyncpg.create_pool(POSTGRES_URL, min_size=2, max_size=10)
    # Ensure schema supports timestamp metadata.
    # This is safe/idempotent and allows existing volumes to keep working.
    async with db_pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS extra JSONB DEFAULT '{}'::jsonb"
        )
    gk = (os.getenv("GEMINI_API_KEY") or "").strip()
    if gk:
        gemini_client = genai.Client(api_key=gk)
        log.info("Gemini API vision fallback enabled")
    for worker_id in range(WORKER_CONCURRENCY):
        asyncio.create_task(worker_loop(worker_id))
    log.info(f"Ingestion worker started with {WORKER_CONCURRENCY} consumers ✓")


@app.on_event("shutdown")
async def shutdown():
    await redis_client.close()
    await db_pool.close()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "component": "ingestion-worker",
        "vision": {
            "ollama_configured": bool(OLLAMA_URL),
            "gemini_configured": gemini_client is not None,
            "order": VISION_ORDER,
        },
    }


def format_ts(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m:02d}:{s:05.2f}"


def extract_text_file(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except Exception as e:
        log.warning(f"Text read failed: {e}")
        return ""


def extract_pdf(path: Path) -> str:
    try:
        import fitz
        doc = fitz.open(str(path))
        parts = []
        for page in doc:
            parts.append(page.get_text())
        doc.close()
        return "\n\n".join(parts)
    except Exception as e:
        log.warning(f"PDF extraction failed: {e}")
        return ""


async def extract_image_text(file_path: Path) -> str:
    loop = asyncio.get_event_loop()

    def _call():
        image = Image.open(file_path)
        return pytesseract.image_to_string(image)

    try:
        text = await loop.run_in_executor(None, _call)
        log.info(f"Extracted {len(text)} chars from image {file_path.name}")
        return text.strip()
    except Exception as e:
        log.warning(f"Image text extraction failed for {file_path.name}: {e}")
        return ""


def _vision_image_mime(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/jpeg")


async def ollama_vision_caption(image_path: Path, user_prompt: str) -> str:
    if not OLLAMA_URL:
        return ""
    raw = image_path.read_bytes()
    b64 = base64.standard_b64encode(raw).decode("ascii")
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_VISION_MODEL,
                    "messages": [{"role": "user", "content": user_prompt, "images": [b64]}],
                    "stream": False,
                },
            )
        if r.status_code != 200:
            log.warning(f"Ollama vision HTTP {r.status_code}: {r.text[:300]}")
            return ""
        msg = r.json().get("message") or {}
        return (msg.get("content") or "").strip()
    except Exception as e:
        log.warning(f"Ollama vision failed: {e}")
        return ""


async def gemini_vision_caption(image_path: Path, user_prompt: str) -> str:
    if not gemini_client:
        return ""
    loop = asyncio.get_event_loop()
    data = image_path.read_bytes()
    mime = _vision_image_mime(image_path)

    def _call():
        resp = gemini_client.models.generate_content(
            model=GEMINI_VISION_MODEL,
            contents=[
                types.Part.from_bytes(data=data, mime_type=mime),
                user_prompt,
            ],
        )
        t = getattr(resp, "text", None)
        if t:
            return str(t).strip()
        cands = getattr(resp, "candidates", None) or []
        if cands and getattr(cands[0], "content", None):
            parts = cands[0].content.parts or []
            for p in parts:
                tx = getattr(p, "text", None)
                if tx:
                    return str(tx).strip()
        return ""

    try:
        return await loop.run_in_executor(None, _call)
    except Exception as e:
        log.warning(f"Gemini vision failed: {e}")
        return ""


async def vision_caption(image_path: Path, user_prompt: str) -> str:
    if VISION_ORDER == "gemini_only":
        return await gemini_vision_caption(image_path, user_prompt)
    if VISION_ORDER == "ollama_only":
        return await ollama_vision_caption(image_path, user_prompt)
    out = ""
    if OLLAMA_URL:
        out = await ollama_vision_caption(image_path, user_prompt)
    if out.strip():
        return out
    return await gemini_vision_caption(image_path, user_prompt)


def _vision_backends_available() -> bool:
    return bool(OLLAMA_URL) or gemini_client is not None


async def enrich_image_local(file_path: Path) -> str:
    ocr = await extract_image_text(file_path)
    vis = ""
    if _vision_backends_available():
        vis = await vision_caption(
            file_path,
            "Describe visually: objects, people, layout, on-image text, setting. Be concise for search.",
        )
    parts = []
    if vis.strip():
        parts.append(f"[Visual description]\n{vis.strip()}")
    if ocr.strip():
        parts.append(f"[OCR text]\n{ocr.strip()}")
    if not parts:
        return ""
    return "\n\n".join(parts)


async def get_whisper_model():
    global whisper_model
    if whisper_model is not None:
        return whisper_model
    from faster_whisper import WhisperModel

    loop = asyncio.get_event_loop()

    def _load():
        target = WHISPER_MODEL_PATH.strip() or WHISPER_MODEL_SIZE
        return WhisperModel(
            target,
            device="cpu",
            compute_type="int8",
            download_root=WHISPER_DOWNLOAD_ROOT,
            local_files_only=WHISPER_LOCAL_ONLY,
        )

    whisper_model = await loop.run_in_executor(None, _load)
    return whisper_model


async def transcribe_audio_segments(file_path: Path) -> list[tuple[float, float, str]]:
    model = await get_whisper_model()
    loop = asyncio.get_event_loop()

    def _call():
        segments, _ = model.transcribe(str(file_path), vad_filter=True, beam_size=5)
        return [(float(s.start), float(s.end), s.text.strip()) for s in segments if s.text and s.text.strip()]

    try:
        segs = await loop.run_in_executor(None, _call)
        log.info(f"Transcribed {len(segs)} segments from audio {file_path.name}")
        return segs
    except Exception as e:
        log.warning(f"Audio transcription failed for {file_path.name}: {e}")
        return []


async def transcribe_video_segments(file_path: Path) -> list[tuple[float, float, str]]:
    loop = asyncio.get_event_loop()

    def _extract_audio() -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(file_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            tmp_path,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return tmp_path

    audio_tmp = None
    try:
        audio_tmp = await loop.run_in_executor(None, _extract_audio)
        segs = await transcribe_audio_segments(Path(audio_tmp))
        log.info(f"Transcribed {len(segs)} segments from video {file_path.name}")
        return segs
    except Exception as e:
        log.warning(f"Video transcription failed for {file_path.name}: {e}")
        return []
    finally:
        if audio_tmp:
            Path(audio_tmp).unlink(missing_ok=True)


def chunk_timed_segments(
    segments: list[tuple[float, float, str]],
    chunk_size: int = CHUNK_SIZE,
) -> list[tuple[str, float, float]]:
    if not segments:
        return []
    out: list[tuple[str, float, float]] = []
    buf_lines: list[str] = []
    buf_start: float | None = None
    buf_end: float | None = None
    buf_words = 0

    for start, end, raw in segments:
        text = (raw or "").strip()
        if not text:
            continue
        line = f"[{format_ts(start)}–{format_ts(end)}] {text}"
        wc = len(text.split())
        if buf_start is None:
            buf_start = start
        buf_end = end
        buf_lines.append(line)
        buf_words += wc
        if buf_words >= chunk_size:
            body = "\n".join(buf_lines)
            out.append((body, float(buf_start), float(buf_end)))
            buf_lines = []
            buf_start = None
            buf_end = None
            buf_words = 0

    if buf_lines and buf_start is not None and buf_end is not None:
        out.append(("\n".join(buf_lines), float(buf_start), float(buf_end)))
    return out


def ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not (r.stdout or "").strip():
        return 0.0
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


async def video_visual_chunks(file_path: Path) -> list[tuple[str, float, float]]:
    if not _vision_backends_available():
        return []
    loop = asyncio.get_event_loop()
    dur = await loop.run_in_executor(None, lambda: ffprobe_duration(file_path))
    if dur <= 0:
        return []
    times: list[float] = []
    t = 0.0
    while len(times) < VIDEO_VISUAL_MAX_FRAMES and t < dur:
        times.append(t)
        t += VIDEO_VISUAL_INTERVAL_SEC
    if not times and dur > 0:
        times = [0.0]
    out: list[tuple[str, float, float]] = []
    for ts in times:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            tmp_path = Path(tf.name)
        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(ts),
                "-i",
                str(file_path),
                "-vframes",
                "1",
                "-q:v",
                "2",
                str(tmp_path),
            ]
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size == 0:
                continue
            cap = await vision_caption(
                tmp_path,
                f"Video frame at {format_ts(ts)}. Describe scene, people, objects, actions, on-screen text. Be concise.",
            )
            if cap.strip():
                span = min(3.0, max(0.5, dur - ts))
                out.append((f"[Visual {format_ts(ts)}] {cap.strip()}", ts, ts + span))
        except Exception as e:
            log.warning(f"Video frame at {ts}s failed: {e}")
        finally:
            tmp_path.unlink(missing_ok=True)
    return out


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start += chunk_size - overlap
    return chunks


def sanitize_chunk_content(text: str) -> str:
    t = (text or "").replace("\x00", "")
    return t


async def save_chunks(doc_id: str, chunk_rows: list[tuple[str, dict]]) -> list[str]:
    ids = []
    async with db_pool.acquire() as conn:
        for i, (content, extra) in enumerate(chunk_rows):
            clean_content = sanitize_chunk_content(content)
            if not clean_content.strip():
                continue
            chunk_id = str(uuid.uuid4())
            await conn.execute(
                """INSERT INTO chunks (id, document_id, chunk_index, content, token_count, extra)
                   VALUES ($1,$2,$3,$4,$5,$6::jsonb)""",
                chunk_id,
                doc_id,
                i,
                clean_content,
                len(clean_content.split()),
                json.dumps(extra or {}),
            )
            ids.append(chunk_id)
        await conn.execute(
            "UPDATE documents SET chunk_count=$1, updated_at=NOW() WHERE id=$2",
            len(ids),
            doc_id,
        )
    return ids


def vector_metadata_from_extra(extra: dict) -> dict:
    vm = {}
    if extra.get("time_start_sec") is not None:
        vm["time_start_sec"] = extra["time_start_sec"]
    if extra.get("time_end_sec") is not None:
        vm["time_end_sec"] = extra["time_end_sec"]
    if extra.get("chunk_kind"):
        vm["chunk_kind"] = extra["chunk_kind"]
    return vm


async def process_job(job: dict):
    doc_id = job["doc_id"]
    filename = job["filename"]
    file_type = job["file_type"]
    file_path = Path(job["file_path"])
    retries = int(job.get("_ingestion_retries", 0))

    log.info(f"Processing doc={doc_id} type={file_type}")

    try:
        if not file_path.exists():
            if retries < FILE_RETRY_MAX:
                job["_ingestion_retries"] = retries + 1
                await redis_client.rpush("ingestion:queue", json.dumps(job))
                await asyncio.sleep(FILE_RETRY_DELAY_SEC)
                log.warning(
                    f"doc={doc_id} file not found at {file_path}; requeued ({retries + 1}/{FILE_RETRY_MAX})"
                )
                return
            await set_status(doc_id, "failed", f"File not accessible to ingestion workers: {file_path}")
            return

        await set_status(doc_id, "extracting")
        chunk_rows: list[tuple[str, dict]] = []
        is_multimodal = file_type in ("image", "audio", "video")

        if file_type == "text":
            text = extract_text_file(file_path)
            if not text.strip():
                await set_status(doc_id, "failed", "No text could be extracted")
                return
            await set_status(doc_id, "chunking")
            for c in chunk_text(text) or [text]:
                chunk_rows.append((c, {}))

        elif file_type == "pdf":
            text = extract_pdf(file_path)
            if not text.strip():
                await set_status(doc_id, "failed", "No text could be extracted")
                return
            await set_status(doc_id, "chunking")
            for c in chunk_text(text) or [text]:
                chunk_rows.append((c, {}))

        elif file_type == "image":
            enriched = await enrich_image_local(file_path)
            if not enriched.strip():
                enriched = f"[image file: {file_path.name}]"
            await set_status(doc_id, "chunking")
            parts = chunk_text(enriched) if enriched.strip() else []
            if not parts:
                parts = [enriched]
            for c in parts:
                chunk_rows.append((c, {"chunk_kind": "image"}))

        elif file_type == "audio":
            await set_status(doc_id, "chunking")
            segments = await transcribe_audio_segments(file_path)
            if not segments:
                chunk_rows.append((f"[audio file: {file_path.name}]", {}))
            else:
                for content, t0, t1 in chunk_timed_segments(segments):
                    chunk_rows.append(
                        (
                            content,
                            {
                                "time_start_sec": t0,
                                "time_end_sec": t1,
                                "chunk_kind": "transcript",
                            },
                        )
                    )

        elif file_type == "video":
            await set_status(doc_id, "chunking")
            visual = await video_visual_chunks(file_path)
            for content, t0, t1 in visual:
                chunk_rows.append(
                    (
                        content,
                        {
                            "time_start_sec": t0,
                            "time_end_sec": t1,
                            "chunk_kind": "visual_frame",
                        },
                    )
                )
            segments = await transcribe_video_segments(file_path)
            if segments:
                for content, t0, t1 in chunk_timed_segments(segments):
                    chunk_rows.append(
                        (
                            content,
                            {
                                "time_start_sec": t0,
                                "time_end_sec": t1,
                                "chunk_kind": "transcript",
                            },
                        )
                    )
            if not chunk_rows:
                chunk_rows.append((f"[video file: {file_path.name}]", {}))

        else:
            await set_status(doc_id, "failed", f"Unknown file_type={file_type}")
            return

        if not chunk_rows:
            await set_status(doc_id, "failed", "No chunks produced")
            return

        chunk_ids = await save_chunks(doc_id, chunk_rows)

        await set_status(doc_id, "embedding")
        for chunk_id, (content, extra) in zip(chunk_ids, chunk_rows):
            vm = vector_metadata_from_extra(extra)
            emb_job = {
                "doc_id": doc_id,
                "chunk_id": chunk_id,
                "content": content,
                "is_multimodal": is_multimodal,
                "file_type": file_type,
                "file_path": str(file_path) if is_multimodal else None,
                "vector_metadata": vm,
            }
            await redis_client.lpush("embedding:queue", json.dumps(emb_job))

        log.info(f"doc={doc_id} enqueued {len(chunk_ids)} embedding jobs")

    except Exception as e:
        log.exception(f"Ingestion failed for doc={doc_id}: {e}")
        await set_status(doc_id, "failed", str(e))


async def set_status(doc_id: str, status: str, error: str = None):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE documents SET status=$1, error_msg=$2, updated_at=NOW() WHERE id=$3",
            status,
            error,
            doc_id,
        )


async def worker_loop(worker_id: int):
    log.info(f"Worker-{worker_id} loop started, listening on ingestion:queue")
    while True:
        try:
            item = await redis_client.brpop("ingestion:queue", timeout=QUEUE_POP_TIMEOUT_SEC)
            if item:
                _, raw = item
                job = json.loads(raw)
                await process_job(job)
        except Exception as e:
            log.exception(f"Worker-{worker_id} loop error: {e}")
            await asyncio.sleep(1)
