from dataclasses import dataclass, field
from typing import Dict, List, Optional

from game_engine.constants import MAX_SINGLE_SWING_FIXED, MIST_TURN_DURATION, SOMBRERO_EXTRA_PENALTY


@dataclass
class BamboozleRule:
    text: str
    set_by: int
    turns_remaining: int


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

    bamboozle_rule: Optional[BamboozleRule] = None

    mist_active: bool = False
    mist_turns_remaining: int = 0

    session_token: Optional[str] = None

    # Forfeit flag set by the /forfeit slash command
    forfeit_requested: bool = False

    def current_player_id(self) -> int:
        return self.players[self.current_turn_index]

    def advance_turn(self) -> bool:
        """Advance to next player, increment round if needed.
        Returns True if the Bamboozle Rule just expired."""
        self.current_turn_index += 1
        if self.current_turn_index >= len(self.players):
            self.current_turn_index = 0
            self.current_round += 1

        rule_expired = False
        if self.bamboozle_rule is not None:
            self.bamboozle_rule.turns_remaining -= 1
            if self.bamboozle_rule.turns_remaining <= 0:
                self.bamboozle_rule = None
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
        import random
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
        self.mist_turns_remaining = MIST_TURN_DURATION
