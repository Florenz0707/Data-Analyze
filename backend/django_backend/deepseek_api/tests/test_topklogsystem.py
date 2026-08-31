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
