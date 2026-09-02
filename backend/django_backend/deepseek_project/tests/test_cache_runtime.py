from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from deepseek_project.cache_runtime import (
    cache_metrics_snapshot,
    cache_set,
    get_or_compute,
    reset_cache_metrics,
)


class CacheRuntimeTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        reset_cache_metrics()

    def test_single_key_computation_is_merged_within_worker(self):
        calls = 0
        calls_lock = threading.Lock()
        started = threading.Event()
        release = threading.Event()

        def producer():
            nonlocal calls
            with calls_lock:
                calls += 1
            started.set()
            self.assertTrue(release.wait(timeout=2))
            return "shared answer"

        def invoke():
            return get_or_compute(
                "reply:single-flight-test",
                producer,
                timeout=60,
                cache_kind="reply",
                validator=lambda value: isinstance(value, str) and bool(value),
                max_bytes=1024,
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(invoke) for _ in range(4)]
            self.assertTrue(started.wait(timeout=2))
            release.set()
            results = [future.result(timeout=2) for future in futures]

        self.assertEqual(calls, 1)
        self.assertEqual({value for value, _ in results}, {"shared answer"})
        self.assertGreaterEqual(cache_metrics_snapshot()["local_inflight_waits"], 1)

    def test_large_value_is_not_written(self):
        self.assertFalse(
            cache_set(
                "reply:large-test",
                "x" * 20,
                60,
                cache_kind="reply",
                max_bytes=10,
            )
        )
        self.assertIsNone(cache.get("reply:large-test"))
        self.assertEqual(cache_metrics_snapshot()["skipped_large"], 1)

    @patch("deepseek_project.cache_runtime.cache.set", side_effect=ConnectionError("redis down"))
    @patch("deepseek_project.cache_runtime.cache.get", side_effect=ConnectionError("redis down"))
    def test_cache_failure_falls_back_to_producer(self, _get, _set):
        value, cached = get_or_compute(
            "reply:cache-failure-test",
            lambda: "fallback answer",
            timeout=60,
            cache_kind="reply",
            validator=lambda item: isinstance(item, str) and bool(item),
            max_bytes=1024,
        )

        self.assertEqual(value, "fallback answer")
        self.assertFalse(cached)
        self.assertGreater(cache_metrics_snapshot()["errors"], 0)
