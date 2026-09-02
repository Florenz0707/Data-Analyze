from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.test import SimpleTestCase

from deepseek_project.model_runtime import (
    ModelInstanceCache,
    ModelInstanceKey,
    ProviderClientCache,
    ProviderClientKey,
    clear_model_caches,
    get_cached_http_client,
    get_cached_llm,
    model_load_records,
)


class ModelRuntimeTests(SimpleTestCase):
    def tearDown(self):
        clear_model_caches()

    def test_same_model_is_constructed_once_under_concurrent_access(self):
        cache = ModelInstanceCache(max_size=2)
        key = ModelInstanceKey("ollama", "model-a", "http://localhost:11434")
        created = []

        def factory():
            instance = object()
            created.append(instance)
            return instance

        with ThreadPoolExecutor(max_workers=20) as executor:
            instances = list(executor.map(lambda _: cache.get_or_create(key, factory), range(50)))

        self.assertEqual(len(created), 1)
        self.assertEqual(len({id(instance) for instance in instances}), 1)

    def test_concurrent_different_models_keep_distinct_instances(self):
        cache = ModelInstanceCache(max_size=4)
        keys = [
            ModelInstanceKey("ollama", "model-a", "endpoint"),
            ModelInstanceKey("ollama", "model-b", "endpoint"),
        ]

        def instance_for(key):
            return cache.get_or_create(key, lambda: {"model": key.model})

        with ThreadPoolExecutor(max_workers=20) as executor:
            instances = list(executor.map(lambda index: instance_for(keys[index % 2]), range(50)))

        self.assertEqual({instance["model"] for instance in instances}, {"model-a", "model-b"})
        for key in keys:
            matching = [instance for instance in instances if instance["model"] == key.model]
            self.assertEqual(len({id(instance) for instance in matching}), 1)

    def test_cache_is_bounded_and_model_endpoint_is_part_of_identity(self):
        cache = ModelInstanceCache(max_size=2)
        first_key = ModelInstanceKey("ollama", "model-a", "endpoint-a")
        second_key = ModelInstanceKey("ollama", "model-a", "endpoint-b")
        third_key = ModelInstanceKey("ollama", "model-b", "endpoint-a")

        first = cache.get_or_create(first_key, object)
        second = cache.get_or_create(second_key, object)
        third = cache.get_or_create(third_key, object)

        self.assertEqual(len(cache), 2)
        self.assertIsNot(first, cache.get_or_create(first_key, object))
        self.assertIsNot(second, third)

    def test_unhealthy_model_is_removed_and_rebuilt(self):
        cache = ModelInstanceCache(max_size=2)
        key = ModelInstanceKey("ollama", "model-a", "endpoint")
        first = cache.get_or_create(key, lambda: object())

        class Unhealthy:
            def __init__(self):
                self.healthy = True

            def health_check(self):
                return self.healthy

        cache = ModelInstanceCache(max_size=2)
        first = Unhealthy()
        second = Unhealthy()
        created = iter((first, second))
        cached = cache.get_or_create(key, lambda: next(created))
        first.healthy = False

        rebuilt = cache.get_or_create(key, lambda: next(created))

        self.assertIs(cached, first)
        self.assertIs(rebuilt, second)

    def test_remote_client_cache_reuses_one_connection_pool_per_endpoint(self):
        config = {"MODEL_CACHE_MAX_SIZE": 2}
        with patch("deepseek_project.external_endpoint.create_safe_http_client") as create:
            create.return_value = object()
            first = get_cached_http_client("openai_compat", "https://example.test/v1", config)
            second = get_cached_http_client("openai_compat", "https://example.test/v1", config)

        self.assertIs(first, second)
        create.assert_called_once_with("https://example.test/v1")
        self.assertEqual(len(ProviderClientCache(max_size=1)), 0)
        self.assertEqual(
            ProviderClientKey("openai_compat", "https://example.test/v1").provider, "openai_compat"
        )

    @patch("llm_provider_factory.build_llm_by")
    def test_cached_llm_forwards_user_selected_model(self, build_llm):
        config = {
            "MODEL_CACHE_MAX_SIZE": 4,
            "OLLAMA_CONFIG": {"model": "default", "host": "http://endpoint"},
        }
        build_llm.return_value = object()

        instance, key = get_cached_llm("ollama", "selected-model", config)

        self.assertEqual(key.model, "selected-model")
        self.assertEqual(key.endpoint, "http://endpoint")
        build_llm.assert_called_once_with("ollama", config, model="selected-model")
        self.assertIsNotNone(instance)
        self.assertEqual(len(model_load_records()), 1)

    @patch("llm_provider_factory.build_llm_by")
    def test_unhealthy_cached_llm_is_rebuilt(self, build_llm):
        config = {
            "MODEL_CACHE_MAX_SIZE": 4,
            "OLLAMA_CONFIG": {"model": "default", "host": "http://endpoint"},
        }
        build_llm.side_effect = [object(), object()]

        first, _ = get_cached_llm("ollama", "selected-model", config)
        first.mark_unhealthy()
        second, _ = get_cached_llm("ollama", "selected-model", config)

        self.assertIsNot(first, second)
        self.assertEqual(build_llm.call_count, 2)
