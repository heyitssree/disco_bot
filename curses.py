# curses.py - Curse word triggers, passive reply templates, Boli Points, Kochi slang

from __future__ import annotations

import re
import random

# ---------------------------------------------------------------------------
# Curse word trigger list (case-insensitive matching in bot.py)
# ---------------------------------------------------------------------------

CURSE_WORDS: list[dict] = [
    # Tier 1 — Mild (target loses 5 pts, invoker loses 2 pts)
    {"word": "madiyan",  "meaning": "lazy",       "tier": "Mild",     "points_lost": 3,  "multiplier": 1},
    {"word": "kozhi",    "meaning": "flirt",       "tier": "Mild",     "points_lost": 3,  "multiplier": 1},
    {"word": "vayadi",   "meaning": "chatterbox",  "tier": "Mild",     "points_lost": 3,  "multiplier": 1},
    {"word": "mandan",   "meaning": "fool",        "tier": "Mild",     "points_lost": 3,  "multiplier": 1},
    {"word": "pottan",   "meaning": "idiot",       "tier": "Mild",     "points_lost": 3,  "multiplier": 1},

    # Tier 2 — Moderate (target loses 10 pts, invoker loses 4 pts)
    {"word": "vattan",   "meaning": "crazy",                  "tier": "Moderate", "points_lost": 5, "multiplier": 2},
    {"word": "oolan",    "meaning": "useless",                "tier": "Moderate", "points_lost": 5, "multiplier": 2},
    {"word": "shasi",    "meaning": "clown/embarrassment",    "tier": "Moderate", "points_lost": 5, "multiplier": 2},
    {"word": "vazha",    "meaning": "useless plant",          "tier": "Moderate", "points_lost": 5, "multiplier": 2},
    {"word": "kumbidi",  "meaning": "fraud",                  "tier": "Moderate", "points_lost": 5, "multiplier": 2},
    {"word": "kalippan", "meaning": "worthless person",       "tier": "Moderate", "points_lost": 5, "multiplier": 2},
    {"word": "durantham", "meaning": "disaster",              "tier": "Moderate", "points_lost": 5, "multiplier": 2},

    # Tier 3 — Severe (target loses 15 pts, invoker loses 6 pts)
    {"word": "thallippoli",    "meaning": "worthless",      "tier": "Severe", "points_lost": 7, "multiplier": 3},
    {"word": "perum kallan",   "meaning": "master thief",   "tier": "Severe", "points_lost": 7, "multiplier": 3},
    {"word": "dushtan",        "meaning": "evil person",    "tier": "Severe", "points_lost": 7, "multiplier": 3},
    {"word": "kuzhappakkaran", "meaning": "troublemaker",   "tier": "Severe", "points_lost": 7, "multiplier": 3},
    {"word": "alavalathi",     "meaning": "vagabond/nuisance", "tier": "Severe", "points_lost": 7, "multiplier": 3},
]

# Severe subset that triggers the 3-strike system (Feature 7).
# Derived from Tier 3 entries in CURSE_WORDS (flat list of words for fast matching).
SEVERE_CURSE_WORDS: list[str] = [
    cw["word"] for cw in CURSE_WORDS if cw["tier"] == "Severe"
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
    "{user}, that's Kochi slang. Wrong city, wrong crowd.",
    "{user}, Kochi energy detected. This is Trivandrum — adjust accordingly.",
    "Strong Kochi accent detected from {user}. The server notes it with mild suspicion.",
    "{user}, that slang doesn't land here. Try again.",
    "{user}, Kochi called — they want their slang back.",
    "{user}, that's Ernakulam talk. We do things differently here.",
    "Kochi slang spotted from {user}. Noted. Unimpressed.",
]

# ---------------------------------------------------------------------------
# Doomed prediction templates (static fallback)
# ---------------------------------------------------------------------------

DOOMED_PREDICTIONS: list[str] = [
    "Hey {user}, the universe clocked that word — expect 45 minutes of peak-hour traffic with no phone charge.",
    "{user}, the cosmos heard you and has scheduled a minor inconvenience: two-hour wait, bus never comes.",
    "{user}, not great language — the stars have noted it. Wallet and dignity, same week, gone.",
    "The universe heard that, {user}. Your next cutlet order will be sold out. Self-inflicted.",
    "{user}, bold word choice. The cosmos has flagged your account. Expect delays.",
    "Noted, {user}. The universe has booked you a broken umbrella on a rainy day. Your words, your problem.",
    "{user}, the stars logged that. Something mildly terrible is now pending in your schedule.",
]

