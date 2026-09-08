"""Safe fixed workflow for M8's first read-only tool set.

The workflow deliberately does not let a model choose identity, authorization,
or arbitrary callables. Production integrations must inject a handler for a
registered tool and keep the handler read-only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

ToolHandler = Callable[[Mapping[str, Any], "ToolContext"], Mapping[str, Any]]
_SERVICE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_ALLOWED_TOOLS = (
    "search_logs",
    "query_metrics",
    "get_deployments",
    "get_service_dependencies",
    "search_incidents",
)
_TOOL_ALLOWED_FIELDS = {
    "search_logs": {"service", "keyword", "start", "end", "limit"},
    "query_metrics": {"service", "metric_names", "start", "end", "step_seconds", "limit"},
    "get_deployments": {"service", "environment", "start", "end", "limit"},
    "get_service_dependencies": {"service", "direction", "depth", "limit"},
    "search_incidents": {"service", "query", "start", "end", "status", "limit"},
}
_SENSITIVE_KEYS = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization|cookie)"
)


class ToolExecutionError(Exception):
    """A stable, safe-to-return tool failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ToolContext:
    """Server-derived identity and bounded execution settings."""

    actor_user_id: str
    role: str
    allowed_services: frozenset[str] = frozenset()
    tenant_id: str | None = None
    request_id: str = "-"
    trace_id: str = "-"
    deadline_seconds: float = 2.0
    max_bytes: int = 262_144
    token_budget: int = 2048


@dataclass(frozen=True)
class ToolRequest:
    tool_name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    status: str
    items: tuple[Mapping[str, Any], ...] = ()
    source: str = ""
    error_code: str | None = None
    truncated: bool = False
    result_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "status": self.status,
            "items": list(self.items),
            "source": self.source,
            "error_code": self.error_code,
            "truncated": self.truncated,
            "result_count": self.result_count,
        }


@dataclass(frozen=True)
class AuditEvent:
    event_name: str
    schema_version: str
    timestamp: str
    request_id: str
    trace_id: str
    actor_user_id: str
    role: str
    tool_name: str
    decision: str
    reason_code: str
    arguments_hash: str
    redacted_arguments: Mapping[str, Any]
    status: str
    duration_ms: float
    result_count: int = 0
    result_bytes: int = 0
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_name": self.event_name,
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "actor_user_id": self.actor_user_id,
            "role": self.role,
            "tool_name": self.tool_name,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "arguments_hash": self.arguments_hash,
            "redacted_arguments": dict(self.redacted_arguments),
            "status": self.status,
            "duration_ms": self.duration_ms,
            "result_count": self.result_count,
            "result_bytes": self.result_bytes,
            "truncated": self.truncated,
        }


class AuditStore:
    """Bounded in-process audit index for tests and local read-only inspection."""

    def __init__(self, max_events: int = 10_000) -> None:
        self.max_events = max_events
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self._events.append(event)
        del self._events[: -self.max_events]

    def search(
        self, *, trace_id: str | None = None, tool_name: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            event.as_dict()
            for event in self._events
            if (trace_id is None or event.trace_id == trace_id)
            and (tool_name is None or event.tool_name == tool_name)
        ]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    roles: frozenset[str]
    handler: ToolHandler
    max_items: int
    source: str


def _safe_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _safe_json(v) for k, v in list(value.items())[:50]}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in list(value)[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _redact_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): "[REDACTED]" if _SENSITIVE_KEYS.search(str(key)) else _safe_json(value)
        for key, value in list(arguments.items())[:50]
    }


