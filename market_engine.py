# market_engine.py — Boli Stock Market price computation engine
#
# Prices are computed deterministically per (item_id, date) using a seeded RNG
# so all bot instances agree on today's price without coordination.
# The "Monsoon Insurance" item optionally pulls live rain data from Open-Meteo
# (free, no API key needed). All other items use pure local computation.

from __future__ import annotations

import hashlib
import logging
import math
import random
import sqlite3
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger("navi.market_engine")

# ---------------------------------------------------------------------------
# Volatility multipliers: fraction of base_price that price can swing each day
# ---------------------------------------------------------------------------
_VOLATILITY_RANGE: dict[str, tuple[float, float]] = {
    "very_low": (0.97, 1.03),   # ±3%
    "low":      (0.93, 1.07),   # ±7%
    "medium":   (0.88, 1.12),   # ±12%
    "high":     (0.80, 1.20),   # ±20%
}

# Minimum price floor: items can never go below 20% of base_price
_PRICE_FLOOR_PCT = 0.20

# Price ceiling: 3× base_price
_PRICE_CEIL_PCT = 3.0

# Day-of-week modifiers (0=Mon, 4=Fri, 6=Sun)
_DOW_MODIFIER: dict[str, dict[int, float]] = {
    "it_layoff_hedge": {4: 1.15, 0: 0.90},   # Friday spike, Monday dip
    "cricket_mood_index": {5: 1.12, 6: 1.10},  # Weekend match days
}

# Open-Meteo endpoint for Trivandrum precipitation (WMO code 61/63/65/80/81/82 = rain)
_TVM_LAT = 8.5241
_TVM_LON = 76.9366
_OPEN_METEO_URL = (
    f"https://api.open-meteo.com/v1/forecast"
    f"?latitude={_TVM_LAT}&longitude={_TVM_LON}"
    f"&daily=precipitation_sum&timezone=Asia%2FCalcutta&forecast_days=1"
)


def _seeded_rng(item_id: str, for_date: date) -> random.Random:
    """Return a deterministic RNG seeded from item_id + date."""
    seed_str = f"{item_id}:{for_date.isoformat()}"
    seed_int = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16) % (2**32)
    return random.Random(seed_int)


def _compute_price(
    item_id: str,
    base_price: int,
    volatility: str,
    for_date: date,
    rain_mm: float = 0.0,
) -> int:
    """Compute today's price for an item using deterministic seeded RNG.

    The result is the same regardless of how many times it is called for the
    same (item_id, date) pair — safe to call repeatedly.
    """
    rng = _seeded_rng(item_id, for_date)
    lo, hi = _VOLATILITY_RANGE.get(volatility, (0.90, 1.10))
    multiplier = rng.uniform(lo, hi)

    # Day-of-week override
    dow = for_date.weekday()
    if item_id in _DOW_MODIFIER and dow in _DOW_MODIFIER[item_id]:
        multiplier *= _DOW_MODIFIER[item_id][dow]

    # Monsoon Insurance rain boost: every mm of rain over 5mm adds 2%
    if item_id == "monsoon_insurance" and rain_mm > 5.0:
        rain_boost = 1.0 + min((rain_mm - 5.0) * 0.02, 0.50)  # cap at +50%
        multiplier *= rain_boost

    # Coconut Oil: slow sine wave keyed to day-of-year
    if item_id == "coconut_oil_commodity":
        day_of_year = for_date.timetuple().tm_yday
        sine_factor = 1.0 + 0.08 * math.sin(2 * math.pi * day_of_year / 365)
        multiplier *= sine_factor

    raw = base_price * multiplier
    floor = max(1, int(base_price * _PRICE_FLOOR_PCT))
    ceil = int(base_price * _PRICE_CEIL_PCT)
    return max(floor, min(ceil, round(raw)))


async def _fetch_rain_mm() -> float:
    """Fetch today's precipitation forecast for Trivandrum from Open-Meteo.

    Returns 0.0 on any network or parse failure (fail-safe).
    """
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(_OPEN_METEO_URL, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    rain_list = data.get("daily", {}).get("precipitation_sum", [0.0])
                    return float(rain_list[0]) if rain_list else 0.0
    except Exception as exc:
        logger.warning("Open-Meteo fetch failed: %s", exc)
    return 0.0


async def update_all_prices(conn: sqlite3.Connection) -> None:
    """Recompute and persist prices for all market items.

    Called once per day at midnight IST. Records price history for each item.
    """
    from schema import (
        get_market_items,
        update_market_price,
        record_price_history,
    )

    today = date.today()
    date_str = today.isoformat()

    # Fetch rain data once for Monsoon Insurance
    rain_mm = await _fetch_rain_mm()
    logger.info("Market price update: Trivandrum rain today = %.1f mm", rain_mm)

    items = get_market_items(conn)
    for item in items:
        new_price = _compute_price(
            item["item_id"],
            item["base_price"],
            item["volatility"],
            today,
            rain_mm=rain_mm if item["item_id"] == "monsoon_insurance" else 0.0,
        )
        update_market_price(conn, item["item_id"], new_price)
        record_price_history(conn, item["item_id"], new_price, date_str)
        logger.info(
            "Market: %s — %d → %d (%.1f%%)",
            item["item_id"], item["current_price"], new_price,
            100 * (new_price - item["current_price"]) / max(item["current_price"], 1),
        )

    logger.info("Market price update complete (%d items).", len(items))


def get_trend_arrow(current_price: int, base_price: int) -> str:
    """Return a simple trend emoji based on current vs base price."""
    ratio = current_price / max(base_price, 1)
    if ratio >= 1.10:
        return "📈"
    if ratio <= 0.90:
        return "📉"
    return "➡️"
