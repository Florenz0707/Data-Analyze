#!/usr/bin/env python3
"""Measure cross-process single-flight behavior with the production Redis cache."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from multiprocessing import Barrier, Queue, get_context
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend" / "django_backend"


def worker(
    backend_path: str,
    key: str,
    counter_key: str,
    rounds: int,
    barrier: Barrier,
    queue: Queue,
) -> None:
    """Run one independent Django/Redis client process."""
    sys.path.insert(0, backend_path)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "deepseek_project.settings")
    import django

    django.setup()

    from redis import Redis

    counter_client = Redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
    answer = "cross-process single-flight answer"
    try:
        for round_number in range(rounds):
            barrier.wait(timeout=10)
            round_key = f"{key}:{round_number}"

            def producer() -> str:
                counter_client.incr(counter_key)
                time.sleep(0.1)
                return answer

            value, cached = get_or_compute_for_process(round_key, producer)
            queue.put(
                {
                    "pid": os.getpid(),
                    "round": round_number,
                    "value": value,
                    "cached": cached,
                }
            )
            barrier.wait(timeout=10)
    finally:
        counter_client.close()


def get_or_compute_for_process(key: str, producer: Any) -> tuple[Any, bool]:
    """Import cache code after Django setup in each spawned process."""
    from deepseek_project.cache_runtime import get_or_compute

    return get_or_compute(
        key,
        producer,
        timeout=30,
        cache_kind="m2_cross_process",
        validator=lambda value: isinstance(value, str) and bool(value),
    )


def run(processes: int, rounds: int) -> dict[str, Any]:
    if processes < 2 or rounds < 1:
        raise ValueError("processes must be >= 2 and rounds must be >= 1")

    from redis import Redis

    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    client = Redis.from_url(redis_url)
    client.ping()
    namespace = uuid.uuid4().hex
    cache_key = f"m2:cross-process:{namespace}"
    counter_key = f"m2:cross-process:producer-count:{namespace}"
    client.delete(cache_key, counter_key, *[f"{cache_key}:{index}" for index in range(rounds)])

    context = get_context("spawn")
    barrier = context.Barrier(processes)
    queue = context.Queue()
    children = [
        context.Process(
            target=worker,
            args=(str(BACKEND), cache_key, counter_key, rounds, barrier, queue),
        )
        for _ in range(processes)
    ]
    started = time.perf_counter()
    for child in children:
        child.start()
    results = [queue.get(timeout=rounds * 20) for _ in range(processes * rounds)]
    for child in children:
        child.join(timeout=10)
    elapsed = time.perf_counter() - started

    producer_count = int(client.get(counter_key) or 0)
    values = {item["value"] for item in results}
    exit_codes = [child.exitcode for child in children]
    client.delete(cache_key, counter_key, *[f"{cache_key}:{index}" for index in range(rounds)])
    client.close()

    expected_calls = rounds
    return {
        "redis_url": redis_url.split("@")[-1],
        "processes": processes,
        "rounds": rounds,
        "total_calls": processes * rounds,
        "producer_count": producer_count,
        "expected_producer_count": expected_calls,
        "all_values_equal": values == {"cross-process single-flight answer"},
        "child_exit_codes": exit_codes,
        "elapsed_seconds": round(elapsed, 3),
        "pass": (
            producer_count == expected_calls
            and values == {"cross-process single-flight answer"}
            and all(code == 0 for code in exit_codes)
        ),
        "scope": "independent spawned Django processes using Redis atomic lock; no real model call",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--processes", type=int, default=4)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.processes, args.rounds)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
