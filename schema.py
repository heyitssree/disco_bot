# schema.py - DuckDB schema management for Navi (disco_bot)
# Creates and manages all tables. Call init_db() at bot startup.

from __future__ import annotations

import csv
import logging
import math
import time
import functools
from datetime import date, datetime
from pathlib import Path
from typing import Callable, TypeVar

import duckdb
import os

logger = logging.getLogger("navi.schema")

_T = TypeVar("_T")

# ---------------------------------------------------------------------------
# DB write retry with exponential backoff (handles "database is locked")
# ---------------------------------------------------------------------------

_DB_RETRY_ATTEMPTS = 5
_DB_RETRY_BASE_DELAY = 0.1  # seconds


def _db_write(fn: Callable[[], _T]) -> _T:
    """Execute a DuckDB write callable with exponential backoff on lock errors.

    Retries up to _DB_RETRY_ATTEMPTS times (0.1s, 0.2s, 0.4s, 0.8s, 1.6s).
    Checkpoints after every successful write so data is always in the main .db
    file and never only in the WAL — prevents data loss on unclean shutdown.
    Re-raises the last exception if all attempts fail.
    """
    last_exc: Exception | None = None
    for attempt in range(_DB_RETRY_ATTEMPTS):
        try:
            result = fn()
            try:
                # Force WAL → main DB flush after every write
                _conn_ref and _conn_ref.checkpoint()
            except Exception:
                pass
            return result
        except Exception as exc:
            msg = str(exc).lower()
            if "database is locked" not in msg and "conflicting lock" not in msg:
                raise
            last_exc = exc
            delay = _DB_RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "DuckDB locked (attempt %d/%d) — retrying in %.2fs", attempt + 1, _DB_RETRY_ATTEMPTS, delay
            )
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]


_conn_ref: "duckdb.DuckDBPyConnection | None" = None


_db_path_env = os.getenv("DB_PATH")
DB_PATH = Path(_db_path_env) if _db_path_env else Path("data") / "astro_bot.db"

# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def init_db() -> duckdb.DuckDBPyConnection:
    """Create the DuckDB database and all tables if they don't exist.

    Returns an open connection that should be reused for the bot's lifetime.
    """
    global _conn_ref
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    _conn_ref = conn
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            key VARCHAR PRIMARY KEY,
            value_str VARCHAR,
            value_float DOUBLE,
            value_int INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_perks (
            user_id     BIGINT NOT NULL,
            perk_type   VARCHAR NOT NULL,
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
        CREATE SEQUENCE IF NOT EXISTS local_knowledge_seq START 1
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

    # Emoji for link-summary reaction (Feature 3)
    conn.execute("""
        INSERT INTO bot_config (key, value_str)
        SELECT 'link_summary_emoji', '📰'
        WHERE NOT EXISTS (SELECT 1 FROM bot_config WHERE key = 'link_summary_emoji');
    """)

    # Safe ALTER TABLE migrations — add new columns to existing tables if absent
    conn.execute("ALTER TABLE user_stats ADD COLUMN IF NOT EXISTS strikes INTEGER DEFAULT 0")

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
            VALUES (nextval('predictions_cache_seq'), ?, ?, ?, ?)
            """,
            [cache_type, user_id, original_prompt, template_text],
        )
        conn.commit()
    _db_write(_write)


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

    def _write() -> None:
        if existing is None:
            conn.execute(
                """
                INSERT INTO user_stats (user_id, username, rashi, boli_points, last_seen, prediction_count)
                VALUES (?, ?, ?, 0, current_timestamp, 0)
                """,
                [user_id, username, rashi],
            )
        elif rashi:
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
    _db_write(_write)


def update_boli_points(
    conn: duckdb.DuckDBPyConnection,
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


def increment_prediction_count(
    conn: duckdb.DuckDBPyConnection,
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
    def _write() -> None:
        conn.execute(
            """
            INSERT INTO user_prediction_history (id, user_id, prediction_text)
            VALUES (nextval('prediction_history_seq'), ?, ?)
            """,
            [user_id, prediction_text],
        )
        conn.commit()
    _db_write(_write)


def get_todays_user_prediction(
    conn: duckdb.DuckDBPyConnection,
    user_id: int,
) -> str | None:
    """Check if the user already received a prediction today, return it if so."""
    row = conn.execute(
        """
        SELECT prediction_text FROM user_prediction_history
        WHERE user_id = ? AND CAST(timestamp AS DATE) = current_date
        ORDER BY timestamp DESC LIMIT 1
        """,
        [user_id]
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Curse logs
# ---------------------------------------------------------------------------

def log_curse(
    conn: duckdb.DuckDBPyConnection,
    user_id: int,
    username: str,
    curse_used: str,
) -> None:
    def _write() -> None:
        conn.execute(
            """
            INSERT INTO curse_logs (id, user_id, username, curse_used)
            VALUES (nextval('curse_logs_seq'), ?, ?, ?)
            """,
            [user_id, username, curse_used],
        )
        conn.commit()
    _db_write(_write)


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
                VALUES (nextval('daily_omens_seq'), ?, ?, ?)
                """,
                [text, landmark, today],
            )
        conn.commit()
    _db_write(_write)


