#!/usr/bin/env python3
"""Compare the legacy raw-row index with M4 structured-document retrieval."""

from __future__ import annotations

import argparse
import json
import math
import resource
import shutil
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import chromadb

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend" / "django_backend"
M0 = ROOT / "evaluation" / "m0"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(M0))

from data_pipeline import iter_llama_documents  # noqa: E402
from run_retrieval_baseline import (  # noqa: E402
    build_document_map,
    configured_models,
    embed_queries,
    load_cases,
    load_config,
    rank_metrics,
    sha256,
    summarize,
)
from validate_dataset import validate  # noqa: E402

DEFAULT_DATASET = M0 / "gold_queries.jsonl"
DEFAULT_OUTPUT = Path(__file__).with_name("evidence") / "retrieval_comparison.json"


def lexical_tokens(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", (text or "").casefold())


def bm25_scores(query: str, contents: list[str]) -> list[float]:
    query_terms = set(lexical_tokens(query))
    documents = [lexical_tokens(content) for content in contents]
    if not query_terms or not documents:
        return [0.0] * len(documents)
    document_frequency = {
        term: sum(term in set(tokens) for tokens in documents) for term in query_terms
    }
    average_length = sum(len(tokens) for tokens in documents) / len(documents) or 1.0
    scores = []
    for tokens in documents:
        length_factor = 0.25 + 0.75 * len(tokens) / average_length
        score = 0.0
        for term in query_terms:
            term_frequency = tokens.count(term)
            if not term_frequency:
                continue
            idf = math.log(
                1
                + (len(documents) - document_frequency[term] + 0.5)
                / (document_frequency[term] + 0.5)
            )
            score += idf * (term_frequency * 2.2) / (term_frequency + 1.2 * length_factor)
        scores.append(score)
    return scores


def normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    low, high = min(values), max(values)
    if math.isclose(low, high):
        return [1.0 if high > 0 else 0.0 for _ in values]
    return [(value - low) / (high - low) for value in values]


def source_id(metadata: dict) -> str:
    return f"{metadata['source_file']}:{int(metadata['source_row']):06d}"


def safe_metadata(metadata: dict) -> dict:
    return {
        key: value
        for key, value in metadata.items()
        if value is not None and isinstance(value, (str, int, float, bool))
    }


def query_collection(
    collection,
    cases: list[dict],
    embeddings: list[list[float]],
    *,
    mode: str,
    top_k: int = 10,
    candidate_multiplier: int = 3,
) -> tuple[list[dict], float]:
    started = time.perf_counter()
    candidate_k = min(
        collection.count(),
        top_k * candidate_multiplier if mode in {"hybrid", "reranker"} else top_k,
    )
    raw = collection.query(
        query_embeddings=embeddings,
        n_results=candidate_k,
        include=["documents", "distances", "metadatas"],
    )
    query_seconds = time.perf_counter() - started
    per_case = []
    for index, case in enumerate(cases):
        candidates = []
        for document, distance, metadata in zip(
            raw["documents"][index],
            raw["distances"][index],
            raw["metadatas"][index],
            strict=True,
        ):
            metadata = dict(metadata or {})
            candidates.append(
                {
                    "document_id": source_id(metadata),
                    "content": document,
                    "vector_score": 1.0 / (1.0 + float(distance)),
                    "metadata": metadata,
                }
            )
        if mode in {"hybrid", "reranker"}:
            vector_scores = normalize([item["vector_score"] for item in candidates])
            lexical_scores = normalize(
                bm25_scores(case["query"], [item["content"] for item in candidates])
            )
            for item, vector_score, lexical_score in zip(
                candidates, vector_scores, lexical_scores, strict=True
            ):
                item["lexical_score"] = lexical_score
                item["score"] = 0.7 * vector_score + 0.3 * lexical_score
            if mode == "reranker":
                candidates.sort(
                    key=lambda item: (
                        -item["lexical_score"],
                        -item["score"],
                        item["document_id"],
                    )
                )
            else:
                candidates.sort(key=lambda item: (-item["score"], item["document_id"]))
        else:
            for item in candidates:
                item["score"] = item["vector_score"]
        ranked = [[item["document_id"]] for item in candidates[:top_k]]
        result = {
            "case_id": case["case_id"],
            "category": case["category"],
            "is_negative": case["is_negative"],
            "relevant_log_ids": case["relevant_log_ids"],
            "retrieved_log_ids": ranked,
            "top_score": candidates[0]["score"] if candidates else None,
            "candidate_scores": [
                {"document_id": item["document_id"], "score": item["score"]} for item in candidates
            ],
        }
        result.update(rank_metrics(set(case["relevant_log_ids"]), ranked))
        per_case.append(result)
    return per_case, query_seconds


def build_structured_index(client, data_root: Path, collection_name: str):
    collection = client.create_collection(collection_name)
    documents = list(iter_llama_documents(data_root))
    started = time.perf_counter()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    total_embedding_seconds = 0.0
    for start in range(0, len(documents), 4):
        batch = documents[start : start + 4]
        embed_started = time.perf_counter()
        embeddings = embed_queries(
            "http://127.0.0.1:11434",
            "bge-large:latest",
            [document.text for document in batch],
            300.0,
        )
        total_embedding_seconds += time.perf_counter() - embed_started
        collection.add(
            ids=[document.id_ for document in batch],
            documents=[document.text for document in batch],
            metadatas=[safe_metadata(document.metadata) for document in batch],
            embeddings=embeddings,
        )
    return {
        "collection": collection,
        "document_count": len(documents),
        "total_seconds": time.perf_counter() - started,
        "embedding_seconds": total_embedding_seconds,
        "peak_rss_delta_kib": max(
            0, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - rss_before
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--legacy-collection", default="log_collection_bge_large_latest")
    args = parser.parse_args()
    cases = load_cases(args.dataset)
    validation = validate(cases)
    if validation["errors"]:
        raise SystemExit(json.dumps(validation, ensure_ascii=False, indent=2))
    config = load_config()
    provider, embedding_provider, embedding_model = configured_models(config)
    if embedding_provider != "ollama" or embedding_model != "bge-large:latest":
        raise SystemExit("comparison currently requires Ollama bge-large:latest embeddings")

    client = chromadb.PersistentClient(path=str(BACKEND / "data" / "vector_stores"))
    legacy = client.get_collection(args.legacy_collection)
    query_embeddings = embed_queries(
        "http://127.0.0.1:11434", embedding_model, [case["query"] for case in cases], 300.0
    )
    legacy_map, source_row_count = build_document_map()
    legacy_started = time.perf_counter()
    legacy_raw = legacy.query(
        query_embeddings=query_embeddings,
        n_results=min(10, legacy.count()),
        include=["documents", "distances"],
    )
    legacy_query_seconds = time.perf_counter() - legacy_started
    legacy_per_case = []
    for index, case in enumerate(cases):
        ranked = [
            [log_id for log_id in legacy_map.get(document, [])]
            for document in legacy_raw["documents"][index]
        ]
        result = {
            "case_id": case["case_id"],
            "category": case["category"],
            "is_negative": case["is_negative"],
            "relevant_log_ids": case["relevant_log_ids"],
            "retrieved_log_ids": ranked,
            "top_score": 1.0 / (1.0 + legacy_raw["distances"][index][0]) if ranked else None,
        }
        result.update(rank_metrics(set(case["relevant_log_ids"]), ranked))
        legacy_per_case.append(result)

    with tempfile.TemporaryDirectory(prefix="data-analyze-m4-comparison-") as temp_dir:
        structured_root = Path(temp_dir) / "logs"
        structured_root.mkdir()
        for path in sorted((BACKEND / "data" / "log").glob("*.csv")):
            shutil.copy2(path, structured_root / path.name)
        build_started = time.perf_counter()
        structured = build_structured_index(
            chromadb.PersistentClient(path=temp_dir), structured_root, "m4_structured"
        )
        structured_build_seconds = time.perf_counter() - build_started
        structured_collection = structured["collection"]
        structured_vector, structured_query_seconds = query_collection(
            structured_collection, cases, query_embeddings, mode="vector"
        )
        structured_hybrid, hybrid_query_seconds = query_collection(
            structured_collection, cases, query_embeddings, mode="hybrid"
        )
        structured_reranker, reranker_query_seconds = query_collection(
            structured_collection, cases, query_embeddings, mode="reranker"
        )
        threshold_candidates = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
        threshold_results = []
        for threshold in threshold_candidates:
            threshold_per_case = []
            for item in structured_vector:
                ranked = [
                    [candidate["document_id"]]
                    for candidate in item["candidate_scores"]
                    if candidate["score"] >= threshold
                ][:10]
                threshold_item = {
                    "case_id": item["case_id"],
                    "category": item["category"],
                    "is_negative": item["is_negative"],
                    "relevant_log_ids": item["relevant_log_ids"],
                    "hit_count": len(ranked),
                }
                threshold_item.update(rank_metrics(set(item["relevant_log_ids"]), ranked))
                threshold_per_case.append(threshold_item)
            positive = [item for item in threshold_per_case if not item["is_negative"]]
            negative = [item for item in threshold_per_case if item["is_negative"]]
            positive_metrics = summarize(positive)
            negative_false_accept = statistics.fmean(item["hit_count"] > 0 for item in negative)
            threshold_results.append(
                {
                    "threshold": threshold,
                    "positive_recall_at_10": positive_metrics["recall_at_10"],
                    "positive_mrr_at_10": positive_metrics["mrr_at_10"],
                    "negative_false_accept_rate": negative_false_accept,
                    "negative_no_evidence_rate": 1.0 - negative_false_accept,
                }
            )

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
            "legacy_collection": args.legacy_collection,
            "legacy_collection_count": legacy.count(),
            "structured_collection_count": structured["document_count"],
            "source_row_count": source_row_count,
            "embedding_dimensions": len(query_embeddings[0]) if query_embeddings else 0,
            "structured_build_seconds": structured_build_seconds,
            "structured_embedding_seconds": structured["embedding_seconds"],
            "structured_query_seconds": structured_query_seconds,
            "hybrid_query_seconds": hybrid_query_seconds,
            "legacy_query_seconds": legacy_query_seconds,
            "reranker_query_seconds": reranker_query_seconds,
            "structured_peak_rss_delta_kib": structured["peak_rss_delta_kib"],
        },
        "metrics": {
            "legacy_raw_vector": summarize(legacy_per_case),
            "m4_structured_vector": summarize(structured_vector),
            "m4_structured_hybrid": summarize(structured_hybrid),
            "m4_structured_reranker": summarize(structured_reranker),
        },
        "threshold_sweep": threshold_results,
        "per_case": {
            "legacy_raw_vector": legacy_per_case,
            "m4_structured_vector": structured_vector,
            "m4_structured_hybrid": structured_hybrid,
            "m4_structured_reranker": structured_reranker,
        },
        "scope_note": (
            "Fair comparison uses the same 40 positive and 10 negative M0 cases and the same "
            "1708 root-level CSV rows as the legacy collection. The 164386-record recursive "
            "cleaned corpus is not embedded in this comparison because Ollama rejects batches "
            "whose combined input exceeds its context length; the production builder now uses "
            "configurable batch size 4."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"output": str(args.output), "metrics": report["metrics"]}, ensure_ascii=False, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
