from __future__ import annotations

import json
import logging
import tempfile
import uuid
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory, SimpleTestCase

from deepseek_project.middleware import RequestTraceMiddleware
from deepseek_project.observability import (
    JsonFormatter,
    LevelRangeFilter,
    current_request_context,
    reset_request_context,
    resolve_request_context,
    set_request_context,
)


class ObservabilityTests(SimpleTestCase):
    def test_request_context_accepts_safe_ids_and_rejects_untrusted_values(self):
        request_id = str(uuid.uuid4())
        trace_id = uuid.uuid4().hex

        resolved = resolve_request_context({"X-Request-ID": request_id, "X-Trace-ID": trace_id})
        replaced = resolve_request_context(
            {"X-Request-ID": "Bearer secret", "X-Trace-ID": "not-a-trace"}
        )

        self.assertEqual(resolved, (request_id, trace_id))
        self.assertNotEqual(replaced[0], "Bearer secret")
        self.assertNotEqual(replaced[1], "not-a-trace")
        self.assertEqual(len(replaced[1]), 32)

    def test_middleware_sets_response_headers_and_context(self):
        factory = RequestFactory()
        request_id = str(uuid.uuid4())
        trace_id = uuid.uuid4().hex
        observed = {}

        def view(request):
            observed.update(current_request_context())
            return HttpResponse("ok", status=201)

        request = factory.get(
            "/api/health",
            HTTP_X_REQUEST_ID=request_id,
            HTTP_X_TRACE_ID=trace_id,
        )
        response = RequestTraceMiddleware(view)(request)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response["X-Request-ID"], request_id)
        self.assertEqual(response["X-Trace-ID"], trace_id)
        self.assertEqual(observed, {"request_id": request_id, "trace_id": trace_id})
        self.assertEqual(current_request_context(), {"request_id": "-", "trace_id": "-"})

    def test_streaming_middleware_keeps_context_until_iteration_finishes(self):
        factory = RequestFactory()
        observed = []

        def view(request):
            def chunks():
                observed.append(current_request_context())
                yield b"event: done\n\n"

            return StreamingHttpResponse(chunks(), content_type="text/event-stream")

        response = RequestTraceMiddleware(view)(factory.get("/api/stream"))
        request_context = {
            "request_id": response["X-Request-ID"],
            "trace_id": response["X-Trace-ID"],
        }
        self.assertEqual(list(response.streaming_content), [b"event: done\n\n"])
        self.assertEqual(observed, [request_context])
        self.assertEqual(current_request_context(), {"request_id": "-", "trace_id": "-"})

    def test_json_formatter_redacts_credentials_and_preserves_trace_context(self):
        request_id = uuid.uuid4().hex
        trace_id = uuid.uuid4().hex
        tokens = set_request_context(request_id, trace_id)
        try:
            record = logging.getLogger("test.observability").makeRecord(
                "test.observability",
                logging.INFO,
                __file__,
                1,
                "request failed Bearer secret-token api_key=inline-api-key",
                (),
                None,
                extra={
                    "event": "test",
                    "api_key": "plain-api-key",
                    "password": "plain-password",
                    "prompt_hash": "hash-only",
                },
            )
            payload = json.loads(JsonFormatter().format(record))
        finally:
            reset_request_context(tokens)

        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["trace_id"], trace_id)
        self.assertEqual(payload["api_key"], "[REDACTED]")
        self.assertEqual(payload["password"], "[REDACTED]")
        self.assertEqual(payload["prompt_hash"], "hash-only")
        self.assertNotIn("secret-token", json.dumps(payload))
        self.assertNotIn("inline-api-key", json.dumps(payload))
        self.assertNotIn("plain-password", json.dumps(payload))


class PersistentLoggingTests(SimpleTestCase):
    def test_level_range_filter_separates_exact_log_levels(self):
        info_filter = LevelRangeFilter("INFO", "INFO")
        warning_filter = LevelRangeFilter("WARNING", "WARNING")
        info_record = logging.LogRecord("test", logging.INFO, __file__, 1, "info", (), None)
        warning_record = logging.LogRecord(
            "test", logging.WARNING, __file__, 1, "warning", (), None
        )

        self.assertTrue(info_filter.filter(info_record))
        self.assertFalse(info_filter.filter(warning_record))
        self.assertTrue(warning_filter.filter(warning_record))
        self.assertFalse(warning_filter.filter(info_record))

    def test_timed_handler_persists_json_and_creates_a_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "info.jsonl"
            handler = TimedRotatingFileHandler(
                log_path,
                when="S",
                interval=1,
                backupCount=2,
                encoding="utf-8",
                utc=True,
            )
            handler.setFormatter(JsonFormatter())
            handler.addFilter(LevelRangeFilter("INFO", "INFO"))
            try:
                record = logging.LogRecord(
                    "test.persistence",
                    logging.INFO,
                    __file__,
                    1,
                    "persisted event",
                    (),
                    None,
                )
                handler.handle(record)
                handler.flush()
                handler.doRollover()
                handler.handle(record)
                handler.flush()

                payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
                self.assertEqual(payload["level"], "INFO")
                self.assertEqual(payload["message"], "persisted event")
                self.assertTrue(list(Path(temp_dir).glob("info.jsonl.*")))
            finally:
                handler.close()
