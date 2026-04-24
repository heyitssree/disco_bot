# schema.py - DuckDB schema management for AstRobot V2
# Creates and manages all tables. Call init_db() at bot startup.

from __future__ import annotations

import csv
import logging
from datetime import date, datetime
from pathlib import Path

import duckdb

logger = logging.getLogger("astrobot.schema")

DB_PATH = Path("data") / "astro_bot.db"

# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db() -> duckdb.DuckDBPyConnection:
    """Create the DuckDB database and all tables if they don't exist.

    Returns an open connection that should be reused for the bot's lifetime.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    _create_tables(conn)
    logger.info("DuckDB initialised at %s", DB_PATH)
    return conn


def _create_tables(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS predictions_cache (
            id          INTEGER PRIMARY KEY,
            cache_type  VARCHAR NOT NULL,
            user_id     BIGINT,
            original_prompt VARCHAR,
            template_text   VARCHAR NOT NULL,
            timestamp   TIMESTAMP DEFAULT current_timestamp
        )
    """)
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS predictions_cache_seq START 1
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id         BIGINT PRIMARY KEY,
            username        VARCHAR NOT NULL,
            rashi           VARCHAR,
            boli_points     INTEGER DEFAULT 0,
            last_seen       TIMESTAMP DEFAULT current_timestamp,
            prediction_count INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_prediction_history (
            id          INTEGER PRIMARY KEY,
            user_id     BIGINT NOT NULL,
            prediction_text VARCHAR NOT NULL,
            timestamp   TIMESTAMP DEFAULT current_timestamp
        )
    """)
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS prediction_history_seq START 1
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS curse_logs (
            id          INTEGER PRIMARY KEY,
            user_id     BIGINT NOT NULL,
            username    VARCHAR NOT NULL,
            curse_used  VARCHAR NOT NULL,
            timestamp   TIMESTAMP DEFAULT current_timestamp
        )
    """)
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS curse_logs_seq START 1
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_omens (
            id              INTEGER PRIMARY KEY,
            generated_text  VARCHAR NOT NULL,
            landmark        VARCHAR NOT NULL,
            omen_date       DATE NOT NULL UNIQUE
        )
    """)
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS daily_omens_seq START 1
    """)

    conn.commit()


# ---------------------------------------------------------------------------
# Predictions cache
# ---------------------------------------------------------------------------

def get_cached_prediction(
    conn: duckdb.DuckDBPyConnection,
    cache_type: str,
    min_count: int = 50,
) -> str | None:
    """Return a random cached template for a given cache_type, or None.

    Only serves from cache when at least *min_count* entries exist.
    """
    result = conn.execute(
        "SELECT COUNT(*) FROM predictions_cache WHERE cache_type = ?",
        [cache_type],
    ).fetchone()
    count = result[0] if result else 0

    if count < min_count:
        return None

    row = conn.execute(
        """
        SELECT template_text FROM predictions_cache
        WHERE cache_type = ?
        ORDER BY RANDOM() LIMIT 1
        """,
        [cache_type],
    ).fetchone()
    return row[0] if row else None


