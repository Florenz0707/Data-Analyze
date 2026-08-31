from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from ninja.errors import Throttled

from deepseek_api.models import APIKey, ExternalLLMAPI, History, RateLimitBucket, Session
from deepseek_api.services import create_api_key, decrypt_external_api_key


class ApiIntegrationTests(TestCase):
    def register_and_login(self, username: str) -> str:
        register = self.client.post(
            "/api/users/register",
            data=json.dumps({"username": username, "password": "S3cure-password!"}),
            content_type="application/json",
        )
        self.assertEqual(register.status_code, 200)
        login = self.client.post(
            "/api/users/login",
            data=json.dumps({"username": username, "password": "S3cure-password!"}),
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)
        return login["Authorization"]

    def authenticated_client(self, username: str) -> Client:
        client = Client()
        token = self.register_and_login(username)
        client.defaults["HTTP_AUTHORIZATION"] = token
        return client

    def test_register_rejects_duplicate_username_and_empty_input(self):
        self.register_and_login("duplicate-user")

        duplicate = self.client.post(
            "/api/users/register",
            data=json.dumps({"username": "duplicate-user", "password": "S3cure-password!"}),
            content_type="application/json",
        )
        empty = self.client.post(
            "/api/users/register",
            data=json.dumps({"username": "", "password": ""}),
            content_type="application/json",
        )

        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(duplicate.json()["code"], "RESOURCE_CONFLICT")
        self.assertEqual(empty.json()["code"], "VALIDATION_ERROR")

    def test_protected_endpoint_rejects_missing_or_invalid_token(self):
        missing = self.client.get("/api/sessions")
        self.client.defaults["HTTP_AUTHORIZATION"] = "Bearer invalid-token"
        invalid = self.client.get("/api/sessions")

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(missing.json()["code"], "AUTH_REQUIRED")
        self.assertEqual(invalid.json()["code"], "AUTH_INVALID")

    def test_protected_endpoints_reject_missing_authentication(self):
        cases = [
            self.client.get("/api/sessions/history", {"session_id": "session"}),
            self.client.delete("/api/sessions/history?session_id=session"),
            self.client.get("/api/llm/providers"),
            self.client.get("/api/llm/local_models"),
            self.client.post("/api/sessions", data="{}", content_type="application/json"),
            self.client.delete("/api/sessions", data="{}", content_type="application/json"),
            self.client.get("/api/sessions"),
            self.client.get("/api/llm/my"),
            self.client.post("/api/llm/select", data="{}", content_type="application/json"),
            self.client.post("/api/llm/extern", data="{}", content_type="application/json"),
            self.client.get("/api/llm/extern"),
            self.client.delete("/api/llm/extern", data="{}", content_type="application/json"),
            self.client.post("/api/llm/chat", data="{}", content_type="application/json"),
        ]

        self.assertTrue(all(response.status_code == 401 for response in cases))

    def test_expired_and_malformed_bearer_tokens_are_rejected(self):
        api_key = create_api_key("expired-user")
        api_key.expiry_time = 0
        api_key.save(update_fields=["expiry_time"])

        expired = self.client.get("/api/sessions", HTTP_AUTHORIZATION=f"Bearer {api_key.key}")
        malformed = self.client.get("/api/sessions", HTTP_AUTHORIZATION="Token anything")

        self.assertEqual(expired.status_code, 401)
        self.assertEqual(malformed.status_code, 401)
        self.assertEqual(expired.json()["code"], "AUTH_INVALID")
        self.assertEqual(malformed.json()["code"], "AUTH_INVALID")
        self.assertTrue(APIKey.objects.filter(pk=api_key.pk).exists())

    def test_login_rejects_invalid_and_inactive_users(self):
        empty = self.client.post(
            "/api/users/login",
            data=json.dumps({"username": "", "password": ""}),
            content_type="application/json",
        )
        invalid = self.client.post(
            "/api/users/login",
            data=json.dumps({"username": "missing", "password": "wrong"}),
            content_type="application/json",
        )
        User.objects.create_user(username="inactive", password="S3cure-password!", is_active=False)
        inactive = self.client.post(
            "/api/users/login",
            data=json.dumps({"username": "inactive", "password": "S3cure-password!"}),
            content_type="application/json",
        )

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(invalid.status_code, 403)
        self.assertEqual(inactive.status_code, 403)
        self.assertEqual(empty.json()["code"], "VALIDATION_ERROR")
        self.assertEqual(invalid.json()["code"], "AUTH_INVALID")
        self.assertEqual(inactive.json()["code"], "AUTH_FORBIDDEN")

    @override_settings(RATE_LIMIT_LOGIN_MAX=1, RATE_LIMIT_LOGIN_INTERVAL=60)
    def test_login_rate_limit_returns_retry_after(self):
        self.client.post(
            "/api/users/register",
            data=json.dumps({"username": "limited-login", "password": "S3cure-password!"}),
            content_type="application/json",
        )
        payload = json.dumps({"username": "limited-login", "password": "S3cure-password!"})

        first = self.client.post("/api/users/login", data=payload, content_type="application/json")
        second = self.client.post("/api/users/login", data=payload, content_type="application/json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["code"], "RATE_LIMITED")
        self.assertGreaterEqual(int(second["Retry-After"]), 1)
        self.assertLessEqual(int(second["Retry-After"]), 60)
        self.assertEqual(RateLimitBucket.objects.filter(scope="login").count(), 2)

    def test_route_guards_return_stable_errors_when_called_directly(self):
        import deepseek_api.api as api_module

        request = SimpleNamespace(auth=None, COOKIES={})
        self.assertEqual(api_module.chat(request, SimpleNamespace())[0], 401)
        self.assertEqual(api_module.history(request, "session")[0], 401)
        self.assertEqual(api_module.clear_history(request)[0], 401)
        self.assertEqual(api_module.get_llm_providers(request)[0], 401)
        self.assertEqual(api_module.get_llm_local_models(request)[0], 401)
        self.assertEqual(api_module.create_session(request, SimpleNamespace())[0], 401)
        self.assertEqual(api_module.delete_session(request, SimpleNamespace())[0], 401)
        self.assertEqual(api_module.list_sessions(request)[0], 401)
        self.assertEqual(api_module.get_my_llm(request)[0], 401)
        self.assertEqual(api_module.select_llm(request, SimpleNamespace())[0], 401)
        self.assertEqual(api_module.add_external_api(request, SimpleNamespace())[0], 401)
        self.assertEqual(api_module.list_external_models(request)[0], 401)
        self.assertEqual(api_module.delete_external_model(request, SimpleNamespace())[0], 401)
        self.assertEqual(api_module.refresh(SimpleNamespace(COOKIES={})).status_code, 400)
        self.assertEqual(
            api_module.refresh(SimpleNamespace(COOKIES={"refresh_token": "invalid"})).status_code,
            403,
        )

        missing_session = SimpleNamespace(auth=APIKey(user="missing-user"))
        self.assertEqual(api_module.clear_history(missing_session, "missing")[0], 401)
        self.assertEqual(
            api_module.delete_external_model(missing_session, SimpleNamespace(model_name=""))[0],
            400,
        )

    def test_malformed_request_uses_unified_validation_error(self):
        client = self.authenticated_client("validation-user")

        response = client.post(
            "/api/llm/chat",
            data=json.dumps({"session_id": "missing-input"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "VALIDATION_ERROR")
        self.assertTrue(response.json()["details"])

    @override_settings(RATE_LIMIT_CHAT_MAX=1, RATE_LIMIT_CHAT_INTERVAL=60)
    @patch("deepseek_api.api.generate_with_user_llm", return_value="limited answer")
    def test_chat_rate_limit_blocks_burst(self, _generate):
        client = self.authenticated_client("limited-chat")
        payload = json.dumps({"session_id": "limited", "user_input": "question"})

        first = client.post("/api/llm/chat", data=payload, content_type="application/json")
        second = client.post("/api/llm/chat", data=payload, content_type="application/json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["code"], "RATE_LIMITED")
        self.assertGreaterEqual(int(second["Retry-After"]), 1)
        self.assertLessEqual(int(second["Retry-After"]), 60)
        self.assertEqual(_generate.call_count, 1)

    def test_throttled_exception_uses_retry_after_and_stable_code(self):
        import deepseek_api.api as api_module

        response = api_module.api.on_exception(SimpleNamespace(), Throttled(11))

        self.assertEqual(response.status_code, 429)
        self.assertEqual(json.loads(response.content)["code"], "RATE_LIMITED")
        self.assertEqual(response["Retry-After"], "11")

    @patch("deepseek_api.api.get_allowed_providers", side_effect=RuntimeError("unexpected"))
    def test_unexpected_exception_uses_safe_internal_error_response(self, _get_providers):
        client = self.authenticated_client("internal-error-user")

        response = client.get("/api/llm/providers")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {
                "code": "INTERNAL_ERROR",
                "error": "服务内部错误，请稍后再试",
            },
        )

    @patch("deepseek_api.api.authenticate")
    def test_login_rejects_inactive_user_from_auth_backend(self, authenticate):
        authenticate.return_value = SimpleNamespace(is_active=False)

        result = __import__("deepseek_api.api", fromlist=["login"]).login(
            SimpleNamespace(), SimpleNamespace(username="inactive", password="password")
        )

        self.assertEqual(result[0], 403)

    def test_user_cannot_read_another_users_session_history(self):
        alice = self.authenticated_client("alice")
        create = alice.post(
            "/api/sessions",
            data=json.dumps({"session_id": "shared-session"}),
            content_type="application/json",
        )
        self.assertEqual(create.status_code, 201)

        bob = self.authenticated_client("bob")
        history = bob.get("/api/sessions/history", {"session_id": "shared-session"})
        delete = bob.delete(
            "/api/sessions",
            data=json.dumps({"session_id": "shared-session"}),
            content_type="application/json",
        )

        self.assertEqual(history.status_code, 404)
        self.assertEqual(delete.status_code, 404)

    def test_session_history_pagination_clear_and_delete(self):
        client = self.authenticated_client("history-user")
        create = client.post(
            "/api/sessions",
            data=json.dumps({"session_id": "history-session"}),
            content_type="application/json",
        )
        self.assertEqual(create.status_code, 201)
        user = User.objects.get(username="history-user")
        session = Session.objects.get(session_id="history-session", user=user)
        History.objects.create(
            session=session,
            sequence=1,
            user_input="one",
            response="reply one",
        )
        History.objects.create(
            session=session,
            sequence=2,
            user_input="two",
            response="reply two",
        )

        listing = client.get("/api/sessions")
        history = client.get("/api/sessions/history", {"session_id": "history-session", "limit": 1})
        first_id, second_id = list(
            History.objects.filter(session=session).order_by("id").values_list("id", flat=True)
        )
        before = client.get(
            "/api/sessions/history",
            {"session_id": "history-session", "before_id": second_id, "limit": 1},
        )
        after = client.get(
            "/api/sessions/history",
            {"session_id": "history-session", "after_id": first_id, "limit": 1},
        )
        before_cursor = client.get(
            "/api/sessions/history",
            {
                "session_id": "history-session",
                "before_cursor": after.json()["next_before_cursor"],
                "limit": 1,
            },
        )
        after_cursor = client.get(
            "/api/sessions/history",
            {
                "session_id": "history-session",
                "after_cursor": history.json()["next_after_cursor"],
                "limit": 1,
            },
        )
        invalid_pagination = client.get(
            "/api/sessions/history",
            {
                "session_id": "history-session",
                "before_id": second_id,
                "after_id": first_id,
            },
        )
        clear = client.delete("/api/sessions/history?session_id=history-session")
        deleted = client.delete(
            "/api/sessions",
            data=json.dumps({"session_id": "history-session"}),
            content_type="application/json",
        )

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json(), {"sessions": ["history-session"]})
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()["turns"]), 1)
        self.assertEqual(history.json()["turns"][0]["id"], first_id)
        self.assertEqual(history.json()["turns"][0]["sequence"], 1)
        self.assertIn("message_id", history.json()["turns"][0])
        self.assertIn("created_at", history.json()["turns"][0])
        self.assertTrue(history.json()["has_more_after"])
        self.assertIsInstance(history.json()["next_after_cursor"], str)
        self.assertEqual(before.json()["turns"][0]["user_input"], "one")
        self.assertFalse(before.json()["has_more_before"])
        self.assertEqual(after.json()["turns"][0]["user_input"], "two")
        self.assertFalse(after.json()["has_more_after"])
        self.assertEqual(before_cursor.json()["turns"][0]["user_input"], "one")
        self.assertEqual(after_cursor.json()["turns"][0]["user_input"], "two")
        self.assertEqual(invalid_pagination.status_code, 400)
        self.assertEqual(clear.status_code, 200)
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(Session.objects.filter(session_id="history-session").exists())

    def test_session_title_is_independent_and_delete_cascades_history(self):
        client = self.authenticated_client("session-title-user")
        create = client.post(
            "/api/sessions",
            data=json.dumps({"session_id": "titled-session", "title": "  项目讨论  "}),
            content_type="application/json",
        )
        user = User.objects.get(username="session-title-user")
        session = Session.objects.get(session_id="titled-session", user=user)
        history = History.objects.create(
            session=session,
            sequence=1,
            user_input="question",
            response="answer",
        )

        self.assertEqual(create.status_code, 201)
        self.assertEqual(create.json(), {"session_id": "titled-session", "title": "项目讨论"})
        self.assertEqual(History.objects.filter(session=session).count(), 1)

        deleted = client.delete(
            "/api/sessions",
            data=json.dumps({"session_id": "titled-session"}),
            content_type="application/json",
        )

        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(History.objects.filter(pk=history.pk).exists())

    def test_blank_session_title_falls_back_to_session_id(self):
        client = self.authenticated_client("blank-title-user")

        response = client.post(
            "/api/sessions",
            data=json.dumps({"session_id": "fallback-session", "title": "  "}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["title"], "fallback-session")

    def test_session_creation_and_deletion_validate_empty_and_duplicate_ids(self):
        client = self.authenticated_client("session-validation-user")
        empty_create = client.post(
            "/api/sessions", data=json.dumps({"session_id": ""}), content_type="application/json"
        )
        create = client.post(
            "/api/sessions",
            data=json.dumps({"session_id": "session"}),
            content_type="application/json",
        )
        duplicate = client.post(
            "/api/sessions",
            data=json.dumps({"session_id": "session"}),
            content_type="application/json",
        )
        empty_delete = client.delete(
            "/api/sessions", data=json.dumps({"session_id": ""}), content_type="application/json"
        )

        self.assertEqual(empty_create.status_code, 400)
        self.assertEqual(create.status_code, 201)
        self.assertEqual(create.json()["title"], "session")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(empty_delete.status_code, 400)

    @patch("deepseek_api.api._validate_openai_compat", return_value=False)
    def test_external_model_rejects_unavailable_endpoint(self, _validate):
        client = self.authenticated_client("external-invalid-user")
        response = client.post(
            "/api/llm/extern",
            data=json.dumps(
                {
                    "base_url": "https://93.184.216.34/v1",
                    "model_name": "remote-model",
                    "api_key": "fake-key",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "MODEL_UNAVAILABLE")
        self.assertEqual(ExternalLLMAPI.objects.count(), 0)

    @patch("deepseek_api.api._validate_openai_compat")
    def test_external_model_rejects_ssrf_endpoint_before_probe(self, validate):
        client = self.authenticated_client("external-ssrf-user")
        response = client.post(
            "/api/llm/extern",
            data=json.dumps(
                {
                    "base_url": "http://127.0.0.1:8080/v1",
                    "model_name": "remote-model",
                    "api_key": "fake-key",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "VALIDATION_ERROR")
        validate.assert_not_called()
        self.assertEqual(ExternalLLMAPI.objects.count(), 0)

    @patch("openai.OpenAI")
    def test_external_endpoint_probe_reports_success_and_failure(self, openai_class):
        from deepseek_api.api import _validate_openai_compat

        client = openai_class.return_value
        client.chat.completions.create.return_value = Mock(id="probe-id")
        self.assertTrue(_validate_openai_compat("https://93.184.216.34/v1", "key", "model"))
        client.chat.completions.create.side_effect = RuntimeError("secret-key echoed by provider")
        with self.assertLogs("deepseek_api.api", level="WARNING") as logs:
            self.assertFalse(_validate_openai_compat("https://93.184.216.34/v1", "key", "model"))
        self.assertNotIn("secret-key", "\n".join(logs.output))

    def test_authenticated_chat_rejects_empty_input(self):
        client = self.authenticated_client("empty-chat-user")

        response = client.post(
            "/api/llm/chat",
            data=json.dumps({"session_id": "session", "user_input": " "}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "VALIDATION_ERROR")

    @patch("deepseek_api.api.generate_with_user_llm", return_value="history answer")
    def test_chat_on_mode_uses_recent_history(self, _generate):
        client = self.authenticated_client("history-chat-user")
        client.post(
            "/api/sessions",
            data=json.dumps({"session_id": "history-chat"}),
            content_type="application/json",
        )
        user = User.objects.get(username="history-chat-user")
        session = Session.objects.get(session_id="history-chat", user=user)
        History.objects.create(
            session=session,
            sequence=1,
            user_input="old question",
            response="old answer",
        )
        session.next_history_sequence = 1
        session.save(update_fields=["next_history_sequence"])

        response = client.post(
            "/api/llm/chat",
            data=json.dumps(
                {
                    "session_id": "history-chat",
                    "user_input": "new question",
                    "use_history": "on",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(_generate.call_count, 1)

    @patch("deepseek_api.api.generate_with_user_llm", return_value="idempotent answer")
    def test_chat_message_id_makes_retries_idempotent(self, generate):
        client = self.authenticated_client("idempotent-chat-user")
        payload = {
            "session_id": "idempotent-session",
            "user_input": "same request",
            "message_id": "b4b4b4b4-1111-4aaa-8bbb-123456789abc",
        }

        first = client.post(
            "/api/llm/chat", data=json.dumps(payload), content_type="application/json"
        )
        second = client.post(
            "/api/llm/chat", data=json.dumps(payload), content_type="application/json"
        )

        user = User.objects.get(username="idempotent-chat-user")
        session = Session.objects.get(session_id="idempotent-session", user=user)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(History.objects.filter(session=session).count(), 1)
        self.assertEqual(History.objects.get(session=session).sequence, 1)
        self.assertEqual(session.next_history_sequence, 1)

    def test_refresh_uses_cookie_and_rotates_access_expiry(self):
        first_access = self.register_and_login("refresh-user")
        first_refresh = self.client.cookies["refresh_token"].value

        refreshed = self.client.post("/api/refresh")
        missing = Client().post("/api/refresh")

        self.assertEqual(refreshed.status_code, 200)
        self.assertTrue(refreshed["Authorization"].startswith("Bearer "))
        self.assertNotEqual(refreshed["Authorization"], first_access)
        self.assertNotEqual(self.client.cookies["refresh_token"].value, first_refresh)
        self.assertEqual(missing.status_code, 400)

    def test_refresh_token_reuse_revokes_session(self):
        self.register_and_login("refresh-reuse-user")
        stale_refresh = self.client.cookies["refresh_token"].value

        self.assertEqual(self.client.post("/api/refresh").status_code, 200)
        stale_client = Client()
        stale_client.cookies["refresh_token"] = stale_refresh
        reused = stale_client.post("/api/refresh")

        self.assertEqual(reused.status_code, 403)
        api_key = APIKey.objects.get(user="refresh-reuse-user")
        self.assertIsNotNone(api_key.revoked_at)

    def test_logout_revokes_access_and_clears_refresh_cookie(self):
        client = self.authenticated_client("logout-user")
        api_key = APIKey.objects.get(user="logout-user")

        response = client.post("/api/logout")
        protected = client.get("/api/sessions")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"message": "退出成功"})
        self.assertEqual(protected.status_code, 401)
        api_key.refresh_from_db()
        self.assertIsNotNone(api_key.revoked_at)

    @patch("deepseek_api.api.get_allowed_providers", return_value=["ollama", "transformers"])
    @patch(
        "deepseek_api.api.get_local_models",
        return_value={"ollama": ["model-a"], "transformers": []},
    )
    def test_llm_provider_and_model_endpoints(self, _models, _providers):
        client = self.authenticated_client("provider-user")

        providers = client.get("/api/llm/providers")
        models = client.get("/api/llm/local_models")
        current = client.get("/api/llm/my")
        selected = client.post(
            "/api/llm/select",
            data=json.dumps({"provider": "ollama", "model": "model-a"}),
            content_type="application/json",
        )
        invalid = client.post(
            "/api/llm/select",
            data=json.dumps({"provider": "unknown"}),
            content_type="application/json",
        )

        self.assertEqual(providers.json(), {"providers": ["ollama", "transformers"]})
        self.assertEqual(models.json(), {"ollama": ["model-a"], "transformers": []})
        self.assertEqual(current.status_code, 200)
        self.assertEqual(selected.json(), {"provider": "ollama", "model": "model-a"})
        self.assertEqual(invalid.status_code, 400)

    @patch("deepseek_api.api._validate_openai_compat", return_value=True)
    def test_external_model_crud_and_validation(self, _validate):
        client = self.authenticated_client("external-user")
        empty = client.post(
            "/api/llm/extern",
            data=json.dumps({"base_url": "", "model_name": "", "api_key": ""}),
            content_type="application/json",
        )
        added = client.post(
            "/api/llm/extern",
            data=json.dumps(
                {
                    "base_url": "https://93.184.216.34/v1",
                    "model_name": "remote-model",
                    "api_key": "fake-key",
                    "alias": "Remote",
                }
            ),
            content_type="application/json",
        )
        listed = client.get("/api/llm/extern")
        stored = ExternalLLMAPI.objects.get(user="external-user", model_name="remote-model")
        self.assertNotEqual(stored.api_key_encrypted, "fake-key")
        self.assertEqual(decrypt_external_api_key(stored.api_key_encrypted), "fake-key")
        deleted = client.delete(
            "/api/llm/extern",
            data=json.dumps({"model_name": "Remote"}),
            content_type="application/json",
        )
        not_found = client.delete(
            "/api/llm/extern",
            data=json.dumps({"model_name": "Remote"}),
            content_type="application/json",
        )

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(added.status_code, 200)
        self.assertEqual(listed.json(), {"models_list": ["Remote"]})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(not_found.status_code, 404)
        self.assertEqual(ExternalLLMAPI.objects.count(), 0)

    @patch(
        "deepseek_api.services._get_default_provider_model",
        return_value=("ollama", "default-model"),
    )
    @patch("deepseek_api.api._validate_openai_compat", return_value=True)
    def test_external_model_selection_and_delete_fallback(self, _validate, _default_model):
        client = self.authenticated_client("external-selection-user")
        payload = {
            "base_url": "https://93.184.216.34/v1",
            "model_name": "remote-model",
            "api_key": "fake-key",
            "alias": "Remote",
        }
        added = client.post(
            "/api/llm/extern", data=json.dumps(payload), content_type="application/json"
        )
        selected = client.post(
            "/api/llm/select",
            data=json.dumps({"provider": "Remote", "model": "stale-model"}),
            content_type="application/json",
        )
        current = client.get("/api/llm/my")
        deleted = client.delete(
            "/api/llm/extern",
            data=json.dumps({"model_name": "Remote"}),
            content_type="application/json",
        )
        fallback = client.get("/api/llm/my")

        self.assertEqual(added.status_code, 200)
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json(), {"provider": "Remote", "model": "remote-model"})
        self.assertEqual(current.json(), {"provider": "Remote", "model": "remote-model"})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(fallback.json()["provider"], "ollama")

    @patch("deepseek_api.api._validate_openai_compat", return_value=True)
    def test_external_alias_conflict_is_rejected(self, _validate):
        client = self.authenticated_client("external-alias-user")
        first = {
            "base_url": "https://93.184.216.34/v1",
            "model_name": "model-a",
            "api_key": "fake-key-a",
            "alias": "Shared",
        }
        second = {**first, "model_name": "model-b", "api_key": "fake-key-b"}

        created = client.post(
            "/api/llm/extern", data=json.dumps(first), content_type="application/json"
        )
        conflict = client.post(
            "/api/llm/extern", data=json.dumps(second), content_type="application/json"
        )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "RESOURCE_CONFLICT")

    @override_settings(RATE_LIMIT_MODEL_VALIDATE_MAX=1, RATE_LIMIT_MODEL_VALIDATE_INTERVAL=60)
    @patch("deepseek_api.api._validate_openai_compat", return_value=True)
    def test_model_validation_has_separate_rate_limit(self, _validate):
        client = self.authenticated_client("limited-model-validation")
        payload = {
            "base_url": "https://93.184.216.34/v1",
            "model_name": "remote-model",
            "api_key": "fake-key",
        }

        first = client.post(
            "/api/llm/extern", data=json.dumps(payload), content_type="application/json"
        )
        second = client.post(
            "/api/llm/extern", data=json.dumps(payload), content_type="application/json"
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["code"], "RATE_LIMITED")
        self.assertGreaterEqual(int(second["Retry-After"]), 1)
        self.assertLessEqual(int(second["Retry-After"]), 60)

    @patch("deepseek_api.api.generate_with_user_llm", return_value="fake answer")
    def test_chat_persists_history_and_hits_cache(self, generate):
        client = self.authenticated_client("chat-user")
        payload = {
            "session_id": "chat-session",
            "user_input": "same question",
            "use_history": "off",
        }

        first = client.post(
            "/api/llm/chat", data=json.dumps(payload), content_type="application/json"
        )
        second = client.post(
            "/api/llm/chat", data=json.dumps(payload), content_type="application/json"
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), {"reply": "fake answer"})
        self.assertEqual(second.json(), {"reply": "fake answer"})
        self.assertEqual(generate.call_count, 1)
        user = User.objects.get(username="chat-user")
        session = Session.objects.get(session_id="chat-session", user=user)
        self.assertEqual(History.objects.filter(session=session).count(), 2)
        self.assertTrue(Session.objects.filter(pk=session.pk).exists())

    @patch("deepseek_api.api.generate_with_user_llm", side_effect=RuntimeError("fake unavailable"))
    def test_chat_returns_service_unavailable_when_model_is_disabled(self, _generate):
        client = self.authenticated_client("unavailable-user")

        response = client.post(
            "/api/llm/chat",
            data=json.dumps({"session_id": "session", "user_input": "question"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("服务未启用模型", response.json()["error"])
        self.assertFalse(
            Session.objects.filter(session_id="session", user__username="unavailable-user").exists()
        )
        self.assertFalse(History.objects.exists())
