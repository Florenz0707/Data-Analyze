"""Small dependency-free Prometheus metrics registry for the Django service."""

from __future__ import annotations

import math
import os
import re
import resource
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_PATH_ID_RE = re.compile(r"^(?:[0-9]+|[0-9a-f]{16,}|[0-9a-f-]{36})$", re.IGNORECASE)


def _label_key(labels: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(key), _label_value(value)) for key, value in labels.items()))


def _label_value(value: Any) -> str:
    text = str(value)
    return text[:80] if text else "unknown"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _sample(name: str, labels: tuple[tuple[str, str], ...], value: float) -> str:
    if labels:
        rendered = ",".join(f'{key}="{_escape(item)}"' for key, item in labels)
        return f"{name}{{{rendered}}} {value:g}"
    return f"{name} {value:g}"


@dataclass
class _Histogram:
    buckets: tuple[float, ...] = DEFAULT_BUCKETS
    counts: list[int] = field(default_factory=lambda: [0] * (len(DEFAULT_BUCKETS) + 1))
    count: int = 0
    total: float = 0.0

    def observe(self, value: float) -> None:
        value = max(0.0, value)
        self.count += 1
        self.total += value
        for index, boundary in enumerate(self.buckets):
            if value <= boundary:
                self.counts[index] += 1
        self.counts[-1] += 1


