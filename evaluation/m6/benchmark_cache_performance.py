#!/usr/bin/env python3
"""Measure the M6 cache layer against the local Redis instance.

This benchmark deliberately uses deterministic producers instead of a real LLM
or embedding service.  It measures cache behavior and overhead, not model
quality or end-to-end chat latency.
"""

# Django must be initialized before importing application models/services.
# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import platform
import statistics
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import django

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend" / "django_backend"
DEFAULT_OUTPUT = Path(__file__).with_name("evidence") / "cache_performance_baseline.json"

sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "deepseek_project.settings")
django.setup()

from django.conf import settings  # noqa: E402
from django.core.cache import cache  # noqa: E402

from deepseek_api.models import APIKey  # noqa: E402
from deepseek_api.services import (  # noqa: E402
    _build_reply_cache_key,
    compute_cached_reply,
    get_cached_reply,
    set_cached_reply,
)
from deepseek_project.cache_runtime import (  # noqa: E402
    build_retrieval_cache_key,
    cache_delete,
    cache_metrics_snapshot,
    get_or_compute,
    reset_cache_metrics,
)
from topklogsystem import TopKLogSystem  # noqa: E402

logging.basicConfig(level=logging.WARNING)
logging.getLogger("topklogsystem").setLevel(logging.WARNING)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def summarize(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "min_ms": min(values) * 1000 if values else None,
        "p50_ms": statistics.median(values) * 1000 if values else None,
        "p95_ms": percentile(values, 0.95) * 1000 if values else None,
        "p99_ms": percentile(values, 0.99) * 1000 if values else None,
        "max_ms": max(values) * 1000 if values else None,
        "mean_ms": statistics.fmean(values) * 1000 if values else None,
    }


def redis_info() -> dict[str, int | str | None]:
    backend = getattr(cache, "_cache", None)
    get_client = getattr(backend, "get_client", None)
    if not callable(get_client):
        return {"backend": type(backend).__name__, "redis_version": None}
    info = get_client(write=False).info()
    return {
        "backend": type(backend).__name__,
        "redis_version": info.get("redis_version"),
        "used_memory_bytes": int(info.get("used_memory", 0)),
        "evicted_keys": int(info.get("evicted_keys", 0)),
        "connected_clients": int(info.get("connected_clients", 0)),
    }


class FakeRetriever:
    def __init__(self) -> None:
        self.calls = 0
        self.kwargs: dict[str, Any] = {}

    def as_retriever(self, **kwargs: Any) -> FakeRetriever:
        self.kwargs = kwargs
        return self

    def retrieve(self, query: str) -> list[Any]:
        self.calls += 1
        node = SimpleNamespace(
            node_id="benchmark-log-1",
            metadata={"source": "benchmark"},
        )
        return [SimpleNamespace(node=node, text=f"evidence for {query}", score=0.99)]


def build_fake_retrieval_system(index_version: str) -> tuple[TopKLogSystem, FakeRetriever]:
    system = TopKLogSystem.__new__(TopKLogSystem)
    retriever = FakeRetriever()
    system.log_index = retriever
    system.embedding = object()
    system.embedding_key = SimpleNamespace(provider="benchmark", model="embedding-v1")
    system.index_source_version = index_version
    system.index_version = index_version
    system.retrieval_mode = "vector"
    system.retrieval_candidate_multiplier = 3
    system.retrieval_min_score = 0.0
    system.hybrid_vector_weight = 0.7
    system.hybrid_lexical_weight = 0.3
    system.reranker_enabled = False
    system.cache_schema_version = "m6-cache-benchmark-v1"
    system.retrieval_cache_ttl = 300
    system.cache_max_object_bytes = int(settings.CACHE_MAX_OBJECT_BYTES)
    system.default_top_k = 1
    return system, retriever


