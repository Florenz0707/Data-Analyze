"""Build and publish one versioned log index."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Build a versioned log index and publish it after a successful build."

    def add_arguments(self, parser):
        parser.add_argument("--config", help="Path to llm_config.yaml")

    def handle(self, *args, **options):
        from topklogsystem import TopKLogSystem

        system = TopKLogSystem(
            config_path=options.get("config"),
            force_versioned_index=True,
        )
        state = system.index_state_store.load()
        version = system.index_spec.version
        record = (state.get("versions") or {}).get(version, {})
        self.stdout.write(
            json.dumps(
                {
                    "status": record.get("status"),
                    "index_version": version,
                    "collection_name": record.get("collection_name"),
                    "document_count": record.get("document_count"),
                },
                ensure_ascii=False,
            )
        )
        if record.get("status") != "ready":
            raise CommandError(
                f"版本化索引构建未发布: version={version}, status={record.get('status')}"
            )
