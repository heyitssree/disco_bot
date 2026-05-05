# schema.py - SQLite schema management for Navi (disco_bot)
# Creates and manages all tables. Call init_db() at bot startup.

from __future__ import annotations

import csv
import logging
import math
import random
import sqlite3
import time
import functools
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TypeVar

import os

logger = logging.getLogger("navi.schema")

_T = TypeVar("_T")

# ---------------------------------------------------------------------------
# DB write retry with exponential backoff (handles "database is locked")
# ---------------------------------------------------------------------------

_DB_RETRY_ATTEMPTS = 5
_DB_RETRY_BASE_DELAY = 0.1  # seconds


def _db_write(fn: Callable[[], _T]) -> _T:
    """Execute a SQLite write callable with exponential backoff on lock errors.

    Retries up to _DB_RETRY_ATTEMPTS times (0.1s, 0.2s, 0.4s, 0.8s, 1.6s).
    Re-raises the last exception if all attempts fail.
    """
    last_exc: Exception | None = None
    for attempt in range(_DB_RETRY_ATTEMPTS):
        try:
            result = fn()
            return result
        except Exception as exc:
            msg = str(exc).lower()
            if "database is locked" not in msg:
                raise
            last_exc = exc
            delay = _DB_RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "SQLite locked (attempt %d/%d) — retrying in %.2fs", attempt + 1, _DB_RETRY_ATTEMPTS, delay
            )
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]


_conn_ref: "sqlite3.Connection | None" = None


_db_path_env = os.getenv("DB_PATH")
DB_PATH = Path(_db_path_env) if _db_path_env else Path("data") / "astro_bot.db"

# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db() -> sqlite3.Connection:
    """Create the SQLite database and all tables if they don't exist.

    Returns an open connection that should be reused for the bot's lifetime.
    WAL mode enables crash-safe concurrent reads without locking corruption.
    """
    global _conn_ref
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(DB_PATH),
        check_same_thread=False,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _conn_ref = conn
    _create_tables(conn)
    logger.info("SQLite initialised at %s", DB_PATH)
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS predictions_cache (
            id          INTEGER PRIMARY KEY,
            cache_type  TEXT NOT NULL,
            user_id     INTEGER,
            original_prompt TEXT,
            template_text   TEXT NOT NULL,
            timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id         INTEGER PRIMARY KEY,
            username        TEXT NOT NULL,
            rashi           TEXT,
            boli_points     INTEGER DEFAULT 0,
            experience      INTEGER DEFAULT 0,
            last_seen       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            prediction_count INTEGER DEFAULT 0,
            extra_actions   INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS gift_log (
            id          INTEGER PRIMARY KEY,
            sender_id   INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL,
            amount      INTEGER NOT NULL,
            gifted_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS partner_score_log (
            id              INTEGER PRIMARY KEY,
            game_id         TEXT NOT NULL UNIQUE,
            user_id         INTEGER NOT NULL,
            guild_id        INTEGER NOT NULL,
            username        TEXT NOT NULL,
            raw_points      INTEGER NOT NULL,
            boli_awarded    INTEGER NOT NULL,
            xp_awarded      INTEGER NOT NULL,
            game_type       TEXT DEFAULT 'default',
            received_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_items (
            item_id       TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            emoji         TEXT NOT NULL,
            base_price    INTEGER NOT NULL,
            current_price INTEGER NOT NULL,
            volatility    TEXT DEFAULT 'medium',
            description   TEXT,
            last_updated  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_holdings (
            id             INTEGER PRIMARY KEY,
            user_id        INTEGER NOT NULL,
            item_id        TEXT NOT NULL,
            quantity       INTEGER NOT NULL DEFAULT 1,
            purchase_price INTEGER NOT NULL,
            purchased_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at     TIMESTAMP NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_price_history (
            id          INTEGER PRIMARY KEY,
            item_id     TEXT NOT NULL,
            price       INTEGER NOT NULL,
            recorded_at DATE NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_prediction_history (
            id          INTEGER PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            prediction_text TEXT NOT NULL,
            timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS curse_logs (
            id          INTEGER PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            curse_used  TEXT NOT NULL,
            timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_omens (
            id              INTEGER PRIMARY KEY,
            generated_text  TEXT NOT NULL,
            landmark        TEXT NOT NULL,
            omen_date       DATE NOT NULL UNIQUE
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            value_str TEXT,
            value_float REAL,
            value_int INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_perks (
            user_id     INTEGER NOT NULL,
            perk_type   TEXT NOT NULL,
            expires_at  TIMESTAMP NOT NULL,
            PRIMARY KEY (user_id, perk_type)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS local_knowledge (
            id          INTEGER PRIMARY KEY,
            category    TEXT NOT NULL,
            term        TEXT NOT NULL,
            description TEXT NOT NULL,
            tags        TEXT DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS app_emojis (
            emoji_id    TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            last_used   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            original_id TEXT,
            animated    INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS local_media (
            shortcut    TEXT PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            file_path   TEXT NOT NULL,
            media_type  TEXT NOT NULL,
            source_url  TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bless_logs (
            id          INTEGER PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            bless_used  TEXT NOT NULL,
            timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Per-game daily play counts (Feature: gambling decoupled from cosmic actions)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS game_daily_counts (
            user_id     INTEGER NOT NULL,
            game_name   TEXT NOT NULL,
            play_date   DATE NOT NULL,
            play_count  INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, game_name, play_date)
        )
    """)

    # Gemini-powered mini-games (2 attempts per user per day each)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS navi_challenge_attempts (
            user_id    INTEGER NOT NULL,
            play_date  DATE NOT NULL,
            play_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, play_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS type_race_attempts (
            user_id    INTEGER NOT NULL,
            play_date  DATE NOT NULL,
            play_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, play_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gemini_score_comments (
            id           INTEGER PRIMARY KEY,
            game_type    TEXT NOT NULL,
            score_bucket INTEGER NOT NULL,
            comment      TEXT NOT NULL
        )
    """)

    # Analytics tables
    conn.execute("""
        CREATE TABLE IF NOT EXISTS command_events (
            id          INTEGER PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            username    TEXT NOT NULL,
            command     TEXT NOT NULL,
            subcommand  TEXT,
            channel_id  INTEGER,
            guild_id    INTEGER,
            latency_ms  INTEGER,
            used_cache  INTEGER DEFAULT 0,
            timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_events (
            id          INTEGER PRIMARY KEY,
            event_type  TEXT NOT NULL,
            detail      TEXT,
            timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_call_log (
            id          INTEGER PRIMARY KEY,
            key_used    TEXT NOT NULL,
            cache_hit   INTEGER DEFAULT 0,
            command     TEXT,
            latency_ms  INTEGER,
            error       TEXT,
            timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Insert default config if empty
    conn.execute("""
        INSERT INTO bot_config (key, value_float, value_int)
        SELECT 'astro_cooldown_seconds', NULL, 60
        WHERE NOT EXISTS (SELECT 1 FROM bot_config WHERE key = 'astro_cooldown_seconds');
    """)
    conn.execute("""
        INSERT INTO bot_config (key, value_float)
        SELECT 'cache_reuse_chance', 0.50
        WHERE NOT EXISTS (SELECT 1 FROM bot_config WHERE key = 'cache_reuse_chance');
    """)
    conn.execute("""
        INSERT INTO bot_config (key, value_float)
        SELECT 'kochi_reply_chance', 0.28
        WHERE NOT EXISTS (SELECT 1 FROM bot_config WHERE key = 'kochi_reply_chance');
    """)
    conn.execute("""
        INSERT INTO bot_config (key, value_float)
        SELECT 'curse_reply_chance', 0.25
        WHERE NOT EXISTS (SELECT 1 FROM bot_config WHERE key = 'curse_reply_chance');
    """)
    conn.execute("""
        INSERT INTO bot_config (key, value_float)
        SELECT 'reversal_chance_owner', 0.45
        WHERE NOT EXISTS (SELECT 1 FROM bot_config WHERE key = 'reversal_chance_owner');
    """)
    conn.execute("""
        INSERT INTO bot_config (key, value_str)
        SELECT 'WEATHER_ALERT_TIME', '07:00'
        WHERE NOT EXISTS (SELECT 1 FROM bot_config WHERE key = 'WEATHER_ALERT_TIME');
    """)
    # Feature on/off flags (1 = enabled, 0 = disabled)
    for flag in (
        "feature_kochi_replies", "feature_curse_replies", "feature_boli_points",
        "feature_welcome", "feature_navi", "feature_vibe_check", "feature_kanmanilla",
        "feature_audit", "feature_mod_tldr", "feature_link_summary", "feature_strikes",
        "feature_navi_challenge", "feature_type_race",
    ):
        conn.execute(f"""
            INSERT INTO bot_config (key, value_int)
            SELECT '{flag}', 1
            WHERE NOT EXISTS (SELECT 1 FROM bot_config WHERE key = '{flag}');
        """)
    # Master kill switch — default OFF (0 = bot is alive)
    conn.execute("""
        INSERT INTO bot_config (key, value_int)
        SELECT 'master_killswitch', 0
        WHERE NOT EXISTS (SELECT 1 FROM bot_config WHERE key = 'master_killswitch');
    """)

    conn.execute("""
        INSERT INTO bot_config (key, value_int)
        SELECT 'local_media_max_per_user', 20
        WHERE NOT EXISTS (SELECT 1 FROM bot_config WHERE key = 'local_media_max_per_user');
    """)
    conn.execute("""
        INSERT INTO bot_config (key, value_int)
        SELECT 'local_media_max_global', 200
        WHERE NOT EXISTS (SELECT 1 FROM bot_config WHERE key = 'local_media_max_global');
    """)

    # Emoji for link-summary reaction (Feature 3)
    conn.execute("""
        INSERT INTO bot_config (key, value_str)
        SELECT 'link_summary_emoji', '📰'
        WHERE NOT EXISTS (SELECT 1 FROM bot_config WHERE key = 'link_summary_emoji');
    """)

    # Safe ALTER TABLE migrations — SQLite does not support IF NOT EXISTS on ALTER TABLE
    for col_sql in (
        "ALTER TABLE user_stats ADD COLUMN strikes INTEGER DEFAULT 0",
        "ALTER TABLE user_stats ADD COLUMN daily_action_count INTEGER DEFAULT 0",
        "ALTER TABLE user_stats ADD COLUMN last_action_date DATE",
        "ALTER TABLE user_stats ADD COLUMN extra_actions INTEGER DEFAULT 0",
        # Tiered action refill tracking (Feature: tiered shop pricing)
        "ALTER TABLE user_stats ADD COLUMN daily_refill_count INTEGER DEFAULT 0",
        "ALTER TABLE user_stats ADD COLUMN last_refill_date DATE",
        # local_media: extended storage type support
        "ALTER TABLE local_media ADD COLUMN storage_type TEXT DEFAULT 'local'",
        "ALTER TABLE local_media ADD COLUMN discord_id TEXT",
        "ALTER TABLE local_media ADD COLUMN discord_name TEXT",
        "ALTER TABLE local_media ADD COLUMN animated INTEGER DEFAULT 0",
        # Feature 2: decouple XP from Boli Points
        "ALTER TABLE user_stats ADD COLUMN experience INTEGER DEFAULT 0",
    ):
        try:
            conn.execute(col_sql)
        except Exception:
            pass  # column already exists

    # Seed experience from existing boli_points for all users who haven't been migrated yet
    conn.execute("""
        UPDATE user_stats SET experience = boli_points
        WHERE experience = 0 AND boli_points > 0
    """)

    # Seed market items if table is empty
    _seed_market_items(conn)

    conn.commit()


# ---------------------------------------------------------------------------
# Predictions cache
# ---------------------------------------------------------------------------

def get_cached_prediction(
    conn: sqlite3.Connection,
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
    conn: sqlite3.Connection,
    cache_type: str,
    template_text: str,
    user_id: int | None = None,
    original_prompt: str | None = None,
) -> None:
    """Save a generalized (templatized) prediction to the cache."""
    exists = conn.execute(
        "SELECT 1 FROM predictions_cache WHERE template_text = ? AND cache_type = ?",
        [template_text, cache_type],
    ).fetchone()
    if exists:
        return

    def _write() -> None:
        conn.execute(
            """
            INSERT INTO predictions_cache (id, cache_type, user_id, original_prompt, template_text)
            VALUES (NULL, ?, ?, ?, ?)
            """,
            [cache_type, user_id, original_prompt, template_text],
        )
        conn.commit()
    _db_write(_write)


# ---------------------------------------------------------------------------
# User profiles
# ---------------------------------------------------------------------------

def get_user_profile(
    conn: sqlite3.Connection,
    user_id: int,
) -> dict | None:
    """Return user profile dict or None if user doesn't exist."""
    row = conn.execute(
        "SELECT user_id, username, rashi, boli_points, experience, last_seen, prediction_count, "
        "daily_action_count, last_action_date, extra_actions "
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
        "experience": row[4],
        "last_seen": row[5],
        "prediction_count": row[6],
        "daily_action_count": row[7],
        "last_action_date": row[8],
        "extra_actions": row[9],
    }


def upsert_user(
    conn: sqlite3.Connection,
    user_id: int,
    username: str,
    rashi: str | None = None,
) -> None:
    """Insert or update a user record."""
    existing = get_user_profile(conn, user_id)

    def _write() -> None:
        if existing is None:
            conn.execute(
                """
                INSERT INTO user_stats (user_id, username, rashi, boli_points, last_seen, prediction_count)
                VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP, 0)
                """,
                [user_id, username, rashi],
            )
        elif rashi:
            conn.execute(
                "UPDATE user_stats SET username=?, rashi=?, last_seen=CURRENT_TIMESTAMP WHERE user_id=?",
                [username, rashi, user_id],
            )
        else:
            conn.execute(
                "UPDATE user_stats SET username=?, last_seen=CURRENT_TIMESTAMP WHERE user_id=?",
                [username, user_id],
            )
        conn.commit()
    _db_write(_write)


def update_boli_points(
    conn: sqlite3.Connection,
    user_id: int,
    delta: int,
) -> None:
    """Add (or subtract) Boli Points for a user."""
    _db_write(lambda: (
        conn.execute(
            "UPDATE user_stats SET boli_points = boli_points + ? WHERE user_id = ?",
            [delta, user_id],
        ),
        conn.commit(),
    ))


def add_experience(
    conn: sqlite3.Connection,
    user_id: int,
    delta: int,
) -> None:
    """Add XP for a user. XP never decreases — negative deltas are silently clamped to 0."""
    if delta <= 0:
        return
    _db_write(lambda: (
        conn.execute(
            "UPDATE user_stats SET experience = experience + ? WHERE user_id = ?",
            [delta, user_id],
        ),
        conn.commit(),
    ))


def get_experience(conn: sqlite3.Connection, user_id: int) -> int:
    """Return raw experience points for a user (0 if not found)."""
    row = conn.execute(
        "SELECT experience FROM user_stats WHERE user_id = ?", [user_id]
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def increment_prediction_count(
    conn: sqlite3.Connection,
    user_id: int,
) -> None:
    _db_write(lambda: (
        conn.execute(
            "UPDATE user_stats SET prediction_count = prediction_count + 1 WHERE user_id = ?",
            [user_id],
        ),
        conn.commit(),
    ))


def get_leaderboard(
    conn: sqlite3.Connection,
    limit: int = 10,
    offset: int = 0,
) -> list[dict]:
    """Return users sorted by Boli Points, with optional pagination offset."""
    rows = conn.execute(
        "SELECT username, rashi, boli_points, prediction_count "
        "FROM user_stats ORDER BY boli_points DESC LIMIT ? OFFSET ?",
        [limit, offset],
    ).fetchall()
    return [
        {"username": r[0], "rashi": r[1], "boli_points": r[2], "prediction_count": r[3]}
        for r in rows
    ]


def count_leaderboard_entries(conn: sqlite3.Connection) -> int:
    """Return total number of users with a stats record."""
    row = conn.execute("SELECT COUNT(*) FROM user_stats").fetchone()
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# User prediction history (memory)
# ---------------------------------------------------------------------------

def get_last_n_predictions(
    conn: sqlite3.Connection,
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
    conn: sqlite3.Connection,
    user_id: int,
    prediction_text: str,
) -> None:
    """Persist a prediction to the user's history."""
    def _write() -> None:
        conn.execute(
            """
            INSERT INTO user_prediction_history (id, user_id, prediction_text)
            VALUES (NULL, ?, ?)
            """,
            [user_id, prediction_text],
        )
        conn.commit()
    _db_write(_write)


def get_todays_user_prediction(
    conn: sqlite3.Connection,
    user_id: int,
) -> str | None:
    """Check if the user already received a prediction today, return it if so."""
    row = conn.execute(
        """
        SELECT prediction_text FROM user_prediction_history
        WHERE user_id = ? AND DATE(timestamp) = DATE('now')
        ORDER BY timestamp DESC LIMIT 1
        """,
        [user_id]
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Curse logs
# ---------------------------------------------------------------------------

def log_curse(
    conn: sqlite3.Connection,
    user_id: int,
    username: str,
    curse_used: str,
) -> None:
    def _write() -> None:
        conn.execute(
            """
            INSERT INTO curse_logs (id, user_id, username, curse_used)
            VALUES (NULL, ?, ?, ?)
            """,
            [user_id, username, curse_used],
        )
        conn.commit()
    _db_write(_write)


# ---------------------------------------------------------------------------
# Daily omens
# ---------------------------------------------------------------------------

def get_todays_omen(conn: sqlite3.Connection) -> str | None:
    """Return today's cached omen text, or None if not yet generated."""
    row = conn.execute(
        "SELECT generated_text FROM daily_omens WHERE omen_date = ?",
        [date.today()],
    ).fetchone()
    return row[0] if row else None


def save_daily_omen(
    conn: sqlite3.Connection,
    text: str,
    landmark: str,
) -> None:
    today = date.today()
    existing = conn.execute(
        "SELECT id FROM daily_omens WHERE omen_date = ?", [today]
    ).fetchone()

    def _write() -> None:
        if existing:
            conn.execute(
                "UPDATE daily_omens SET generated_text=?, landmark=? WHERE omen_date=?",
                [text, landmark, today],
            )
        else:
            conn.execute(
                """
                INSERT INTO daily_omens (id, generated_text, landmark, omen_date)
                VALUES (NULL, ?, ?, ?)
                """,
                [text, landmark, today],
            )
        conn.commit()
    _db_write(_write)


# ---------------------------------------------------------------------------
# Bot Config
# ---------------------------------------------------------------------------

def get_config_float(conn: sqlite3.Connection, key: str, default: float) -> float:
    row = conn.execute("SELECT value_float FROM bot_config WHERE key = ?", [key]).fetchone()
    return float(row[0]) if row and row[0] is not None else default

def get_config_int(conn: sqlite3.Connection, key: str, default: int) -> int:
    row = conn.execute("SELECT value_int FROM bot_config WHERE key = ?", [key]).fetchone()
    return int(row[0]) if row and row[0] is not None else default

def set_config_float(conn: sqlite3.Connection, key: str, value: float) -> None:
    _db_write(lambda: (
        conn.execute("UPDATE bot_config SET value_float = ? WHERE key = ?", [value, key]),
        conn.commit(),
    ))

def set_config_int(conn: sqlite3.Connection, key: str, value: int) -> None:
    _db_write(lambda: (
        conn.execute("UPDATE bot_config SET value_int = ? WHERE key = ?", [value, key]),
        conn.commit(),
    ))

def get_config_str(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value_str FROM bot_config WHERE key = ?", [key]).fetchone()
    return str(row[0]) if row and row[0] is not None else default

def set_config_str(conn: sqlite3.Connection, key: str, value: str) -> None:
    _db_write(lambda: (
        conn.execute(
            "INSERT INTO bot_config (key, value_str) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value_str = excluded.value_str",
            [key, value],
        ),
        conn.commit(),
    ))

def get_all_configs(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT key, value_str, value_float, value_int FROM bot_config").fetchall()
    config = {}
    for r in rows:
        key = r[0]
        if r[2] is not None:
            config[key] = r[2]
        elif r[3] is not None:
            config[key] = r[3]
        else:
            config[key] = r[1]
    return config


# ---------------------------------------------------------------------------
# Strike system (Feature 7)
# ---------------------------------------------------------------------------

def get_user_strikes(conn: sqlite3.Connection, user_id: int) -> int:
    """Return the current strike count for a user (0 if not found)."""
    row = conn.execute(
        "SELECT strikes FROM user_stats WHERE user_id = ?", [user_id]
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def increment_user_strikes(conn: sqlite3.Connection, user_id: int) -> int:
    """Increment strikes by 1 and return the new count."""
    _db_write(lambda: (
        conn.execute(
            "UPDATE user_stats SET strikes = strikes + 1 WHERE user_id = ?", [user_id]
        ),
        conn.commit(),
    ))
    return get_user_strikes(conn, user_id)


def reset_user_strikes(conn: sqlite3.Connection, user_id: int) -> None:
    """Reset a user's strikes to 0."""
    _db_write(lambda: (
        conn.execute("UPDATE user_stats SET strikes = 0 WHERE user_id = ?", [user_id]),
        conn.commit(),
    ))


def set_user_points(conn: sqlite3.Connection, user_id: int, username: str, points: int, prediction_count: int = 0, rashi: str | None = None) -> None:
    """Upsert a user's Boli Points directly — used for manual data restore."""
    existing = get_user_profile(conn, user_id)
    def _write() -> None:
        if existing is None:
            conn.execute(
                "INSERT INTO user_stats (user_id, username, rashi, boli_points, last_seen, prediction_count) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)",
                [user_id, username, rashi, points, prediction_count],
            )
        else:
            conn.execute(
                "UPDATE user_stats SET boli_points=?, prediction_count=?, username=?, rashi=COALESCE(?, rashi) WHERE user_id=?",
                [points, prediction_count, username, rashi, user_id],
            )
        conn.commit()
    _db_write(_write)


def reset_all_strikes(conn: sqlite3.Connection) -> int:
    """Reset strikes to 0 for every user. Returns the number of rows affected."""
    count = conn.execute("SELECT COUNT(*) FROM user_stats WHERE strikes > 0").fetchone()[0]
    _db_write(lambda: (
        conn.execute("UPDATE user_stats SET strikes = 0"),
        conn.commit(),
    ))
    return count


# ---------------------------------------------------------------------------
# Local Knowledge seed
# ---------------------------------------------------------------------------

_LOCAL_KNOWLEDGE_DATA: list[tuple[str, str, str, str]] = [
    # (category, term, description, tags)
    # ---- LANDMARKS ----
    ("landmark", "Palayam / Connemara Market", "The bustling heart of the city for wholesale groceries, flowers, and chaos", "market,heritage,morning"),
    ("landmark", "Thampanoor", "The absolute epicenter of transit — central Railway Station and KSRTC bus stand", "transport,morning,junction"),
    ("landmark", "KD Puram", "Local abbreviation for Kesavadasapuram — a major traffic bottleneck and residential hub", "transport,evening,junction"),
    ("landmark", "Vellayambalam to Kowdiar Stretch", "The VIP road — wide, shaded by massive trees, lined with cafes and Rajapatham aesthetics", "posh,morning,culture"),
    ("landmark", "Chalai Market", "The labyrinthine heritage market near East Fort — from hardware to wholesale stationery", "market,heritage,nostalgia"),
    ("landmark", "Ponmudi", "The local hill station — standard weekend ride for youths and bikers", "weekend,nature,nostalgia"),
    ("landmark", "Sreekaryam / Pongumoodu", "Gateways to the tech-corridor — Technopark and Kazhakkoottam", "IT-crowd,weekday,morning"),
    ("landmark", "Museum Campus", "Napier Museum, Zoo, and Kanakakkunnu Palace — ultimate morning-walk and family hangout spot", "morning,heritage,family"),
    ("landmark", "Technopark", "Kerala's largest IT park — the realm of the IT Ambitions crowd", "IT-crowd,weekday,morning"),
    ("landmark", "East Fort", "Historical heart of the city near Padmanabhaswamy Temple — always busy", "heritage,morning,market"),
    ("landmark", "Vazhuthacaud", "The coaching centre universe — students everywhere with heavy bags", "students,morning,weekday"),
    ("landmark", "Jagathy", "Residential locality with a distinct old-city character", "nostalgia,morning"),
    ("landmark", "Sasthamangalam", "Quiet, leafy residential area — the old-money neighbourhood", "posh,morning"),
    ("landmark", "Pettah", "Junction area connecting Thampanoor to the old city — chaotic and busy", "transport,morning,junction"),
    ("landmark", "Varkala Cliff", "The Bali of TVM — laid-back cliffside vibes and backpacker energy", "beach,relaxed,weekend"),
    ("landmark", "Kanakakkunnu Palace", "Evening dates, cultural exhibitions, and open-air events in a colonial setting", "evening,posh,culture"),
    ("landmark", "Vizhinjam Port", "Massive cranes and non-stop development talk — the city's infrastructure pride", "infrastructure,modern,weekend"),
    ("landmark", "Akkulam Tourist Village", "Weekend picnic nostalgia — paddle boats and school-trip memories", "weekend,nostalgia,family"),
    ("landmark", "Technopark Phase 3", "The Ganga and Yamuna buildings — IT crowd gossip central", "IT-crowd,morning,weekday"),
    ("landmark", "Beemapally", "Vibrant market quarter with deep spiritual energy and Uroos festival fame", "market,spiritual,weekend"),
    ("landmark", "Vellayambalam Square", "The neon-lit city heart — always busy, never boring", "evening,junction"),
    ("landmark", "Shankumugham Beach", "Sunset rounds and the iconic giant mermaid statue by the sea", "evening,beach,sunset"),
    ("landmark", "Manaveeyam Veedhi", "The intellectual and cultural street — bookshops, debates, street art", "culture,arts,evening"),
    ("landmark", "Putharikandam Maidanam", "Political rallies and massive city exhibitions — Trivandrum's public square", "politics,events,morning"),
    ("landmark", "Kowdiar Avenue", "Posh road where the Rajahs live — broad, quiet, and dripping with old wealth", "posh,morning"),
    ("landmark", "Padmanabhaswamy Temple", "The world's richest temple — Vault B rumors never end", "spiritual,history,morning"),
    ("landmark", "Priyadarshini Planetarium", "School trip nostalgia — where every Trivandrum child learned about space", "nostalgia,family,morning"),
    ("landmark", "Napier Museum", "Morning walkers' paradise — heritage building, peacocks, and bureaucrats jogging", "morning,heritage"),
    ("landmark", "Lulu Mall TVM", "The new weekend traffic nightmare — shopping, food court, and parking grief", "weekend,shopping"),
    ("landmark", "Aruvikkara Dam", "Quick getaway for fresh water, peace, and picnics outside the city", "weekend,nature"),
    ("landmark", "Neyyar Dam", "Scenic spot with the lion safari — proper family outing material", "weekend,nature,family"),
    ("landmark", "Ponmudi Hill Station", "Mist-covered hairpin turns and cool air — the classic TVM escape", "weekend,nature,nostalgia"),
    ("landmark", "Agasthyarkoodam", "The ultimate trekking goal for serious hikers — breathtaking summit views", "nature,adventure,weekend"),
    # ---- FOOD ----
    ("food", "Boli and Paal Payasam", "The undisputed king of Trivandrum Sadya — sweet crepe mashed into warm milk payasam", "feast,traditional"),
    ("food", "Kethel's Chicken (Rahmaniya)", "Legendary roadside eatery — signature spicy chicken that draws queues", "street-food,spicy,nostalgia"),
    ("food", "Zam Zam (Palayam)", "Pioneer of Al Faham and Shawarma in the city — a cultural institution for youth", "street-food,evening,nostalgia"),
    ("food", "Indian Coffee House (Thampanoor)", "Iconic spiraling red-brick building — beetroot cutlets and uniformed waiters", "nostalgia,snack,morning"),
    ("food", "Sree Muruka Cafe (SMS)", "Famous for the classic Kerala combo: Pazham Pori and Beef Roast", "snack,morning,nostalgia"),
    ("food", "Maha Boly", "The dedicated shop for buying Boli in bulk — every TVM event needs this place", "snack,sweet,nostalgia"),
    ("food", "Rasavadai", "Popular evening snack heavily influenced by Tamil cuisine — spicy and addictive", "snack,spicy,evening"),
    ("food", "Evening chaya from thattukada", "Street tea at a local stall — mandatory daily ritual for every true Trivandrumite", "evening,social,nostalgia"),
    ("food", "Kizhi Parotta", "Steamed in banana leaf and soaked in rich gravy — dinner perfection", "dinner,rich"),
    ("food", "Pazham Pori and Beef Roast", "The Kochi-origin combo that Trivandrum has fully and proudly adopted", "snack,evening"),
    ("food", "Pazhamkanji", "Fermented rice served in earthen pots with fresh fish fry — morning soul food", "morning,traditional"),
    ("food", "Nadan Pothu Roast", "Spicy beef roast that pairs with absolutely anything on the menu", "dinner,spicy"),
    ("food", "Kappa and Meen Mulakittathu", "Spicy fish curry and mashed cassava — the classic Kerala lunch combo", "lunch,traditional"),
    ("food", "Sharjah Shake", "The quintessential Kerala milkshake — thick, sweet, and nostalgic", "evening,dessert,nostalgia"),
    ("food", "Unnakkaya", "Sweet banana snack stuffed with coconut — traditional teatime treat", "snack,sweet,traditional"),
    ("food", "Thattukada Omelette", "Street-style omelette heavy on onions and green chilies — late-night staple", "street-food,evening"),
    ("food", "Kulukki Sarbath", "Shaken lemonade with basil seeds — the summer street drink of choice", "drink,summer,evening"),
    ("food", "Fish Peera", "Small fish cooked with grated coconut — humble and delicious traditional dish", "lunch,traditional"),
    # ---- CULTURE ----
    ("culture", "IFFK (International Film Festival of Kerala)", "Every December the city turns into a cinephile paradise — delegates with Kalamandalam bags everywhere", "culture,arts,evening"),
    ("culture", "Attukal Pongala", "World's largest gathering of women — entire city shuts down, every street becomes an open-air kitchen", "festival,spiritual,morning"),
    ("culture", "Techie vs Core City Divide", "Gentle cultural gap between IT crowd in Technopark and traditional inner-city residents of Kowdiar/Sasthamangalam", "IT-crowd,culture,weekday"),
    ("culture", "Ramachandran Textiles East Fort", "The absolute juggernaut of local shopping — if a local buys budget clothes, they are here", "market,nostalgia,morning"),
    ("culture", "Evening Chaya at Thattukada", "Trivandrum operates on a slower bureaucratic clock — evening tea with politics and cinema discussion is sacred", "evening,social,nostalgia"),
    ("culture", "Tagore Theatre Events", "Heart of the city's arts scene — theatre, music, and cultural programmes", "culture,arts,evening"),
    ("culture", "Napier Museum Morning Walk", "High-profile bureaucrats and retirees jogging between heritage buildings — a daily Trivandrum ritual", "morning,posh,heritage"),
    ("culture", "KSRTC Minnal Bus", "Highway terror incarnate — the fear of God on every inter-city route", "transport,nostalgia"),
    ("culture", "Technopark Swipe Cards", "The mark of the IT Ambitions crowd — the badge that separates tech from the rest", "IT-crowd,weekday"),
    ("culture", "Beemapally Uroos", "Massive annual spiritual and market event — draws the whole city to the southern quarter", "festival,spiritual,weekend"),
    ("culture", "Onam Lighting", "Vellayambalam to East Fort glowing in festive lights — the city at its most beautiful", "festival,evening"),
    ("culture", "Chaver Bus Drivers", "The legendary private bus drivers who treat every route as a Formula 1 race", "transport,nostalgia"),
    ("culture", "Thampanoor Rush Hour", "The ultimate test of patience — every mode of transport colliding at once", "transport,morning"),
    ("culture", "Vazhuthacaud Coaching Vibe", "Thousands of students cramming for exams — the coaching centre capital of TVM", "students,morning,weekday"),
    ("culture", "Rainy Day Chaya", "The collective Malayali monsoon obsession — tea tastes better when it rains, fact", "monsoon,evening,social"),
    ("culture", "Technopark Friday Home-Runs", "Mass exodus of IT employees every Friday evening — entire Kazhakkoottam empties out", "IT-crowd,evening"),
    ("culture", "Padmanabhaswamy Treasure Mystery", "Endless speculation about Vault B — the rumour that never dies in TVM", "history,mystery"),
    ("culture", "Chalai Market Squeeze", "The beautiful chaos of buying everything from a pin to an elephant in one labyrinthine market", "market,heritage,nostalgia"),
    ("culture", "KD Puram Traffic Jam", "The legendary jam that defines TVM evenings — nothing moves, everyone accepts it", "transport,evening"),
    # ---- EXPRESSIONS ----
    ("expression", "Appi", "Term of endearment for a baby or child — true locals know its innocent Trivandrum roots", "language,local"),
    ("expression", "Kili poyi", "Literally the bird flew away — used when someone is utterly confused or shocked", "language,local"),
    ("expression", "Oola", "Useless, pathetic, or of very poor quality — the ultimate dismissal", "language,local"),
    ("expression", "Shokam", "A sad, boring, or pathetic situation — Trivandrum's favourite word for disappointment", "language,local"),
    ("expression", "Chumma", "Simply or for no reason — a universal Malayali word heavily overused in TVM", "language,local"),
    ("expression", "Eda / Edi", "Informal hey used constantly among friends — gender variant matters in Trivandrum", "language,local"),
    ("expression", "Thirontharam", "How locals casually pronounce Thiruvananthapuram at speed — the true insider marker", "language,local"),
    ("expression", "Vayye?", "Are you not well? or Can't you do it? — frequently sarcastic", "language,local"),
    ("expression", "Pillacha", "Respectful but familiar address for an older man — shopkeeper or neighbour energy", "language,local"),
    ("expression", "Kidilam / Kidu", "Absolutely awesome or fantastic — the highest praise in the TVM lexicon", "language,local"),
    ("expression", "Vishayam", "Matter, issue, situation — as in what is this vishayam?", "language,local"),
    ("expression", "Lokam", "World or scene — IT lokam means the tech crowd and their universe", "language,local"),
    ("expression", "Chetta", "Elder brother; respectful address for older men you don't know well", "language,local"),
    ("expression", "Mone", "Son; affectionate address that can also sound patronising depending on tone", "language,local"),
    ("expression", "IT Ambitions", "The Technopark crowd who believe they have escaped Thirontharam — they haven't", "language,local,IT-crowd"),
]


def seed_local_knowledge(conn: sqlite3.Connection, force: bool = False) -> None:
    """Populate local_knowledge table from curated data. Skips if already seeded unless force=True."""
    count = conn.execute("SELECT COUNT(*) FROM local_knowledge").fetchone()[0]
    if count > 0 and not force:
        logger.info("local_knowledge already seeded (%d rows) — skipping.", count)
        return

    def _write() -> None:
        conn.execute("DELETE FROM local_knowledge")
        conn.executemany(
            """
            INSERT INTO local_knowledge (id, category, term, description, tags)
            VALUES (NULL, ?, ?, ?, ?)
            """,
            [(cat, term, desc, tags) for cat, term, desc, tags in _LOCAL_KNOWLEDGE_DATA],
        )
        conn.commit()

    _db_write(_write)
    logger.info("Seeded local_knowledge with %d entries.", len(_LOCAL_KNOWLEDGE_DATA))


# ---------------------------------------------------------------------------
# Backup / maintenance
# ---------------------------------------------------------------------------

def export_stats_csv(
    conn: sqlite3.Connection,
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


def get_table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Return row counts for all tables. Used by /health command."""
    tables = ["predictions_cache", "user_stats", "user_prediction_history", "curse_logs", "daily_omens"]
    counts = {}
    for table in tables:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        counts[table] = row[0] if row else 0
    return counts


def get_health_stats(conn: sqlite3.Connection) -> dict:
    """Return a rich set of runtime statistics for the /health command."""
    stats: dict = {}

    # --- User activity ---
    stats["total_users"] = (
        conn.execute("SELECT COUNT(*) FROM user_stats").fetchone() or (0,)
    )[0]
    stats["active_today"] = (
        conn.execute(
            "SELECT COUNT(*) FROM user_stats WHERE DATE(last_seen) = DATE('now')"
        ).fetchone() or (0,)
    )[0]
    stats["active_week"] = (
        conn.execute(
            "SELECT COUNT(*) FROM user_stats WHERE last_seen >= datetime('now', '-7 days')"
        ).fetchone() or (0,)
    )[0]
    stats["total_boli_points"] = (
        conn.execute("SELECT COALESCE(SUM(boli_points), 0) FROM user_stats").fetchone() or (0,)
    )[0]
    top_row = conn.execute(
        "SELECT username, boli_points FROM user_stats ORDER BY boli_points DESC LIMIT 1"
    ).fetchone()
    stats["top_user"] = f"{top_row[0]} ({top_row[1]} pts)" if top_row else "N/A"

    # --- Prediction activity ---
    stats["predictions_today"] = (
        conn.execute(
            "SELECT COUNT(*) FROM user_prediction_history WHERE DATE(timestamp) = DATE('now')"
        ).fetchone() or (0,)
    )[0]
    cache_rows = conn.execute(
        "SELECT cache_type, COUNT(*) FROM predictions_cache GROUP BY cache_type ORDER BY cache_type"
    ).fetchall()
    stats["cache_by_type"] = {r[0]: r[1] for r in cache_rows}

    # --- Curse log ---
    stats["curses_today"] = (
        conn.execute(
            "SELECT COUNT(*) FROM curse_logs WHERE DATE(timestamp) = DATE('now')"
        ).fetchone() or (0,)
    )[0]
    stats["curses_total"] = (
        conn.execute("SELECT COUNT(*) FROM curse_logs").fetchone() or (0,)
    )[0]
    top_curse = conn.execute(
        "SELECT curse_used, COUNT(*) AS n FROM curse_logs GROUP BY curse_used ORDER BY n DESC LIMIT 1"
    ).fetchone()
    stats["top_curse"] = f'"{top_curse[0]}" × {top_curse[1]}' if top_curse else "N/A"

    # --- Active perks ---
    stats["active_perks"] = (
        conn.execute(
            "SELECT COUNT(*) FROM user_perks WHERE expires_at > CURRENT_TIMESTAMP"
        ).fetchone() or (0,)
    )[0]

    return stats


# ---------------------------------------------------------------------------
# Daily action quota (curses + blessings combined, 20/day) — resets at midnight IST
# ---------------------------------------------------------------------------

_IST = timezone(timedelta(hours=5, minutes=30))


def _today_ist() -> date:
    """Return the current date in IST (UTC+5:30), used for daily quota resets."""
    return datetime.now(_IST).date()


def get_daily_action_count(conn: sqlite3.Connection, user_id: int) -> int:
    """Return today's combined curse/bless action count. Returns 0 if the record is from a previous day."""
    row = conn.execute(
        "SELECT daily_action_count, last_action_date FROM user_stats WHERE user_id = ?",
        [user_id],
    ).fetchone()
    if not row:
        return 0
    count, last_date = row
    if last_date != _today_ist():
        return 0
    return int(count) if count else 0


def increment_daily_action_count(conn: sqlite3.Connection, user_id: int) -> int:
    """Increment daily action count (resetting to 1 if it's a new day). Returns new count."""
    today = _today_ist()
    _db_write(lambda: (
        conn.execute(
            """
            UPDATE user_stats
            SET
                daily_action_count = CASE WHEN last_action_date = ? THEN daily_action_count + 1 ELSE 1 END,
                last_action_date = ?
            WHERE user_id = ?
            """,
            [today, today, user_id],
        ),
        conn.commit(),
    ))
    row = conn.execute(
        "SELECT daily_action_count FROM user_stats WHERE user_id = ?", [user_id]
    ).fetchone()
    return int(row[0]) if row and row[0] else 1


def get_extra_actions(conn: sqlite3.Connection, user_id: int) -> int:
    """Return the number of purchased extra actions remaining for a user."""
    row = conn.execute(
        "SELECT extra_actions FROM user_stats WHERE user_id = ?", [user_id]
    ).fetchone()
    return int(row[0]) if row and row[0] else 0


def decrement_extra_actions(conn: sqlite3.Connection, user_id: int) -> None:
    """Consume one extra action token, flooring at 0."""
    _db_write(lambda: (
        conn.execute(
            "UPDATE user_stats SET extra_actions = CASE WHEN extra_actions > 0 THEN extra_actions - 1 ELSE 0 END WHERE user_id = ?",
            [user_id],
        ),
        conn.commit(),
    ))


def add_extra_actions(conn: sqlite3.Connection, user_id: int, amount: int) -> None:
    """Add purchased extra action tokens to a user's balance."""
    _db_write(lambda: (
        conn.execute(
            "UPDATE user_stats SET extra_actions = extra_actions + ? WHERE user_id = ?",
            [amount, user_id],
        ),
        conn.commit(),
    ))


# ---------------------------------------------------------------------------
# Daily action refill tracking (tiered shop pricing: 30 Boli → 50 Boli → blocked)
# ---------------------------------------------------------------------------

def get_daily_refill_count(conn: sqlite3.Connection, user_id: int) -> int:
    """Return how many action refills the user has bought today (IST). 0 if a different day."""
    row = conn.execute(
        "SELECT daily_refill_count, last_refill_date FROM user_stats WHERE user_id = ?",
        [user_id],
    ).fetchone()
    if not row:
        return 0
    count, last_date = row
    if last_date != _today_ist():
        return 0
    return int(count) if count else 0


def increment_daily_refill_count(conn: sqlite3.Connection, user_id: int) -> int:
    """Increment today's refill count (resetting to 1 if it's a new day). Returns new count."""
    today = _today_ist()
    _db_write(lambda: (
        conn.execute(
            """
            UPDATE user_stats
            SET
                daily_refill_count = CASE WHEN last_refill_date = ? THEN daily_refill_count + 1 ELSE 1 END,
                last_refill_date = ?
            WHERE user_id = ?
            """,
            [today, today, user_id],
        ),
        conn.commit(),
    ))
    row = conn.execute(
        "SELECT daily_refill_count FROM user_stats WHERE user_id = ?", [user_id]
    ).fetchone()
    return int(row[0]) if row and row[0] else 1


# ---------------------------------------------------------------------------
# Per-game daily play quota (30 combined plays across all games per day)
# ---------------------------------------------------------------------------

_GAME_DAILY_LIMIT = 30


def get_game_daily_count(conn: sqlite3.Connection, user_id: int, game_name: str) -> int:
    """Return today's play count for the given game (IST). Returns 0 if no record today."""
    today = _today_ist()
    row = conn.execute(
        "SELECT play_count FROM game_daily_counts WHERE user_id = ? AND game_name = ? AND play_date = ?",
        [user_id, game_name, today],
    ).fetchone()
    return int(row[0]) if row else 0


def get_total_game_daily_count(conn: sqlite3.Connection, user_id: int) -> int:
    """Return total plays across ALL games today (IST). Used for the combined 30/day limit."""
    today = _today_ist()
    row = conn.execute(
        "SELECT COALESCE(SUM(play_count), 0) FROM game_daily_counts WHERE user_id = ? AND play_date = ?",
        [user_id, today],
    ).fetchone()
    return int(row[0]) if row else 0


def increment_game_daily_count(conn: sqlite3.Connection, user_id: int, game_name: str) -> int:
    """Increment today's play count for the given game. Returns the new count."""
    today = _today_ist()
    _db_write(lambda: (
        conn.execute(
            """
            INSERT INTO game_daily_counts (user_id, game_name, play_date, play_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT (user_id, game_name, play_date) DO UPDATE SET
                play_count = play_count + 1
            """,
            [user_id, game_name, today],
        ),
        conn.commit(),
    ))
    row = conn.execute(
        "SELECT play_count FROM game_daily_counts WHERE user_id = ? AND game_name = ? AND play_date = ?",
        [user_id, game_name, today],
    ).fetchone()
    return int(row[0]) if row else 1


# ---------------------------------------------------------------------------
# Gemini mini-games (navi_challenge, type_race) — 2 attempts per user per day
# ---------------------------------------------------------------------------

_GEMINI_GAME_DAILY_LIMIT = 2


def get_gemini_game_daily_count(conn: sqlite3.Connection, user_id: int, game_name: str) -> int:
    """Return today's attempt count for navi_challenge or type_race."""
    table = "navi_challenge_attempts" if game_name == "navi_challenge" else "type_race_attempts"
    today = _today_ist()
    row = conn.execute(
        f"SELECT play_count FROM {table} WHERE user_id = ? AND play_date = ?",
        [user_id, today],
    ).fetchone()
    return int(row[0]) if row else 0


def increment_gemini_game_count(conn: sqlite3.Connection, user_id: int, game_name: str) -> int:
    """Increment attempt count. Returns new count."""
    table = "navi_challenge_attempts" if game_name == "navi_challenge" else "type_race_attempts"
    today = _today_ist()
    _db_write(lambda: (
        conn.execute(
            f"INSERT INTO {table} (user_id, play_date, play_count) VALUES (?, ?, 1) "
            f"ON CONFLICT (user_id, play_date) DO UPDATE SET play_count = play_count + 1",
            [user_id, today],
        ),
        conn.commit(),
    ))
    row = conn.execute(
        f"SELECT play_count FROM {table} WHERE user_id = ? AND play_date = ?",
        [user_id, today],
    ).fetchone()
    return int(row[0]) if row else 1


def get_score_comment(conn: sqlite3.Connection, game_type: str, score: int) -> str | None:
    """Return a cached funny comment for the given score bucket, or None if uncached."""
    bucket = min(score // 10, 9)
    rows = conn.execute(
        "SELECT comment FROM gemini_score_comments WHERE game_type = ? AND score_bucket = ?",
        [game_type, bucket],
    ).fetchall()
    return random.choice(rows)[0] if rows else None


def save_score_comment(conn: sqlite3.Connection, game_type: str, score: int, comment: str) -> None:
    """Cache a funny Gemini-generated score comment for future reuse."""
    bucket = min(score // 10, 9)
    _db_write(lambda: (
        conn.execute(
            "INSERT INTO gemini_score_comments (game_type, score_bucket, comment) VALUES (?, ?, ?)",
            [game_type, bucket, comment],
        ),
        conn.commit(),
    ))


def get_random_knowledge_terms(conn: sqlite3.Connection, num_categories: int = 3) -> list[dict]:
    """Return one random term per randomly-selected category for Navi Challenge."""
    cats = conn.execute("SELECT DISTINCT category FROM local_knowledge").fetchall()
    cat_list = [r[0] for r in cats]
    if not cat_list:
        return []
    selected_cats = random.sample(cat_list, min(num_categories, len(cat_list)))
    results = []
    for cat in selected_cats:
        rows = conn.execute(
            "SELECT term, description FROM local_knowledge WHERE category = ? ORDER BY RANDOM() LIMIT 1",
            [cat],
        ).fetchall()
        if rows:
            results.append({"category": cat, "term": rows[0][0], "description": rows[0][1]})
    return results


def reset_gemini_game_count(conn: sqlite3.Connection, user_id: int, game_name: str) -> None:
    """Delete today's attempt record for a user in navi_challenge or type_race."""
    table = "navi_challenge_attempts" if game_name == "navi_challenge" else "type_race_attempts"
    today = _today_ist()
    _db_write(lambda: (
        conn.execute(f"DELETE FROM {table} WHERE user_id = ? AND play_date = ?", [user_id, today]),
        conn.commit(),
    ))


def reset_gambling_count(conn: sqlite3.Connection, user_id: int) -> None:
    """Delete all of today's gambling play counts for a user."""
    today = _today_ist()
    _db_write(lambda: (
        conn.execute(
            "DELETE FROM game_daily_counts WHERE user_id = ? AND play_date = ?",
            [user_id, today],
        ),
        conn.commit(),
    ))


# ---------------------------------------------------------------------------
# Lucky draw: users active previous day (7am IST prev → 6:59am IST today)
# ---------------------------------------------------------------------------

def get_active_user_ids_previous_day(conn: sqlite3.Connection) -> list[int]:
    """Return user_ids active during the previous IST day window (7am–6:59am).

    'Active' means last_seen fell within that window.
    Used for the daily lucky draw at 7am IST.
    """
    # Previous day window in UTC:
    # 7:00am IST = 1:30am UTC, so window = yesterday 01:30:00 UTC → today 01:29:59 UTC
    rows = conn.execute(
        """
        SELECT user_id FROM user_stats
        WHERE last_seen >= datetime('now', '-1 day', 'start of day', '+1 hours', '+30 minutes')
          AND last_seen <  datetime('now', 'start of day', '+1 hours', '+30 minutes')
        """,
    ).fetchall()
    return [r[0] for r in rows]


def get_top_n_from_user_ids(
    conn: sqlite3.Connection,
    user_ids: list[int],
    n: int = 10,
) -> list[dict]:
    """Return up to n users sorted by boli_points DESC, filtered to the given user_id list.

    Returns list of dicts with user_id, username, boli_points.
    """
    if not user_ids:
        return []
    placeholders = ",".join("?" * len(user_ids))
    rows = conn.execute(
        f"""
        SELECT user_id, username, boli_points FROM user_stats
        WHERE user_id IN ({placeholders})
        ORDER BY boli_points DESC
        LIMIT ?
        """,
        [*user_ids, n],
    ).fetchall()
    return [{"user_id": r[0], "username": r[1], "boli_points": r[2]} for r in rows]


# ---------------------------------------------------------------------------
# Leveling system
# ---------------------------------------------------------------------------

def points_for_level(level: int) -> int:
    """Total Boli Points required to reach *level* from zero.

    Formula: 5 * n * (n + 3)
    Per-level cost grows by 10 each step:
      Level 1  →  20 pts total
      Level 10 →  650 pts total
      Level 50 →  13,250 pts total
      Level 100 → 51,500 pts total
    """
    if level <= 0:
        return 0
    n = min(level, 100)
    return 5 * n * (n + 3)


# ---------------------------------------------------------------------------
# User Perks (Boli Marketplace)
# ---------------------------------------------------------------------------

def has_active_perk(
    conn: sqlite3.Connection,
    user_id: int,
    perk_type: str,
) -> bool:
    """Return True if the user has a non-expired perk of the given type."""
    row = conn.execute(
        "SELECT 1 FROM user_perks WHERE user_id = ? AND perk_type = ? AND expires_at > CURRENT_TIMESTAMP",
        [user_id, perk_type],
    ).fetchone()
    return row is not None


def grant_perk(
    conn: sqlite3.Connection,
    user_id: int,
    perk_type: str,
    duration_hours: float = 24,
) -> None:
    """Grant a timed perk to a user, extending if they already have one.

    duration_hours may be fractional (e.g. 0.5 for 30 minutes).
    """
    duration_seconds = int(duration_hours * 3600)

    def _write() -> None:
        conn.execute(
            """
            INSERT INTO user_perks (user_id, perk_type, expires_at)
            VALUES (?, ?, datetime('now', '+' || ? || ' seconds'))
            ON CONFLICT (user_id, perk_type) DO UPDATE SET
                expires_at = CASE
                    WHEN user_perks.expires_at > excluded.expires_at
                    THEN user_perks.expires_at
                    ELSE excluded.expires_at
                END
            """,
            [user_id, perk_type, duration_seconds],
        )
        conn.commit()
    _db_write(_write)


def get_perk_expiry(
    conn: sqlite3.Connection,
    user_id: int,
    perk_type: str,
) -> datetime | None:
    """Return the expiry datetime of a perk, or None if not active."""
    row = conn.execute(
        "SELECT expires_at FROM user_perks WHERE user_id = ? AND perk_type = ? AND expires_at > CURRENT_TIMESTAMP",
        [user_id, perk_type],
    ).fetchone()
    if not row:
        return None
    val = row[0]
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            return None
    return None


def clear_perk(
    conn: sqlite3.Connection,
    user_id: int,
    perk_type: str,
) -> bool:
    """Delete a perk record (active or expired) for a user. Returns True if a row was removed."""
    def _write() -> int:
        cur = conn.execute(
            "DELETE FROM user_perks WHERE user_id = ? AND perk_type = ?",
            [user_id, perk_type],
        )
        conn.commit()
        return cur.rowcount
    return _db_write(_write) > 0


def get_level_from_points(points: int) -> int:
    """Compute level (0–100) from lifetime XP (experience column).

    Solves 5n(n+3) ≤ points for the largest integer n.
    """
    if points <= 0:
        return 0
    # 5n² + 15n - points = 0  →  n = (-15 + sqrt(225 + 20·points)) / 10
    n = int((-15 + math.sqrt(225 + 20 * points)) / 10)
    return min(max(n, 0), 100)


# ---------------------------------------------------------------------------
# Local Media (user-stolen emojis/stickers stored on disk)
# ---------------------------------------------------------------------------

def save_local_media(
    conn: sqlite3.Connection,
    user_id: int,
    shortcut: str,
    file_path: str,
    media_type: str,
    source_url: str | None = None,
    storage_type: str = "local",
    discord_id: str | None = None,
    discord_name: str | None = None,
    animated: bool = False,
) -> None:
    """Insert a local media entry, replacing any existing entry for the same shortcut."""
    def _write() -> None:
        conn.execute(
            """
            INSERT INTO local_media
                (shortcut, user_id, file_path, media_type, source_url, created_at,
                 storage_type, discord_id, discord_name, animated)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?)
            ON CONFLICT (shortcut) DO UPDATE SET
                user_id = excluded.user_id,
                file_path = excluded.file_path,
                media_type = excluded.media_type,
                source_url = excluded.source_url,
                created_at = CURRENT_TIMESTAMP,
                storage_type = excluded.storage_type,
                discord_id = excluded.discord_id,
                discord_name = excluded.discord_name,
                animated = excluded.animated
            """,
            [shortcut, user_id, file_path, media_type, source_url,
             storage_type, discord_id, discord_name, int(animated)],
        )
        conn.commit()
    _db_write(_write)


def get_local_media(
    conn: sqlite3.Connection,
    shortcut: str,
) -> dict | None:
    """Return local media entry for the given shortcut, or None if not found."""
    row = conn.execute(
        """SELECT shortcut, user_id, file_path, media_type, source_url, created_at,
                  storage_type, discord_id, discord_name, animated
           FROM local_media WHERE shortcut = ?""",
        [shortcut],
    ).fetchone()
    if not row:
        return None
    return {
        "shortcut": row[0],
        "user_id": row[1],
        "file_path": row[2],
        "media_type": row[3],
        "source_url": row[4],
        "created_at": row[5],
        "storage_type": row[6] or "local",
        "discord_id": row[7],
        "discord_name": row[8],
        "animated": bool(row[9]) if row[9] is not None else False,
    }


def get_user_local_media_count(conn: sqlite3.Connection, user_id: int) -> int:
    """Return how many local media entries the user has saved."""
    row = conn.execute(
        "SELECT COUNT(*) FROM local_media WHERE user_id = ?", [user_id]
    ).fetchone()
    return row[0] if row else 0


def get_global_local_media_count(conn: sqlite3.Connection) -> int:
    """Return total number of local media entries stored globally."""
    row = conn.execute("SELECT COUNT(*) FROM local_media").fetchone()
    return row[0] if row else 0


def list_user_local_media(
    conn: sqlite3.Connection,
    user_id: int,
    media_type: str | None = None,
) -> list[dict]:
    """Return all local media entries owned by a user, newest first.

    Includes storage_type, discord_id, discord_name, and animated so callers
    can build emoji previews without a second per-row DB fetch.
    """
    if media_type:
        rows = conn.execute(
            """SELECT shortcut, file_path, media_type, created_at,
                      storage_type, discord_id, discord_name, animated
               FROM local_media
               WHERE user_id = ? AND media_type = ?
               ORDER BY created_at DESC""",
            [user_id, media_type],
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT shortcut, file_path, media_type, created_at,
                      storage_type, discord_id, discord_name, animated
               FROM local_media
               WHERE user_id = ?
               ORDER BY created_at DESC""",
            [user_id],
        ).fetchall()
    return [
        {
            "shortcut":     r[0],
            "file_path":    r[1],
            "media_type":   r[2],
            "created_at":   r[3],
            "storage_type": r[4] or "local",
            "discord_id":   r[5],
            "discord_name": r[6],
            "animated":     bool(r[7]) if r[7] is not None else False,
        }
        for r in rows
    ]


def delete_local_media(conn: sqlite3.Connection, shortcut: str) -> bool:
    """Delete a local media entry by shortcut. Returns True if a row was deleted."""
    existing = conn.execute(
        "SELECT 1 FROM local_media WHERE shortcut = ?", [shortcut]
    ).fetchone()
    if not existing:
        return False
    _db_write(lambda: (
        conn.execute("DELETE FROM local_media WHERE shortcut = ?", [shortcut]),
        conn.commit(),
    ))
    return True


# ---------------------------------------------------------------------------
# Bless logs (parallel to curse_logs, used by /leaderboard)
# ---------------------------------------------------------------------------

def log_bless(
    conn: sqlite3.Connection,
    user_id: int,
    username: str,
    bless_used: str,
) -> None:
    """Log a successful bless event (recipient's perspective)."""
    def _write() -> None:
        conn.execute(
            """
            INSERT INTO bless_logs (id, user_id, username, bless_used)
            VALUES (NULL, ?, ?, ?)
            """,
            [user_id, username, bless_used],
        )
        conn.commit()
    _db_write(_write)


# ---------------------------------------------------------------------------
# Fun leaderboards (/leaderboard command)
# ---------------------------------------------------------------------------

def get_curse_leaderboard(
    conn: sqlite3.Connection,
    limit: int = 5,
) -> list[dict]:
    """Return the most-cursed users (excluding backfires from the count)."""
    rows = conn.execute(
        """
        SELECT user_id, username, COUNT(*) AS hits
        FROM curse_logs
        WHERE curse_used NOT LIKE 'backfire_%' AND curse_used NOT LIKE 'proxy_%'
        GROUP BY user_id, username
        ORDER BY hits DESC
        LIMIT ?
        """,
        [limit],
    ).fetchall()
    return [{"user_id": r[0], "username": r[1], "hits": r[2]} for r in rows]


def get_bless_leaderboard(
    conn: sqlite3.Connection,
    limit: int = 5,
) -> list[dict]:
    """Return the most-blessed users."""
    rows = conn.execute(
        """
        SELECT user_id, username, COUNT(*) AS hits
        FROM bless_logs
        GROUP BY user_id, username
        ORDER BY hits DESC
        LIMIT ?
        """,
        [limit],
    ).fetchall()
    return [{"user_id": r[0], "username": r[1], "hits": r[2]} for r in rows]


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def log_command_event(
    conn: sqlite3.Connection,
    user_id: int,
    username: str,
    command: str,
    subcommand: str | None = None,
    channel_id: int | None = None,
    guild_id: int | None = None,
    latency_ms: int | None = None,
    used_cache: int = 0,
) -> None:
    """Record a single command invocation for usage analytics."""
    def _write() -> None:
        conn.execute(
            """
            INSERT INTO command_events
                (id, user_id, username, command, subcommand, channel_id, guild_id, latency_ms, used_cache)
            VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [user_id, username, command, subcommand, channel_id, guild_id, latency_ms, used_cache],
        )
        conn.commit()
    _db_write(_write)


def log_session_event(
    conn: sqlite3.Connection,
    event_type: str,
    detail: str | None = None,
) -> None:
    """Record a bot lifecycle event (start, stop, ready, error)."""
    def _write() -> None:
        conn.execute(
            "INSERT INTO session_events (id, event_type, detail) VALUES (NULL, ?, ?)",
            [event_type, detail],
        )
        conn.commit()
    _db_write(_write)


# ---------------------------------------------------------------------------
# Application Emoji management (LRU cache for uploaded images)
# ---------------------------------------------------------------------------

_APP_EMOJI_LIMIT = 2000
_APP_EMOJI_EVICT_THRESHOLD = 1900


def save_app_emoji(
    conn: sqlite3.Connection,
    emoji_id: str,
    name: str,
    animated: bool = False,
    original_id: str | None = None,
) -> None:
    """Record a newly uploaded application emoji in the DB."""
    def _write() -> None:
        conn.execute(
            """
            INSERT INTO app_emojis (emoji_id, name, last_used, original_id, animated)
            VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
            ON CONFLICT (emoji_id) DO UPDATE SET
                name = excluded.name,
                last_used = CURRENT_TIMESTAMP,
                animated = excluded.animated
            """,
            [emoji_id, name, original_id, int(animated)],
        )
        conn.commit()
    _db_write(_write)


def get_oldest_app_emoji(conn: sqlite3.Connection) -> dict | None:
    """Return the least-recently-used application emoji record, or None if none exist."""
    row = conn.execute(
        "SELECT emoji_id, name, animated FROM app_emojis ORDER BY last_used ASC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return {"emoji_id": row[0], "name": row[1], "animated": bool(row[2])}


def delete_app_emoji_record(conn: sqlite3.Connection, emoji_id: str) -> None:
    """Remove an application emoji record from the DB."""
    _db_write(lambda: (
        conn.execute("DELETE FROM app_emojis WHERE emoji_id = ?", [emoji_id]),
        conn.commit(),
    ))


def update_app_emoji_last_used(conn: sqlite3.Connection, emoji_id: str) -> None:
    """Bump the last_used timestamp for an application emoji (LRU refresh)."""
    _db_write(lambda: (
        conn.execute(
            "UPDATE app_emojis SET last_used = CURRENT_TIMESTAMP WHERE emoji_id = ?",
            [emoji_id],
        ),
        conn.commit(),
    ))


def count_app_emojis(conn: sqlite3.Connection) -> int:
    """Return the number of application emojis tracked in the DB."""
    row = conn.execute("SELECT COUNT(*) FROM app_emojis").fetchone()
    return row[0] if row else 0


def log_api_call(
    conn: sqlite3.Connection,
    key_used: str,
    cache_hit: bool = False,
    command: str | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
) -> None:
    """Record a Gemini API call for cost and cache visibility."""
    def _write() -> None:
        conn.execute(
            """
            INSERT INTO api_call_log (id, key_used, cache_hit, command, latency_ms, error)
            VALUES (NULL, ?, ?, ?, ?, ?)
            """,
            [key_used, int(cache_hit), command, latency_ms, error],
        )
        conn.commit()
    _db_write(_write)


# ---------------------------------------------------------------------------
# Gift log (daily cap tracking)
# ---------------------------------------------------------------------------

def get_gift_daily_total(conn: sqlite3.Connection, sender_id: int) -> int:
    """Return total Boli gifted by sender_id today (UTC date)."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount), 0) FROM gift_log
        WHERE sender_id = ? AND DATE(gifted_at) = DATE('now')
        """,
        [sender_id],
    ).fetchone()
    return int(row[0]) if row else 0


def record_gift(
    conn: sqlite3.Connection,
    sender_id: int,
    recipient_id: int,
    amount: int,
) -> None:
    """Record a gift transaction for daily cap tracking."""
    def _write() -> None:
        conn.execute(
            "INSERT INTO gift_log (sender_id, recipient_id, amount) VALUES (?, ?, ?)",
            [sender_id, recipient_id, amount],
        )
        conn.commit()
    _db_write(_write)


def get_random_recent_user(
    conn: sqlite3.Connection,
    exclude_id: int,
) -> dict | None:
    """Return a random user who used /navi in the last 24 hours, excluding exclude_id.

    Returns dict with keys user_id and username, or None if no candidates.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT u.user_id, u.username
        FROM user_stats u
        INNER JOIN user_prediction_history h ON h.user_id = u.user_id
        WHERE u.user_id != ?
          AND h.timestamp >= datetime('now', '-1 day')
        """,
        [exclude_id],
    ).fetchall()
    if not rows:
        return None
    row = random.choice(rows)
    return {"user_id": row[0], "username": row[1]}


# ---------------------------------------------------------------------------
# Partner score log (deduplication + audit)
# ---------------------------------------------------------------------------

def partner_score_exists(conn: sqlite3.Connection, game_id: str) -> bool:
    """Return True if this game_id has already been processed."""
    row = conn.execute(
        "SELECT 1 FROM partner_score_log WHERE game_id = ?", [game_id]
    ).fetchone()
    return row is not None


def log_partner_score(
    conn: sqlite3.Connection,
    game_id: str,
    user_id: int,
    guild_id: int,
    username: str,
    raw_points: int,
    boli_awarded: int,
    xp_awarded: int,
    game_type: str = "default",
) -> None:
    """Persist an inbound partner-bot score submission."""
    def _write() -> None:
        conn.execute(
            """
            INSERT INTO partner_score_log
                (game_id, user_id, guild_id, username, raw_points, boli_awarded, xp_awarded, game_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [game_id, user_id, guild_id, username, raw_points, boli_awarded, xp_awarded, game_type],
        )
        conn.commit()
    _db_write(_write)


def get_recent_partner_logs(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Return the most recent partner score submissions."""
    rows = conn.execute(
        """
        SELECT game_id, username, game_type, raw_points, boli_awarded, received_at
        FROM partner_score_log ORDER BY received_at DESC LIMIT ?
        """,
        [limit],
    ).fetchall()
    return [
        {
            "game_id": r[0], "username": r[1], "game_type": r[2],
            "raw_points": r[3], "boli_awarded": r[4], "received_at": r[5],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Stock Market
# ---------------------------------------------------------------------------

_MARKET_SEED_ITEMS: list[dict] = [
    {
        "item_id": "monsoon_insurance",
        "name": "Monsoon Insurance",
        "emoji": "🌧️",
        "base_price": 80,
        "current_price": 80,
        "volatility": "high",
        "description": "Price rises when it rains in Trivandrum. Climate anxiety, monetized.",
    },
    {
        "item_id": "it_layoff_hedge",
        "name": "IT Layoff Hedge",
        "emoji": "📱",
        "base_price": 60,
        "current_price": 60,
        "volatility": "medium",
        "description": "Spikes every Friday. Technopark vibes only.",
    },
    {
        "item_id": "banana_republic",
        "name": "Banana Republic Stock",
        "emoji": "🍌",
        "base_price": 40,
        "current_price": 40,
        "volatility": "low",
        "description": "Slow decay with random noise. A classic.",
    },
    {
        "item_id": "cricket_mood_index",
        "name": "Cricket Mood Index",
        "emoji": "🏏",
        "base_price": 70,
        "current_price": 70,
        "volatility": "high",
        "description": "Extremely volatile during IPL season.",
    },
    {
        "item_id": "chaya_kada_futures",
        "name": "Chaya Kada Futures",
        "emoji": "☕",
        "base_price": 30,
        "current_price": 30,
        "volatility": "very_low",
        "description": "The safest investment in Kerala. Tiny but steady gains.",
    },
    {
        "item_id": "coconut_oil_commodity",
        "name": "Coconut Oil Commodity",
        "emoji": "🌴",
        "base_price": 50,
        "current_price": 50,
        "volatility": "low",
        "description": "Follows a slow sine wave. Buy low, sell after the rains.",
    },
]


def _seed_market_items(conn: sqlite3.Connection) -> None:
    """Insert default market items if the table is empty."""
    count = conn.execute("SELECT COUNT(*) FROM market_items").fetchone()[0]
    if count > 0:
        return
    for item in _MARKET_SEED_ITEMS:
        conn.execute(
            """
            INSERT OR IGNORE INTO market_items
                (item_id, name, emoji, base_price, current_price, volatility, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [item["item_id"], item["name"], item["emoji"],
             item["base_price"], item["current_price"],
             item["volatility"], item["description"]],
        )


def get_market_items(conn: sqlite3.Connection) -> list[dict]:
    """Return all market items with current prices."""
    rows = conn.execute(
        "SELECT item_id, name, emoji, base_price, current_price, volatility, description "
        "FROM market_items ORDER BY item_id"
    ).fetchall()
    return [
        {
            "item_id": r[0], "name": r[1], "emoji": r[2],
            "base_price": r[3], "current_price": r[4],
            "volatility": r[5], "description": r[6],
        }
        for r in rows
    ]


def get_market_item(conn: sqlite3.Connection, item_id: str) -> dict | None:
    """Return a single market item by item_id, or None."""
    row = conn.execute(
        "SELECT item_id, name, emoji, base_price, current_price, volatility, description "
        "FROM market_items WHERE item_id = ?",
        [item_id],
    ).fetchone()
    if not row:
        return None
    return {
        "item_id": row[0], "name": row[1], "emoji": row[2],
        "base_price": row[3], "current_price": row[4],
        "volatility": row[5], "description": row[6],
    }


def update_market_price(
    conn: sqlite3.Connection,
    item_id: str,
    new_price: int,
) -> None:
    """Set a market item's current price and update last_updated."""
    _db_write(lambda: (
        conn.execute(
            "UPDATE market_items SET current_price = ?, last_updated = CURRENT_TIMESTAMP WHERE item_id = ?",
            [new_price, item_id],
        ),
        conn.commit(),
    ))


def record_price_history(
    conn: sqlite3.Connection,
    item_id: str,
    price: int,
    date_str: str,
) -> None:
    """Persist a daily price point for history display."""
    def _write() -> None:
        conn.execute(
            "INSERT INTO market_price_history (item_id, price, recorded_at) VALUES (?, ?, ?)",
            [item_id, price, date_str],
        )
        conn.commit()
    _db_write(_write)


def get_price_history(
    conn: sqlite3.Connection,
    item_id: str,
    days: int = 7,
) -> list[dict]:
    """Return the last N days of price history for an item."""
    rows = conn.execute(
        """
        SELECT recorded_at, price FROM market_price_history
        WHERE item_id = ?
        ORDER BY recorded_at DESC LIMIT ?
        """,
        [item_id, days],
    ).fetchall()
    return [{"date": r[0], "price": r[1]} for r in reversed(rows)]


def buy_holding(
    conn: sqlite3.Connection,
    user_id: int,
    item_id: str,
    quantity: int,
    purchase_price: int,
    expires_at: datetime,
) -> None:
    """Add a new holding row for a user purchase."""
    # Store as UTC without timezone offset so SQLite CURRENT_TIMESTAMP comparisons work correctly
    expires_str = expires_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    def _write() -> None:
        conn.execute(
            """
            INSERT INTO user_holdings (user_id, item_id, quantity, purchase_price, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [user_id, item_id, quantity, purchase_price, expires_str],
        )
        conn.commit()
    _db_write(_write)


def get_user_holdings(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Return all non-expired holdings for a user, joined with market item info."""
    rows = conn.execute(
        """
        SELECT h.id, h.item_id, m.name, m.emoji, m.current_price,
               h.quantity, h.purchase_price, h.purchased_at, h.expires_at
        FROM user_holdings h
        JOIN market_items m ON m.item_id = h.item_id
        WHERE h.user_id = ? AND h.expires_at > CURRENT_TIMESTAMP
        ORDER BY h.purchased_at
        """,
        [user_id],
    ).fetchall()
    return [
        {
            "id": r[0], "item_id": r[1], "name": r[2], "emoji": r[3],
            "current_price": r[4], "quantity": r[5], "purchase_price": r[6],
            "purchased_at": r[7], "expires_at": r[8],
        }
        for r in rows
    ]


def get_user_holding_for_item(
    conn: sqlite3.Connection,
    user_id: int,
    item_id: str,
) -> list[dict]:
    """Return non-expired holdings for a user for a specific item (FIFO order)."""
    rows = conn.execute(
        """
        SELECT id, quantity, purchase_price, purchased_at, expires_at
        FROM user_holdings
        WHERE user_id = ? AND item_id = ? AND expires_at > CURRENT_TIMESTAMP
        ORDER BY purchased_at ASC
        """,
        [user_id, item_id],
    ).fetchall()
    return [
        {
            "id": r[0], "quantity": r[1], "purchase_price": r[2],
            "purchased_at": r[3], "expires_at": r[4],
        }
        for r in rows
    ]


def reduce_holding(
    conn: sqlite3.Connection,
    holding_id: int,
    qty_to_remove: int,
) -> None:
    """Reduce quantity of a holding row, deleting it if it reaches 0."""
    def _write() -> None:
        conn.execute(
            "UPDATE user_holdings SET quantity = quantity - ? WHERE id = ?",
            [qty_to_remove, holding_id],
        )
        conn.execute("DELETE FROM user_holdings WHERE id = ? AND quantity <= 0", [holding_id])
        conn.commit()
    _db_write(_write)


def expire_stale_holdings(
    conn: sqlite3.Connection,
) -> list[dict]:
    """Delete all expired holding rows and return them (for notification purposes)."""
    rows = conn.execute(
        """
        SELECT h.user_id, m.name, m.emoji, h.quantity, h.purchase_price
        FROM user_holdings h
        JOIN market_items m ON m.item_id = h.item_id
        WHERE h.expires_at <= CURRENT_TIMESTAMP
        """
    ).fetchall()
    expired = [
        {"user_id": r[0], "name": r[1], "emoji": r[2], "quantity": r[3], "purchase_price": r[4]}
        for r in rows
    ]
    if expired:
        _db_write(lambda: (
            conn.execute("DELETE FROM user_holdings WHERE expires_at <= CURRENT_TIMESTAMP"),
            conn.commit(),
        ))
    return expired
