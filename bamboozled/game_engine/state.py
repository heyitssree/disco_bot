import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from game_engine.constants import MAX_SINGLE_SWING_FIXED, MIST_TURN_DURATION, SAFE_OPENTDB_CATEGORY_IDS, SOMBRERO_EXTRA_PENALTY, STARTING_POINTS, TOTAL_ROUNDS


@dataclass
class GameState:
    channel_id: int
    host_id: int

    players: List[int] = field(default_factory=list)
    player_names: Dict[int, str] = field(default_factory=dict)
    scores: Dict[int, int] = field(default_factory=dict)

    current_round: int = 1
    current_turn_index: int = 0

    active: bool = False
    game_over: bool = False

    golden_pass: Dict[int, bool] = field(default_factory=dict)
    silenced: Dict[int, bool] = field(default_factory=dict)

    sombrero_holder: Optional[int] = None
    sombrero_penalty: int = SOMBRERO_EXTRA_PENALTY

    # Predefined Bamboozle Rule — dict with keys: id, name, description, is_permanent, turns_remaining
    active_bamboozle_rule: Optional[dict] = None

    mist_active: bool = False
    mist_turns_remaining: int = 0
    mist_turn_duration: int = MIST_TURN_DURATION  # overridable by Mist Magnet rule

    session_token: Optional[str] = None

    forfeit_requested: bool = False

    # Per-player consecutive correct answer streak (reset on wrong/timeout)
    consecutive_correct: Dict[int, int] = field(default_factory=dict)
    # Per-player total correct answers this game (for Boli calculation)
    correct_answer_count: Dict[int, int] = field(default_factory=dict)
    # Seconds the active player took to answer this turn (set after answer/timeout)
    answer_time_seconds: float = 0.0

    # Game configuration (set by host at start)
    total_rounds: int = TOTAL_ROUNDS
    question_difficulty: Optional[str] = None   # "easy"/"medium"/"hard"/None for mixed
    question_categories: List[int] = field(default_factory=list)  # empty = all categories

    # Test mode: no Boli points awarded, no stats recorded (admin-toggled per guild)
    test_mode: bool = False

    # Thread ID if game was moved to a Discord thread; None if running in main channel
    thread_id: Optional[int] = None

    # Message ID of the lobby embed posted in the channel (used as the primary registry key)
    lobby_id: Optional[int] = None

    # Timestamp of most-recent player join (used for lobby timeout)
    last_join_time: float = field(default_factory=time.time)

    # Tracks whether each player has used their one Switcheroo this game
    switcheroo_used: Dict[int, bool] = field(default_factory=dict)

    # Shuffled category cycle for question variety when "All Categories" is selected
    category_rotation: List[int] = field(default_factory=list)
    category_rotation_index: int = 0

    def current_player_id(self) -> int:
        return self.players[self.current_turn_index]

    def advance_turn(self) -> bool:
        """Advance to next player, increment round if needed.
        Returns True if a non-permanent Bamboozle Rule just expired."""
        self.current_turn_index += 1
        if self.current_turn_index >= len(self.players):
            self.current_turn_index = 0
            self.current_round += 1

        rule_expired = False
        if self.active_bamboozle_rule is not None and not self.active_bamboozle_rule["is_permanent"]:
            self.active_bamboozle_rule["turns_remaining"] -= 1
            if self.active_bamboozle_rule["turns_remaining"] <= 0:
                self.active_bamboozle_rule = None
                rule_expired = True
        return rule_expired

    def is_solo(self) -> bool:
        return len(self.players) == 1

    def next_player_in_order(self, player_id: int) -> int:
        idx = self.players.index(player_id)
        return self.players[(idx + 1) % len(self.players)]

    def player_display_name(self, player_id: int) -> str:
        name = self.player_names.get(player_id, str(player_id))
        if player_id == self.sombrero_holder:
            name = f"🪅 {name}"
        return name

    def apply_points(self, player_id: int, delta: int, bypass_cap: bool = False) -> int:
        """Apply delta respecting MAX_SINGLE_SWING_FIXED. Returns actual delta applied."""
        if not bypass_cap:
            delta = max(-MAX_SINGLE_SWING_FIXED, min(MAX_SINGLE_SWING_FIXED, delta))
        self.scores[player_id] = self.scores.get(player_id, 0) + delta
        return delta

    def scores_display(self) -> Dict[int, str]:
        if self.mist_active:
            phrases = [
                "unknowable", "somewhere between bad and worse",
                "the mist conceals this", "??", "lost in the fog",
            ]
            return {pid: random.choice(phrases) for pid in self.players}
        return {pid: str(self.scores.get(pid, 0)) for pid in self.players}

    def decrement_mist(self) -> bool:
        """Tick mist down one turn. Returns True if mist just lifted."""
        if self.mist_active:
            self.mist_turns_remaining -= 1
            if self.mist_turns_remaining <= 0:
                self.mist_active = False
                self.mist_turns_remaining = 0
                return True
        return False

    def activate_mist(self):
        self.mist_active = True
        self.mist_turns_remaining = self.mist_turn_duration

    def next_auto_category(self) -> Optional[int]:
        """Return next category from the rotation (used when no specific category is selected)."""
        if not self.category_rotation:
            return None
        cat = self.category_rotation[self.category_rotation_index % len(self.category_rotation)]
        self.category_rotation_index += 1
        return cat

    def pick_question_category(self) -> Optional[int]:
        """Return the category to use for the next question.
        Randomly picks from selected categories, or falls back to the full rotation."""
        if self.question_categories:
            return random.choice(self.question_categories)
        return self.next_auto_category()

    def reset_for_new_game(self) -> None:
        """Reset game to lobby state, keeping players. Call after a game ends."""
        self.scores = {pid: STARTING_POINTS for pid in self.players}
        self.current_round = 1
        self.current_turn_index = 0
        self.active = False
        self.game_over = False
        self.golden_pass = {}
        self.silenced = {}
        self.sombrero_holder = None
        self.sombrero_penalty = SOMBRERO_EXTRA_PENALTY
        self.active_bamboozle_rule = None
        self.mist_active = False
        self.mist_turns_remaining = 0
        self.mist_turn_duration = MIST_TURN_DURATION
        self.session_token = None
        self.forfeit_requested = False
        self.consecutive_correct = {}
        self.correct_answer_count = {}
        self.answer_time_seconds = 0.0
        self.switcheroo_used = {}
        self.category_rotation = []
        self.category_rotation_index = 0
        self.question_categories = []
        self.thread_id = None
        self.lobby_id = None
        self.last_join_time = time.time()
