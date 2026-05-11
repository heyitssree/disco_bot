import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent / "bamboozled.db"


async def init_db() -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS players (
                discord_id TEXT PRIMARY KEY,
                username TEXT,
                games_played INTEGER DEFAULT 0,
                games_won INTEGER DEFAULT 0,
                total_points_earned INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS game_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                played_at TEXT,
                channel_id TEXT,
                winner_id TEXT,
                player_count INTEGER,
                final_scores TEXT
            )
        """)
        # Tracks channels with active games so orphans can be announced on restart
        await db.execute("""
            CREATE TABLE IF NOT EXISTS active_channels (
                channel_id TEXT PRIMARY KEY
            )
        """)
        await db.commit()


async def register_active_channel(channel_id: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO active_channels (channel_id) VALUES (?)", (channel_id,)
        )
        await db.commit()


async def unregister_active_channel(channel_id: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("DELETE FROM active_channels WHERE channel_id = ?", (channel_id,))
        await db.commit()


async def get_orphaned_channels() -> list[str]:
    async with aiosqlite.connect(_DB_PATH) as db:
        async with db.execute("SELECT channel_id FROM active_channels") as cur:
            rows = await cur.fetchall()
    channel_ids = [r[0] for r in rows]
    # Clear them all
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("DELETE FROM active_channels")
        await db.commit()
    return channel_ids


async def upsert_player(discord_id: str, username: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO players (discord_id, username, games_played, games_won, total_points_earned)
            VALUES (?, ?, 0, 0, 0)
            ON CONFLICT(discord_id) DO UPDATE SET username = excluded.username
            """,
            (discord_id, username),
        )
        await db.commit()


async def update_player_stats(discord_id: str, won: bool, final_score: int) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            """
            UPDATE players
            SET games_played = games_played + 1,
                games_won = games_won + ?,
                total_points_earned = total_points_earned + ?
            WHERE discord_id = ?
            """,
            (1 if won else 0, max(0, final_score), discord_id),
        )
        await db.commit()


async def save_game_result(
    channel_id: str, winner_id: str, player_count: int, final_scores: dict
) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO game_results (played_at, channel_id, winner_id, player_count, final_scores)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                channel_id,
                winner_id,
                player_count,
                json.dumps(final_scores),
            ),
        )
        await db.commit()


async def get_leaderboard(limit: int = 10) -> list[tuple]:
    async with aiosqlite.connect(_DB_PATH) as db:
        async with db.execute(
            """
            SELECT username, games_won, games_played, total_points_earned
            FROM players
            ORDER BY games_won DESC, total_points_earned DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            return await cur.fetchall()


async def get_player_stats(discord_id: str) -> Optional[tuple]:
    async with aiosqlite.connect(_DB_PATH) as db:
        async with db.execute(
            "SELECT username, games_played, games_won, total_points_earned FROM players WHERE discord_id = ?",
            (discord_id,),
        ) as cur:
            return await cur.fetchone()
