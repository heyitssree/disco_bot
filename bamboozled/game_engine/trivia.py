import asyncio
import html
import json
import logging
import random
from pathlib import Path
from typing import Optional

import aiohttp

from game_engine.constants import SAFE_OPENTDB_CATEGORY_IDS

logger = logging.getLogger(__name__)

_OPENTDB_API = "https://opentdb.com/api.php"
_OPENTDB_TOKEN = "https://opentdb.com/api_token.php"

_EMERGENCY_QUESTIONS = [
    {
        "question": "What is the capital of France?",
        "correct_answer": "Paris",
        "incorrect_answers": ["London", "Berlin", "Madrid"],
        "category": "Geography",
        "difficulty": "easy",
    },
    {
        "question": "How many sides does a hexagon have?",
        "correct_answer": "6",
        "incorrect_answers": ["5", "7", "8"],
        "category": "Mathematics",
        "difficulty": "easy",
    },
    {
        "question": "Which planet is known as the Red Planet?",
        "correct_answer": "Mars",
        "incorrect_answers": ["Venus", "Jupiter", "Saturn"],
        "category": "Science",
        "difficulty": "easy",
    },
    {
        "question": "What is 7 multiplied by 8?",
        "correct_answer": "56",
        "incorrect_answers": ["48", "54", "63"],
        "category": "Mathematics",
        "difficulty": "easy",
    },
    {
        "question": "Who wrote Romeo and Juliet?",
        "correct_answer": "William Shakespeare",
        "incorrect_answers": ["Charles Dickens", "Jane Austen", "Mark Twain"],
        "category": "Literature",
        "difficulty": "easy",
    },
]


def _decode(q: dict) -> dict:
    return {
        "question": html.unescape(q["question"]),
        "correct_answer": html.unescape(q["correct_answer"]),
        "incorrect_answers": [html.unescape(a) for a in q["incorrect_answers"]],
        "category": html.unescape(q.get("category", "Unknown")),
        "difficulty": q.get("difficulty", "medium"),
    }


async def fetch_session_token() -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(_OPENTDB_TOKEN, params={"command": "request"}) as resp:
                data = await resp.json(content_type=None)
                if data.get("response_code") == 0:
                    return data.get("token")
    except Exception as exc:
        logger.warning("Failed to fetch OpenTDB session token: %s", exc)
    return None


async def _reset_token(token: str) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _OPENTDB_TOKEN, params={"command": "reset", "token": token}
            ) as resp:
                data = await resp.json(content_type=None)
                if data.get("response_code") == 0:
                    return data.get("token")
    except Exception as exc:
        logger.warning("Failed to reset OpenTDB token: %s", exc)
    return None


async def fetch_question(
    session_token: Optional[str] = None,
    difficulty: Optional[str] = None,
    category: Optional[int] = None,
) -> tuple[Optional[dict], Optional[str]]:
    """Fetch one question. Returns (question_dict, token). Falls back on failure."""
    params: dict = {
        "amount": 1,
        "type": "multiple",
        "category": category if category is not None else random.choice(SAFE_OPENTDB_CATEGORY_IDS),
    }
    if difficulty:
        params["difficulty"] = difficulty
    if session_token:
        params["token"] = session_token

    async def _try() -> Optional[dict]:
        async with aiohttp.ClientSession() as session:
            async with session.get(_OPENTDB_API, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return None
                return await resp.json(content_type=None)

    data: Optional[dict] = None
    try:
        data = await _try()
    except Exception as exc:
        logger.warning("OpenTDB attempt 1 failed: %s", exc)

    # Token exhausted — reset and update param
    if data and data.get("response_code") == 4 and session_token:
        session_token = await _reset_token(session_token)
        if session_token:
            params["token"] = session_token
        data = None  # force retry

    if not (data and data.get("response_code") == 0):
        await asyncio.sleep(3)
        try:
            data = await _try()
        except Exception as exc:
            logger.warning("OpenTDB attempt 2 failed: %s", exc)
            data = None

    if data and data.get("response_code") == 0 and data.get("results"):
        return _decode(data["results"][0]), session_token

    # Fallback: custom_questions.json
    custom_path = Path(__file__).parent.parent / "data" / "custom_questions.json"
    if custom_path.exists():
        try:
            with open(custom_path, encoding="utf-8") as fh:
                qs = json.load(fh)
            if qs:
                logger.warning("Using custom question fallback.")
                return _decode(random.choice(qs)), session_token
        except Exception as exc:
            logger.warning("Failed to load custom_questions.json: %s", exc)

    # Emergency hardcode
    logger.warning("Using emergency hardcoded question.")
    return _decode(random.choice(_EMERGENCY_QUESTIONS)), session_token


def shuffle_answers(question: dict) -> tuple[list[str], int]:
    """Return (shuffled_answers, correct_index)."""
    answers = [question["correct_answer"]] + list(question["incorrect_answers"])
    random.shuffle(answers)
    return answers, answers.index(question["correct_answer"])