# ---------------------------------------------------------------------------
# Bot Config
# ---------------------------------------------------------------------------

def get_config_float(conn: duckdb.DuckDBPyConnection, key: str, default: float) -> float:
    row = conn.execute("SELECT value_float FROM bot_config WHERE key = ?", [key]).fetchone()
    return float(row[0]) if row and row[0] is not None else default

def get_config_int(conn: duckdb.DuckDBPyConnection, key: str, default: int) -> int:
    row = conn.execute("SELECT value_int FROM bot_config WHERE key = ?", [key]).fetchone()
    return int(row[0]) if row and row[0] is not None else default

def set_config_float(conn: duckdb.DuckDBPyConnection, key: str, value: float) -> None:
    _db_write(lambda: (
        conn.execute("UPDATE bot_config SET value_float = ? WHERE key = ?", [value, key]),
        conn.commit(),
    ))

def set_config_int(conn: duckdb.DuckDBPyConnection, key: str, value: int) -> None:
    _db_write(lambda: (
        conn.execute("UPDATE bot_config SET value_int = ? WHERE key = ?", [value, key]),
        conn.commit(),
    ))

def get_config_str(conn: duckdb.DuckDBPyConnection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value_str FROM bot_config WHERE key = ?", [key]).fetchone()
    return str(row[0]) if row and row[0] is not None else default

def set_config_str(conn: duckdb.DuckDBPyConnection, key: str, value: str) -> None:
    _db_write(lambda: (
        conn.execute(
            "INSERT INTO bot_config (key, value_str) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value_str = excluded.value_str",
            [key, value],
        ),
        conn.commit(),
    ))

def get_all_configs(conn: duckdb.DuckDBPyConnection) -> dict:
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

def get_user_strikes(conn: duckdb.DuckDBPyConnection, user_id: int) -> int:
    """Return the current strike count for a user (0 if not found)."""
    row = conn.execute(
        "SELECT strikes FROM user_stats WHERE user_id = ?", [user_id]
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def increment_user_strikes(conn: duckdb.DuckDBPyConnection, user_id: int) -> int:
    """Increment strikes by 1 and return the new count."""
    _db_write(lambda: (
        conn.execute(
            "UPDATE user_stats SET strikes = strikes + 1 WHERE user_id = ?", [user_id]
        ),
        conn.commit(),
    ))
    return get_user_strikes(conn, user_id)


def reset_user_strikes(conn: duckdb.DuckDBPyConnection, user_id: int) -> None:
    """Reset a user's strikes to 0."""
    _db_write(lambda: (
        conn.execute("UPDATE user_stats SET strikes = 0 WHERE user_id = ?", [user_id]),
        conn.commit(),
    ))


def set_user_points(conn: duckdb.DuckDBPyConnection, user_id: int, username: str, points: int, prediction_count: int = 0, rashi: str | None = None) -> None:
    """Upsert a user's Boli Points directly — used for manual data restore."""
    existing = get_user_profile(conn, user_id)
    def _write() -> None:
        if existing is None:
            conn.execute(
                "INSERT INTO user_stats (user_id, username, rashi, boli_points, last_seen, prediction_count) VALUES (?, ?, ?, ?, current_timestamp, ?)",
                [user_id, username, rashi, points, prediction_count],
            )
        else:
            conn.execute(
                "UPDATE user_stats SET boli_points=?, prediction_count=?, username=?, rashi=COALESCE(?, rashi) WHERE user_id=?",
                [points, prediction_count, username, rashi, user_id],
            )
        conn.commit()
    _db_write(_write)


def reset_all_strikes(conn: duckdb.DuckDBPyConnection) -> int:
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


def seed_local_knowledge(conn: duckdb.DuckDBPyConnection, force: bool = False) -> None:
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
            VALUES (nextval('local_knowledge_seq'), ?, ?, ?, ?)
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


def get_health_stats(conn: duckdb.DuckDBPyConnection) -> dict:
    """Return a rich set of runtime statistics for the /health command."""
    stats: dict = {}

    # --- User activity ---
    stats["total_users"] = (
        conn.execute("SELECT COUNT(*) FROM user_stats").fetchone() or (0,)
    )[0]
    stats["active_today"] = (
        conn.execute(
            "SELECT COUNT(*) FROM user_stats WHERE CAST(last_seen AS DATE) = current_date"
        ).fetchone() or (0,)
    )[0]
    stats["active_week"] = (
        conn.execute(
            "SELECT COUNT(*) FROM user_stats WHERE last_seen >= current_timestamp - INTERVAL 7 DAY"
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
            "SELECT COUNT(*) FROM user_prediction_history WHERE CAST(timestamp AS DATE) = current_date"
        ).fetchone() or (0,)
    )[0]
    cache_rows = conn.execute(
        "SELECT cache_type, COUNT(*) FROM predictions_cache GROUP BY cache_type ORDER BY cache_type"
    ).fetchall()
    stats["cache_by_type"] = {r[0]: r[1] for r in cache_rows}

    # --- Curse log ---
    stats["curses_today"] = (
        conn.execute(
            "SELECT COUNT(*) FROM curse_logs WHERE CAST(timestamp AS DATE) = current_date"
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
            "SELECT COUNT(*) FROM user_perks WHERE expires_at > current_timestamp"
        ).fetchone() or (0,)
    )[0]

    return stats


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
    conn: duckdb.DuckDBPyConnection,
    user_id: int,
    perk_type: str,
) -> bool:
    """Return True if the user has a non-expired perk of the given type."""
    row = conn.execute(
        "SELECT 1 FROM user_perks WHERE user_id = ? AND perk_type = ? AND expires_at > current_timestamp",
        [user_id, perk_type],
    ).fetchone()
    return row is not None


def grant_perk(
    conn: duckdb.DuckDBPyConnection,
    user_id: int,
    perk_type: str,
    duration_hours: int = 24,
) -> None:
    """Grant a timed perk to a user, extending if they already have one."""
    def _write() -> None:
        conn.execute(
            """
            INSERT INTO user_perks (user_id, perk_type, expires_at)
            VALUES (?, ?, current_timestamp + INTERVAL (?) HOUR)
            ON CONFLICT (user_id, perk_type) DO UPDATE SET
                expires_at = GREATEST(user_perks.expires_at, excluded.expires_at)
            """,
            [user_id, perk_type, duration_hours],
        )
        conn.commit()
    _db_write(_write)


def get_perk_expiry(
    conn: duckdb.DuckDBPyConnection,
    user_id: int,
    perk_type: str,
) -> datetime | None:
    """Return the expiry datetime of a perk, or None if not active."""
    row = conn.execute(
        "SELECT expires_at FROM user_perks WHERE user_id = ? AND perk_type = ? AND expires_at > current_timestamp",
        [user_id, perk_type],
    ).fetchone()
    return row[0] if row else None


def get_level_from_points(points: int) -> int:
    """Compute level (0–100) from total Boli Points.

    Solves 5n(n+3) ≤ points for the largest integer n.
    """
    if points <= 0:
        return 0
    # 5n² + 15n - points = 0  →  n = (-15 + sqrt(225 + 20·points)) / 10
    n = int((-15 + math.sqrt(225 + 20 * points)) / 10)
    return min(max(n, 0), 100)