class MetricsRegistry:
    """Thread-safe counters, gauges and bounded latency histograms."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], _Histogram] = {}

    def increment(
        self, name: str, value: float = 1.0, labels: Mapping[str, Any] | None = None
    ) -> None:
        if not math.isfinite(value) or value < 0:
            raise ValueError("counter increment must be a non-negative finite number")
        key = (name, _label_key(labels))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def set_gauge(self, name: str, value: float, labels: Mapping[str, Any] | None = None) -> None:
        if not math.isfinite(value):
            raise ValueError("gauge value must be finite")
        with self._lock:
            self._gauges[(name, _label_key(labels))] = value

    def observe(
        self,
        name: str,
        value: float,
        labels: Mapping[str, Any] | None = None,
    ) -> None:
        if not math.isfinite(value):
            raise ValueError("histogram observation must be finite")
        key = (name, _label_key(labels))
        with self._lock:
            histogram = self._histograms.setdefault(key, _Histogram())
            histogram.observe(value)

    def reset(self) -> None:
        global _active_request_count
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
        with _active_request_lock:
            _active_request_count = 0

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            names = (
                {name for name, _ in self._counters}
                | {name for name, _ in self._gauges}
                | {name for name, _ in self._histograms}
            )
            for name in sorted(names):
                if name in {item[0] for item in self._histograms}:
                    lines.append(f"# TYPE {name} histogram")
                    for (metric_name, labels), histogram in sorted(self._histograms.items()):
                        if metric_name != name:
                            continue
                        for boundary, count in zip(
                            (*histogram.buckets, "+Inf"), histogram.counts, strict=True
                        ):
                            bucket_labels = dict(labels)
                            bucket_labels["le"] = boundary
                            lines.append(
                                _sample(f"{name}_bucket", _label_key(bucket_labels), count)
                            )
                        lines.append(_sample(f"{name}_sum", labels, histogram.total))
                        lines.append(_sample(f"{name}_count", labels, histogram.count))
                if name in {item[0] for item in self._counters}:
                    lines.append(f"# TYPE {name} counter")
                    for (metric_name, labels), value in sorted(self._counters.items()):
                        if metric_name == name:
                            lines.append(_sample(name, labels, value))
                if name in {item[0] for item in self._gauges}:
                    lines.append(f"# TYPE {name} gauge")
                    for (metric_name, labels), value in sorted(self._gauges.items()):
                        if metric_name == name:
                            lines.append(_sample(name, labels, value))
        return "\n".join(lines) + ("\n" if lines else "")


metrics = MetricsRegistry()
_active_request_count = 0
_active_request_lock = threading.Lock()


def record_request_start() -> None:
    global _active_request_count
    with _active_request_lock:
        _active_request_count += 1
        active = _active_request_count
    metrics.set_gauge("deepseek_http_requests_in_progress", active)
    metrics.set_gauge("deepseek_worker_in_progress", active)


def record_request_complete(
    method: str, path: str, status_code: int, duration_seconds: float
) -> None:
    path = normalize_metric_path(path)
    metrics.increment(
        "deepseek_http_requests_total",
        labels={"method": method, "path": path, "status": status_code},
    )
    metrics.observe(
        "deepseek_http_request_duration_seconds",
        duration_seconds,
        labels={"method": method, "path": path},
    )
    global _active_request_count
    with _active_request_lock:
        _active_request_count = max(0, _active_request_count - 1)
        active = _active_request_count
    metrics.set_gauge("deepseek_http_requests_in_progress", active)
    metrics.set_gauge("deepseek_worker_in_progress", active)
    capacity = _worker_capacity()
    metrics.set_gauge("deepseek_worker_capacity", capacity)
    metrics.set_gauge("deepseek_worker_utilization_ratio", min(1.0, active / capacity))


def record_phase(phase: str, duration_seconds: float, fields: Mapping[str, Any]) -> None:
    outcome = str(fields.get("outcome", "success"))
    metrics.increment("deepseek_phase_calls_total", labels={"phase": phase, "outcome": outcome})
    metrics.observe("deepseek_phase_duration_seconds", duration_seconds, labels={"phase": phase})
    if phase == "retrieval":
        count = int(fields.get("retrieval_count", 0) or 0)
        metrics.increment(
            "deepseek_retrieval_requests_total",
            labels={"outcome": "no_evidence" if count == 0 else "with_evidence"},
        )
        for score in fields.get("evidence_scores", ()) or ():
            try:
                metrics.observe("deepseek_retrieval_score", float(score))
            except (TypeError, ValueError):
                continue
    if phase in {"model", "model_pipeline"}:
        provider = str(fields.get("provider", "unknown"))
        model = str(fields.get("model", "unknown"))
        labels = {"provider": provider, "model": model}
        metrics.increment("deepseek_model_calls_total", labels={**labels, "outcome": outcome})
        if outcome == "timeout":
            metrics.increment("deepseek_model_timeouts_total", labels=labels)
        input_tokens = int(fields.get("input_tokens_estimate", 0) or 0)
        output_tokens = int(fields.get("output_tokens_estimate", 0) or 0)
        metrics.increment("deepseek_model_input_tokens_estimate_total", input_tokens, labels=labels)
        metrics.increment(
            "deepseek_model_output_tokens_estimate_total", output_tokens, labels=labels
        )
        rate = _provider_cost_rate(provider)
        metrics.set_gauge(
            "deepseek_provider_cost_usd_per_1k_tokens", rate, labels={"provider": provider}
        )
        metrics.increment(
            "deepseek_provider_cost_usd_estimate_total",
            (input_tokens + output_tokens) / 1000 * rate,
            labels=labels,
        )


def record_generation(output_mode: str, schema_valid: bool, output_tokens: int = 0) -> None:
    outcome = "structured" if schema_valid else output_mode
    metrics.increment("deepseek_structured_output_total", labels={"outcome": outcome})
    metrics.increment("deepseek_generation_output_tokens_estimate_total", max(0, output_tokens))


def _worker_capacity() -> int:
    try:
        from django.conf import settings

        configured = getattr(settings, "OBSERVABILITY_WORKER_CAPACITY", None)
    except Exception:
        configured = None
    configured = configured or os.getenv("OBSERVABILITY_WORKER_CAPACITY", "1")
    try:
        return max(1, int(configured))
    except ValueError:
        return 1


def _provider_cost_rate(provider: str) -> float:
    """Read optional ``provider=USD/1k`` pairs without storing credentials."""
    try:
        from django.conf import settings

        configured = getattr(settings, "OBSERVABILITY_PROVIDER_COST_USD_PER_1K", "")
    except Exception:
        configured = os.getenv("OBSERVABILITY_PROVIDER_COST_USD_PER_1K", "")
    for item in configured.split(","):
        name, separator, value = item.partition("=")
        if separator and name.strip().lower() == provider.lower():
            try:
                return max(0.0, float(value))
            except ValueError:
                return 0.0
    return 0.0


def _process_memory_bytes() -> int:
    # Linux reports ru_maxrss in KiB; this service is deployed on Linux.
    return max(0, int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024)


def _append_sample(
    lines: list[str], name: str, value: Any, labels: Mapping[str, Any] | None = None
) -> None:
    lines.append(_sample(name, _label_key(labels), float(value)))


def normalize_metric_path(path: str) -> str:
    """Prevent identifiers in fallback paths from creating high-cardinality labels."""
    parts = path.split("/")
    return "/".join(":id" if _PATH_ID_RE.fullmatch(part) else part for part in parts)


def render_metrics() -> str:
    """Render registry metrics plus cache/runtime/process diagnostics."""
    from .cache_runtime import cache_metrics_snapshot
    from .model_runtime import model_runtime_snapshot

    metrics.set_gauge("deepseek_process_resident_memory_bytes", _process_memory_bytes())
    metrics.set_gauge("deepseek_queue_length", 0)
    metrics.set_gauge("deepseek_worker_capacity", _worker_capacity())
    snapshot = cache_metrics_snapshot()
    runtime = model_runtime_snapshot()
    lines = [metrics.render().rstrip("\n")]
    dynamic_types = {
        "deepseek_cache_operations_total": "counter",
        "deepseek_cache_errors_total": "counter",
        "deepseek_cache_skipped_large_total": "counter",
        "deepseek_cache_stampede_waits_total": "counter",
        "deepseek_cache_local_inflight_waits_total": "counter",
        "deepseek_cache_hit_rate": "gauge",
        "deepseek_cache_redis_used_memory_bytes": "gauge",
        "deepseek_cache_redis_evicted_keys": "gauge",
    }
    dynamic_types.update({f"deepseek_{name}": "gauge" for name in runtime})
    lines.extend(f"# TYPE {name} {kind}" for name, kind in sorted(dynamic_types.items()))
    for kind in ("reply", "retrieval"):
        for operation, snapshot_field in (
            ("hit", "hits"),
            ("miss", "misses"),
            ("write", "writes"),
        ):
            _append_sample(
                lines,
                "deepseek_cache_operations_total",
                snapshot.get(f"{kind}_{snapshot_field}", 0),
                {"kind": kind, "operation": operation},
            )
    for name, snapshot_field in (
        ("deepseek_cache_errors_total", "errors"),
        ("deepseek_cache_skipped_large_total", "skipped_large"),
        ("deepseek_cache_stampede_waits_total", "stampede_waits"),
        ("deepseek_cache_local_inflight_waits_total", "local_inflight_waits"),
    ):
        _append_sample(lines, name, snapshot.get(snapshot_field, 0))
    _append_sample(lines, "deepseek_cache_hit_rate", snapshot.get("hit_rate", 0.0))
    if "redis_used_memory_bytes" in snapshot:
        _append_sample(
            lines, "deepseek_cache_redis_used_memory_bytes", snapshot["redis_used_memory_bytes"]
        )
    if "redis_evicted_keys" in snapshot:
        _append_sample(lines, "deepseek_cache_redis_evicted_keys", snapshot["redis_evicted_keys"])
    for name, value in runtime.items():
        _append_sample(lines, f"deepseek_{name}", value)
    return "\n".join(line for line in lines if line) + "\n"
