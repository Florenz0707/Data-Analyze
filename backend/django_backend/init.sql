-- Database initialization script for Django SQLite (db.sqlite3)
-- Usage:
--   1) Apply migrations first to create tables:
--        python manage.py migrate --noinput
--   2) Then run this SQL to seed initial data (optional):
--        sqlite3 db.sqlite3 ".read init.sql"

PRAGMA foreign_keys = ON;
BEGIN;

-- Seed a demo API key (32 chars). Update or remove as needed.
-- Will not duplicate if it already exists.
INSERT OR IGNORE INTO deepseek_api_apikey (key, user, created_at, expiry_time)
VALUES (
  'demo_key',
  'demo',
  CURRENT_TIMESTAMP,
  CAST(strftime('%s','now') AS INTEGER) + 360000 -- expiry in seconds
);

-- Ensure a corresponding rate limit row exists for the demo key.
-- NOTE: For Django ForeignKey fields, the DB column name is typically `<field>_id`.
INSERT OR IGNORE INTO deepseek_api_ratelimit (api_key_id, count, reset_time)
VALUES (
  'demo_key',
  0,
  CAST(strftime('%s','now') AS INTEGER) + 60
);

COMMIT;
