#!/usr/bin/env python3
"""Measure API answer structure, failures, cache behavior, and warm endpoint latency."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from validate_dataset import load_cases, validate

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = Path(__file__).with_name("gold_queries.jsonl")
DEFAULT_OUTPUT = Path(__file__).with_name("evidence") / "api_baseline.json"
EXPECTED_HEADERS = (
    "# 问题诊断",
    "# 可能原因（按概率降序排序）",
    "# 建议的排查步骤",
    "# 临时缓解措施",
    "# 最终修复建议",
)
REFUSAL_MARKERS = ("证据不足", "没有相关", "未找到相关", "无法确定", "知识库中没有", "缺少证据")


class ApiClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token: str | None = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict, dict]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = self.token
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode() or "{}")
                return response.status, payload, dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                payload = json.loads(raw or "{}")
            except json.JSONDecodeError:
                payload = {"error": raw[:200]}
            return exc.code, payload, dict(exc.headers.items())

    def authenticate(self, username: str, password: str) -> None:
        credentials = {"username": username, "password": password}
        status, _, _ = self.request("POST", "/api/users/register", credentials)
        if status not in {200, 409}:
            raise RuntimeError(f"evaluation user registration failed with HTTP {status}")
        status, _, headers = self.request("POST", "/api/users/login", credentials)
        if status != 200:
            raise RuntimeError(f"evaluation user login failed with HTTP {status}")
        authorization = next(
            (value for key, value in headers.items() if key.lower() == "authorization"), None
        )
        if not authorization:
            raise RuntimeError("login response did not contain Authorization header")
        self.token = authorization


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def latency_summary(values: list[float]) -> dict:
    return {
        "count": len(values),
        "min_seconds": min(values) if values else None,
        "p50_seconds": statistics.median(values) if values else None,
        "p95_seconds": percentile(values, 0.95),
        "max_seconds": max(values) if values else None,
    }


def measure_get(url: str, attempts: int, timeout: float) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    latencies = []
    errors = 0
    for _ in range(attempts):
        started = time.perf_counter()
        try:
            with opener.open(url, timeout=timeout) as response:
                response.read()
                if response.status != 200:
                    errors += 1
        except Exception:
            errors += 1
        latencies.append(time.perf_counter() - started)
    return {**latency_summary(latencies), "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--api-base", default="http://127.0.0.1:8081")
    parser.add_argument("--frontend-url", default="http://127.0.0.1:8082/")
    parser.add_argument("--username", default="m0_evaluation")
    parser.add_argument("--limit", type=int, default=0, help="0 runs every case")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--warm-attempts", type=int, default=20)
    args = parser.parse_args()

    password = os.environ.get("M0_EVAL_PASSWORD")
    if not password:
        raise SystemExit("set M0_EVAL_PASSWORD to a non-production test password")
    cases = load_cases(args.dataset)
    validation = validate(cases)
    if validation["errors"]:
        raise SystemExit(json.dumps(validation, ensure_ascii=False, indent=2))
    if args.limit > 0:
        cases = cases[: args.limit]

    client = ApiClient(args.api_base, args.timeout)
    client.authenticate(args.username, password)
    results = []
    cache_probe = None
    for index, case in enumerate(cases, 1):
        session_id = f"m0_{case['case_id'].lower()}"
        client.request("POST", "/api/sessions", {"session_id": session_id})
        started = time.perf_counter()
        status, payload, _ = client.request(
            "POST",
            "/api/llm/chat",
            {"session_id": session_id, "user_input": case["query"], "use_history": "off"},
        )
        latency = time.perf_counter() - started
        answer = payload.get("reply") if isinstance(payload, dict) else None
        answer = answer if isinstance(answer, str) else ""
        item = {
            "case_id": case["case_id"],
            "category": case["category"],
            "is_negative": case["is_negative"],
            "http_status": status,
            "latency_seconds": latency,
            "answer_chars": len(answer),
            "answer_sha256": hashlib.sha256(answer.encode()).hexdigest() if answer else None,
            "structure_valid": all(header in answer for header in EXPECTED_HEADERS),
            "negative_refusal_marker": any(marker in answer for marker in REFUSAL_MARKERS),
            "error": None if status == 200 else str(payload.get("error", "request failed"))[:200],
        }
        results.append(item)
        if index == 1 and status == 200:
            repeat_started = time.perf_counter()
            repeat_status, repeat_payload, _ = client.request(
                "POST",
                "/api/llm/chat",
                {"session_id": session_id, "user_input": case["query"], "use_history": "off"},
            )
            repeat_latency = time.perf_counter() - repeat_started
            repeat_answer = (
                repeat_payload.get("reply", "") if isinstance(repeat_payload, dict) else ""
            )
            cache_probe = {
                "first_seconds": latency,
                "repeat_seconds": repeat_latency,
                "repeat_http_status": repeat_status,
                "same_answer": answer == repeat_answer,
            }
        print(f"[{index}/{len(cases)}] {case['case_id']} status={status} latency={latency:.3f}s")

    successful = [item for item in results if item["http_status"] == 200]
    latencies = [item["latency_seconds"] for item in successful]
    negative_success = [item for item in successful if item["is_negative"]]
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "api_base": args.api_base,
        "frontend_url": args.frontend_url,
        "evaluation_user": args.username,
        "case_count": len(results),
        "success_count": len(successful),
        "failure_count": len(results) - len(successful),
        "failure_rate": (len(results) - len(successful)) / len(results) if results else 0.0,
        "empty_answer_count": sum(item["answer_chars"] == 0 for item in successful),
        "structure_valid_count": sum(item["structure_valid"] for item in successful),
        "structure_valid_rate": (
            sum(item["structure_valid"] for item in successful) / len(successful)
            if successful
            else 0.0
        ),
        "negative_refusal_marker_rate": (
            sum(item["negative_refusal_marker"] for item in negative_success)
            / len(negative_success)
            if negative_success
            else None
        ),
        "chat_latency": latency_summary(latencies),
        "cache_probe": cache_probe,
        "warm_backend_openapi": measure_get(
            args.api_base.rstrip("/") + "/api/openapi.json",
            args.warm_attempts,
            min(args.timeout, 10),
        ),
        "warm_frontend": measure_get(args.frontend_url, args.warm_attempts, min(args.timeout, 10)),
        "human_evaluation": {
            "cause_correctness": "pending",
            "evidence_consistency": "pending",
            "step_executability": "pending",
        },
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "failure_rate",
                    "structure_valid_rate",
                    "negative_refusal_marker_rate",
                    "chat_latency",
                    "cache_probe",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
