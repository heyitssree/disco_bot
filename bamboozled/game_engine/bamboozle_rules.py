from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from game_engine.constants import (
    BAMBOOZLE_HOT_STREAK_BONUS,
    BAMBOOZLE_KARMA_TAX_POINTS,
    BAMBOOZLE_LUCKY_LAST_BONUS,
    BAMBOOZLE_SLOW_BURN_EXTRA_PENALTY,
    BAMBOOZLE_SLOW_BURN_THRESHOLD_SECONDS,
    BAMBOOZLE_SPEED_TAX_PENALTY,
    BAMBOOZLE_SPEED_TAX_THRESHOLD_SECONDS,
    BAMBOOZLE_TIMEOUT_TERROR_COST,
    BAMBOOZLE_UNDERDOG_BOOST_POINTS,
    TIMEOUT_POINTS,
    WRONG_ANSWER_POINTS,
)

if TYPE_CHECKING:
    from game_engine.state import GameState


@dataclass
class BamboozleRuleDef:
    id: int
    name: str
    description: str
    is_permanent: bool


BAMBOOZLE_RULES: list[BamboozleRuleDef] = [
    BamboozleRuleDef(1, "Speed Tax",
        "Correct in under 5s? Lose 25 pts anyway. Slow down!", False),
    BamboozleRuleDef(2, "Slow Burn",
        "Take 20+ seconds then get it wrong or timeout? Extra -50 pts.", False),
    BamboozleRuleDef(3, "Double Jeopardy",
        "Wrong answers cost -100 pts instead of -50 for ALL players.", False),
    BamboozleRuleDef(4, "Hot Streak",
        "Two correct answers in a row earns +75 bonus pts.", False),
    BamboozleRuleDef(5, "Karma Tax",
        "First place loses 50 pts at the start of their turn.", False),
    BamboozleRuleDef(6, "Underdog Boost",
        "Last place gains 50 pts at the start of their turn.", False),
    BamboozleRuleDef(7, "Timeout Terror",
        "Timeouts cost -200 pts instead of -100 for everyone.", False),
    BamboozleRuleDef(8, "Lucky Last",
        "Last in turn order to answer correctly earns +100 bonus.", False),
    BamboozleRuleDef(9, "Streak Breaker",
        "First place drawing a Chance Card draws a Wango Card instead.", False),
    BamboozleRuleDef(10, "Mist Magnet",
        "PERMANENT: Mist duration increases to 4 turns for the game.", True),
    BamboozleRuleDef(11, "Sombrero Curse",
        "PERMANENT: Sombrero penalty rises to 50 pts per wrong answer.", True),
]


def get_rule(rule_id: int) -> Optional[BamboozleRuleDef]:
    for rule in BAMBOOZLE_RULES:
        if rule.id == rule_id:
            return rule
    return None


def make_active_rule(rule_id: int, num_players: int) -> dict:
    rule = get_rule(rule_id)
    if rule is None:
        raise ValueError(f"Unknown rule id: {rule_id}")
    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "is_permanent": rule.is_permanent,
        "turns_remaining": num_players if not rule.is_permanent else -1,
    }


def get_effective_wrong_penalty(active_rule: Optional[dict]) -> int:
    """Return the base wrong-answer penalty, doubled to -100 under Double Jeopardy (Rule 3)."""
    if active_rule and active_rule["id"] == 3:
        return -100
    return WRONG_ANSWER_POINTS


def get_effective_timeout_penalty(active_rule: Optional[dict]) -> int:
    """Return the timeout penalty, increased to -200 under Timeout Terror (Rule 7)."""
    if active_rule and active_rule["id"] == 7:
        return -BAMBOOZLE_TIMEOUT_TERROR_COST
    return TIMEOUT_POINTS


