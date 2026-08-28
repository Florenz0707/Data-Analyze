#!/usr/bin/env python3
"""Remove only explicitly named synthetic M0 users and their local records."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend" / "django_backend"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("usernames", nargs="+", help="exact synthetic usernames to remove")
    args = parser.parse_args()
    if any(not username.startswith("m0_evaluation") for username in args.usernames):
        raise SystemExit("refusing to remove a username without the m0_evaluation prefix")
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "deepseek_project.settings")
    import django

    django.setup()
    from deepseek_api.models import APIKey, ConversationSession, ExternalLLMAPI, History, Session
    from django.contrib.auth.models import User

    result = {}
    for username in args.usernames:
        result[username] = {
            "history": History.objects.filter(user=username).delete()[0],
            "sessions": Session.objects.filter(user=username).delete()[0],
            "legacy_sessions": ConversationSession.objects.filter(user=username).delete()[0],
            "external_models": ExternalLLMAPI.objects.filter(user=username).delete()[0],
            "api_keys_and_children": APIKey.objects.filter(user=username).delete()[0],
            "django_users": User.objects.filter(username=username).delete()[0],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
