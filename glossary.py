# glossary.py - Trivandrum Manglish slang, landmarks, food, culture
# Includes time-aware and live weather context helpers for prompt injection.

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger("astrobot.glossary")

# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------

EXPRESSIONS: dict[str, str] = {
    "Appi": "Term of endearment for a baby/child",
    "Kili poyi": "Literally 'the bird flew away' - when someone is confused or shocked",
    "Oola": "Useless, pathetic, or poor quality",
    "Shokam": "Sad, boring, or pathetic situation",
    "Chumma": "Simply, for no reason",
    "Eda": "Informal 'hey' (used among friends, male)",
    "Edi": "Informal 'hey' (used among friends, female)",
    "Vayye?": "Are you not well? or Can't you do it? (often sarcastic)",
    "Pillacha": "Respectful address for older man (shopkeeper/neighbor)",
    "Kidilam": "Absolutely awesome or fantastic",
    "Kidu": "Absolutely awesome or fantastic",
    "Thirontharam": "Local pronunciation of Thiruvananthapuram",
    "Vishayam": "Matter, issue, situation — as in 'what is this vishayam?'",
    "Lokam": "World, scene — 'IT lokam' means the IT crowd/world",
    "Chetta": "Elder brother; used to address older men respectfully",
    "Mone": "Son; affectionate address (also slightly patronising)",
    "IT Ambitions": "The Technopark/IT crowd who think they have escaped Thirontharam",
}

# ---------------------------------------------------------------------------
# Landmarks
# ---------------------------------------------------------------------------

LANDMARKS: list[str] = [
    "Palayam",
    "Thampanoor",
    "KD Puram",
    "Vellayambalam",
    "Kowdiar",
    "Chalai Market",
    "Ponmudi",
    "Sreekaryam",
    "Kazhakkoottam",
    "Museum Campus",
    "Technopark",
    "Connemara Market",
    "East Fort",
    "Sasthamangalam",
    "Pongumoodu",
    "Pettah",
    "Vazhuthacaud",
    "Jagathy",
]

# ---------------------------------------------------------------------------
# Food & eateries
# ---------------------------------------------------------------------------

FOOD: list[str] = [
    "Boli and Paal Payasam",
    "Kethel's Chicken (Rahmaniya)",
    "Zam Zam Palayam",
    "Indian Coffee House Thampanoor",
    "Sree Muruka Cafe",
    "Rasavadai",
    "Pazham Pori and Beef Roast",
    "Maha Boly",
    "Evening chaya from thattukada",
    "Shawarma from Zam Zam",
]

# ---------------------------------------------------------------------------
# Culture
# ---------------------------------------------------------------------------

CULTURE: list[str] = [
    "IFFK (International Film Festival of Kerala)",
    "Tagore Theatre",
    "Attukal Pongala",
    "Ramachandran Textiles East Fort",
    "Technopark",
    "Thattukada (street tea stall)",
    "KSRTC bus stand",
    "Napier Museum",
    "Kanakakkunnu Palace",
]

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
    95: "thunderstorm, AstRobot advises staying home",
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


def get_time_context() -> dict:
    """Return a dict describing the current time period in Trivandrum.

    Keys: period (str), landmark_hint (str), personality_addendum (str)
    """
    now = datetime.now(_IST)
    hour = now.hour

    if 6 <= hour < 10:
        return {
            "period": "morning",
            "landmark_hint": "Thampanoor KSRTC stand",
            "personality_addendum": (
                "It is morning rush hour in Thirontharam. You might hint at the chaos "
                "at Thampanoor or the fact that nobody has had their chaya yet. "
                "VARY YOUR PREDICTIONS wildly — do not always talk about buses or tea. "
                "Instead, find strange new ways the morning can go wrong."
            ),
        }
    elif 10 <= hour < 14:
        return {
            "period": "noon",
            "landmark_hint": "Thampanoor footpath",
            "personality_addendum": (
                "It is scorching noon in Thirontharam. You are irritable because of the heat. "
                "You might optionally mention sweating or poor decisions made in the sun, "
                "but YOU MUST VARY YOUR TOPICS. Do not repeat 'melting' every time."
            ),
        }
    elif 14 <= hour < 16:
        return {
            "period": "afternoon",
            "landmark_hint": "Palayam",
            "personality_addendum": (
                "It is a sleepy afternoon in Thirontharam. Everyone is in post-lunch stupor. "
                "Your predictions are sluggish but still dramatic."
            ),
        }
    elif 16 <= hour < 19:
        return {
            "period": "evening",
            "landmark_hint": "thattukada near Technopark",
            "personality_addendum": (
                "It is evening in Thirontharam. "
                "You might occasionally wonder if people have had their evening chaya and Pazham Pori. "
                "CRITICAL: VARY your topics wildly. Talk about traffic, bad dates, office politics, "
                "or random cosmic doom. Do not get stuck talking about thattukadas."
            ),
        }
    elif 19 <= hour < 22:
        return {
            "period": "night",
            "landmark_hint": "KD Puram",
            "personality_addendum": (
                "It is evening and the Technopark IT crowd is stuck in traffic. "
                "You pity them but also find it funny. "
                "VARY YOUR PREDICTIONS: talk about anything from lost keys to bad internet, "
                "do not restrict yourself to just traffic jokes."
            ),
        }
    else:
        return {
            "period": "late night",
            "landmark_hint": "Trivandrum at night",
            "personality_addendum": (
                "It is late night in Thirontharam. The city has gone to sleep. "
                "You hint at mysterious late-night energies, bad life decisions made after midnight, "
                "or the existential dread of waking up early tomorrow. "
                "Predictions should be slightly darker or philosophical, but DO NOT always mention ghosts or Museum Campus. VARY your late-night topics."
            ),
        }


# ---------------------------------------------------------------------------
# Formatted glossary for prompt injection
# ---------------------------------------------------------------------------

def get_glossary_text() -> str:
    """Returns formatted glossary for prompt injection (random subset to ensure variety)."""
    import random
    
    # Pick random subset to force LLM to vary its topics on every request
    expr_items = random.sample(list(EXPRESSIONS.items()), k=min(4, len(EXPRESSIONS)))
    exprs = ", ".join([f"{k} ({v})" for k, v in expr_items])
    
    landmarks = ", ".join(random.sample(LANDMARKS, k=min(4, len(LANDMARKS))))
    foods = ", ".join(random.sample(FOOD, k=min(3, len(FOOD))))
    culture = ", ".join(random.sample(CULTURE, k=min(3, len(CULTURE))))
    
    return f"""
AVAILABLE EXPRESSIONS (Trivandrum Manglish): {exprs}
AVAILABLE LANDMARKS: {landmarks}
AVAILABLE FOOD & EATERIES: {foods}
AVAILABLE CULTURE: {culture}
"""