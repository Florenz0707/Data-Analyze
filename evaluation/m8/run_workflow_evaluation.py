#!/usr/bin/env python3
"""Run the deterministic offline M8 Workflow safety and contract evaluation."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend" / "django_backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from deepseek_project.agent_workflow import (  # noqa: E402
    AgentWorkflow,
    ToolContext,
    ToolRequest,
    build_default_registry,
)


def handlers(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    del context
    return {
        "source": f"fixture-{arguments.get('service', 'global')}",
        "items": [{"evidence": "fixture", "service": arguments.get("service", "api")}],
    }


def run_case(
    name: str, workflow: AgentWorkflow, requests: list[ToolRequest], context: ToolContext
) -> dict[str, Any]:
    result = workflow.run(name, requests, context)
    return {
        "name": name,
        "status": result["status"],
        "steps": result["steps"],
        "error_codes": [item["error_code"] for item in result["results"]],
        "trace_id": result["trace_id"],
    }


def evaluate() -> dict[str, Any]:
    registry = build_default_registry(
        {
            name: handlers
            for name in (
                "search_logs",
                "query_metrics",
                "get_deployments",
                "get_service_dependencies",
                "search_incidents",
            )
        }
    )
    workflow = AgentWorkflow(registry)
    operator = ToolContext(
        actor_user_id="evaluation-user",
        role="operator_readonly",
        allowed_services=frozenset({"api", "worker"}),
        request_id="m8-evaluation-request",
        trace_id="m8-evaluation-trace",
    )
    analyst = ToolContext(
        actor_user_id="evaluation-user",
        role="analyst",
        allowed_services=frozenset({"api"}),
        request_id="m8-evaluation-request",
        trace_id="m8-evaluation-trace",
    )
    valid_requests = [
        ToolRequest("search_logs", {"service": "api", "keyword": "error"}),
        ToolRequest(
            "query_metrics", {"service": "api", "metric_names": ["deepseek_http_requests_total"]}
        ),
        ToolRequest("get_deployments", {"service": "api"}),
        ToolRequest("get_service_dependencies", {"service": "api"}),
        ToolRequest("search_incidents", {"service": "api", "query": "timeout"}),
    ]
    cases = [
        run_case(f"valid task {index + 1:02d}", workflow, [request], operator)
        for index in range(8)
        for request in valid_requests
    ]
    security_cases = [
        ("unknown tool", [ToolRequest("shell", {})], operator),
        ("role denied", [ToolRequest("get_deployments", {})], analyst),
        ("identity override", [ToolRequest("search_logs", {"actor_user_id": "admin"})], analyst),
        (
            "range rejected",
            [
                ToolRequest(
                    "search_logs", {"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"}
                )
            ],
            analyst,
        ),
        ("service denied", [ToolRequest("search_logs", {"service": "worker"})], analyst),
        (
            "metric denied",
            [ToolRequest("query_metrics", {"metric_names": ["bad metric"]})],
            analyst,
        ),
        ("step denied", [ToolRequest("search_logs", {"limit": 101})], analyst),
        ("unknown argument", [ToolRequest("search_logs", {"command": "rm"})], analyst),
        ("empty question", [ToolRequest("search_logs", {})], analyst),
        ("step budget", [ToolRequest("search_logs", {})] * 6, operator),
    ]
    for name, requests, case_context in security_cases:
        cases.append(
            run_case("" if name == "empty question" else name, workflow, requests, case_context)
        )
    passed = (
        len(cases) == 50
        and all(item["status"] == "completed" for item in cases[:40])
        and cases[40]["error_codes"] == ["TOOL_NOT_ALLOWED"]
        and cases[41]["error_codes"] == ["ROLE_FORBIDDEN"]
        and cases[42]["error_codes"] == ["IDENTITY_OVERRIDE"]
        and cases[43]["error_codes"] == ["RANGE_EXCEEDED"]
        and cases[44]["error_codes"] == ["SERVICE_FORBIDDEN"]
        and cases[45]["error_codes"] == ["METRIC_FORBIDDEN"]
        and cases[46]["error_codes"] == ["SCHEMA_INVALID"]
        and cases[47]["error_codes"] == ["SCHEMA_INVALID"]
        and cases[48]["status"] == "needs_human_review"
        and cases[49]["status"] == "needs_human_review"
    )
    return {
        "suite": "m8_fixed_workflow_offline",
        "cases": cases,
        "case_count": len(cases),
        "pass": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    result = evaluate()
    result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
