from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.db import connection
from pathlib import Path

class Command(BaseCommand):
    help = "Initialize the SQLite database: run migrations and seed data from init.sql if present."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-seed",
            action="store_true",
            help="Only run migrations, skip executing init.sql",
        )
        parser.add_argument(
            "--sql",
            type=str,
            default="init.sql",
            help="Path to SQL file to execute after migrations (default: init.sql at project root)",
        )

    def handle(self, *args, **options):
        # 1) Apply migrations non-interactively
        self.stdout.write(self.style.WARNING("Applying migrations..."))
        call_command("migrate", interactive=False, verbosity=1)

        if options.get("no_seed"):
            self.stdout.write(self.style.SUCCESS("Migrations applied. Skipping seeding (no-seed)."))
            return

        # 2) Execute init.sql if exists
        sql_path = Path(options["sql"]).resolve()
        if not sql_path.exists():
            # try project base dir (two levels up from this file -> app/management/commands)
            base_dir = Path(__file__).resolve().parents[3]
            candidate = base_dir / options["sql"]
            if candidate.exists():
                sql_path = candidate

        if not sql_path.exists():
            self.stdout.write(self.style.WARNING(f"No SQL seed file found at {options['sql']}. Skipping seeding."))
            return

        sql = sql_path.read_text(encoding="utf-8")
        if not sql.strip():
            self.stdout.write(self.style.WARNING("Seed file is empty. Skipping seeding."))
            return

        self.stdout.write(self.style.WARNING(f"Executing seed SQL: {sql_path}"))
        with connection.cursor() as cursor:
            # For SQLite, use raw connection executescript when available for multi-statement
            raw = getattr(cursor, "connection", None)
            # In Django, cursor.connection is a wrapper; actual DB-API connection at connection.connection
            # get the real underlying connection
            try:
                real = connection.connection
            except Exception:
                real = None

            executed = False
            if real is not None and hasattr(real, "executescript"):
                real.executescript(sql)
                executed = True

            if not executed:
                # Fallback: naive split by ';' (won't handle semicolons in strings)
                statements = [s.strip() for s in sql.split(';') if s.strip()]
                for stmt in statements:
                    cursor.execute(stmt)

        self.stdout.write(self.style.SUCCESS("Database initialized successfully."))
