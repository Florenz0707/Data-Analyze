"""Deterministic read-only adapters for M8 evaluation and service integration."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from .core import ToolContext, ToolHandler


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _match_time(item: Mapping[str, Any], arguments: Mapping[str, Any]) -> bool:
    timestamp = _time(item.get("timestamp"))
    start = _time(arguments.get("start"))
    end = _time(arguments.get("end"))
    return (
        timestamp is None
        or (start is None or timestamp >= start)
        and (end is None or timestamp <= end)
    )


def _copy_items(items: Iterable[Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
    return [{str(key): value for key, value in item.items()} for item in list(items)[:limit]]


class InMemoryReadOnlyDataSource:
    """A bounded, immutable-at-query-boundary source for adapters and evaluation.

    Real deployments can replace this object with clients for their read APIs;
    this class intentionally exposes no mutation method.
    """

    def __init__(
        self,
        *,
        logs: Iterable[Mapping[str, Any]] = (),
        metrics: Iterable[Mapping[str, Any]] = (),
        deployments: Iterable[Mapping[str, Any]] = (),
        dependencies: Iterable[Mapping[str, Any]] = (),
        incidents: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        self._records = {
            "logs": tuple(dict(item) for item in logs),
            "metrics": tuple(dict(item) for item in metrics),
            "deployments": tuple(dict(item) for item in deployments),
            "dependencies": tuple(dict(item) for item in dependencies),
            "incidents": tuple(dict(item) for item in incidents),
        }

    def search_logs(self, arguments: Mapping[str, Any], context: ToolContext) -> Mapping[str, Any]:
        return self._search_text("logs", arguments, context, "keyword")

    def query_metrics(
        self, arguments: Mapping[str, Any], context: ToolContext
    ) -> Mapping[str, Any]:
        names = set(arguments.get("metric_names", []))
        items = [
            item
            for item in self._records["metrics"]
            if (not names or item.get("name") in names)
            and (not arguments.get("service") or item.get("service") == arguments["service"])
            and _match_time(item, arguments)
        ]
        return {
            "source": "metrics-read-api",
            "items": _copy_items(items, arguments.get("limit", 100)),
        }

    def get_deployments(
        self, arguments: Mapping[str, Any], context: ToolContext
    ) -> Mapping[str, Any]:
        return self._filter_common("deployments", arguments, context)

    def get_service_dependencies(
        self, arguments: Mapping[str, Any], context: ToolContext
    ) -> Mapping[str, Any]:
        items = [
            item
            for item in self._records["dependencies"]
            if item.get("service") == arguments.get("service")
            and arguments.get("direction", "both") in {"both", item.get("direction")}
            and int(item.get("depth", 1)) <= arguments.get("depth", 1)
        ]
        return {
            "source": "dependency-read-api",
            "items": _copy_items(items, arguments.get("limit", 100)),
        }

    def search_incidents(
        self, arguments: Mapping[str, Any], context: ToolContext
    ) -> Mapping[str, Any]:
        return self._search_text("incidents", arguments, context, "query")

    def handlers(self) -> dict[str, ToolHandler]:
        return {
            "search_logs": self.search_logs,
            "query_metrics": self.query_metrics,
            "get_deployments": self.get_deployments,
            "get_service_dependencies": self.get_service_dependencies,
            "search_incidents": self.search_incidents,
        }

    def _filter_common(
        self, kind: str, arguments: Mapping[str, Any], context: ToolContext
    ) -> Mapping[str, Any]:
        del context
        items = [
            item
            for item in self._records[kind]
            if (not arguments.get("service") or item.get("service") == arguments["service"])
            and (
                not arguments.get("environment")
                or item.get("environment") == arguments["environment"]
            )
            and _match_time(item, arguments)
        ]
        return {
            "source": f"{kind}-read-api",
            "items": _copy_items(items, arguments.get("limit", 100)),
        }

    def _search_text(
        self,
        kind: str,
        arguments: Mapping[str, Any],
        context: ToolContext,
        field: str,
    ) -> Mapping[str, Any]:
        del context
        needle = str(arguments.get(field, "")).lower()
        items = [
            item
            for item in self._records[kind]
            if (not arguments.get("service") or item.get("service") == arguments["service"])
            and (not needle or needle in str(item.get("message", item.get("summary", ""))).lower())
            and _match_time(item, arguments)
        ]
        return {
            "source": f"{kind}-read-api",
            "items": _copy_items(items, arguments.get("limit", 100)),
        }
