# curses.py - Curse word triggers, passive reply templates, Boli Points, Kochi slang

from __future__ import annotations

import re
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
    "Aiyo {user}, the stars heard what you said and now Ponmudi mist will follow you all week. Oola situation.",
    "Eda {user}, the universe clocked your language and has booked you 45 minutes of KD Puram traffic with zero phone charge. Chumma.",
    "Aiyo {user}, saying that near Chalai Market? Cosmos says you will lose your wallet AND your dignity this week.",
    "{user}, AstRobot did not make this happen — your own words did. Thampanoor bus stand, 2 hours, bus never comes. Shokam.",
    "Eda {user}, the stars noted your vocabulary. Museum Campus traffic during peak hour is your cosmic punishment. Karma is a KSRTC bus.",
    "{user}, the universe heard that word and decided: Indian Coffee House, all evening, cutlet sold out. Your doing, not mine. Chumma.",
    "Aiyo {user}, Chalai Market at night is shokam enough — your words made it worse. Zam Zam ran out of shawarma. Self-inflicted.",
]

# ---------------------------------------------------------------------------
# Curse-back reply templates (static fallback)
# ---------------------------------------------------------------------------

CURSE_BACK_REPLIES: list[str] = [
    "Aiyo {user}, the cosmos saw what you typed. Ponmudi mist will be your future — unclear and damp.",
    "Eda {user}, that word? The stars billed it to your account. KSRTC bus at Thampanoor — not moving, like your luck.",
    "{user}, the universe has a ledger. That word just added one shokam entry. AstRobot is merely the messenger.",
    "Aiyo {user}, the stars decided — go drink chaya at thattukada and reflect on your word choices. Oola.",
    "{user}, the cosmos heard that. Ninte future darker than KD Puram at 7pm. Your words, your karma. Shokam.",
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


def contains_curse_word(text: str) -> tuple[bool, str | None]:
    """Return (matched, word) using word boundaries to avoid false positives.

    Uses \\b so e.g. 'mandarin' will not match 'mandan'.
    """
    lower = text.lower()
    for word in CURSE_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lower):
            return True, word
    return False, None


def contains_boli_trigger(content: str) -> list[str]:
    """Return list of unique BOLI_TRIGGER_WORDS found in content (case-insensitive).

    Uses word boundaries so e.g. 'kidilam' won't match inside 'akidilam'.
    Multi-word phrases like 'kili poyi' are matched as a contiguous span.
    """
    found: list[str] = []
    for word in BOLI_TRIGGER_WORDS:
        # Build a pattern: \b around single tokens, flexible space for multi-word
        pattern = r"\b" + r"\s+".join(re.escape(part) for part in word.split()) + r"\b"
        if re.search(pattern, content, re.IGNORECASE):
            found.append(word)
    return found


def contains_kochi_slang(content: str) -> bool:
    """Return True if content contains any Kochi-specific slang (whole-word match)."""
    for slang in KOCHI_SLANG:
        pattern = r"\b" + r"\s+".join(re.escape(part) for part in slang.split()) + r"\b"
        if re.search(pattern, content, re.IGNORECASE):
            return True
    return False