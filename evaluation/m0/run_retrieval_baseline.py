#!/usr/bin/env python3
"""Run deterministic retrieval metrics against the existing Chroma collection."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import chromadb
import pandas as pd
import yaml
from validate_dataset import load_cases, validate

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend" / "django_backend"
DEFAULT_DATASET = Path(__file__).with_name("gold_queries.jsonl")
DEFAULT_OUTPUT = Path(__file__).with_name("evidence") / "retrieval_baseline.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_config() -> dict:
    with (BACKEND / "config" / "llm_config.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def configured_models(config: dict) -> tuple[str, str, str]:
    provider = str(config.get("LLM_PROVIDER") or "ollama").lower()
    embedding_provider = str(config.get("EMBEDDING_PROVIDER") or provider).lower()
    provider_config = config.get("OLLAMA_CONFIG") or {}
    embedding_model = str(provider_config.get("embedding_model") or "")
    return provider, embedding_provider, embedding_model


def build_document_map() -> tuple[dict[str, list[str]], int]:
    mapping: dict[str, list[str]] = defaultdict(list)
    count = 0
    for path in sorted((BACKEND / "data" / "log").glob("*.csv")):
        frame = pd.read_csv(path)
        for row_number, row in enumerate(frame.itertuples(index=False), 1):
            # LlamaIndex strips the leading space introduced by the historical
            # ``replace("Pandas", " ")`` serialization before persisting text.
            content = str(row).replace("Pandas", " ").strip()
            mapping[content].append(f"{path.name}:{row_number:06d}")
            count += 1
    return dict(mapping), count


def embed_queries(
    endpoint: str, model: str, queries: list[str], timeout: float
) -> list[list[float]]:
    payload = json.dumps({"model": model, "input": queries}).encode()
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        body = json.load(response)
    embeddings = body.get("embeddings") or []
    if len(embeddings) != len(queries):
        raise RuntimeError(
            f"Ollama returned {len(embeddings)} embeddings for {len(queries)} queries"
        )
    return embeddings


def rank_metrics(relevant: set[str], ranked: list[list[str]]) -> dict[str, float]:
    if not relevant:
        return {}
    flattened_prefixes = []
    seen = set()
    for ids_at_rank in ranked:
        seen.update(ids_at_rank)
        flattened_prefixes.append(set(seen))
    recalls = {}
    for cutoff in (1, 5, 10):
        prefix = flattened_prefixes[min(cutoff, len(flattened_prefixes)) - 1]
        recalls[f"recall_at_{cutoff}"] = len(prefix & relevant) / len(relevant)
    reciprocal_rank = 0.0
    dcg = 0.0
    for rank, ids_at_rank in enumerate(ranked[:10], 1):
        if relevant.intersection(ids_at_rank):
            if reciprocal_rank == 0.0:
                reciprocal_rank = 1.0 / rank
            dcg += 1.0 / math.log2(rank + 1)
    ideal_hits = min(len(relevant), 10)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return {
        **recalls,
        "mrr_at_10": reciprocal_rank,
        "ndcg_at_10": dcg / ideal_dcg if ideal_dcg else 0.0,
    }


def summarize(per_case: list[dict]) -> dict[str, float]:
    positives = [item for item in per_case if not item["is_negative"]]
    keys = ("recall_at_1", "recall_at_5", "recall_at_10", "mrr_at_10", "ndcg_at_10")
    return {key: statistics.fmean(item[key] for item in positives) for key in keys}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--collection")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    validation = validate(cases)
    if validation["errors"]:
        raise SystemExit(json.dumps(validation, ensure_ascii=False, indent=2))
    config = load_config()
    provider, embedding_provider, embedding_model = configured_models(config)
    if embedding_provider != "ollama":
        raise SystemExit(
            f"this baseline runner currently requires Ollama embeddings, got {embedding_provider}"
        )

    client = chromadb.PersistentClient(path=str(BACKEND / "data" / "vector_stores"))
    collections = client.list_collections()
    collection_name = args.collection or (
        collections[0].name if len(collections) == 1 else "log_collection_bge_large_latest"
    )
    collection = client.get_collection(collection_name)
    document_map, source_row_count = build_document_map()

    embed_started = time.perf_counter()
    embeddings = embed_queries(
        args.ollama_url, embedding_model, [case["query"] for case in cases], args.timeout
    )
    embed_seconds = time.perf_counter() - embed_started
    runs = []
    for run_number in range(1, max(1, args.repeats) + 1):
        started = time.perf_counter()
        raw = collection.query(
            query_embeddings=embeddings,
            n_results=min(10, collection.count()),
            include=["documents", "distances"],
        )
        query_seconds = time.perf_counter() - started
        per_case = []
        unmatched_documents = 0
        for index, case in enumerate(cases):
            ranked_ids = []
            for document in raw["documents"][index]:
                mapped = document_map.get(document, [])
                if not mapped:
                    unmatched_documents += 1
                ranked_ids.append(mapped)
            item = {
                "case_id": case["case_id"],
                "category": case["category"],
                "is_negative": case["is_negative"],
                "relevant_log_ids": case["relevant_log_ids"],
                "retrieved_log_ids": ranked_ids,
                "distances": raw["distances"][index],
            }
            item.update(rank_metrics(set(case["relevant_log_ids"]), ranked_ids))
            per_case.append(item)
        runs.append(
            {
                "run": run_number,
                "query_seconds": query_seconds,
                "metrics": summarize(per_case),
                "unmatched_documents": unmatched_documents,
                "per_case": per_case,
            }
        )

    metric_keys = runs[0]["metrics"]
    mean_metrics = {
        key: statistics.fmean(run["metrics"][key] for run in runs) for key in metric_keys
    }
    metric_spread = {
        key: max(run["metrics"][key] for run in runs) - min(run["metrics"][key] for run in runs)
        for key in metric_keys
    }
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": {
            "path": str(args.dataset.relative_to(ROOT)),
            "sha256": sha256(args.dataset),
            "case_count": len(cases),
            "positive_count": sum(not case["is_negative"] for case in cases),
            "negative_count": sum(case["is_negative"] for case in cases),
        },
        "runtime": {
            "llm_provider": provider,
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
            "collection": collection_name,
            "collection_count": collection.count(),
            "source_row_count": source_row_count,
            "embedding_dimensions": len(embeddings[0]) if embeddings else 0,
            "embedding_seconds": embed_seconds,
            "repeat_count": len(runs),
        },
        "metrics": mean_metrics,
        "metric_spread": metric_spread,
        "negative_case_note": "Ranking metrics exclude negative cases because the current retriever has no abstention threshold and always returns top-k results.",
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"output": str(args.output), "metrics": mean_metrics, "spread": metric_spread}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
