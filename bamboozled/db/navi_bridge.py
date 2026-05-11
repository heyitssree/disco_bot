import logging
from pathlib import Path

import aiosqlite

from game_engine.constants import NAVI_DB_PATH

logger = logging.getLogger(__name__)


async def award_boli(user_id: int, amount: int, reason: str) -> bool:
    """Award Boli Points to a user in Navi's database.
    Returns True on success, False on failure.
    Does nothing and returns False if NAVI_DB_PATH is not configured.
    """
    if not NAVI_DB_PATH:
        logger.warning("Boli bridge: NAVI_DB_PATH not configured — skipping award.")
        return False
    if not Path(NAVI_DB_PATH).exists():
        logger.warning("Boli bridge: DB file not found at %s — skipping.", NAVI_DB_PATH)
        return False
    try:
        async with aiosqlite.connect(NAVI_DB_PATH) as db:
            cursor = await db.execute(
                "UPDATE user_stats SET boli_points = boli_points + ? WHERE user_id = ?",
                (amount, user_id),
            )
            await db.commit()
            if cursor.rowcount == 0:
                logger.info("Boli bridge: user %d not in Navi DB — skipping.", user_id)
                return False
        logger.info("Boli bridge: +%d to user %d (%s)", amount, user_id, reason)
        return True
    except Exception as exc:
        logger.warning("Boli bridge: failed to award boli — %s", exc)
        return False
