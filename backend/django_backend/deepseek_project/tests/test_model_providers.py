from unittest.mock import patch

from django.test import SimpleTestCase
from llm_provider_factory import build_embedding_by, build_llm_by
from model_providers import get_adapter, normalize_provider, registered_providers


class ProviderRegistryTests(SimpleTestCase):
    def test_builtin_provider_registry_and_embedding_alias(self):
        self.assertTrue(
            {"transformers", "ollama", "openai_compat", "dashscope"}.issubset(
                registered_providers(embedding=False)
            )
        )
        self.assertIn("hf", registered_providers(embedding=True))
        self.assertNotIn("hf", registered_providers(embedding=False))
        self.assertEqual(normalize_provider("HF"), "transformers")
        self.assertIs(get_adapter("hf"), get_adapter("transformers"))

    def test_unknown_provider_has_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "不支持的 model provider"):
            get_adapter("not-a-provider")

    def test_adapter_metadata_resolves_current_model_fields(self):
        config = {
            "OLLAMA_CONFIG": {"model": "chat", "embedding_model": "embed", "host": "http://ollama"},
            "DASHSCOPE_CONFIG": {"chat_model": "qwen", "embedding_model": "text-embedding-v4"},
        }
        ollama = get_adapter("ollama")
        dashscope = get_adapter("dashscope")

        self.assertEqual(ollama.resolve_model(config), "chat")
        self.assertEqual(ollama.resolve_model(config, embedding=True), "embed")
        self.assertEqual(dashscope.resolve_model(config), "qwen")
        self.assertEqual(dashscope.resolve_model(config, embedding=True), "text-embedding-v4")
        self.assertEqual(ollama.cache_identity(config), "http://ollama")

    @patch("llm_provider_factory.get_adapter")
    def test_factory_delegates_llm_to_registry_adapter(self, get_adapter_mock):
        adapter = get_adapter_mock.return_value
        adapter.build_llm.return_value = "llm"

        result = build_llm_by("ollama", {"OLLAMA_CONFIG": {}}, model="selected")

        self.assertEqual(result, "llm")
        get_adapter_mock.assert_called_once_with("ollama")
        adapter.build_llm.assert_called_once_with({"OLLAMA_CONFIG": {}}, model="selected")

    @patch("llm_provider_factory.get_adapter")
    def test_factory_delegates_embedding_to_registry_adapter(self, get_adapter_mock):
        adapter = get_adapter_mock.return_value
        adapter.build_embedding.return_value = ("embedding", "model")

        result = build_embedding_by("hf", {"TRANSFORMERS_CONFIG": {}}, model="selected")

        self.assertEqual(result, ("embedding", "model"))
        get_adapter_mock.assert_called_once_with("hf")
        adapter.build_embedding.assert_called_once_with(
            {"TRANSFORMERS_CONFIG": {}}, model="selected"
        )

    def test_capabilities_are_declared_without_changing_pipeline(self):
        self.assertTrue(get_adapter("ollama").capabilities.streaming)
        self.assertTrue(get_adapter("ollama").capabilities.json_mode)
        self.assertTrue(get_adapter("openai_compat").capabilities.tool_calling)
        self.assertFalse(get_adapter("transformers").capabilities.tool_calling)
