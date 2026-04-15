#!/usr/bin/env python3
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def http_request(method: str, url: str, payload: dict | None = None) -> tuple[int, str]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url=url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.getcode(), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear Qdrant vector DB collection.")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--collection", default="rag_chunks")
    parser.add_argument("--vector-dim", type=int, default=3072)
    parser.add_argument("--distance", default="Cosine", choices=["Cosine", "Dot", "Euclid", "Manhattan"])
    parser.add_argument("--no-recreate", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    base = args.qdrant_url.rstrip("/")
    collection = urllib.parse.quote(args.collection, safe="")
    collection_url = f"{base}/collections/{collection}"

    if not args.yes:
        print(f"This will delete vector collection '{args.collection}' at {base}.")
        confirm = input("Type 'yes' to continue: ").strip().lower()
        if confirm != "yes":
            print("Cancelled.")
            return 0

    code, body = http_request("DELETE", collection_url)
    if code not in (200, 404):
        print(f"Failed to delete collection (HTTP {code}): {body}")
        return 1
    print(f"Deleted collection '{args.collection}' (or it did not exist).")

    if args.no_recreate:
        print("Skipped recreation.")
        return 0

    payload = {
        "vectors": {
            "size": args.vector_dim,
            "distance": args.distance,
        }
    }
    code, body = http_request("PUT", collection_url, payload)
    if code not in (200,):
        print(f"Failed to recreate collection (HTTP {code}): {body}")
        return 1

    print(f"Recreated collection '{args.collection}' with dim={args.vector_dim}, distance={args.distance}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
