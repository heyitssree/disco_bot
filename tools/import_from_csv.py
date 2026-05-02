"""
import_from_csv.py — One-time migration: import user_stats from a CSV backup into a fresh SQLite DB.

Usage:
    cd ~/disco_bot
    source .venv/bin/activate
    python tools/import_from_csv.py data/backup_before_sqlite.csv

The script will:
  1. Open (or create) the SQLite database
  2. Ensure the schema exists (calls schema.init_db)
  3. Import all rows from the CSV into user_stats using INSERT OR REPLACE

Run this ONCE after the bot has been stopped and before restarting with the new code.
"""

import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from schema import init_db, DB_PATH

CSV_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/backup_before_sqlite.csv")

if not CSV_PATH.exists():
    print(f"[!] CSV not found: {CSV_PATH}")
    print("    Export from the old DuckDB first if needed, or run the bot once to create the schema.")
    sys.exit(1)

print(f"[+] Opening SQLite at {DB_PATH}")
conn = init_db()

imported = 0
skipped = 0

with open(CSV_PATH, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO user_stats
                    (user_id, username, rashi, boli_points, prediction_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    int(row["user_id"]),
                    row["username"],
                    row.get("rashi") or None,
                    int(row.get("boli_points", 0)),
                    int(row.get("prediction_count", 0)),
                ],
            )
            imported += 1
        except Exception as exc:
            print(f"  [!] Skipped row {row}: {exc}")
            skipped += 1

conn.commit()
conn.close()

print(f"[✓] Imported {imported} users from {CSV_PATH}")
if skipped:
    print(f"[!] Skipped {skipped} rows (see errors above)")
print("\nNext steps:")
print("  python bot.py          # start the bot")
print("  /health                # verify user count and Boli totals")
