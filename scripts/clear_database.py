#!/usr/bin/env python3
import argparse
import subprocess
import sys


def run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def ensure_compose_services() -> None:
    run(["docker", "compose", "ps"])


def clear_postgres() -> None:
    sql = (
        "TRUNCATE TABLE embedding_jobs, chunks, documents, query_logs RESTART IDENTITY CASCADE;"
    )
    run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "raguser",
            "-d",
            "ragdb",
            "-c",
            sql,
        ]
    )


def clear_redis() -> None:
    run(["docker", "compose", "exec", "-T", "redis", "redis-cli", "DEL", "ingestion:queue"])
    run(["docker", "compose", "exec", "-T", "redis", "redis-cli", "DEL", "embedding:queue"])
    run(["docker", "compose", "exec", "-T", "redis", "redis-cli", "DEL", "clients:last_seen"])
    run(["docker", "compose", "exec", "-T", "redis", "redis-cli", "DEL", "clients:meta"])
    run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "redis",
            "redis-cli",
            "--scan",
            "--pattern",
            "vec:search:*",
        ]
    )
    run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "redis",
            "sh",
            "-lc",
            "redis-cli --scan --pattern 'vec:search:*' | xargs -r redis-cli del",
        ]
    )


def clear_qdrant() -> None:
    script = (
        "import asyncio\n"
        "from qdrant_client import AsyncQdrantClient\n"
        "from qdrant_client.models import Distance, VectorParams\n"
        "async def main():\n"
        "  c=AsyncQdrantClient(url='http://qdrant:6333')\n"
        "  try:\n"
        "    await c.delete_collection('rag_chunks')\n"
        "  except Exception:\n"
        "    pass\n"
        "  await c.create_collection('rag_chunks', vectors_config=VectorParams(size=3072, distance=Distance.COSINE))\n"
        "  await c.close()\n"
        "asyncio.run(main())\n"
    )
    run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "vector-store",
            "python",
            "-c",
            script,
        ]
    )


def clear_uploaded_files() -> None:
    run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "gateway",
            "sh",
            "-lc",
            "rm -rf /uploads/*",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clear Distributed RAG runtime data (Postgres, Redis, Qdrant, uploads)."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    args = parser.parse_args()

    if not args.yes:
        print("This will permanently clear runtime data for this project.")
        confirm = input("Type 'yes' to continue: ").strip().lower()
        if confirm != "yes":
            print("Cancelled.")
            return 0

    try:
        ensure_compose_services()
        clear_postgres()
        clear_redis()
        clear_qdrant()
        clear_uploaded_files()
    except subprocess.CalledProcessError as exc:
        print(f"\nFailed while running command (exit {exc.returncode}).")
        return 1

    print("\nDatabase and runtime state cleared successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
