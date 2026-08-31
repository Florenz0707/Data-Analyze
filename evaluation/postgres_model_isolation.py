#!/usr/bin/env python3
"""Verify concurrent API model isolation against the configured PostgreSQL database.

The script deliberately replaces the provider constructor and model system with
deterministic fakes. It exercises real PostgreSQL connections, Django ORM
transactions, Session writes, user preferences, and the API request path
without calling Ollama or another external model service.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, BrokenBarrierError
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "deepseek_project.settings")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "django_backend"))

import django

django.setup()

from deepseek_api.models import APIKey, History, Session, UserLLMPreference  # noqa: E402
from deepseek_project.model_runtime import clear_model_caches  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.db import close_old_connections, connection  # noqa: E402
from django.test import Client  # noqa: E402

REQUESTS = 50
WORKERS = 20


class FakeModel:
    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model


class FakeSystem:
    def query(self, prompt: str, *, llm: FakeModel) -> dict[str, str]:
        del prompt
        return {"response": f"{llm.provider}:{llm.model}"}


def _request(
    index: int,
    *,
    tokens: dict[str, str],
    usernames: tuple[str, str],
    session_prefix: str,
    start_barrier: Barrier,
) -> tuple[int, str, str]:
    close_old_connections()
    username = usernames[index % len(usernames)]
    model = "pg-isolation-model-a" if index % 2 == 0 else "pg-isolation-model-b"
    client = Client(HTTP_HOST="127.0.0.1")
    try:
        # Synchronize only the first worker batch. Applying the barrier to all
        # 50 jobs would deadlock the final batch of 10 jobs in a 20-worker pool.
        if index < WORKERS:
            start_barrier.wait(timeout=30)
        response = client.post(
            "/api/llm/chat",
            data=json.dumps(
                {
                    "session_id": f"{session_prefix}-{index}",
                    "user_input": f"concurrent-query-{index}",
                    "use_history": "off",
                    "message_id": str(uuid.uuid4()),
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {tokens[username]}",
        )
        if response.status_code != 200:
            return index, model, f"HTTP {response.status_code}: {response.content[:200]!r}"
        return index, model, str(response.json().get("reply"))
    except BrokenBarrierError as exc:
        return index, model, f"barrier error: {exc}"
    except Exception as exc:
        return index, model, f"request error: {type(exc).__name__}: {exc}"
    finally:
        close_old_connections()


def main() -> int:
    if connection.vendor != "postgresql":
        raise SystemExit(f"需要 PostgreSQL，当前数据库是 {connection.vendor!r}")

    suffix = uuid.uuid4().hex[:12]
    usernames = (f"pg_iso_a_{suffix}", f"pg_iso_b_{suffix}")
    session_prefix = f"pg-isolation-{suffix}"
    tokens: dict[str, str] = {}
    start_barrier = Barrier(WORKERS)
    fake_system = FakeSystem()

    try:
        expiry = int(time.time()) + 3600
        for username in usernames:
            User.objects.create_user(username=username, password="unused-test-password")
            token = f"pg-test-token-{suffix}-{len(tokens)}-{uuid.uuid4().hex}"
            api_key = APIKey.objects.create(user=username, key=token, expiry_time=expiry)
            UserLLMPreference.objects.create(
                user=api_key,
                provider="ollama",
                model="pg-isolation-model-a"
                if username == usernames[0]
                else "pg-isolation-model-b",
            )
            tokens[username] = token

        with (
            patch("deepseek_api.services._get_system", return_value=fake_system),
            patch(
                "llm_provider_factory.build_llm_by",
                side_effect=lambda provider, config, model: FakeModel(provider, model),
            ),
            patch("llama_index.llms.langchain.LangChainLLM", side_effect=lambda llm: llm),
        ):
            clear_model_caches()
            with ThreadPoolExecutor(max_workers=WORKERS) as executor:
                results = list(
                    executor.map(
                        lambda index: _request(
                            index,
                            tokens=tokens,
                            usernames=usernames,
                            session_prefix=session_prefix,
                            start_barrier=start_barrier,
                        ),
                        range(REQUESTS),
                    )
                )

        mismatches = [
            (index, expected_model, actual)
            for index, expected_model, actual in results
            if actual != f"ollama:{expected_model}"
        ]
        history_count = History.objects.filter(
            session__session_id__startswith=session_prefix
        ).count()
        session_count = Session.objects.filter(session_id__startswith=session_prefix).count()
        if mismatches or history_count != REQUESTS or session_count != REQUESTS:
            print(
                {
                    "requests": REQUESTS,
                    "mismatches": mismatches[:5],
                    "history_count": history_count,
                    "session_count": session_count,
                }
            )
            return 1

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), current_schema()")
            database, schema = cursor.fetchone()
        print(
            {
                "database": database,
                "schema": schema,
                "requests": REQUESTS,
                "workers": WORKERS,
                "mismatches": 0,
                "history_count": history_count,
                "session_count": session_count,
            }
        )
        return 0
    finally:
        clear_model_caches()
        Session.objects.filter(session_id__startswith=session_prefix).delete()
        APIKey.objects.filter(user__in=usernames).delete()
        User.objects.filter(username__in=usernames).delete()
        close_old_connections()


if __name__ == "__main__":
    raise SystemExit(main())
