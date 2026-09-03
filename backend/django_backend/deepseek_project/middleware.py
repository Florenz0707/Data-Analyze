"""HTTP middleware for request/trace IDs and request completion events."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

from django.http import HttpRequest, HttpResponse

from .metrics import normalize_metric_path, record_request_complete, record_request_start
from .observability import (
    log_phase,
    reset_request_context,
    resolve_request_context,
    set_request_context,
)

logger = logging.getLogger(__name__)


class RequestTraceMiddleware:
    """Bind IDs for the complete request, including lazy streaming content."""

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id, trace_id = resolve_request_context(request.headers)
        request.request_id = request_id
        request.trace_id = trace_id
        tokens = set_request_context(request_id, trace_id)
        started = time.perf_counter()
        record_request_start()
        logger.info(
            "request.started",
            extra={
                "event": "request.started",
                "method": request.method,
                "path": request.path,
            },
        )
        try:
            response = self.get_response(request)
        except Exception:
            log_phase(
                logger,
                "request",
                started,
                event="request.completed",
                method=request.method,
                path=request.path,
                status_code=500,
                outcome="error",
            )
            record_request_complete(
                request.method, self._metric_path(request), 500, time.perf_counter() - started
            )
            reset_request_context(tokens)
            raise

        response["X-Request-ID"] = request_id
        response["X-Trace-ID"] = trace_id
        if getattr(response, "streaming", False):
            response.streaming_content = self._stream_with_completion(
                response.streaming_content,
                request,
                started,
            )
            reset_request_context(tokens)
            return response

        log_phase(
            logger,
            "request",
            started,
            event="request.completed",
            method=request.method,
            path=request.path,
            status_code=response.status_code,
            outcome="success" if response.status_code < 400 else "error",
        )
        record_request_complete(
            request.method,
            self._metric_path(request),
            response.status_code,
            time.perf_counter() - started,
        )
        reset_request_context(tokens)
        return response

    def _stream_with_completion(
        self,
        content: Iterator[Any],
        request: HttpRequest,
        started: float,
    ) -> Iterator[Any]:
        tokens = set_request_context(request.request_id, request.trace_id)
        try:
            yield from content
        except Exception:
            log_phase(
                logger,
                "request",
                started,
                event="request.completed",
                method=request.method,
                path=request.path,
                status_code=500,
                outcome="error",
            )
            record_request_complete(
                request.method, self._metric_path(request), 500, time.perf_counter() - started
            )
            raise
        else:
            log_phase(
                logger,
                "request",
                started,
                event="request.completed",
                method=request.method,
                path=request.path,
                status_code=200,
                outcome="success",
            )
            record_request_complete(
                request.method, self._metric_path(request), 200, time.perf_counter() - started
            )
        finally:
            reset_request_context(tokens)

    @staticmethod
    def _metric_path(request: HttpRequest) -> str:
        route = getattr(getattr(request, "resolver_match", None), "route", None)
        return route or normalize_metric_path(request.path)
