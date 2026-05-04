# api_server.py — Lightweight FastAPI HTTP endpoint for partner bot score ingestion
#
# Runs as an asyncio task alongside the main Discord bot process.
# POST /score  accepts a JSON payload from trusted partner bots,
# validates the API key, deduplicates on game_id, normalizes the raw score,
# and awards Boli + XP to the target user in the shared SQLite DB.
#
# Setup on GCP VM:
#   1. Add PARTNER_BOT_API_KEY=<secret> to .env
#   2. Open port 8080 in GCP firewall rules (VPC network → Firewall → allow TCP 8080)
#   3. Share the VM's external IP + port + key with the partner bot owner
#
# Deduplication: game_id is stored in partner_score_log with a UNIQUE constraint.
# A duplicate submission returns HTTP 409 without awarding points.

from __future__ import annotations

import logging
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("navi.api_server")

# ---------------------------------------------------------------------------
# Score normalization
# ---------------------------------------------------------------------------

# 1:1 passthrough: raw_points (sent by partner bot) → Boli awarded directly.
# Negative raw_points = player failed → 0 Boli.
# The partner bot is responsible for sending meaningful point values.

# XP awarded equals half the Boli awarded (minimum 5 XP for any valid submission)
_XP_RATIO = 0.5
_MIN_XP = 5

# Rate limit: max submissions per guild per minute
_RATE_LIMIT_RPM = 10
_rate_counters: dict[int, list[float]] = defaultdict(list)


def _normalize_score(raw_points: int) -> int:
    """1:1 mapping: raw score → Boli. Negative values (fail/loss) → 0."""
    return max(0, int(raw_points))


def _is_rate_limited(guild_id: int) -> bool:
    """Simple fixed-window rate limiter: max 10 requests per minute per guild."""
    now = time.time()
    window = [t for t in _rate_counters[guild_id] if now - t < 60]
    _rate_counters[guild_id] = window
    if len(window) >= _RATE_LIMIT_RPM:
        return True
    _rate_counters[guild_id].append(now)
    return False


# ---------------------------------------------------------------------------
# FastAPI app factory — called from bot.py on_ready with the shared db_conn
# ---------------------------------------------------------------------------

def create_app(db_conn: sqlite3.Connection, allowed_guild_id: int | None) -> Any:
    """Build and return the FastAPI app.

    db_conn:          The shared SQLite connection from the bot process.
    allowed_guild_id: If set, only submissions for this guild are accepted.
                      Pass None to accept any guild_id (less secure).
    """
    try:
        from fastapi import FastAPI, HTTPException, Request, Depends
        from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
        from pydantic import BaseModel
    except ImportError:
        logger.error(
            "FastAPI / pydantic not installed. Run: pip install fastapi uvicorn[standard] pydantic"
        )
        return None

    from schema import (
        upsert_user,
        update_boli_points,
        add_experience,
        partner_score_exists,
        log_partner_score,
        get_config_int,
        get_recent_partner_logs,
    )

    API_KEY = os.getenv("PARTNER_BOT_API_KEY", "")
    if not API_KEY:
        logger.warning("PARTNER_BOT_API_KEY not set — /score endpoint will reject all requests.")

    app = FastAPI(title="Navi Partner Score API", version="1.0")
    bearer = HTTPBearer()

    class ScorePayload(BaseModel):
        user_id: int
        guild_id: int
        username: str
        points: int               # raw game score (negative = deduction/fail)
        game_id: str | None = None  # match_id for deduplication
        game_type: str = "default"  # e.g. "wordle"

    def _verify_key(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> None:
        if not API_KEY or creds.credentials != API_KEY:
            raise HTTPException(status_code=401, detail="Invalid API key.")

    @app.post("/score", dependencies=[Depends(_verify_key)])
    async def ingest_score(payload: ScorePayload, request: Request) -> dict:
        """Accept a game score from a trusted partner bot and award Boli + XP."""

        # Guild validation
        if allowed_guild_id and payload.guild_id != allowed_guild_id:
            raise HTTPException(status_code=403, detail="Guild not authorised.")

        # Rate limit
        if _is_rate_limited(payload.guild_id):
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 10 req/min per guild.")

        # Deduplication
        game_id = payload.game_id or f"auto:{payload.user_id}:{payload.guild_id}:{int(time.time())}"
        if payload.game_id and partner_score_exists(db_conn, payload.game_id):
            raise HTTPException(status_code=409, detail=f"game_id '{payload.game_id}' already processed.")

        # Check feature toggle — admin can disable partner point awards via /admin partner_toggle
        if not get_config_int(db_conn, "feature_partner_api", 1):
            raise HTTPException(status_code=503, detail="Partner API awards are currently disabled by admin.")

        # Normalize score → Boli (1:1 passthrough)
        boli = _normalize_score(payload.points)
        xp = max(_MIN_XP, int(boli * _XP_RATIO)) if boli > 0 else 0

        # Award points
        upsert_user(db_conn, payload.user_id, payload.username)
        if boli > 0:
            update_boli_points(db_conn, payload.user_id, boli)
            add_experience(db_conn, payload.user_id, xp)

        # Log
        log_partner_score(
            db_conn,
            game_id=game_id,
            user_id=payload.user_id,
            guild_id=payload.guild_id,
            username=payload.username,
            raw_points=payload.points,
            boli_awarded=boli,
            xp_awarded=xp,
            game_type=payload.game_type,
        )

        logger.info(
            "Partner score: user=%s game_type=%s raw=%d → boli=%d xp=%d",
            payload.username, payload.game_type, payload.points, boli, xp,
        )

        return {
            "status": "ok",
            "boli_awarded": boli,
            "xp_awarded": xp,
            "game_id": game_id,
        }

    @app.get("/score/recent")
    async def recent_scores(
        limit: int = 10,
        creds: HTTPAuthorizationCredentials = Depends(bearer),
    ) -> dict:
        """Return recent partner score submissions (admin audit endpoint)."""
        if not API_KEY or creds.credentials != API_KEY:
            raise HTTPException(status_code=401, detail="Invalid API key.")
        logs = get_recent_partner_logs(db_conn, limit=min(limit, 50))
        return {"logs": logs}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

    return app


async def run_api_server(db_conn: sqlite3.Connection, allowed_guild_id: int | None = None) -> None:
    """Start the uvicorn server as a coroutine (run with asyncio.create_task).

    Listens on 0.0.0.0:8080 by default. Override with API_SERVER_PORT env var.
    """
    try:
        import uvicorn
    except ImportError:
        logger.error("uvicorn not installed — partner API server will not start. Run: pip install uvicorn[standard]")
        return

    app = create_app(db_conn, allowed_guild_id)
    if app is None:
        return

    port = int(os.getenv("API_SERVER_PORT", "8080"))
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)
    logger.info("Partner API server starting on port %d.", port)
    await server.serve()
