from pathlib import Path
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from deepseek_api.models import APIKey, RateLimit


class Command(BaseCommand):
    help = "Initialize database: run migrations and seed safe demo data (ORM). Optionally execute a custom SQL file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-seed",
            action="store_true",
            help="Only run migrations, skip creating demo data.",
        )
        parser.add_argument(
            "--use-sql",
            action="store_true",
            help=(
                "Also execute a raw SQL file after ORM seeding. Use with caution; "
                "statements against non-existing tables will be skipped."
            ),
        )
        parser.add_argument(
            "--sql",
            type=str,
            default="init.sql",
            help="Path to SQL file to execute when --use-sql is provided (default: init.sql at project root)",
        )

    def handle(self, *args, **options):
        # 1) Apply migrations non-interactively
        self.stdout.write(self.style.WARNING("Applying migrations..."))
        call_command("migrate", interactive=False, verbosity=1)

        # 2) Seed demo data via ORM (safe & schema-aware)
        if options.get("no_seed"):
            self.stdout.write(self.style.SUCCESS("Migrations applied. Skipping seeding (--no-seed)."))
        else:
            self._seed_via_orm()

        # 3) Optionally run raw SQL, but be tolerant to schema differences
        if options.get("use_sql"):
            self._execute_sql_safe(options["sql"])

        self.stdout.write(self.style.SUCCESS("Database initialization completed."))

    # ---------------- internal helpers ---------------- #

    def _seed_via_orm(self):
        now = int(time.time())
        # Create a demo API key if not exists
        demo_key_value = "demo_key"
        demo_user = "demo"
        api_key_obj, created = APIKey.objects.get_or_create(
            key=demo_key_value,
            defaults={
                "user": demo_user,
                "expiry_time": now + 360000,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created demo API key: {demo_key_value} for user '{demo_user}'"))
        else:
            # ensure expiry is in the future
            if api_key_obj.expiry_time < now + 60:
                api_key_obj.expiry_time = now + 360000
                api_key_obj.save(update_fields=["expiry_time"])
            self.stdout.write(f"Demo API key already exists: {demo_key_value}")

        # Ensure a rate limit row exists for this key
        rl, rl_created = RateLimit.objects.get_or_create(
            api_key=api_key_obj,
            defaults={
                "count": 0,
                "reset_time": now + 60,
            },
        )
        if not rl_created:
            # If reset_time already passed, reset it
            if rl.reset_time < now:
                rl.count = 0
                rl.reset_time = now + 60
                rl.save(update_fields=["count", "reset_time"])
            self.stdout.write(self.style.NOTICE("RateLimit row already exists for demo key"))
        else:
            self.stdout.write(self.style.SUCCESS("Created RateLimit row for demo key"))

    def _execute_sql_safe(self, sql_path_str: str):
        # Resolve path: first as given, then project root fallback
        sql_path = Path(sql_path_str).resolve()
        if not sql_path.exists():
            base_dir = Path(__file__).resolve().parents[3]
            candidate = base_dir / sql_path_str
            if candidate.exists():
                sql_path = candidate

        if not sql_path.exists():
            self.stdout.write(self.style.WARNING(f"--use-sql provided but file not found: {sql_path_str}. Skipping."))
            return

        sql = sql_path.read_text(encoding="utf-8")
        if not sql.strip():
            self.stdout.write(self.style.WARNING("SQL file is empty. Skipping."))
            return

        self.stdout.write(self.style.WARNING(f"Executing SQL (best-effort): {sql_path}"))
        statements = [s.strip() for s in sql.split(';') if s.strip()]
        with connection.cursor() as cursor:
            # Execute each statement independently; ignore missing-table errors
            for stmt in statements:
                try:
                    with transaction.atomic():
                        cursor.execute(stmt)
                except Exception as e:
                    # Skip statements incompatible with current schema
                    self.stdout.write(self.style.NOTICE(f"Skipped statement due to error: {e}"))
        self.stdout.write(self.style.SUCCESS("Finished executing SQL (best-effort)."))
