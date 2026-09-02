from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from deepseek_project.cache_runtime import reset_cache_metrics
from django.core.cache import cache
from django.test import SimpleTestCase


class RetrievalCacheTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        reset_cache_metrics()

    def test_retrieval_result_is_reused_and_isolated_by_filter(self):
        from topklogsystem import TopKLogSystem

        system = TopKLogSystem.__new__(TopKLogSystem)
        system.log_index = Mock()
        system.embedding = object()
        system.embedding_key = SimpleNamespace(provider="ollama", model="embedding-v1")
        system.default_top_k = 5
        system.retrieval_cache_ttl = 60
        system.cache_max_object_bytes = 262_144
        system.cache_schema_version = "m6-v1"
        system.index_source_version = "index-v1"
        system.retrieval_mode = "vector"
        system.retrieval_candidate_multiplier = 3
        system.retrieval_min_score = 0.0
        system.hybrid_vector_weight = 0.7
        system.hybrid_lexical_weight = 0.3
        system.reranker_enabled = False
        system.log_index.as_retriever.return_value.retrieve.return_value = [
            SimpleNamespace(
                text="database timeout",
                score=0.9,
                node=SimpleNamespace(node_id="log-1", metadata={"service": "payments"}),
            )
        ]

        first = system.retrieve_logs("timeout", metadata_filter={"service": "payments"})
        second = system.retrieve_logs("timeout", metadata_filter={"service": "payments"})
        filtered = system.retrieve_logs("timeout", metadata_filter={"service": "search"})

        self.assertEqual(first, second)
        self.assertEqual(filtered, [])
        self.assertEqual(system.log_index.as_retriever.call_count, 2)
