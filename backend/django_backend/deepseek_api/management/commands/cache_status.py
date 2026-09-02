import json

from deepseek_project.cache_runtime import cache_metrics_snapshot
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Show cache hit-rate, request-merge and Redis memory/eviction diagnostics."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(cache_metrics_snapshot(), ensure_ascii=False, sort_keys=True))
