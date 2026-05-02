# slang_scorer.py — Local dictionary-based Malayalam slang scorer (TVM vs Kochi)
# Zero API cost: pure in-memory token matching against slang_data.json.

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger("navi.slang_scorer")

_SLANG_DATA_PATH = Path("data") / "slang_data.json"

# variation_token → {"points": int, "region": str, "word_ml": str}
_variation_map: dict[str, dict] = {}

_PUNCTUATION_RE = re.compile(r"[^\w\s]")


def load_slang_data(path: Path = _SLANG_DATA_PATH) -> None:
    """Load slang_data.json and build the in-memory variation lookup map.

    Call once during bot on_ready(). Idempotent — safe to call again on reload.
    """
    global _variation_map
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.error("slang_data.json not found at %s — slang scoring disabled.", path)
        return
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse slang_data.json: %s — slang scoring disabled.", exc)
        return

    new_map: dict[str, dict] = {}
    for entry in data.get("slang_dictionary", []):
        meta = {
            "points": entry["points"],
            "region": entry["region"],
            "word_ml": entry.get("word_ml", ""),
        }
        for variation in entry.get("variations", []):
            token = variation.strip().lower()
            if token:
                new_map[token] = meta

    _variation_map = new_map
    logger.info("Slang scorer loaded %d variation tokens from %s.", len(_variation_map), path)


def score_message(content: str) -> int:
    """Return total Boli point delta for a message based on the slang dictionary.

    TVM slang adds points; Kochi slang deducts points.
    Each unique variation token is counted at most once per message.
    Returns 0 if no slang detected or slang data not loaded.
    """
    if not _variation_map:
        return 0

    cleaned = _PUNCTUATION_RE.sub(" ", content.lower())
    tokens = cleaned.split()

    seen_tokens: set[str] = set()
    total_delta = 0
    for token in tokens:
        if token in _variation_map and token not in seen_tokens:
            seen_tokens.add(token)
            total_delta += _variation_map[token]["points"]

    return total_delta


def get_slang_matches(content: str) -> list[dict]:
    """Return list of matched slang entries for debugging/logging.

    Each entry: {"token": str, "points": int, "region": str, "word_ml": str}
    """
    if not _variation_map:
        return []

    cleaned = _PUNCTUATION_RE.sub(" ", content.lower())
    tokens = cleaned.split()

    seen_tokens: set[str] = set()
    matches: list[dict] = []
    for token in tokens:
        if token in _variation_map and token not in seen_tokens:
            seen_tokens.add(token)
            meta = _variation_map[token]
            matches.append({"token": token, **meta})

    return matches
