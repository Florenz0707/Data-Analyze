from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from django.test import SimpleTestCase


class TopKLogSystemIsolationTests(SimpleTestCase):
    @patch("topklogsystem.VectorStoreIndex")
    @patch("topklogsystem.StorageContext")
    @patch("topklogsystem.ChromaVectorStore")
    @patch("topklogsystem.Settings", new_callable=lambda: SimpleNamespace())
    @patch("llm_provider_factory.build_providers")
    def test_fake_providers_use_a_temporary_chroma_directory(
        self, build_providers, settings, chroma_store, storage_context, vector_index
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