def apply_on_set(rule: dict, game: "GameState") -> Optional[str]:
    """Apply permanent rule effects immediately when selected. Returns announcement or None."""
    rule_id = rule["id"]
    if rule_id == 10:  # Mist Magnet
        game.mist_turn_duration = 4
        return "🌫️ The Mist now lasts **4 turns** for the rest of the game."
    if rule_id == 11:  # Sombrero Curse
        game.sombrero_penalty = 50
        return "🪅 The Sombrero penalty is now **50 pts** per wrong answer for the rest of the game."
    return None


def apply_start_of_turn(rule: dict, game: "GameState", player_id: int) -> Optional[str]:
    """Apply rule effects at the start of a player's turn. Returns message or None."""
    rule_id = rule["id"]
    pname = game.player_display_name(player_id)

    if rule_id == 5:  # Karma Tax
        max_score = max(game.scores.values(), default=0)
        if game.scores.get(player_id, 0) >= max_score:
            game.apply_points(player_id, -BAMBOOZLE_KARMA_TAX_POINTS)
            return (
                f"💸 **KARMA TAX!** {pname} is in first place — "
                f"loses **{BAMBOOZLE_KARMA_TAX_POINTS} pts** before the question."
            )
    elif rule_id == 6:  # Underdog Boost
        min_score = min(game.scores.values(), default=0)
        if game.scores.get(player_id, 0) <= min_score:
            game.apply_points(player_id, BAMBOOZLE_UNDERDOG_BOOST_POINTS)
            return (
                f"💪 **UNDERDOG BOOST!** {pname} is in last place — "
                f"gains **{BAMBOOZLE_UNDERDOG_BOOST_POINTS} pts** before the question."
            )
    return None


def apply_after_correct(rule: dict, game: "GameState", player_id: int) -> Optional[str]:
    """Apply rule effects after a correct answer is scored. Returns message or None.

    consecutive_correct must already be incremented before calling this.
    """
    rule_id = rule["id"]
    pname = game.player_display_name(player_id)

    if rule_id == 1:  # Speed Tax
        if game.answer_time_seconds < BAMBOOZLE_SPEED_TAX_THRESHOLD_SECONDS:
            game.apply_points(player_id, -BAMBOOZLE_SPEED_TAX_PENALTY)
            return (
                f"⚡ **SPEED TAX!** {pname} answered in {game.answer_time_seconds:.1f}s — "
                f"too fast! Loses **{BAMBOOZLE_SPEED_TAX_PENALTY} pts**."
            )
    elif rule_id == 4:  # Hot Streak
        streak = game.consecutive_correct.get(player_id, 0)
        if streak >= 2:
            game.apply_points(player_id, BAMBOOZLE_HOT_STREAK_BONUS)
            return (
                f"🔥 **HOT STREAK!** {pname} is on a **{streak}x streak** — "
                f"earns **+{BAMBOOZLE_HOT_STREAK_BONUS} bonus pts**!"
            )
    elif rule_id == 8:  # Lucky Last
        if game.current_turn_index == len(game.players) - 1:
            game.apply_points(player_id, BAMBOOZLE_LUCKY_LAST_BONUS)
            return (
                f"🍀 **LUCKY LAST!** {pname} is the final player in this round — "
                f"earns **+{BAMBOOZLE_LUCKY_LAST_BONUS} bonus pts**!"
            )
    return None


def apply_after_wrong_or_timeout(rule: dict, game: "GameState", player_id: int) -> Optional[str]:
    """Apply Slow Burn (Rule 2) after a wrong answer or timeout. Returns message or None."""
    if rule["id"] == 2:  # Slow Burn
        if game.answer_time_seconds > BAMBOOZLE_SLOW_BURN_THRESHOLD_SECONDS:
            game.apply_points(player_id, -BAMBOOZLE_SLOW_BURN_EXTRA_PENALTY)
            pname = game.player_display_name(player_id)
            return (
                f"🐢 **SLOW BURN!** {pname} took {game.answer_time_seconds:.1f}s — "
                f"extra **{BAMBOOZLE_SLOW_BURN_EXTRA_PENALTY} pts** deducted."
            )
    return None
