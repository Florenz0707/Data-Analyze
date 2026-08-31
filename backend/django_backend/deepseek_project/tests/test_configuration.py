from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from deepseek_project.configuration import (
    ConfigurationError,
    load_database_config,
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
        project_root = Path(__file__).resolve().parents[2]
        config = load_llm_config(
            project_root / "config" / "llm_config.yaml.example", project_root=project_root
        )

        self.assertEqual(config["RESPONSE_TOP_K"], 10)
        self.assertEqual(config["INDEX_BUILD_BATCH_SIZE"], 4)
        self.assertEqual(config["REPLY_CACHE_TTL"], 3600)
        self.assertEqual(config["PROMPT_VERSION"], "m5-v1")
        self.assertEqual(config["INDEX_VERSION"], "v1")
        self.assertNotIn("TOP_K", config)
        self.assertTrue(Path(config["LOG_PATH"]).is_absolute())
        self.assertTrue(Path(config["SYSTEM_PROMPT_PATH"]).is_file())
        self.assertTrue(Path(config["RESPONSE_TEMPLATE_PATH"]).is_file())

    def test_tracked_llm_example_is_used_when_local_config_is_absent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config").mkdir()
            example = root / "config" / "llm_config.yaml.example"
            example.write_text(
                "LLM_PROVIDER: ollama\n"
                "OLLAMA_CONFIG:\n"
                "  model: chat\n"
                "  embedding_model: embedding\n",
                encoding="utf-8",
            )

            config = load_llm_config(project_root=root, validate_paths=False)

        self.assertEqual(config["LLM_PROVIDER"], "ollama")

    def test_database_config_supports_sqlite_default_and_legacy_path_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config").mkdir()
            (root / "config" / "db_config.yaml.example").write_text(
                "DATABASE:\n  ENGINE: sqlite\n  NAME: db.sqlite3\n", encoding="utf-8"
            )

            with mock.patch.dict("os.environ", {"DJANGO_DB_PATH": "data/test.sqlite3"}):
                config = load_database_config(project_root=root)

        self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(config["NAME"], str(root / "data" / "test.sqlite3"))

    def test_database_config_normalizes_mysql_and_postgresql(self):
        import yaml

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mysql_path = root / "mysql.yaml"
            mysql_path.write_text(
                yaml.safe_dump(
                    {
                        "DATABASE": {
                            "ENGINE": "mysql",
                            "NAME": "analytics",
                            "USER": "app",
                            "PASSWORD": "secret",
                            "HOST": "mysql.example",
                            "PORT": 3307,
                        }
                    }
                ),
                encoding="utf-8",
            )
            postgres_path = root / "postgres.yaml"
            postgres_path.write_text(
                "DATABASE:\n  ENGINE: postgres\n  NAME: analytics\n", encoding="utf-8"
            )

            mysql = load_database_config(mysql_path, project_root=root)
            postgres = load_database_config(postgres_path, project_root=root)

        self.assertEqual(mysql["ENGINE"], "django.db.backends.mysql")
        self.assertEqual(mysql["PORT"], "3307")
        self.assertEqual(postgres["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(postgres["PORT"], "5432")

    def test_database_config_expands_environment_placeholders(self):
        import yaml

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "db_config.yaml"
            path.write_text(
                yaml.safe_dump(
                    {
                        "DATABASE": {
                            "ENGINE": "postgresql",
                            "NAME": "analytics",
                            "PASSWORD": "${TEST_DATABASE_PASSWORD}",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict("os.environ", {"TEST_DATABASE_PASSWORD": "secret"}):
                config = load_database_config(path, project_root=root)

        self.assertEqual(config["PASSWORD"], "secret")

    def test_database_config_rejects_invalid_remote_config(self):
        with self.assertRaisesRegex(ConfigurationError, "NAME"):
            self._load_database_config({"ENGINE": "mysql"})

        with self.assertRaisesRegex(ConfigurationError, "不支持的数据库 ENGINE"):
            self._load_database_config({"ENGINE": "oracle", "NAME": "db"})

    def test_legacy_top_k_is_normalized_with_warning(self):
        config = self._load_temp_config({"TOP_K": 7})

        self.assertEqual(config["RESPONSE_TOP_K"], 7)

    def test_negative_reply_cache_ttl_fails_fast(self):
        with self.assertRaisesRegex(ConfigurationError, "REPLY_CACHE_TTL"):
            self._load_temp_config({"REPLY_CACHE_TTL": -1})

    def test_index_build_batch_size_is_bounded(self):
        with self.assertRaisesRegex(ConfigurationError, "INDEX_BUILD_BATCH_SIZE"):
            self._load_temp_config({"INDEX_BUILD_BATCH_SIZE": 33})

    def test_retrieval_configuration_is_normalized_and_validated(self):
        config = self._load_temp_config(
            {
                "RETRIEVAL_MIN_SCORE": "0.25",
                "RETRIEVAL_MODE": "HYBRID",
                "RETRIEVAL_CANDIDATE_MULTIPLIER": "4",
                "HYBRID_VECTOR_WEIGHT": "0.8",
                "HYBRID_LEXICAL_WEIGHT": "0.2",
                "RERANKER_ENABLED": "yes",
            }
        )

        self.assertEqual(config["RETRIEVAL_MIN_SCORE"], 0.25)
        self.assertEqual(config["RETRIEVAL_MODE"], "hybrid")
        self.assertEqual(config["RETRIEVAL_CANDIDATE_MULTIPLIER"], 4)
        self.assertTrue(config["RERANKER_ENABLED"])

        with self.assertRaisesRegex(ConfigurationError, "RETRIEVAL_MIN_SCORE"):
            self._load_temp_config({"RETRIEVAL_MIN_SCORE": 1.1})

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

    def _load_database_config(self, overrides: dict) -> dict:
        import yaml

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "db_config.yaml"
            path.write_text(yaml.safe_dump({"DATABASE": overrides}), encoding="utf-8")
            return load_database_config(path, project_root=root)