def _arguments_hash(arguments: Mapping[str, Any]) -> str:
    encoded = json.dumps(_redact_arguments(arguments), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse_time(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ToolExecutionError("SCHEMA_INVALID", f"{field_name} 必须是 ISO 时间字符串")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolExecutionError("SCHEMA_INVALID", f"{field_name} 时间格式无效") from exc
    if parsed.tzinfo is None:
        raise ToolExecutionError("SCHEMA_INVALID", f"{field_name} 必须包含时区")
    return parsed.astimezone(UTC)


def _validate_arguments(name: str, arguments: Mapping[str, Any], context: ToolContext) -> None:
    if not isinstance(arguments, Mapping):
        raise ToolExecutionError("SCHEMA_INVALID", "arguments 必须是对象")
    forbidden = {"actor_user_id", "tenant_id", "role"} & set(arguments)
    if forbidden:
        raise ToolExecutionError("IDENTITY_OVERRIDE", "不得覆盖服务端身份字段")
    unknown = set(arguments) - _TOOL_ALLOWED_FIELDS[name]
    if unknown:
        raise ToolExecutionError("SCHEMA_INVALID", "包含未定义工具参数")
    service = arguments.get("service")
    if service is not None:
        if not isinstance(service, str) or not _SERVICE_RE.fullmatch(service):
            raise ToolExecutionError("SCHEMA_INVALID", "service 格式无效")
        if context.allowed_services and service not in context.allowed_services:
            raise ToolExecutionError("SERVICE_FORBIDDEN", "service 不在授权范围")
    limit = arguments.get("limit", 100)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ToolExecutionError("SCHEMA_INVALID", "limit 必须在 1～100 之间")
    if name == "query_metrics":
        names = arguments.get("metric_names")
        if not isinstance(names, list) or not names or len(names) > 20:
            raise ToolExecutionError("SCHEMA_INVALID", "metric_names 必须是 1～20 项列表")
        if any(
            not isinstance(item, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_:]{0,127}", item)
            for item in names
        ):
            raise ToolExecutionError("METRIC_FORBIDDEN", "metric_names 含有非法指标")
        step = arguments.get("step_seconds", 60)
        if not isinstance(step, int) or not 15 <= step <= 3600:
            raise ToolExecutionError("SCHEMA_INVALID", "step_seconds 必须在 15～3600 之间")
    if name == "get_service_dependencies":
        depth = arguments.get("depth", 1)
        if not isinstance(depth, int) or not 1 <= depth <= 2:
            raise ToolExecutionError("SCHEMA_INVALID", "depth 必须在 1～2 之间")
        if arguments.get("direction", "both") not in {"upstream", "downstream", "both"}:
            raise ToolExecutionError("SCHEMA_INVALID", "direction 无效")
    if name in {"search_logs", "search_incidents"}:
        text_key = "keyword" if name == "search_logs" else "query"
        text = arguments.get(text_key, "")
        if not isinstance(text, str) or len(text) > 200:
            raise ToolExecutionError("SCHEMA_INVALID", f"{text_key} 最长 200 字符")
    if "start" in arguments or "end" in arguments:
        if "start" not in arguments or "end" not in arguments:
            raise ToolExecutionError("SCHEMA_INVALID", "start 和 end 必须同时提供")
        start = _parse_time(arguments["start"], "start")
        end = _parse_time(arguments["end"], "end")
        if end <= start:
            raise ToolExecutionError("SCHEMA_INVALID", "时间范围必须为正")
        max_seconds = {
            "search_logs": 7200,
            "query_metrics": 86400,
            "get_deployments": 2592000,
            "search_incidents": 7776000,
        }.get(name, 86400)
        if (end - start).total_seconds() > max_seconds:
            raise ToolExecutionError("RANGE_EXCEEDED", "查询时间范围超过工具上限")


def _unconfigured_handler(arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
    del arguments, context
    raise ToolExecutionError("BACKEND_UNAVAILABLE", "只读工具后端尚未配置")


class ToolRegistry:
    """Deny-by-default registry containing only explicitly registered tools."""

    def __init__(self, specs: Mapping[str, ToolSpec] | None = None) -> None:
        self._specs = dict(specs or {})
        unexpected = set(self._specs) - set(_ALLOWED_TOOLS)
        if unexpected:
            raise ValueError(f"unsupported read-only tools: {sorted(unexpected)}")

    def register(self, spec: ToolSpec) -> None:
        if spec.name not in _ALLOWED_TOOLS:
            raise ValueError("only the approved read-only tools may be registered")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise ToolExecutionError("TOOL_NOT_ALLOWED", "工具不在只读注册表") from exc

    def schemas(self) -> dict[str, dict[str, Any]]:
        """Expose versionable JSON-Schema-like contracts for model/tool adapters."""
        return {name: _tool_schema(name) for name in self._specs}


class AgentWorkflow:
    """Fixed classify → plan → execute → verify → answer workflow."""

    def __init__(
        self, registry: ToolRegistry, max_steps: int = 5, audit_store: AuditStore | None = None
    ) -> None:
        self.registry = registry
        self.max_steps = max_steps
        self.audit_store = audit_store

    def run(
        self, question: str, requests: list[ToolRequest], context: ToolContext
    ) -> dict[str, Any]:
        started = time.perf_counter()
        if not isinstance(question, str) or not question.strip():
            return self._handoff("SCHEMA_INVALID", "问题不能为空", context, started)
        if len(requests) > self.max_steps:
            return self._handoff("STEP_LIMIT_EXCEEDED", "超过 Workflow 最大步数", context, started)

        results: list[ToolResult] = []
        token_estimate = max(1, (len(question.encode("utf-8")) + 3) // 4)
        for request in requests:
            token_estimate += max(
                1, len(json.dumps(request.arguments, ensure_ascii=False).encode("utf-8")) // 4
            )
            if token_estimate > context.token_budget:
                return self._handoff(
                    "TOKEN_BUDGET_EXCEEDED", "超过 Workflow Token 预算", context, started
                )
            results.append(self._execute(request, context))
            if results[-1].status == "timeout":
                break

        successful = [
            result for result in results if result.status == "ok" and result.result_count > 0
        ]
        status = "completed" if successful else "needs_human_review"
        answer = (
            f"已完成 {len(successful)} 个只读查询，获得 {sum(item.result_count for item in successful)} 条证据。"
            if successful
            else "当前没有获得可验证证据，建议人工接管或检查只读数据源。"
        )
        return {
            "status": status,
            "classification": "read_only_diagnostic",
            "steps": len(results),
            "token_estimate": token_estimate,
            "results": [result.as_dict() for result in results],
            "answer": answer,
            "trace_id": context.trace_id,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    def _execute(self, request: ToolRequest, context: ToolContext) -> ToolResult:
        started = time.perf_counter()
        args = request.arguments if isinstance(request.arguments, Mapping) else {}
        try:
            spec = self.registry.get(request.tool_name)
            if context.role not in spec.roles:
                raise ToolExecutionError("ROLE_FORBIDDEN", "当前角色无权使用该工具")
            _validate_arguments(spec.name, args, context)
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(spec.handler, args, context)
            try:
                payload = future.result(timeout=min(context.deadline_seconds, 5.0))
            except TimeoutError as exc:
                future.cancel()
                raise ToolExecutionError("TOOL_TIMEOUT", "只读工具调用超时") from exc
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            result = self._result_from_payload(spec, payload, context.max_bytes)
        except ToolExecutionError as exc:
            result = ToolResult(
                request.tool_name,
                "timeout" if exc.code == "TOOL_TIMEOUT" else "denied",
                error_code=exc.code,
            )
            self._audit(request, context, "deny", exc.code, result, started)
            return result
        except Exception:
            logger.exception(
                "agent.tool.handler_failed",
                extra={"event": "agent.tool.handler_failed", "tool_name": request.tool_name},
            )
            result = ToolResult(request.tool_name, "error", error_code="TOOL_FAILED")
            self._audit(request, context, "allow", "HANDLER_FAILED", result, started)
            return result
        self._audit(request, context, "allow", "EXECUTED", result, started)
        return result

    @staticmethod
    def _result_from_payload(
        spec: ToolSpec, payload: Mapping[str, Any], max_bytes: int
    ) -> ToolResult:
        if not isinstance(payload, Mapping):
            raise ToolExecutionError("TOOL_FAILED", "工具返回格式无效")
        raw_items = payload.get("items", [])
        if not isinstance(raw_items, list):
            raise ToolExecutionError("TOOL_FAILED", "工具 items 格式无效")
        items = tuple(item for item in raw_items[: spec.max_items] if isinstance(item, Mapping))
        result = ToolResult(
            spec.name, "ok", items, str(payload.get("source", spec.source)), result_count=len(items)
        )
        encoded = json.dumps(result.as_dict(), ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > max_bytes:
            raise ToolExecutionError("RESPONSE_TOO_LARGE", "工具响应超过大小上限")
        return result

    def _audit(
        self,
        request: ToolRequest,
        context: ToolContext,
        decision: str,
        reason: str,
        result: ToolResult,
        started: float,
    ) -> None:
        event = AuditEvent(
            event_name="agent.tool.audit",
            schema_version="m8-v1",
            timestamp=datetime.now(UTC).isoformat(),
            request_id=context.request_id,
            trace_id=context.trace_id,
            actor_user_id=context.actor_user_id,
            role=context.role,
            tool_name=request.tool_name,
            decision=decision,
            reason_code=reason,
            arguments_hash=_arguments_hash(request.arguments),
            redacted_arguments=_redact_arguments(request.arguments),
            status=result.status,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            result_count=result.result_count,
            result_bytes=len(json.dumps(result.as_dict(), ensure_ascii=False).encode("utf-8")),
            truncated=result.truncated,
        )
        logger.info("agent.tool.audit", extra=event.as_dict())
        if self.audit_store is not None:
            self.audit_store.append(event)

    @staticmethod
    def _handoff(code: str, message: str, context: ToolContext, started: float) -> dict[str, Any]:
        logger.info(
            "agent.workflow.handoff",
            extra={
                "event": "agent.workflow.handoff",
                "reason_code": code,
                "trace_id": context.trace_id,
            },
        )
        return {
            "status": "needs_human_review",
            "classification": "read_only_diagnostic",
            "steps": 0,
            "results": [],
            "answer": message,
            "trace_id": context.trace_id,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }


def build_default_registry(
    handlers: Mapping[str, ToolHandler] | None = None,
) -> ToolRegistry:
    """Build the approved registry; absent handlers remain safely unavailable."""
    handlers = handlers or {}
    roles = {"analyst", "operator_readonly"}
    specs = {
        name: ToolSpec(
            name=name,
            roles=frozenset(
                roles
                if name not in {"get_deployments", "get_service_dependencies", "search_incidents"}
                else {"operator_readonly"}
            ),
            handler=handlers.get(name, _unconfigured_handler),
            max_items=100,
            source=f"{name}-read-api",
        )
        for name in _ALLOWED_TOOLS
    }
    return ToolRegistry(specs)


def _tool_schema(name: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "service": {"type": "string", "pattern": r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$"},
        "start": {"type": "string", "format": "date-time"},
        "end": {"type": "string", "format": "date-time"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    }
    if name == "search_logs":
        properties["keyword"] = {"type": "string", "maxLength": 200}
    elif name == "query_metrics":
        properties.update(
            {
                "metric_names": {"type": "array", "minItems": 1, "maxItems": 20},
                "step_seconds": {"type": "integer", "minimum": 15, "maximum": 3600},
            }
        )
    elif name == "get_deployments":
        properties["environment"] = {"type": "string", "maxLength": 64}
    elif name == "get_service_dependencies":
        properties.update(
            {
                "direction": {"type": "string", "enum": ["upstream", "downstream", "both"]},
                "depth": {"type": "integer", "minimum": 1, "maximum": 2},
            }
        )
    elif name == "search_incidents":
        properties.update(
            {"query": {"type": "string", "maxLength": 200}, "status": {"type": "string"}}
        )
    return {"type": "object", "properties": properties, "additionalProperties": False}
