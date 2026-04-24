# curses.py - Curse word triggers, passive reply templates, Boli Points, Kochi slang

from __future__ import annotations

import random

# ---------------------------------------------------------------------------
# Curse word trigger list (case-insensitive matching in bot.py)
# ---------------------------------------------------------------------------

CURSE_WORDS: list[str] = [
    "pottan", "potti", "mandan", "mandabuddhi", "modan",
    "vattan", "vatti", "oola", "thallippoli", "vivaramillathavan",
    "buddhisunyan", "madiyan", "madichi", "vayadi", "kozhi",
    "kunjamma", "paala", "enapechi", "kanda mrigam", "kochu kalla",
    "dushtan", "chunk", "loose", "kumpidi", "pokri",
    "veruppirayan", "alavalathi", "shasi", "gundabiju", "kuzhappakkaran",
]

# ---------------------------------------------------------------------------
# Boli Points — trigger words (earn +5 pts per unique word per message)
# ---------------------------------------------------------------------------

BOLI_TRIGGER_WORDS: list[str] = [
    "kidilam", "pillacha", "kidu", "appi", "shokam",
    "chumma", "kili poyi", "vishayam", "mone", "chetta",
    "thirontharam", "boli", "paal payasam",
]

# ---------------------------------------------------------------------------
# Kochi/outside slang detection (triggers condescending response, no point penalty)
# ---------------------------------------------------------------------------

KOCHI_SLANG: list[str] = [
    "machane", "machi", "sayi", "da scene", "yov", "monae",
    "adipoli", "njaan", "sheri aano",
]

# ---------------------------------------------------------------------------
# Condescending responses to Kochi slang (funny, not mean-spirited)
# ---------------------------------------------------------------------------

KOCHI_SLANG_RESPONSES: list[str] = [
    "Eda {user}, this is Thirontharam. Keep that Kochi talk at Ernakulam South station.",
    "Aiyo {user}, 'machane'? We don't use that here mone. Say it properly or go back to Marine Drive.",
    "{user}, AstRobot detects strong Kochi energy in this message. The stars are confused and slightly offended.",
    "Eda {user}, wrong city, wrong slang. Thirontharam vibes only. Chumma adjust cheyyane.",
    "Aiyo {user}, nee Ernakulam-karan aano? Sit down, have some Boli, and learn to speak properly.",
    "{user}, the cosmic alignment is disturbed by your Kochi accent. AstRobot is disappointed but not surprised. Shokam.",
    "Eda {user}, this slang is from Kochi side only. Here we say things properly. Adjust aakane mone.",
]

# ---------------------------------------------------------------------------
# Doomed prediction templates (static fallback)
# ---------------------------------------------------------------------------

DOOMED_PREDICTIONS: list[str] = [
    "Nee oru {curse} aanu. Ponmudi mist-pole ninte future also unclear. Kili poyi!",
    "Eda {user}, ninte stars say you will get stuck in KD Puram traffic for 45 minutes on a Tuesday with no charge in your phone. Chumma vayadi ayikko.",
    "Aiyo {user}, nee oru {curse} aanu. This week avoid Chalai Market or you will lose your wallet AND your sense of direction.",
    "{user} ya, AstRobot is watching. Your stars say you will wait at Thampanoor bus stand for 2 hours for a bus that never comes. Shokam aanu.",
    "Eda {user}, ninte rashi meedhi KSRTC bus thana. You will get stuck in Museum Campus traffic during Attukal Pongala. Karma has noted your recent behaviour.",
    "{user}, nee oru {curse} aanu. Your palm lines say you will spend all evening at Indian Coffee House waiting for a cutlet that was sold out. Chumma.",
    "Aiyo {user}, ninte future is darker than Chalai Market at night. You will go to Zam Zam and find out they ran out of shawarma. Kidilam aayirunnu!",
]

# ---------------------------------------------------------------------------
# Curse-back reply templates (static fallback)
# ---------------------------------------------------------------------------

CURSE_BACK_REPLIES: list[str] = [
    "Aiyo {user}, nee oru {curse} aanu. Ponmudi mist-pole ninte future also unclear. Kili poyi!",
    "Eda {user}, nee oru {curse}! Ninte aadu (star) is sitting in the 7th house like a KSRTC bus at Thampanoor — not moving.",
    "{user}, nee oru {curse} aanu. Chumma vayadi ayikko. AstRobot is judging you from Vellayambalam.",
    "Aiyo {user}, nee oru {curse}! Go drink chaya at thattukada and think about what you said. Oola.",
    "{user}, nee oru {curse} aanu. Ninte future darker than KD Puram at 7pm. Shokam.",
]

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_random_curse() -> str:
    """Returns a random curse word from the list."""
    return random.choice(CURSE_WORDS)


def get_random_doomed_prediction(username: str) -> str:
    """Returns a filled doomed prediction template."""
    template = random.choice(DOOMED_PREDICTIONS)
    curse = get_random_curse()
    return template.format(user=username, curse=curse)


def get_random_curse_back(username: str) -> str:
    """Returns a filled curse-back template."""
    template = random.choice(CURSE_BACK_REPLIES)
    curse = get_random_curse()
    return template.format(user=username, curse=curse)


def get_random_kochi_response(username: str) -> str:
    """Returns a condescending response to Kochi slang usage."""
    template = random.choice(KOCHI_SLANG_RESPONSES)
    return template.format(user=username)


def contains_boli_trigger(content: str) -> list[str]:
    """Return list of unique BOLI_TRIGGER_WORDS found in content (case-insensitive)."""
    content_lower = content.lower()
    return [word for word in BOLI_TRIGGER_WORDS if word in content_lower]


def contains_kochi_slang(content: str) -> bool:
    """Return True if content contains any Kochi-specific slang."""
    content_lower = content.lower()
    return any(slang in content_lower for slang in KOCHI_SLANG)