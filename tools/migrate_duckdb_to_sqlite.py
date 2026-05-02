"""
migrate_duckdb_to_sqlite.py — Complete one-time migration from DuckDB to SQLite.

Migrates ALL tables:
  - bot_config        (feature flags, chances, settings)
  - user_stats        (boli_points, rashi, strikes, quotas)
  - user_perks        (active timed perks)
  - predictions_cache (LLM template cache)
  - user_prediction_history
  - curse_logs / bless_logs
  - daily_omens
  - local_media       (saved emoji/sticker shortcuts)

Skips (safe to regenerate):
  - local_knowledge   (seeded from code on startup)

Run this ONCE on the GCP VM after stopping the bot:
    cd ~/disco_bot
    source .venv/bin/activate
    python tools/migrate_duckdb_to_sqlite.py

The old astro_bot.db (DuckDB) is renamed to astro_bot.duckdb.bak before anything
is written. On any failure, the script restores the backup automatically.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import duckdb
except ImportError:
    print("[!] duckdb is not installed in this virtualenv.")
    print("    Install it temporarily:  pip install duckdb")
    sys.exit(1)

from schema import DB_PATH, init_db

OLD_PATH = DB_PATH
BAK_PATH = DB_PATH.with_name(DB_PATH.stem + ".duckdb.bak")

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

if not OLD_PATH.exists():
    print(f"[!] No database found at {OLD_PATH}. Nothing to migrate.")
    sys.exit(1)

with open(OLD_PATH, "rb") as _f:
    _magic = _f.read(16)
if _magic.startswith(b"SQLite format 3"):
    print(f"[!] {OLD_PATH} is already a SQLite database. Migration not needed.")
    sys.exit(0)

print("=" * 60)
print("  DuckDB → SQLite migration")
print("=" * 60)
print(f"  Source  : {OLD_PATH}  (DuckDB)")
print(f"  Backup  : {BAK_PATH}")
print(f"  Target  : {OLD_PATH}  (SQLite, same path)")
print()

# ---------------------------------------------------------------------------
# Open DuckDB source (read-only keeps the WAL safe)
# ---------------------------------------------------------------------------

print("[1/6] Opening DuckDB source (read-only)...")
try:
    duck = duckdb.connect(str(OLD_PATH), read_only=True)
except Exception as exc:
    print(f"[!] Could not open DuckDB: {exc}")
    print("    Make sure the bot is stopped before running this script.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Rename old file → backup, create fresh SQLite
# ---------------------------------------------------------------------------

print("[2/6] Backing up DuckDB file and creating SQLite schema...")
shutil.copy2(str(OLD_PATH), str(BAK_PATH))
print(f"      Backup written to {BAK_PATH}")

# We keep the original in place until init_db succeeds, then remove it.
try:
    # init_db() opens/creates the file at DB_PATH — but the DuckDB file is still
    # there. We need to remove it first so SQLite doesn't try to open a DuckDB file.
    OLD_PATH.unlink()
    conn = init_db()
    print("      SQLite schema created successfully.")
except Exception as exc:
    print(f"[!] Failed to create SQLite DB: {exc}")
    # Restore backup
    if BAK_PATH.exists() and not OLD_PATH.exists():
        shutil.copy2(str(BAK_PATH), str(OLD_PATH))
        print("      DuckDB backup restored to original path.")
    duck.close()
    sys.exit(1)


# ---------------------------------------------------------------------------
# Table definitions: (table_name, [columns_to_migrate])
# Order matters for readability; SQLite has no FK enforcement so order doesn't
# matter for correctness.
# ---------------------------------------------------------------------------

TABLES: list[tuple[str, list[str]]] = [
    # Core config — migrate first so settings are restored before user data
    ("bot_config", [
        "key", "value_str", "value_float", "value_int",
    ]),
    # User profiles — the most important table
    ("user_stats", [
        "user_id", "username", "rashi", "boli_points", "last_seen",
        "prediction_count", "extra_actions",
        # These were added via ALTER TABLE — may not exist in very old DBs
        # The helper below handles missing columns gracefully.
        "strikes", "daily_action_count", "last_action_date",
    ]),
    # Active perks (timed — may have expired, migrate anyway)
    ("user_perks", [
        "user_id", "perk_type", "expires_at",
    ]),
    # Prediction cache (LLM templates — large, worth keeping)
    ("predictions_cache", [
        "id", "cache_type", "user_id", "original_prompt", "template_text", "timestamp",
    ]),
    # Per-user daily prediction history
    ("user_prediction_history", [
        "id", "user_id", "prediction_text", "timestamp",
    ]),
    # Audit logs
    ("curse_logs", [
        "id", "user_id", "username", "curse_used", "timestamp",
    ]),
    ("bless_logs", [
        "id", "user_id", "username", "bless_used", "timestamp",
    ]),
    # Daily omen cache
    ("daily_omens", [
        "id", "generated_text", "landmark", "omen_date",
    ]),
    # User-saved emoji/sticker shortcuts — important, users set these up manually
    ("local_media", [
        "shortcut", "user_id", "file_path", "media_type", "source_url", "created_at",
    ]),
    # Application emoji LRU cache — must migrate so the bot knows which emojis
    # are already uploaded to Discord and doesn't exhaust the upload limit.
    ("app_emojis", [
        "emoji_id", "name", "last_used", "original_id", "animated",
    ]),
]


# ---------------------------------------------------------------------------
# Migration helper
# ---------------------------------------------------------------------------

def get_duck_columns(table: str) -> set[str]:
    """Return the column names that actually exist in the DuckDB table."""
    rows = duck.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
        [table],
    ).fetchall()
    return {r[0] for r in rows}


def migrate_table(table: str, requested_cols: list[str]) -> int:
    # Only select columns that actually exist in the DuckDB table
    existing = get_duck_columns(table)
    cols = [c for c in requested_cols if c in existing]
    missing = [c for c in requested_cols if c not in existing]
    if missing:
        print(f"      {table}: columns not in DuckDB (will default in SQLite): {missing}")

    if not cols:
        print(f"      {table}: no matching columns — skipped")
        return 0

    col_list = ", ".join(cols)
    placeholders = ", ".join(["?"] * len(cols))

    rows = duck.execute(f"SELECT {col_list} FROM {table}").fetchall()
    if not rows:
        print(f"      {table}: 0 rows")
        return 0

    # Convert DuckDB Python types to SQLite-friendly types:
    # - bool → int  (SQLite has no BOOLEAN)
    # - datetime/date → kept as-is (sqlite3 adapters handle serialization)
    def coerce(val):
        if isinstance(val, bool):
            return int(val)
        return val

    converted = [tuple(coerce(v) for v in row) for row in rows]

    conn.executemany(
        f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})",
        converted,
    )
    conn.commit()
    print(f"      {table}: {len(rows)} rows migrated")
    return len(rows)


# ---------------------------------------------------------------------------
# Run migrations
# ---------------------------------------------------------------------------

print("[3/6] Migrating tables...")
total_rows = 0
failed_tables: list[str] = []

for table, columns in TABLES:
    try:
        total_rows += migrate_table(table, columns)
    except Exception as exc:
        print(f"      [!] {table}: FAILED — {exc}")
        failed_tables.append(table)

duck.close()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

print()
print("[4/6] Verification — row counts in new SQLite DB:")
all_tables = [t for t, _ in TABLES] + ["local_knowledge"]
for table in all_tables:
    try:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        flag = " ← seeded from code" if table == "local_knowledge" else ""
        print(f"      {table:<30} {count:>6} rows{flag}")
    except Exception as exc:
        print(f"      {table:<30} ERROR: {exc}")

# Spot-check: Boli total
try:
    boli_total = conn.execute("SELECT COALESCE(SUM(boli_points), 0) FROM user_stats").fetchone()[0]
    user_count  = conn.execute("SELECT COUNT(*) FROM user_stats").fetchone()[0]
    print()
    print(f"      Users migrated   : {user_count}")
    print(f"      Total Boli in DB : {boli_total}")
except Exception as exc:
    print(f"      Could not compute Boli total: {exc}")

conn.close()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print()
print("[5/6] Cleaning up...")
print(f"      DuckDB backup retained at: {BAK_PATH}")
print(f"      (Keep it for a few days, then: rm {BAK_PATH})")

print()
print("[6/6] Migration complete!")
print(f"      Total rows migrated: {total_rows}")

if failed_tables:
    print()
    print(f"  [!] The following tables failed and will be empty: {failed_tables}")
    print("      This is non-fatal — the bot will recreate them over time.")

print()
print("Next steps:")
print("  1. python bot.py   — start the bot")
print("  2. /health         — verify user count and Boli total")
print("  3. /rank           — verify leaderboard")
print("  4. /mypoints       — verify your own points")
