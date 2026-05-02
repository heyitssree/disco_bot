# glossary.py - Trivandrum Manglish slang, landmarks, food, culture
# Includes time-aware and live weather context helpers for prompt injection.

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger("navi.glossary")

# ---------------------------------------------------------------------------
# Rashi (Star Signs) — Trivandrum-flavoured
# ---------------------------------------------------------------------------

RASHIS: list[str] = [
    "Medam (Aries)", "Edavam (Taurus)", "Mithunam (Gemini)",
    "Karkidakam (Cancer)", "Chingam (Leo)", "Kanni (Virgo)",
    "Thulam (Libra)", "Vrischikam (Scorpio)", "Dhanu (Sagittarius)",
    "Makaram (Capricorn)", "Kumbham (Aquarius)", "Meenam (Pisces)",
]

# ---------------------------------------------------------------------------
# WMO weather code → Manglish description
# ---------------------------------------------------------------------------

_WMO_CODE_MAP: dict[range | int, str] = {
    0: "clear sky, hot and sunny",
    1: "mostly clear, some clouds loitering",
    2: "partly cloudy, suspicious sky",
    3: "overcast, grey like a Monday in Thampanoor",
    45: "foggy, like Ponmudi in the morning",
    48: "heavy fog, complete visibility shokam",
    51: "light drizzle, wear your chappal carefully",
    53: "moderate drizzle, umbrella recommended",
    55: "heavy drizzle, basically raining",
    61: "light rain, the kind that ruins your plans",
    63: "moderate rain, KSRTC bus will be late",
    65: "heavy rain, stay inside and eat Pazham Pori",
    71: "light snowfall",   # virtually impossible in TVM but handled
    80: "rain showers, Chalai Market flooding possible",
    95: "thunderstorm, Navi advises staying home",
    99: "severe thunderstorm, full shokam situation",
}


def _decode_wmo(code: int) -> str:
    """Map a WMO weather code to a Manglish description string."""
    if code in _WMO_CODE_MAP:
        return _WMO_CODE_MAP[code]
    # Range-based fallback
    if 2 <= code <= 3:
        return "partly cloudy"
    if 51 <= code <= 57:
        return "drizzling, carry umbrella mone"
    if 61 <= code <= 67:
        return "raining like Ponmudi monsoon — full wet"
    if 71 <= code <= 77:
        return "misty, Ponmudi energy"
    if 80 <= code <= 82:
        return "rain showers coming and going"
    if 95 <= code <= 99:
        return "thunderstorm — full dramatic weather"
    return "weather unclear, even the stars don't know"


# ---------------------------------------------------------------------------
# Weather cache (15-minute in-memory, avoids per-message API spam)
# ---------------------------------------------------------------------------

_weather_cache: tuple[str, datetime] | None = None  # (text, expiry)
_WEATHER_CACHE_MINUTES = 15

_OPEN_METEO_CURRENT_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=8.5241&longitude=76.9366"
    "&current=temperature_2m,weathercode,wind_speed_10m"
    "&timezone=Asia%2FKolkata"
)
_OPEN_METEO_DAILY_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=8.5241&longitude=76.9366"
    "&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum"
    "&timezone=Asia%2FKolkata&forecast_days=1"
)

_WEATHER_FALLBACKS: list[str] = [
    "scorching heat, no rain, typical Thirontharam",
    "light drizzle, humidity through the roof",
    "cloudy with suspicious-looking sky",
    "hot and humid, classic coastal weather",
    "evening breeze from the sea, actually pleasant for once",
]


