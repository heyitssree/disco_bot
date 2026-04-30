# template_resolver.py — Milestone-based response formatter for Boli Points rewards.
# Selects English, Malayalam reward, or penalty template based on user's boli_points.

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("navi.template_resolver")

_TEMPLATES_PATH = Path("data") / "templates.json"

_REWARD_THRESHOLD = 500   # boli_points >= this → use ml_reward templates
_PENALTY_THRESHOLD = 0    # boli_points < this (i.e. negative) → use en_penalty templates

_templates: dict = {}


def load_templates(path: Path = _TEMPLATES_PATH) -> None:
    """Load templates.json into memory. Call once during bot on_ready()."""
    global _templates
    try:
        with open(path, encoding="utf-8") as f:
            _templates = json.load(f)
    except FileNotFoundError:
        logger.error("templates.json not found at %s — falling back to plain English.", path)
        _templates = {}
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse templates.json: %s — falling back to plain English.", exc)
        _templates = {}
    else:
        logger.info("Template resolver loaded from %s.", path)


def get_template(command: str, boli_points: int, **kwargs: object) -> str:
    """Return the appropriate template string for *command*, formatted with **kwargs**.

    Selection logic:
    - boli_points < 0      → "en_penalty"
    - boli_points >= 500   → "ml_reward"
    - otherwise            → "en"

    Falls back gracefully if the template is missing.
    """
    commands = _templates.get("commands", {})
    variants = commands.get(command, {})

    if boli_points < _PENALTY_THRESHOLD:
        variant_key = "en_penalty"
    elif boli_points >= _REWARD_THRESHOLD:
        variant_key = "ml_reward"
    else:
        variant_key = "en"

    template = variants.get(variant_key) or variants.get("en", "")
    if not template:
        logger.warning("No template found for command=%r variant=%r", command, variant_key)
        return ""

    try:
        return template.format(**kwargs)
    except KeyError as exc:
        logger.warning("Template format error for command=%r: missing key %s", command, exc)
        return template