def measure_reply_cache(iterations: int, namespace: str) -> tuple[dict[str, Any], list[str]]:
    user = APIKey(user=f"m6-cache-benchmark-{namespace}")
    prompt = f"M6 cache warm reply {namespace}"
    session_id = f"m6-cache-session-{namespace}"
    reply = "# 问题诊断\nbenchmark cached answer"
    if not set_cached_reply(prompt, reply, session_id, user):
        raise RuntimeError("reply cache seed was not stored")
    key = _build_reply_cache_key(prompt, session_id, user)
    latencies = []
    values = []
    for _ in range(iterations):
        started = time.perf_counter()
        value = get_cached_reply(prompt, session_id, user)
        latencies.append(time.perf_counter() - started)
        values.append(value)
    if any(value != reply for value in values):
        raise RuntimeError("reply cache returned an unexpected value")

    cold_latencies = []
    cold_keys = []
    for index in range(max(10, min(iterations, 50))):
        cold_prompt = f"M6 cache cold reply {namespace}-{index}"
        cold_key = _build_reply_cache_key(cold_prompt, session_id, user)
        started = time.perf_counter()
        value, cached = compute_cached_reply(
            cold_prompt,
            session_id,
            user,
            lambda: reply,
        )
        cold_latencies.append(time.perf_counter() - started)
        cold_keys.append(cold_key)
        if value != reply or cached:
            raise RuntimeError("reply cache cold path did not compute exactly once")
    return {
        "warm_hit": summarize(latencies),
        "cold_compute_and_store": summarize(cold_latencies),
        "warm_hit_value_chars": len(reply),
    }, [key, *cold_keys]


def measure_retrieval_cache(iterations: int, namespace: str) -> tuple[dict[str, Any], list[str]]:
    index_version = f"m6-cache-benchmark-index-{namespace}"
    system, retriever = build_fake_retrieval_system(index_version)
    query = f"M6 retrieval warm query {namespace}"
    common = {
        "index_version": index_version,
        "embedding_provider": "benchmark",
        "embedding_model": "embedding-v1",
        "top_k": 1,
        "retrieval_mode": "vector",
        "candidate_multiplier": 3,
        "min_score": 0.0,
        "vector_weight": 0.7,
        "lexical_weight": 0.3,
        "reranker_enabled": False,
        "metadata_filter": {},
        "schema_version": "m6-cache-benchmark-v1",
        "namespace": "retrieval-v1",
    }
    warm_key = build_retrieval_cache_key(query, **common)
    cold_latencies = []
    cold_keys = []
    for index in range(max(10, min(iterations, 50))):
        cold_query = f"M6 retrieval cold query {namespace}-{index}"
        cold_key = build_retrieval_cache_key(cold_query, **common)
        started = time.perf_counter()
        result = system.retrieve_logs(cold_query, top_k=1)
        cold_latencies.append(time.perf_counter() - started)
        cold_keys.append(cold_key)
        if len(result) != 1:
            raise RuntimeError("retrieval cold path returned an unexpected result")

    first_started = time.perf_counter()
    first_result = system.retrieve_logs(query, top_k=1)
    first_latency = time.perf_counter() - first_started
    if len(first_result) != 1:
        raise RuntimeError("retrieval warm seed returned an unexpected result")
    warm_latencies = []
    for _ in range(iterations):
        started = time.perf_counter()
        result = system.retrieve_logs(query, top_k=1)
        warm_latencies.append(time.perf_counter() - started)
        if len(result) != 1:
            raise RuntimeError("retrieval cache returned an unexpected value")

    filtered_a = build_retrieval_cache_key(query, **{**common, "metadata_filter": {"source": "a"}})
    filtered_b = build_retrieval_cache_key(query, **{**common, "metadata_filter": {"source": "b"}})
    return {
        "warm_hit": summarize(warm_latencies),
        "cold_retrieve_and_store": summarize(cold_latencies),
        "warm_seed_seconds": first_latency,
        "retriever_calls": retriever.calls,
        "expected_retriever_calls": max(10, min(iterations, 50)) + 1,
        "filter_identity_isolated": filtered_a != filtered_b,
    }, [warm_key, *cold_keys]