def get_current_weather_context() -> str:
    """Fetch current Trivandrum weather for inline prompt injection.

    Cached for 15 minutes. Returns a short Manglish description string.
    """
    global _weather_cache
    now = datetime.now()

    if _weather_cache and now < _weather_cache[1]:
        return _weather_cache[0]

    try:
        resp = requests.get(_OPEN_METEO_CURRENT_URL, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        current = data["current"]
        temp = current["temperature_2m"]
        code = current["weathercode"]
        condition = _decode_wmo(code)
        description = f"{temp}°C, {condition}"
        _weather_cache = (description, now + timedelta(minutes=_WEATHER_CACHE_MINUTES))
        logger.debug("Weather fetched: %s", description)
        return description
    except Exception as exc:
        logger.warning("Weather API failed: %s — using fallback.", exc)
        fallback = random.choice(_WEATHER_FALLBACKS)
        _weather_cache = (fallback, now + timedelta(minutes=5))
        return fallback


def get_daily_weather_forecast() -> dict:
    """Fetch today's full-day Trivandrum weather forecast for the morning post.

    Returns dict with keys: max_temp, min_temp, rain_mm, condition.
    """
    try:
        resp = requests.get(_OPEN_METEO_DAILY_URL, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        daily = data["daily"]
        code = daily["weathercode"][0]
        return {
            "max_temp": daily["temperature_2m_max"][0],
            "min_temp": daily["temperature_2m_min"][0],
            "rain_mm": daily["precipitation_sum"][0],
            "condition": _decode_wmo(code),
        }
    except Exception as exc:
        logger.warning("Daily weather forecast failed: %s — using fallback.", exc)
        return {
            "max_temp": 34.0,
            "min_temp": 26.0,
            "rain_mm": 0.0,
            "condition": "hot and relentlessly sunny",
        }


# ---------------------------------------------------------------------------
# Time-aware context
# ---------------------------------------------------------------------------

_IST = timezone(timedelta(hours=5, minutes=30))

# Rotating "avoid this topic" addendums injected per time period to stop the AI
# from defaulting to the same metaphors in each slot.
_MORNING_AVOIDS: list[str] = [
    "Do NOT talk about buses, tea, or traffic jams this time.",
    "Avoid chaya, KSRTC, and Thampanoor station — find a fresh angle.",
    "Skip the morning-commute metaphors. Talk about something unexpected instead.",
    "No bus references or tea jokes this round. The cosmos has other opinions.",
]
_NOON_AVOIDS: list[str] = [
    "Do NOT use 'melting', 'sweating', or heat metaphors this time.",
    "Avoid sun, heat, and sweat jokes — find something more original.",
    "Skip the obvious noon-heat angle. The universe has stranger doom today.",
    "No 'scorching' or 'unbearable heat' this round. Try cosmic irony instead.",
]
_AFTERNOON_AVOIDS: list[str] = [
    "Do NOT mention lunch, naps, or post-meal sluggishness.",
    "Avoid 'sleepy afternoon' tropes — find a stranger afternoon omen.",
    "Skip the drowsy-office-worker angle. Surprise the reader.",
    "No references to food coma or sleeping at desks this time.",
]
_EVENING_AVOIDS: list[str] = [
    "Do NOT mention thattukada, Pazham Pori, or evening snacks.",
    "Avoid the evening-chaya ritual entirely — go somewhere unexpected.",
    "Skip the 'going home from office' angle this round.",
    "No street food or evening-snack references. Pick a different doom.",
]
_NIGHT_AVOIDS: list[str] = [
    "Do NOT mention traffic, IT crowd, or Technopark this time.",
    "Avoid the stuck-in-traffic trope — find a different night-time fate.",
    "Skip the KD Puram traffic jam. The cosmos has fresher punishments.",
    "No office-commute jokes this round. Try something existential instead.",
]
_LATENIGHT_AVOIDS: list[str] = [
    "Do NOT mention ghosts, Museum Campus, or darkness.",
    "Avoid the 'mysterious late-night energy' cliché. Go philosophical.",
    "Skip ghost references entirely — the stars have stranger late-night truths.",
    "No horror or haunted-place references this round. Try regret or ambition.",
]


def get_time_context() -> dict:
    """Return a dict describing the current time period in Trivandrum.

    Keys: period (str), landmark_hint (str), personality_addendum (str).
    The personality_addendum includes a rotating topic-avoidance instruction
    to prevent the LLM from defaulting to the same metaphors every slot.
    """
    now = datetime.now(_IST)
    hour = now.hour
    # Rotate avoidance hint using the current minute so it shifts each call
    slot = now.minute % 4

    if 6 <= hour < 10:
        avoid = _MORNING_AVOIDS[slot]
        return {
            "period": "morning",
            "landmark_hint": "Thampanoor KSRTC stand",
            "personality_addendum": (
                "It is morning rush hour in Thirontharam. "
                "VARY YOUR PREDICTIONS wildly — find strange new ways the morning can go wrong. "
                f"ROTATION RULE: {avoid}"
            ),
        }
    elif 10 <= hour < 14:
        avoid = _NOON_AVOIDS[slot]
        return {
            "period": "noon",
            "landmark_hint": "Thampanoor footpath",
            "personality_addendum": (
                "It is scorching noon in Thirontharam. You are irritable. "
                "YOU MUST VARY YOUR TOPICS across predictions. "
                f"ROTATION RULE: {avoid}"
            ),
        }
    elif 14 <= hour < 16:
        avoid = _AFTERNOON_AVOIDS[slot]
        return {
            "period": "afternoon",
            "landmark_hint": "Palayam",
            "personality_addendum": (
                "It is a sleepy afternoon in Thirontharam. Your predictions are sluggish but still dramatic. "
                f"ROTATION RULE: {avoid}"
            ),
        }
    elif 16 <= hour < 19:
        avoid = _EVENING_AVOIDS[slot]
        return {
            "period": "evening",
            "landmark_hint": "thattukada near Technopark",
            "personality_addendum": (
                "It is evening in Thirontharam. "
                "CRITICAL: VARY your topics wildly — traffic, bad dates, office politics, random cosmic doom. "
                f"ROTATION RULE: {avoid}"
            ),
        }
    elif 19 <= hour < 22:
        avoid = _NIGHT_AVOIDS[slot]
        return {
            "period": "night",
            "landmark_hint": "KD Puram",
            "personality_addendum": (
                "It is night in Thirontharam. "
                "VARY YOUR PREDICTIONS: lost keys, bad internet, unreplied texts, existential dread. "
                f"ROTATION RULE: {avoid}"
            ),
        }
    else:
        avoid = _LATENIGHT_AVOIDS[slot]
        return {
            "period": "late night",
            "landmark_hint": "Trivandrum at night",
            "personality_addendum": (
                "It is late night in Thirontharam. The city has gone to sleep. "
                "Predictions should be slightly darker or philosophical. VARY your late-night topics. "
                f"ROTATION RULE: {avoid}"
            ),
        }


# ---------------------------------------------------------------------------
# DB-backed glossary for prompt injection
# ---------------------------------------------------------------------------

def _current_time_tag() -> str:
    """Map current IST hour to a tag used to prioritise time-relevant DB entries."""
    hour = datetime.now(_IST).hour
    if 6 <= hour < 10:
        return "morning"
    if 10 <= hour < 17:
        return "weekday"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def get_contextual_sample(
    db_conn,
    category: str,
    limit: int = 5,
    time_tag: str | None = None,
) -> list[dict]:
    """Return a random sample of local_knowledge rows for a category.

    When time_tag is provided, rows whose tags column contains that tag are
    sorted first, then filled with random others to reach *limit*.
    """
    if time_tag:
        rows = db_conn.execute(
            """
            SELECT term, description, tags FROM local_knowledge
            WHERE category = ?
            ORDER BY (CASE WHEN tags LIKE '%' || ? || '%' THEN 0 ELSE 1 END),
                     random()
            LIMIT ?
            """,
            [category, time_tag, limit],
        ).fetchall()
    else:
        rows = db_conn.execute(
            """
            SELECT term, description, tags FROM local_knowledge
            WHERE category = ?
            ORDER BY random()
            LIMIT ?
            """,
            [category, limit],
        ).fetchall()
    return [{"term": r[0], "description": r[1], "tags": r[2]} for r in rows]


def get_glossary_text(db_conn) -> str:
    """Return a formatted glossary snippet for prompt injection.

    Pulls a time-aware random sample from the local_knowledge SQLite table.
    Each line includes the term name and its usage description so Gemini has
    actionable context, not just a bare list of names.
    """
    time_tag = _current_time_tag()
    parts: list[str] = []
    for category, limit in [("expression", 4), ("landmark", 4), ("food", 3), ("culture", 3)]:
        items = get_contextual_sample(db_conn, category, limit, time_tag)
        if not items:
            continue
        parts.append(f"[{category.upper()}]")
        for item in items:
            parts.append(f"- {item['term']}: {item['description']}")
    return "\n".join(parts)