# ---------------------------------------------------------------------------
# Curse-back reply templates (static fallback)
# ---------------------------------------------------------------------------

CURSE_BACK_REPLIES: list[str] = [
    "{user}, the cosmos saw that. Your next commute will be longer than it should be.",
    "{user}, the universe has a ledger. That word just added an entry. Good luck out there.",
    "Noted, {user}. The stars have flagged your account for minor future inconveniences.",
    "{user}, the cosmos heard that. Something slightly annoying is now pending for you.",
    "{user}, bold choice of words. The universe took note. Results incoming.",
]

# ---------------------------------------------------------------------------
# Compliment tier list (used by "chunk @user" command)
# ---------------------------------------------------------------------------

COMPLIMENTS: list[dict] = [
    # Tier 1 — Affectionate & Sweet (5 pts)
    {"word": "muthe",        "meaning": "pearl / dear",         "tier": "Affectionate & Sweet", "points": 5},
    {"word": "chakkara",     "meaning": "sugar / sweetheart",   "tier": "Affectionate & Sweet", "points": 5},
    {"word": "ponnumuthe",   "meaning": "golden pearl",         "tier": "Affectionate & Sweet", "points": 5},
    {"word": "uyir",         "meaning": "life / my everything", "tier": "Affectionate & Sweet", "points": 5},
    {"word": "vavakutty",   "meaning": "darling / dear one",   "tier": "Affectionate & Sweet", "points": 5},
    {"word": "pookie", "meaning": "pookie", "tier": "Adorable & Sweet", "points": 5},
    # Tier 2 — Friendship (10 pts)
    {"word": "chunk",        "meaning": "best friend",          "tier": "Friendship",           "points": 10},
    {"word": "machane",      "meaning": "bro / buddy",          "tier": "Friendship",           "points": 10},
    {"word": "chankidippu",  "meaning": "heartbeat / bestie",   "tier": "Friendship",           "points": 10},
    {"word": "aliyan",       "meaning": "bestie / bro",         "tier": "Friendship",           "points": 10},
    # Tier 3 — Hype & Legend (15 pts)
    {"word": "puli",         "meaning": "tiger / legend",       "tier": "Hype & Legend",        "points": 15},
    {"word": "killadi",      "meaning": "master / legend",      "tier": "Hype & Legend",        "points": 15},
    {"word": "kiduve",       "meaning": "awesome person",       "tier": "Hype & Legend",        "points": 15},
    {"word": "poli",         "meaning": "fire / awesome",       "tier": "Hype & Legend",        "points": 15},
    {"word": "mass",         "meaning": "legendary / swag",     "tier": "Hype & Legend",        "points": 15},
    {"word": "minnal",       "meaning": "lightning / stunning", "tier": "Hype & Legend",        "points": 15},
    {"word": "chakkaramuthu", "meaning": "sweetest",             "tier": "Hype & Legend",        "points": 15},
    # Tier 4 — Respect (15 pts)
    {"word": "karanavar",   "meaning": "wise elder",  "tier": "Respect",    "points": 15},
    {"word": "tharavadi",   "meaning": "noble",        "tier": "Respect",    "points": 15},
    {"word": "kalakki",     "meaning": "rockstar",     "tier": "Hype & Legend", "points": 15},
    # Friendship extras
    {"word": "muthalali",   "meaning": "boss",         "tier": "Friendship", "points": 10},
    # Affectionate extras
    {"word": "minnaram",    "meaning": "shining light","tier": "Affectionate & Sweet", "points": 5},
]


def get_random_compliment() -> dict:
    """Returns a random compliment dict with word, meaning, tier, and points."""
    return random.choice(COMPLIMENTS)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_random_curse_dict() -> dict:
    """Returns a random curse entry dict (with word, tier, points_lost, multiplier)."""
    return random.choice(CURSE_WORDS)


def get_random_curse() -> str:
    """Returns a random curse word string (for passive detection replies)."""
    return random.choice(CURSE_WORDS)["word"]


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
    for entry in CURSE_WORDS:
        word = entry["word"]
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