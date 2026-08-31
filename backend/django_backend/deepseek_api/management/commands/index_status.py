"""Print the persisted versioned index state."""

from __future__ import annotations

import json

from data_pipeline import IndexStateStore
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Show current and historical log index build states."

    def add_arguments(self, parser):
        parser.add_argument(
            "--state-file",
            default="data/vector_stores/.index_state.json",
            help="Path to the index state JSON file",
        )

    def handle(self, *args, **options):
        state = IndexStateStore(options["state_file"]).load()
        self.stdout.write(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