def run_single_flight_round(key: str, concurrency: int, answer: str) -> tuple[float, int]:
    ready_barrier = threading.Barrier(concurrency + 1)
    producer_calls = 0
    producer_lock = threading.Lock()

    def producer() -> str:
        nonlocal producer_calls
        with producer_lock:
            producer_calls += 1
        time.sleep(0.05)
        return answer

    def call() -> tuple[str, bool]:
        ready_barrier.wait(timeout=5)
        return get_or_compute(
            key,
            producer,
            timeout=30,
            cache_kind="benchmark",
            validator=lambda value: isinstance(value, str) and bool(value),
        )

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(call) for _ in range(concurrency)]
        ready_barrier.wait(timeout=5)
        results = [future.result() for future in futures]
    if producer_calls != 1 or any(value != answer for value, _ in results):
        raise RuntimeError("single-flight did not merge concurrent producers")
    return time.perf_counter() - started, producer_calls


def measure_single_flight(
    rounds: int, concurrency: int, namespace: str
) -> tuple[dict[str, Any], list[str]]:
    latencies = []
    producer_counts = []
    keys = []
    answer = f"single-flight answer {namespace}"
    for round_number in range(rounds):
        key = f"m6:benchmark:single-flight:{namespace}:{round_number}"
        keys.append(key)
        elapsed, producer_calls = run_single_flight_round(key, concurrency, answer)
        latencies.append(elapsed)
        producer_counts.append(producer_calls)
    return {
        "rounds": rounds,
        "concurrency": concurrency,
        "wall_time": summarize(latencies),
        "producer_count_total": sum(producer_counts),
        "producer_count_per_round": producer_counts,
        "all_rounds_one_producer": all(count == 1 for count in producer_counts),
        "scope": "one Python worker process; local Future plus configured Redis lock",
    }, keys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--single-flight-rounds", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.iterations < 20 or args.single_flight_rounds < 1 or args.concurrency < 2:
        raise SystemExit("iterations>=20, single-flight-rounds>=1 and concurrency>=2 are required")

    backend_name = type(getattr(cache, "_cache", None)).__name__
    if "Redis" not in backend_name:
        raise SystemExit(f"expected the production Redis cache backend, got {backend_name}")
    redis_before = redis_info()
    namespace = uuid.uuid4().hex
    reset_cache_metrics()
    benchmark_started = time.perf_counter()
    keys: list[str] = []
    try:
        reply, reply_keys = measure_reply_cache(args.iterations, namespace)
        keys.extend(reply_keys)
        retrieval, retrieval_keys = measure_retrieval_cache(args.iterations, namespace)
        keys.extend(retrieval_keys)
        single_flight, single_flight_keys = measure_single_flight(
            args.single_flight_rounds, args.concurrency, namespace
        )
        keys.extend(single_flight_keys)
        redis_during = redis_info()
    finally:
        for key in keys:
            cache_delete(key, cache_kind="benchmark_cleanup")
        cache.close()
    redis_after = redis_info()

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "benchmark": "M6 cache performance baseline",
        "scope": "cache-layer microbenchmark; deterministic fake producer/retriever; no external model or network",
        "conditions": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "backend_root": str(BACKEND.relative_to(ROOT)),
            "dataset": "not used; deterministic fake producer/retriever",
            "vector_count": 0,
            "input_output_tokens": "not applicable; no model call",
            "django_testing": bool(getattr(settings, "TESTING", False)),
            "redis_url_host_port": "127.0.0.1:6379",
            "iterations": args.iterations,
            "single_flight_rounds": args.single_flight_rounds,
            "single_flight_concurrency": args.concurrency,
            "cache_max_object_bytes": int(settings.CACHE_MAX_OBJECT_BYTES),
        },
        "duration_seconds": time.perf_counter() - benchmark_started,
        "redis_before": redis_before,
        "redis_during": redis_during,
        "redis_after": redis_after,
        "reply_cache": reply,
        "retrieval_cache": retrieval,
        "single_flight": single_flight,
        "cache_metrics": cache_metrics_snapshot(),
        "limitations": [
            "This is not an end-to-end HTTP chat benchmark and does not measure real model generation latency.",
            "Single-flight concurrency is within one Python worker process; multi-process contention requires a separate deployment benchmark.",
            "Redis memory and eviction values are instance-wide gauges and may include unrelated local keys.",
        ],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "reply_warm_hit": report["reply_cache"]["warm_hit"],
                "retrieval_warm_hit": report["retrieval_cache"]["warm_hit"],
                "single_flight": report["single_flight"],
                "redis": report["redis_after"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
