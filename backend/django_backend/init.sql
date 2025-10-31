-- Database initialization script for Django SQLite (db.sqlite3)
-- Usage:
--   1) Apply migrations first to create tables:
--        python manage.py migrate --noinput
--   2) Then run this SQL to seed initial data (optional):
--        sqlite3 db.sqlite3 ".read init.sql"

PRAGMA foreign_keys = ON;
BEGIN;


-- Ensure a corresponding rate limit row exists for the demo key.
-- NOTE: For Django ForeignKey fields, the DB column name is typically `<field>_id`.
INSERT OR IGNORE INTO deepseek_api_ratelimit (api_key_id, count, reset_time)
VALUES (1,
        0,
        CAST(strftime('%s', 'now') AS INTEGER) + 60);

COMMIT;
