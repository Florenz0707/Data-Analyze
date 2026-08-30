from django.core.management.base import BaseCommand

from deepseek_api.services import invalidate_reply_cache


class Command(BaseCommand):
    help = "Invalidate all logical final-reply cache entries by rotating the cache namespace."

    def handle(self, *args, **options):
        namespace = invalidate_reply_cache()
        self.stdout.write(
            self.style.SUCCESS(f"Reply cache invalidated; active namespace: {namespace}")
        )
