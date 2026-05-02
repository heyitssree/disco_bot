"""
tools/recover_db.py — Emergency DB recovery tool for Navi.

Run this when the bot fails with:
    sqlite3.DatabaseError: file is not a database

It will:
  1. Check whether astro_bot.db is a valid SQLite file
  2. If not, back it up and create a fresh SQLite DB using schema.py
  3. The bot will start fresh (user data is lost unless a backup exists)

Usage:
    python tools/recover_db.py [--force]

Options:
    --force    Skip the confirmation prompt and proceed immediately.
"""

import sys
import os
import shutil
import sqlite3
from datetime import datetime

# Add parent directory to path
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

from dotenv import load_dotenv
load_dotenv()

import schema

SQLITE_MAGIC = b'SQLite format 3\x00'


def check_db(path: str) -> str:
    """Return 'ok', 'corrupted', or 'missing'."""
    if not os.path.exists(path):
        return 'missing'
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
        if header != SQLITE_MAGIC:
            return 'corrupted'
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA integrity_check")
        conn.close()
        return 'ok'
    except sqlite3.DatabaseError:
        return 'corrupted'
    except Exception:
        return 'corrupted'


def recover(force: bool = False) -> None:
    db_path_env = os.getenv("DB_PATH")
    db_path = db_path_env if db_path_env else os.path.join(parent_dir, "data", "astro_bot.db")

    print(f"=== Navi DB Recovery Tool ===")
    print(f"DB path: {db_path}")

    status = check_db(db_path)

    if status == 'ok':
        print("✅ Database is healthy — no recovery needed.")
        return

    if status == 'missing':
        print("⚠️  Database file not found. Creating fresh database...")
    elif status == 'corrupted':
        print("❌ Database file is corrupted or not a valid SQLite file.")

        if not force:
            answer = input("Back up the bad file and create a fresh database? [y/N]: ").strip().lower()
            if answer != 'y':
                print("Aborted.")
                return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = db_path + f".bad_{timestamp}"
        print(f"Backing up corrupt file to: {backup_path}")
        shutil.move(db_path, backup_path)

    # Create fresh DB
    os.environ["DB_PATH"] = db_path
    # Reload schema's DB_PATH
    import importlib
    importlib.reload(schema)

    print("Creating fresh SQLite database...")
    conn = schema.init_db()
    conn.close()
    print(f"✅ Fresh database created at: {db_path}")
    print("You can now restart the bot with: sudo systemctl restart navi")


if __name__ == '__main__':
    force = '--force' in sys.argv
    recover(force=force)
