from __future__ import annotations

from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from deepseek_api import services
from deepseek_api.models import APIKey, RateLimit
from deepseek_api.services import (
    _build_reply_cache_key,
    check_rate_limit,
    compose_prompt_with_history,
    create_api_key,
    generate_cache_key,
    get_allowed_providers,
    get_cached_reply,
    get_history_cfg,
    get_local_models,
    get_or_create_session,
    get_or_create_user_pref,
    invalidate_reply_cache,
    parse_session_context,
    refresh_access_token,
    select_history_by_similarity,
    set_cached_reply,
    validate_api_key,
)


class ServicePureFunctionTests(SimpleTestCase):
    def test_parse_session_context_handles_continuation_lines(self):
        context = "用户：first\n回复：answer\ncontinuation\n用户：second\n回复：reply"

        self.assertEqual(
            parse_session_context(context),
            [("first", "answer\ncontinuation"), ("second", "reply")],
        )

    def test_parse_session_context_ignores_incomplete_turns(self):
        self.assertEqual(parse_session_context("用户：only user"), [])
        self.assertEqual(parse_session_context("回复：only reply"), [])

    @patch("deepseek_api.services._embed_texts", return_value=None)
    def test_history_selection_falls_back_to_overlap_and_respects_threshold(self, _embed):
        turns = [("database timeout", "a"), ("unrelated topic", "b"), ("database retry", "c")]

        selected = select_history_by_similarity(
            "database issue",
            turns,
            {"max_turns": 8, "top_k": 1, "sim_threshold": 0.1},
        )

        self.assertEqual(selected, [("database timeout", "a")])

    @patch(
        "deepseek_api.services._embed_texts",
        return_value=[[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
    )
    def test_history_selection_uses_embedding_similarity(self, _embed):
        turns = [("matching", "a"), ("different", "b")]

        selected = select_history_by_similarity(
            "query",
            turns,
            {"max_turns": 8, "top_k": 3, "sim_threshold": 0.5},
        )

        self.assertEqual(selected, [("matching", "a")])

    def test_compose_prompt_returns_current_query_when_history_is_empty(self):
        self.assertEqual(compose_prompt_with_history([], "current", {}), "current")

    def test_compose_prompt_truncates_history_using_budget(self):
        prompt = compose_prompt_with_history(
            [("old question", "old answer")],
            "current",
            {"max_tokens": 200},
        )

        self.assertIn("当前用户问题：\ncurrent", prompt)
        self.assertIn("以下为相关的对话历史片段", prompt)

    def test_cache_key_is_stable_and_does_not_expose_prompt(self):
        user = APIKey(user="alice")

        key = _build_reply_cache_key(
            "secret prompt",
            "session-1",
            user,
            parameters={"temperature": 0.2},
            history=[("previous question", "previous answer")],
        )
        selected_key = _build_reply_cache_key(
            "secret prompt",
            "session-1",
            user,
            provider="ollama",
            model="other-model",
            parameters={"temperature": 0.2},
            history=[("previous question", "previous answer")],
        )

        self.assertEqual(
            key,
            _build_reply_cache_key(
                "secret prompt",
                "session-1",
                user,
                parameters={"temperature": 0.2},
                history=[("previous question", "previous answer")],
            ),
        )
        self.assertNotEqual(key, selected_key)
        self.assertNotEqual(
            key,
            _build_reply_cache_key(
                "secret prompt",
                "session-1",
                user,
                parameters={"temperature": 0.7},
                history=[("previous question", "previous answer")],
            ),
        )
        self.assertNotEqual(
            key,
            _build_reply_cache_key(
                "secret prompt",
                "session-1",
                user,
                parameters={"temperature": 0.2},
                history=[("different question", "previous answer")],
            ),
        )
        self.assertNotIn("secret prompt", key)
        self.assertEqual(len(generate_cache_key("value")), 64)


class ServiceDatabaseTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_api_key_creation_reuses_valid_key_and_refreshes_it(self):
        first = create_api_key("alice")
        first_key = first.key
        first_refresh = first.refresh_token

        second = create_api_key("alice")

        self.assertEqual(second.pk, first.pk)
        self.assertEqual(second.key, first_key)
        self.assertNotEqual(second.refresh_token, first_refresh)
        self.assertEqual(APIKey.objects.filter(user="alice").count(), 1)
        self.assertEqual(RateLimit.objects.filter(api_key=second).count(), 1)

    def test_cache_isolated_by_user_and_session(self):
        alice = APIKey(user="alice")
        bob = APIKey(user="bob")

        set_cached_reply("same prompt", "alice reply", "session-1", alice)

        self.assertEqual(get_cached_reply("same prompt", "session-1", alice), "alice reply")
        self.assertIsNone(get_cached_reply("same prompt", "session-1", bob))
        self.assertIsNone(get_cached_reply("same prompt", "session-2", alice))

    def test_cache_uses_configured_ttl_and_rejects_non_success_values(self):
        alice = APIKey(user="alice")

        with patch.object(services.cache, "set", wraps=cache.set) as cache_set:
            self.assertTrue(set_cached_reply("prompt", "reply", "session", alice, timeout=17))
            cache_set.assert_called_once()
            self.assertEqual(cache_set.call_args.args[2], 17)

        self.assertFalse(set_cached_reply("prompt", "", "session", alice))
        self.assertFalse(set_cached_reply("prompt", None, "session", alice))
        self.assertFalse(
            set_cached_reply("prompt", "error payload", "session", alice, cacheable=False)
        )

    def test_cache_namespace_rotation_invalidates_previous_entries(self):
        alice = APIKey(user="alice")

        self.assertTrue(set_cached_reply("prompt", "reply", "session", alice))
        self.assertEqual(get_cached_reply("prompt", "session", alice), "reply")
        namespace = invalidate_reply_cache()

        self.assertTrue(namespace)
        self.assertIsNone(get_cached_reply("prompt", "session", alice))

    def test_invalid_cached_value_is_removed_instead_of_returned(self):
        alice = APIKey(user="alice")
        key = _build_reply_cache_key("prompt", "session", alice)
        cache.set(key, {"error": "model unavailable"}, timeout=60)

        self.assertIsNone(get_cached_reply("prompt", "session", alice))
        self.assertIsNone(cache.get(key))

    def test_token_validation_and_refresh_reject_expired_values(self):
        api_key = create_api_key("alice")
        self.assertTrue(validate_api_key(api_key.key))
        self.assertEqual(refresh_access_token(api_key.refresh_token).pk, api_key.pk)

        api_key.refresh_expiry_time = 0
        api_key.save(update_fields=["refresh_expiry_time"])

        self.assertIsNone(refresh_access_token(api_key.refresh_token))
        self.assertFalse(validate_api_key(api_key.key))

    @override_settings(RATE_LIMIT_MAX=1)
    def test_rate_limit_blocks_second_request_in_same_window(self):
        api_key = create_api_key("rate-user")

        self.assertTrue(check_rate_limit(api_key.key))
        self.assertFalse(check_rate_limit(api_key.key))

    def test_session_and_user_preference_are_created_once(self):
        api_key = create_api_key("alice")

        first_session = get_or_create_session("session", api_key)
        second_session = get_or_create_session("session", api_key)
        first_pref = get_or_create_user_pref(api_key)
        second_pref = get_or_create_user_pref(api_key)

        self.assertEqual(first_session.pk, second_session.pk)
        self.assertEqual(first_pref.pk, second_pref.pk)

    @patch("deepseek_api.services._load_env_cfg")
    def test_history_config_and_allowed_providers_follow_configuration(self, load_cfg):
        load_cfg.return_value = {
            "HISTORY_MODE": "on",
            "HISTORY_MAX_TURNS": 4,
            "HISTORY_TOP_K": 2,
            "HISTORY_SIM_THRESHOLD": 0.5,
            "HISTORY_MAX_TOKENS": 600,
            "LLM_PROVIDER": "dashscope",
        }

        self.assertEqual(
            get_history_cfg(),
            {"mode": "on", "max_turns": 4, "top_k": 2, "sim_threshold": 0.5, "max_tokens": 600},
        )
        self.assertEqual(get_allowed_providers(), ["transformers", "ollama", "dashscope"])

    def test_local_model_config_returns_stable_provider_lists(self):
        models = get_local_models()

        self.assertEqual(set(models), {"transformers", "ollama"})
        self.assertEqual(models["transformers"], sorted(set(models["transformers"])))
        self.assertEqual(models["ollama"], sorted(set(models["ollama"])))

    def test_embedding_helpers_handle_zero_vectors_and_empty_models(self):
        self.assertEqual(services._cosine([], [1.0]), 0.0)
        self.assertEqual(services._cosine([0.0], [1.0]), 0.0)
        self.assertEqual(
            services._embed_texts(
                [],
            ),
            None,
        )

    @patch("deepseek_api.services._get_embed_model")
    def test_embedding_helper_supports_batch_and_single_model_interfaces(self, get_model):
        model = Mock()
        model.get_text_embedding_batch.return_value = [[1.0]]
        get_model.return_value = model

        self.assertEqual(services._embed_texts(["text"]), [[1.0]])

        model.get_text_embedding_batch = None
        model.get_text_embedding.return_value = [2.0]
        self.assertEqual(services._embed_texts(["text"]), [[2.0]])

    @patch("llama_index.llms.langchain.LangChainLLM")
    @patch("deepseek_api.services.build_llm_for_provider")
    @patch("deepseek_api.services._get_system")
    def test_fake_llm_is_passed_to_one_request_without_global_mutation(
        self, get_system, build_llm, llm_wrapper
    ):
        class FakeSystem:
            def query(self, prompt, **kwargs):
                return {"response": f"fake:{prompt}"}

        fake_llm = object()
        get_system.return_value = FakeSystem()
        build_llm.return_value = fake_llm
        llm_wrapper.side_effect = lambda llm: llm
        user = create_api_key("fake-llm-user")
        preference = get_or_create_user_pref(user)
        preference.model = "selected-model"
        preference.save(update_fields=["model"])

        result = services.generate_with_user_llm(user, "question")

        self.assertEqual(result, "fake:question")
        build_llm.assert_called_once_with("ollama", "selected-model")
        llm_wrapper.assert_called_once_with(llm=fake_llm)
