from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from deepseek_project.configuration import (
    ConfigurationError,
    load_llm_config,
    parse_bool,
    parse_csv,
    redacted_config_summary,
)


class ConfigurationTests(SimpleTestCase):
    def test_environment_parsers_are_explicit(self):
        self.assertTrue(parse_bool("yes", False))
        self.assertFalse(parse_bool("off", True))
        self.assertEqual(parse_csv(" http://a, ,http://b ", []), ["http://a", "http://b"])
        with self.assertRaises(ConfigurationError):
            parse_bool("maybe", False)

    def test_repository_config_uses_project_root_and_canonical_top_k(self):
        config = load_llm_config()

        self.assertEqual(config["RESPONSE_TOP_K"], 10)
        self.assertEqual(config["REPLY_CACHE_TTL"], 3600)
        self.assertEqual(config["PROMPT_VERSION"], "v1")
        self.assertEqual(config["INDEX_VERSION"], "v1")
        self.assertNotIn("TOP_K", config)
        self.assertTrue(Path(config["LOG_PATH"]).is_absolute())
        self.assertTrue(Path(config["SYSTEM_PROMPT_PATH"]).is_file())
        self.assertTrue(Path(config["RESPONSE_TEMPLATE_PATH"]).is_file())

    def test_legacy_top_k_is_normalized_with_warning(self):
        config = self._load_temp_config({"TOP_K": 7})

        self.assertEqual(config["RESPONSE_TOP_K"], 7)

    def test_negative_reply_cache_ttl_fails_fast(self):
        with self.assertRaisesRegex(ConfigurationError, "REPLY_CACHE_TTL"):
            self._load_temp_config({"REPLY_CACHE_TTL": -1})

    def test_unknown_provider_fails_before_provider_initialization(self):
        with self.assertRaisesRegex(ConfigurationError, "不支持的 LLM provider"):
            self._load_temp_config({"LLM_PROVIDER": "unknown"})

    def test_invalid_embedding_dimensions_fail_fast(self):
        with self.assertRaisesRegex(ConfigurationError, "embedding_dimensions"):
            self._load_temp_config(
                {
                    "LLM_PROVIDER": "openai_compat",
                    "EMBEDDING_PROVIDER": "openai_compat",
                    "OPENAI_COMPAT_CONFIG": {
                        "model": "chat-model",
                        "embedding_model": "embedding-model",
                        "embedding_dimensions": 0,
                    },
                }
            )

    def test_config_summary_redacts_credentials_and_proxy_userinfo(self):
        summary = redacted_config_summary(
            {
                "LLM_PROVIDER": "openai_compat",
                "OPENAI_COMPAT_CONFIG": {
                    "model": "safe-model",
                    "api_key": "do-not-log",
                    "base_url": "https://user:password@example.test/v1",
                },
            }
        )

        self.assertNotIn("api_key", str(summary))
        self.assertNotIn("password", summary["OPENAI_COMPAT_CONFIG"]["base_url"])
        self.assertEqual(summary["OPENAI_COMPAT_CONFIG"]["base_url"], "https://example.test/v1")

    def _load_temp_config(self, overrides: dict) -> dict:
        import yaml

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data" / "log").mkdir(parents=True)
            (root / "config").mkdir()
            (root / "config" / "system_prompt.yaml").write_text(
                "text: '{query}'\n", encoding="utf-8"
            )
            (root / "config" / "response_template.md").write_text("# Answer\n", encoding="utf-8")
            config = {
                "LLM_PROVIDER": "ollama",
                "EMBEDDING_PROVIDER": "ollama",
                "OLLAMA_CONFIG": {
                    "model": "chat-model",
                    "embedding_model": "embedding-model",
                },
                "LOG_PATH": "data/log",
                "SYSTEM_PROMPT_PATH": "config/system_prompt.yaml",
                "RESPONSE_TEMPLATE_PATH": "config/response_template.md",
            }
            config.update(overrides)
            path = root / "config" / "llm_config.yaml"
            path.write_text(yaml.safe_dump(config), encoding="utf-8")
            return load_llm_config(path, project_root=root)