def save_prediction(
    conn: duckdb.DuckDBPyConnection,
    cache_type: str,
    template_text: str,
    user_id: int | None = None,
    original_prompt: str | None = None,
) -> None:
    """Save a generalized (templatized) prediction to the cache."""
    # Avoid exact duplicates
    exists = conn.execute(
        "SELECT 1 FROM predictions_cache WHERE template_text = ? AND cache_type = ?",
        [template_text, cache_type],
    ).fetchone()
    if exists:
        return

    conn.execute(
        """
        INSERT INTO predictions_cache (id, cache_type, user_id, original_prompt, template_text)
        VALUES (nextval('predictions_cache_seq'), ?, ?, ?, ?)
        """,
        [cache_type, user_id, original_prompt, template_text],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# User profiles
# ---------------------------------------------------------------------------

def get_user_profile(
    conn: duckdb.DuckDBPyConnection,
    user_id: int,
) -> dict | None:
    """Return user profile dict or None if user doesn't exist."""
    row = conn.execute(
        "SELECT user_id, username, rashi, boli_points, last_seen, prediction_count "
        "FROM user_stats WHERE user_id = ?",
        [user_id],
    ).fetchone()
    if not row:
        return None
    return {
        "user_id": row[0],
        "username": row[1],
        "rashi": row[2],
        "boli_points": row[3],
        "last_seen": row[4],
        "prediction_count": row[5],
    }


def upsert_user(
    conn: duckdb.DuckDBPyConnection,
    user_id: int,
    username: str,
    rashi: str | None = None,
) -> None:
    """Insert or update a user record."""
    existing = get_user_profile(conn, user_id)
    if existing is None:
        conn.execute(
            """
            INSERT INTO user_stats (user_id, username, rashi, boli_points, last_seen, prediction_count)
            VALUES (?, ?, ?, 0, current_timestamp, 0)
            """,
            [user_id, username, rashi],
        )
    else:
        # Only update rashi if explicitly provided
        if rashi:
            conn.execute(
                "UPDATE user_stats SET username=?, rashi=?, last_seen=current_timestamp WHERE user_id=?",
                [username, rashi, user_id],
            )
        else:
            conn.execute(
                "UPDATE user_stats SET username=?, last_seen=current_timestamp WHERE user_id=?",
                [username, user_id],
            )
    conn.commit()


def update_boli_points(
    conn: duckdb.DuckDBPyConnection,
    user_id: int,
    delta: int,
) -> None:
    """Add (or subtract) Boli Points for a user."""
    conn.execute(
        "UPDATE user_stats SET boli_points = boli_points + ? WHERE user_id = ?",
        [delta, user_id],
    )
    conn.commit()


def increment_prediction_count(
    conn: duckdb.DuckDBPyConnection,
    user_id: int,
) -> None:
    conn.execute(
        "UPDATE user_stats SET prediction_count = prediction_count + 1 WHERE user_id = ?",
        [user_id],
    )
    conn.commit()


def get_leaderboard(
    conn: duckdb.DuckDBPyConnection,
    limit: int = 10,
) -> list[dict]:
    """Return top N users sorted by Boli Points."""
    rows = conn.execute(
        "SELECT username, rashi, boli_points, prediction_count "
        "FROM user_stats ORDER BY boli_points DESC LIMIT ?",
        [limit],
    ).fetchall()
    return [
        {"username": r[0], "rashi": r[1], "boli_points": r[2], "prediction_count": r[3]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# User prediction history (memory)
# ---------------------------------------------------------------------------

def get_last_n_predictions(
    conn: duckdb.DuckDBPyConnection,
    user_id: int,
    n: int = 3,
) -> list[str]:
    """Return the user's last N doomed predictions, newest first."""
    rows = conn.execute(
        """
        SELECT prediction_text FROM user_prediction_history
        WHERE user_id = ?
        ORDER BY timestamp DESC LIMIT ?
        """,
        [user_id, n],
    ).fetchall()
    return [r[0] for r in rows]


def save_user_prediction(
    conn: duckdb.DuckDBPyConnection,
    user_id: int,
    prediction_text: str,
) -> None:
    """Persist a prediction to the user's history."""
    conn.execute(
        """
        INSERT INTO user_prediction_history (id, user_id, prediction_text)
        VALUES (nextval('prediction_history_seq'), ?, ?)
        """,
        [user_id, prediction_text],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Curse logs
# ---------------------------------------------------------------------------

def log_curse(
    conn: duckdb.DuckDBPyConnection,
    user_id: int,
    username: str,
    curse_used: str,
) -> None:
    conn.execute(
        """
        INSERT INTO curse_logs (id, user_id, username, curse_used)
        VALUES (nextval('curse_logs_seq'), ?, ?, ?)
        """,
        [user_id, username, curse_used],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Daily omens
# ---------------------------------------------------------------------------

def get_todays_omen(conn: duckdb.DuckDBPyConnection) -> str | None:
    """Return today's cached omen text, or None if not yet generated."""
    row = conn.execute(
        "SELECT generated_text FROM daily_omens WHERE omen_date = ?",
        [date.today()],
    ).fetchone()
    return row[0] if row else None


def save_daily_omen(
    conn: duckdb.DuckDBPyConnection,
    text: str,
    landmark: str,
) -> None:
    today = date.today()
    existing = conn.execute(
        "SELECT id FROM daily_omens WHERE omen_date = ?", [today]
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE daily_omens SET generated_text=?, landmark=? WHERE omen_date=?",
            [text, landmark, today],
        )
    else:
        conn.execute(
            """
            INSERT INTO daily_omens (id, generated_text, landmark, omen_date)
            VALUES (nextval('daily_omens_seq'), ?, ?, ?)
            """,
            [text, landmark, today],
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Backup / maintenance
# ---------------------------------------------------------------------------

def export_stats_csv(
    conn: duckdb.DuckDBPyConnection,
    path: str = "data/user_stats_backup.csv",
) -> None:
    """Export user_stats to CSV. Useful before wiping the DB."""
    rows = conn.execute(
        "SELECT user_id, username, rashi, boli_points, prediction_count FROM user_stats"
    ).fetchall()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["user_id", "username", "rashi", "boli_points", "prediction_count"])
        writer.writerows(rows)
    logger.info("Exported %d user records to %s", len(rows), path)


def get_table_counts(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Return row counts for all tables. Used by /health command."""
    tables = ["predictions_cache", "user_stats", "user_prediction_history", "curse_logs", "daily_omens"]
    counts = {}
    for table in tables:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        counts[table] = row[0] if row else 0
    return counts
