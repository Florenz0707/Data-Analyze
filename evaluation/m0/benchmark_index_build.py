#!/usr/bin/env python3
"""Rebuild a temporary Chroma index to measure M0 time and peak RSS."""

from __future__ import annotations

import argparse
import json
import resource
import tempfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import chromadb
from run_retrieval_baseline import build_document_map, configured_models, load_config

DEFAULT_OUTPUT = Path(__file__).with_name("evidence") / "index_build_baseline.json"


def embed_batch(
    endpoint: str, model: str, documents: list[str], timeout: float
) -> list[list[float]]:
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/api/embed",
        data=json.dumps({"model": model, "input": documents}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.load(response).get("embeddings") or []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()
    config = load_config()
    _, embedding_provider, embedding_model = configured_models(config)
    if embedding_provider != "ollama":
        raise SystemExit(
            f"temporary index benchmark requires Ollama embeddings, got {embedding_provider}"
        )
    document_map, source_count = build_document_map()
    documents = []
    ids = []
    for document, log_ids in sorted(document_map.items()):
        for log_id in log_ids:
            documents.append(document)
            ids.append(log_id)
    rss_before_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.perf_counter()
    embedding_seconds = 0.0
    add_seconds = 0.0
    with tempfile.TemporaryDirectory(prefix="data-analyze-m0-index-") as temp_dir:
        client = chromadb.PersistentClient(path=temp_dir)
        collection = client.create_collection("m0_index_build_benchmark")
        for start in range(0, len(documents), args.batch_size):
            batch_documents = documents[start : start + args.batch_size]
            batch_ids = ids[start : start + args.batch_size]
            embed_started = time.perf_counter()
            embeddings = embed_batch(
                args.ollama_url, embedding_model, batch_documents, args.timeout
            )
            embedding_seconds += time.perf_counter() - embed_started
            if len(embeddings) != len(batch_documents):
                raise RuntimeError("embedding count did not match document count")
            add_started = time.perf_counter()
            collection.add(ids=batch_ids, documents=batch_documents, embeddings=embeddings)
            add_seconds += time.perf_counter() - add_started
        indexed_count = collection.count()
    total_seconds = time.perf_counter() - started
    rss_after_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_count": source_count,
        "indexed_count": indexed_count,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "batch_size": args.batch_size,
        "embedding_seconds": embedding_seconds,
        "chroma_add_seconds": add_seconds,
        "total_seconds": total_seconds,
        "peak_rss_kib": rss_after_kib,
        "peak_rss_delta_kib": max(0, rss_after_kib - rss_before_kib),
        "storage": "temporary directory removed after successful measurement",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
