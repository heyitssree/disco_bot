"""
Lightweight SFW content filter for player-submitted Bamboozle Rules.

Strategy:
  1. Normalise common leet-speak substitutions (3->e, @ ->a, etc.)
  2. Strip punctuation / spaces so "f.u.c.k" is caught
  3. Check the normalised string against a hard-coded word list
  4. Also check the original lowercased string (catches whole-word variations)

This is a best-effort defence, not a bulletproof system.  Server moderation
and Discord's own policies are the authoritative layer.
"""

import re
import unicodedata

# ── Normalisation map ──────────────────────────────────────────────────────────
# Maps leet-speak / homoglyph characters to their plain-ASCII equivalents.
_LEET: dict[str, str] = {
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
    "6": "g", "7": "t", "8": "b", "@": "a", "$": "s",
    "!": "i", "|": "i", "(": "c", "+": "t",
}

# ── Word list ──────────────────────────────────────────────────────────────────
# Slurs, severe profanity, and targeted harassment terms.
# Kept deliberately short — common internet filters do not need to be exhaustive
# here; the goal is to catch obvious bad-faith inputs.
_BLOCKED: frozenset[str] = frozenset([
    # Explicit sexual terms
    "fuck", "fuck", "fuk", "fck", "fvck",
    "shit", "sht",
    "cunt", "cnt",
    "ass", "arse",
    "bitch", "btch",
    "cock", "dick", "penis", "vagina", "pussy",
    "sex", "porn", "nude", "naked",
    "rape", "raped", "raping",
    "dildo", "cum", "cumshot", "jizz",
    "blowjob", "handjob",
    # Slurs — racial, homophobic, ableist
    "nigger", "nigga", "nig",
    "faggot", "fag",
    "dyke",
    "tranny",
    "retard", "retarded",
    "spastic", "spaz",
    "chink", "gook", "wetback", "beaner", "spic",
    "kike", "kyke",
    "cracker",
    # Violence / harassment
    "kill yourself", "kys",
    "die",
    "suicide",
    "murder",
    "lynch",
    # Misc
    "nazi", "heil",
    "piss", "pissing",
    "whore", "slut",
    "bastard",
    "dumbass", "jackass", "asshole", "arsehole",
    "motherfucker", "mf",
])


def _normalise(text: str) -> str:
    """Lower-case, apply leet map, strip non-alpha chars."""
    # Unicode normalise to handle accented lookalikes
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower()
    text = "".join(_LEET.get(ch, ch) for ch in text)
    # Remove everything that isn't a letter or digit so "f.u.c.k" -> "fuck"
    return re.sub(r"[^a-z0-9]", "", text)


def is_clean(text: str) -> bool:
    """Return True if the text passes the SFW filter, False if it should be rejected."""
    lowered = text.lower()
    normalised = _normalise(text)

    for word in _BLOCKED:
        norm_word = _normalise(word)
        # Check normalised (leet-stripped) version
        if norm_word in normalised:
            return False
        # Also check the raw lowercased original (catches multi-word phrases like "kill yourself")
        if word in lowered:
            return False

    return True


def rejection_reason() -> str:
    return (
        "⚠️ That rule was rejected by the content filter. "
        "Keep it SFW — write something the whole server can see!"
    )
