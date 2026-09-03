from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, TestCase, override_settings

from deepseek_project.metrics import MetricsRegistry, metrics, record_generation, record_phase


class MetricsRegistryTests(SimpleTestCase):
    def test_registry_renders_counters_gauges_and_histograms(self):
        registry = MetricsRegistry()
        registry.increment("example_requests_total", labels={"route": "/health"})
        registry.set_gauge("example_in_progress", 2)
        registry.observe("example_duration_seconds", 0.02, labels={"route": "/health"})

        rendered = registry.render()

        self.assertIn("# TYPE example_requests_total counter", rendered)
        self.assertIn('example_requests_total{route="/health"} 1', rendered)
        self.assertIn("# TYPE example_in_progress gauge", rendered)
        self.assertIn("example_in_progress 2", rendered)
        self.assertIn("# TYPE example_duration_seconds histogram", rendered)
        self.assertIn('example_duration_seconds_bucket{le="+Inf",route="/health"} 1', rendered)

    def test_phase_and_generation_record_operational_metrics(self):
        metrics.reset()
        record_phase(
            "retrieval",
            0.012,
            {"retrieval_count": 0, "evidence_scores": [], "outcome": "success"},
        )
        record_phase(
            "model_pipeline",
            0.03,
            {
                "provider": "fake",
                "model": "fake-model",
                "input_tokens_estimate": 10,
                "output_tokens_estimate": 4,
            },
        )
        record_phase(
            "model",
            0.04,
            {"provider": "fake", "model": "fake-model", "outcome": "timeout"},
        )
        record_generation("sanitizer_fallback", False, 4)

        rendered = metrics.render()

        self.assertIn('deepseek_retrieval_requests_total{outcome="no_evidence"} 1', rendered)
        self.assertIn(
            'deepseek_model_calls_total{model="fake-model",outcome="success",provider="fake"} 1',
            rendered,
        )
        self.assertIn(
            'deepseek_model_timeouts_total{model="fake-model",provider="fake"} 1', rendered
        )
        self.assertIn('deepseek_structured_output_total{outcome="sanitizer_fallback"} 1', rendered)

    def tearDown(self):
        metrics.reset()


class HealthEndpointTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / ".index_state.json"
        self.state_path.write_text(
            json.dumps(
                {
                    "schema_version": "m4-index-state-v1",
                    "current_version": "idx-test",
                    "versions": {
                        "idx-test": {
                            "status": "ready",
                            "document_count": 3,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @override_settings(ENABLE_LLM=False)
    def test_liveness_is_process_only_and_returns_trace_headers(self):
        response = self.client.get("/api/health/live")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "check": "liveness"})
        self.assertTrue(response["X-Request-ID"])
        self.assertTrue(response["X-Trace-ID"])

    @override_settings(ENABLE_LLM=False)
    def test_readiness_checks_database_cache_index_and_configuration(self):
        with override_settings(INDEX_STATE_FILE=str(self.state_path)):
            response = self.client.get("/api/health/ready")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ready")
        self.assertTrue(all(item["ok"] for item in payload["checks"].values()))

    @override_settings(ENABLE_LLM=False)
    def test_readiness_returns_503_when_current_index_is_not_ready(self):
        missing = Path(self.temp_dir.name) / "missing.json"
        with override_settings(INDEX_STATE_FILE=str(missing)):
            response = self.client.get("/api/health/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["checks"]["index"]["reason"], "no_ready_current_index")

    @override_settings(ENABLE_LLM=False)
    def test_provider_health_does_not_initialize_a_model(self):
        response = self.client.get("/api/health/providers")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["providers"])
        self.assertTrue(all(item["status"] == "disabled" for item in payload["providers"]))

    @override_settings(ENABLE_LLM=False)
    def test_metrics_endpoint_exposes_http_cache_runtime_and_memory_metrics(self):
        metrics.reset()
        self.client.get("/api/health/live")
        with override_settings(INDEX_STATE_FILE=str(self.state_path)):
            response = self.client.get("/api/metrics")

        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("deepseek_http_requests_total", body)
        self.assertIn("deepseek_cache_operations_total", body)
        self.assertIn("deepseek_process_resident_memory_bytes", body)
        self.assertIn("deepseek_queue_length", body)
