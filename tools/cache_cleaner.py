# tools/cache_cleaner.py - Periodic cache generalizer for AstRobot
#
# Run manually: python tools/cache_cleaner.py
# Or via cron on GCP VM (weekly, Sunday 2am IST):
#   30 20 * * 6 cd ~/disco_bot && source .venv/bin/activate && python tools/cache_cleaner.py
#
# NOTE: Safe to run while the bot is running (SQLite WAL mode handles concurrent access).

from __future__ import annotations

import logging
import re
import sqlite3
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("cache_cleaner")

DB_PATH = Path("data") / "astro_bot.db"


def load_known_names(conn: sqlite3.Connection) -> list[str]:
    """Load all known usernames from user_stats."""
    rows = conn.execute("SELECT DISTINCT username FROM user_stats").fetchall()
    # Sort longest first to avoid partial replacements (e.g. "Sree" before "Sreekanth")
    names = sorted([r[0] for r in rows if r[0]], key=len, reverse=True)
    logger.info("Loaded %d known usernames.", len(names))
    return names


def generalize_cache(
    conn: sqlite3.Connection,
    known_names: list[str],
) -> int:
    """Replace known usernames in template_text with {user} placeholder.

    Returns count of rows updated.
    """
    rows = conn.execute(
        "SELECT id, template_text FROM predictions_cache"
    ).fetchall()

    updated = 0
    for row_id, template in rows:
        new_template = template
        for name in known_names:
            # Replace only whole-word occurrences (avoid partial matches)
            pattern = re.compile(re.escape(name), re.IGNORECASE)
            new_template = pattern.sub("{user}", new_template)

        if new_template != template:
            conn.execute(
                "UPDATE predictions_cache SET template_text = ? WHERE id = ?",
                [new_template, row_id],
            )
            updated += 1

    if updated:
        conn.commit()
    return updated


def remove_duplicate_templates(conn: sqlite3.Connection) -> int:
    """Delete exact duplicate template_text entries per cache_type, keeping the oldest."""
    before = conn.execute("SELECT COUNT(*) FROM predictions_cache").fetchone()[0]
    conn.execute("""
        DELETE FROM predictions_cache
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM predictions_cache
            GROUP BY cache_type, template_text
        )
    """)
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM predictions_cache").fetchone()[0]
    return before - after


def report_cache_stats(conn: sqlite3.Connection) -> None:
    """Print cache stats per type."""
    rows = conn.execute(
        "SELECT cache_type, COUNT(*) as cnt FROM predictions_cache GROUP BY cache_type"
    ).fetchall()
    logger.info("Cache stats after cleaning:")
    for cache_type, cnt in rows:
        logger.info("  %-20s : %d templates", cache_type, cnt)


def main() -> None:
    if not DB_PATH.exists():
        logger.error("Database not found at %s. Run the bot first.", DB_PATH)
        sys.exit(1)

    logger.info("Opening SQLite at %s", DB_PATH)
    conn = sqlite3.connect(str(DB_PATH))

    known_names = load_known_names(conn)

    if not known_names:
        logger.info("No usernames found in user_stats. Nothing to generalise.")
    else:
        generalised = generalize_cache(conn, known_names)
        logger.info("Generalised %d cache entries.", generalised)

    dupes_removed = remove_duplicate_templates(conn)
    logger.info("Removed %d duplicate templates.", dupes_removed)

    report_cache_stats(conn)
    conn.close()
    logger.info("Cache cleaning complete.")


if __name__ == "__main__":
    main()
