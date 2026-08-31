from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml
from django.test import SimpleTestCase


class TopKLogSystemIsolationTests(SimpleTestCase):
    @patch("topklogsystem.iter_llama_documents")
    def test_document_batches_bound_index_input(self, documents):
        from topklogsystem import TopKLogSystem

        documents.return_value = iter(["doc-1", "doc-2", "doc-3"])

        self.assertEqual(
            list(TopKLogSystem._document_batches("data/log", batch_size=2)),
            [["doc-1", "doc-2"], ["doc-3"]],
        )

    def test_retrieval_keeps_document_id_score_and_metadata(self):
        from topklogsystem import TopKLogSystem

        system = TopKLogSystem.__new__(TopKLogSystem)
        system.log_index = Mock()
        system.embedding = object()
        system.default_top_k = 5
        system.log_index.as_retriever.return_value.retrieve.return_value = [
            SimpleNamespace(
                text="service failed",
                score=0.91,
                node=SimpleNamespace(
                    node_id="log-123",
                    metadata={"source_file": "sample.csv", "source_row": 2},
                ),
            )
        ]

        self.assertEqual(
            system.retrieve_logs("failure"),
            [
                {
                    "document_id": "log-123",
                    "content": "service failed",
                    "score": 0.91,
                    "metadata": {"source_file": "sample.csv", "source_row": 2},
                }
            ],
        )

    def test_retrieval_applies_metadata_filter_and_threshold(self):
        from topklogsystem import TopKLogSystem

        system = TopKLogSystem.__new__(TopKLogSystem)
        system.log_index = Mock()
        system.embedding = object()
        system.default_top_k = 5
        system.retrieval_min_score = 0.8
        system.log_index.as_retriever.return_value.retrieve.return_value = [
            SimpleNamespace(
                text="matching event",
                score=0.9,
                node=SimpleNamespace(
                    node_id="match",
                    metadata={"service": "payments"},
                ),
            ),
            SimpleNamespace(
                text="wrong service",
                score=0.99,
                node=SimpleNamespace(
                    node_id="wrong-service",
                    metadata={"service": "search"},
                ),
            ),
            SimpleNamespace(
                text="weak event",
                score=0.2,
                node=SimpleNamespace(
                    node_id="weak",
                    metadata={"service": "payments"},
                ),
            ),
        ]

        result = system.retrieve_logs("event", metadata_filter={"service": "payments"})

        self.assertEqual([item["document_id"] for item in result], ["match"])
        system.log_index.as_retriever.assert_called_once()
        self.assertIn("filters", system.log_index.as_retriever.call_args.kwargs)

    def test_no_evidence_status_and_hybrid_scores_are_explicit(self):
        from topklogsystem import TopKLogSystem

        system = TopKLogSystem.__new__(TopKLogSystem)
        system.log_index = Mock()
        system.embedding = object()
        system.default_top_k = 2
        system.retrieval_mode = "hybrid"
        system.retrieval_candidate_multiplier = 3
        system.hybrid_vector_weight = 0.7
        system.hybrid_lexical_weight = 0.3
        system.retrieval_min_score = 0.0
        system.log_index.as_retriever.return_value.retrieve.return_value = [
            SimpleNamespace(
                text="database timeout",
                score=0.8,
                node=SimpleNamespace(node_id="a", metadata={}),
            ),
            SimpleNamespace(
                text="unrelated event",
                score=0.4,
                node=SimpleNamespace(node_id="b", metadata={}),
            ),
        ]

        result = system.retrieve_logs("database timeout")

        self.assertEqual(system.last_retrieval_status, "ok")
        self.assertEqual(result[0]["document_id"], "a")
        self.assertIn("lexical_score", result[0])
        self.assertEqual(system.log_index.as_retriever.call_args.kwargs["similarity_top_k"], 6)

        system.log_index.as_retriever.return_value.retrieve.return_value = []
        self.assertEqual(system.retrieve_logs("missing"), [])
        self.assertEqual(system.last_retrieval_status, "no_evidence")

    @patch("topklogsystem.VectorStoreIndex")
    @patch("topklogsystem.StorageContext")
    @patch("topklogsystem.ChromaVectorStore")
    @patch("llm_provider_factory.build_providers")
    def test_fake_providers_use_a_temporary_chroma_directory(
        self, build_providers, chroma_store, storage_context, vector_index
    ):
        from topklogsystem import TopKLogSystem

        class FakeLLM:
            pass

        class FakeEmbedding:
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data" / "log").mkdir(parents=True)
            (root / "config").mkdir()
            (root / "config" / "system_prompt.yaml").write_text(
                "text: '回答 {query}'\n", encoding="utf-8"
            )
            (root / "config" / "response_template.md").write_text("# Answer\n", encoding="utf-8")
            config_path = root / "config" / "llm_config.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "LLM_PROVIDER": "ollama",
                        "EMBEDDING_PROVIDER": "ollama",
                        "OLLAMA_CONFIG": {
                            "model": "fake-chat",
                            "embedding_model": "fake-embedding",
                        },
                        "LOG_PATH": "data/log",
                        "SYSTEM_PROMPT_PATH": "config/system_prompt.yaml",
                        "RESPONSE_TEMPLATE_PATH": "config/response_template.md",
                        "VECTOR_STORE_PATH": "data/temp-chroma",
                    }
                ),
                encoding="utf-8",
            )
            build_providers.return_value = {
                "llm": FakeLLM(),
                "embedding": FakeEmbedding(),
                "collection_name": "test_collection",
                "llm_key": SimpleNamespace(
                    provider="ollama", model="fake-chat", endpoint="http://localhost"
                ),
                "embedding_key": SimpleNamespace(
                    provider="ollama", model="fake-embedding", endpoint="http://localhost"
                ),
            }
            storage_context.from_defaults.return_value = object()
            chroma_store.return_value = object()
            collection = SimpleNamespace(count=lambda: 0)
            client = SimpleNamespace(get_or_create_collection=lambda _: collection)
            vector_index.from_vector_store.return_value = object()

            with patch("chromadb.PersistentClient", return_value=client):
                system = TopKLogSystem(config_path)

            self.assertIsNotNone(system.log_index)
            self.assertTrue((root / "data" / "temp-chroma").is_dir())
            build_providers.assert_called_once()
