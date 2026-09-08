from __future__ import annotations

import logging
import time
from unittest import TestCase

from deepseek_project.agent_workflow import (
    AgentWorkflow,
    AuditStore,
    InMemoryReadOnlyDataSource,
    ToolContext,
    ToolRequest,
    build_default_registry,
)


def context(**overrides):
    values = {
        "actor_user_id": "user-1",
        "role": "analyst",
        "allowed_services": frozenset({"api"}),
        "request_id": "request-1",
        "trace_id": "trace-1",
    }
    values.update(overrides)
    return ToolContext(**values)


class AgentWorkflowTests(TestCase):
    def test_read_only_adapters_filter_fixture_data(self):
        source = InMemoryReadOnlyDataSource(
            logs=[
                {"service": "api", "message": "timeout"},
                {"service": "worker", "message": "timeout"},
            ],
            metrics=[{"service": "api", "name": "requests", "value": 3}],
            deployments=[{"service": "api", "environment": "staging", "id": "d1"}],
            dependencies=[
                {"service": "api", "direction": "downstream", "depth": 1, "target": "db"}
            ],
            incidents=[{"service": "api", "summary": "timeout", "id": "i1"}],
        )
        registry = build_default_registry(source.handlers())
        result = AgentWorkflow(registry).run(
            "诊断 api",
            [
                ToolRequest("search_logs", {"service": "api", "keyword": "timeout"}),
                ToolRequest("query_metrics", {"service": "api", "metric_names": ["requests"]}),
                ToolRequest("get_deployments", {"service": "api", "environment": "staging"}),
                ToolRequest("get_service_dependencies", {"service": "api"}),
                ToolRequest("search_incidents", {"service": "api", "query": "timeout"}),
            ],
            context(role="operator_readonly"),
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual([item["result_count"] for item in result["results"]], [1, 1, 1, 1, 1])

    def test_token_budget_and_audit_store_stop_expansion(self):
        store = AuditStore(max_events=1)
        registry = build_default_registry({"search_logs": lambda *_: {"items": [{"ok": True}]}})
        result = AgentWorkflow(registry, audit_store=store).run(
            "查询",
            [ToolRequest("search_logs", {})],
            context(token_budget=1),
        )
        self.assertEqual(result["status"], "needs_human_review")
        self.assertEqual(result["steps"], 0)

        result = AgentWorkflow(registry, audit_store=store).run(
            "查询",
            [ToolRequest("search_logs", {})],
            context(),
        )
        self.assertEqual(len(store.search(trace_id="trace-1")), 1)

    def test_registry_exposes_only_versioned_read_only_schemas(self):
        schemas = build_default_registry().schemas()

        self.assertEqual(
            set(schemas),
            {
                "search_logs",
                "query_metrics",
                "get_deployments",
                "get_service_dependencies",
                "search_incidents",
            },
        )
        self.assertTrue(all(schema["additionalProperties"] is False for schema in schemas.values()))

    def test_fixed_workflow_executes_injected_read_only_handler(self):
        registry = build_default_registry(
            {
                "search_logs": lambda arguments, tool_context: {
                    "source": "fake",
                    "items": [{"service": arguments["service"]}],
                }
            }
        )

        result = AgentWorkflow(registry).run(
            "查找 api 错误",
            [
                ToolRequest(
                    "search_logs",
                    {
                        "service": "api",
                        "keyword": "error",
                        "start": "2026-09-04T00:00:00Z",
                        "end": "2026-09-04T00:30:00Z",
                    },
                )
            ],
            context(),
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["results"][0]["result_count"], 1)
        self.assertEqual(result["trace_id"], "trace-1")

    def test_default_backend_never_fabricates_evidence(self):
        result = AgentWorkflow(build_default_registry()).run(
            "查询指标",
            [ToolRequest("query_metrics", {"metric_names": ["deepseek_http_requests_total"]})],
            context(),
        )

        self.assertEqual(result["status"], "needs_human_review")
        self.assertEqual(result["results"][0]["error_code"], "BACKEND_UNAVAILABLE")

    def test_role_and_service_boundaries_are_audited(self):
        registry = build_default_registry({"get_deployments": lambda *_: {"items": [{"id": 1}]}})
        with self.assertLogs(
            "deepseek_project.agent_workflow.core", level=logging.INFO
        ) as captured:
            result = AgentWorkflow(registry).run(
                "查询发布",
                [ToolRequest("get_deployments", {"service": "other"})],
                context(role="analyst"),
            )

        self.assertEqual(result["status"], "needs_human_review")
        self.assertEqual(result["results"][0]["error_code"], "ROLE_FORBIDDEN")
        self.assertIn("agent.tool.audit", captured.output[-1])
        self.assertNotIn("Bearer", captured.output[-1])

    def test_identity_override_and_step_budget_are_rejected(self):
        registry = build_default_registry({"search_logs": lambda *_: {"items": [{"ok": True}]}})
        workflow = AgentWorkflow(registry, max_steps=1)
        result = workflow.run(
            "查询",
            [
                ToolRequest("search_logs", {"actor_user_id": "admin"}),
                ToolRequest("search_logs", {}),
            ],
            context(),
        )
        self.assertEqual(result["status"], "needs_human_review")
        self.assertEqual(result["steps"], 0)

        result = workflow.run(
            "查询",
            [ToolRequest("search_logs", {"actor_user_id": "admin"})],
            context(),
        )
        self.assertEqual(result["results"][0]["error_code"], "IDENTITY_OVERRIDE")

    def test_timeout_stops_workflow(self):
        def slow_handler(*_):
            time.sleep(0.05)
            return {"items": [{"late": True}]}

        result = AgentWorkflow(build_default_registry({"search_logs": slow_handler})).run(
            "查询", [ToolRequest("search_logs", {})], context(deadline_seconds=0.001)
        )

        self.assertEqual(result["status"], "needs_human_review")
        self.assertEqual(result["results"][0]["error_code"], "TOOL_TIMEOUT")
