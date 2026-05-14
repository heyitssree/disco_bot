"""Main game cog: all slash commands, UI components, and game-flow logic."""
import asyncio
import logging
import random
import time
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.game_config_view import GameConfigView
from db.database import (
    get_leaderboard,
    get_player_stats,
    get_test_mode,
    register_active_channel,
    reset_all_scores,
    save_game_result,
    set_test_mode,
    unregister_active_channel,
    update_player_stats,
    upsert_player,
)
from db.navi_bridge import award_boli
from game_engine import commentary as cmnt
from game_engine.cards import (
    CHANCE_CARD_FLAVOUR,
    WANGO_CARD_FLAVOUR,
    ChanceCard,
    WangoCard,
    draw_chance_card,
    draw_wango_card,
)
from game_engine.bamboozle_rules import (
    BAMBOOZLE_RULES,
    apply_after_correct,
    apply_after_wrong_or_timeout,
    apply_on_set,
    apply_start_of_turn,
    get_effective_timeout_penalty,
    get_effective_wrong_penalty,
    make_active_rule,
)
from game_engine.constants import (
    BAMBOOZLE_RULE_INPUT_TIMEOUT_SECONDS,
    BAMBOOZLE_TIMEOUT_TERROR_COST,
    BONUS_ROUND_POINTS,
    CORRECT_ANSWER_POINTS,
    DIFFICULTY_MULTIPLIER,
    DOUBLE_DOWN_BONUS,
    DOUBLE_DOWN_PENALTY,
    DRAW_CARD_TIMEOUT_SECONDS,
    GIFT_STEAL_AMOUNT,
    GOLDEN_MONKEY_BELLY,
    GOLDEN_MONKEY_TAIL,
    GOLDEN_MONKEY_TIMEOUT_SECONDS,
    LUCKY_LLAMA_BONUS,
    MIST_TURN_DURATION,
    NAVI_DB_PATH,
    QUESTION_TIMEOUT_SECONDS,
    REVERSE_UNO_PENALTY,
    SOMBRERO_EXTRA_PENALTY,
    STARTING_POINTS,
    SWITCHEROO_PICK_TIMEOUT_SECONDS,
    TAX_MINIMUM,
    TAX_RATE,
    TIMEOUT_POINTS,
    TOTAL_ROUNDS,
    WANGO_AGAIN_WHEEL_DEPTH_LIMIT,
    DOUBLE_WANGO_CHAIN_LIMIT,
    WRONG_ANSWER_POINTS,
    SAFE_OPENTDB_CATEGORY_IDS,
)
from game_engine.state import GameState
from game_engine.trivia import fetch_question, fetch_session_token, shuffle_answers
from game_engine.wheel import (
    SPIN_SUSPENSE,
    WHEEL_FLAVOUR,
    WheelSegment,
    monkey_choice_segment,
    spin_wheel,
)

logger = logging.getLogger(__name__)

# Primary game registry: keyed by lobby message ID (set when lobby is posted) or by thread ID
# (added when a game moves into a thread).  Multiple concurrent lobbies/games per channel are
# supported because the keys are message/thread IDs, not channel IDs.
_active_games: dict[int, GameState] = {}

# channel_id → list of all GameState objects active in that channel (lobbies + running games).
# Used for player-based lookup when a command is issued outside of a game thread.
_channel_games: dict[int, list[GameState]] = {}

# Lobby message IDs that currently have an active lobby-timeout watcher task.
_lobby_watchers: set[int] = set()

_ANSWER_LABELS = ["A", "B", "C", "D"]


# ─────────────────────────────────────────────────────────────
# UI Components
# ─────────────────────────────────────────────────────────────


class AnswerView(discord.ui.View):
    def __init__(self, answers: list[str], correct_idx: int, active_player_id: int):
        super().__init__(timeout=float(QUESTION_TIMEOUT_SECONDS))
        self.correct_idx = correct_idx
        self.active_player_id = active_player_id
        self.chosen_idx: Optional[int] = None
        self.timed_out = False

        for i, (label, answer) in enumerate(zip(_ANSWER_LABELS, answers)):
            btn = discord.ui.Button(
                label=f"{label}: {answer[:75]}",
                style=discord.ButtonStyle.primary,
                custom_id=f"ans_{i}",
            )
            btn.callback = self._make_cb(i)
            self.add_item(btn)

    def _make_cb(self, idx: int):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.active_player_id:
                await interaction.response.send_message(
                    "⚠️ It's not your turn! Eyes on your own paper!", ephemeral=True
                )
                return
            self.chosen_idx = idx
            self.stop()
            await interaction.response.defer()

        return cb

    async def on_timeout(self):
        self.timed_out = True
        self.stop()


class PlayerSelectView(discord.ui.View):
    """Generic single-player dropdown (Switcheroo / Gift), with optional skip button."""

    def __init__(
        self,
        game: GameState,
        active_player_id: int,
        placeholder: str,
        timeout: float,
        allow_skip: bool = False,
        skip_label: str = "No thanks, skip",
    ):
        super().__init__(timeout=timeout)
        self.active_player_id = active_player_id
        self.target_id: Optional[int] = None
        self.timed_out = False
        self.skipped = False

        options = [
            discord.SelectOption(
                label=game.player_display_name(pid)[:100],
                value=str(pid),
            )
            for pid in game.players
            if pid != active_player_id
        ]
        sel = discord.ui.Select(placeholder=placeholder, options=options)
        sel.callback = self._cb
        self.add_item(sel)

        if allow_skip:
            skip_btn = discord.ui.Button(label=skip_label, style=discord.ButtonStyle.secondary)
            skip_btn.callback = self._skip_cb
            self.add_item(skip_btn)

    async def _cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.active_player_id:
            await interaction.response.send_message(
                "⚠️ That's not your choice to make!", ephemeral=True
            )
            return
        self.target_id = int(interaction.data["values"][0])
        self.stop()
        await interaction.response.defer()

    async def _skip_cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.active_player_id:
            await interaction.response.send_message(
                "⚠️ That's not your choice to make!", ephemeral=True
            )
            return
        self.skipped = True
        self.stop()
        await interaction.response.defer()

    async def on_timeout(self):
        self.timed_out = True
        self.stop()


class DrawCardView(discord.ui.View):
    """Single button the active player clicks to draw a card or spin the wheel.
    Auto-proceeds after DRAW_CARD_TIMEOUT_SECONDS if ignored."""

    def __init__(self, active_player_id: int, label: str):
        super().__init__(timeout=float(DRAW_CARD_TIMEOUT_SECONDS))
        self.active_player_id = active_player_id
        self.clicked = False

        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
        btn.callback = self._cb
        self.add_item(btn)

    async def _cb(self, interaction: discord.Interaction):
        if interaction.user.id != self.active_player_id:
            await interaction.response.send_message(
                "⚠️ Hands off! That's not your card.", ephemeral=True
            )
            return
        self.clicked = True
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

    async def on_timeout(self):
        self.stop()


class BamboozleRuleTriggerButton(discord.ui.View):
    """Public button that triggers an ephemeral Select Menu for rule selection."""

    def __init__(self, active_player_id: int):
        super().__init__(timeout=float(BAMBOOZLE_RULE_INPUT_TIMEOUT_SECONDS) + 10)
        self.active_player_id = active_player_id
        self.selected_rule_id: Optional[int] = None
        self.done = False
        self._activated = False

    @discord.ui.button(label="⚖️ Choose the Law!", style=discord.ButtonStyle.primary)
    async def choose_law(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.active_player_id:
            await interaction.response.send_message(
                "⚠️ This isn't your law to choose!", ephemeral=True
            )
            return
        if self._activated:
            await interaction.response.send_message(
                "⚠️ You're already choosing!", ephemeral=True
            )
            return
        self._activated = True

        options = [
            discord.SelectOption(
                label=rule.name,
                value=str(rule.id),
                description=rule.description[:100],
            )
            for rule in BAMBOOZLE_RULES
        ]
        select = discord.ui.Select(
            placeholder="⚖️ Choose the new Law of the Land...",
            options=options,
        )
        inner_view = discord.ui.View(timeout=float(BAMBOOZLE_RULE_INPUT_TIMEOUT_SECONDS))
        inner_result: dict = {"rule_id": None}

        async def on_select(sel_interaction: discord.Interaction):
            if sel_interaction.user.id != self.active_player_id:
                await sel_interaction.response.send_message("Not yours!", ephemeral=True)
                return
            inner_result["rule_id"] = int(sel_interaction.data["values"][0])
            inner_view.stop()
            await sel_interaction.response.defer()

        select.callback = on_select
        inner_view.add_item(select)

        await interaction.response.send_message(
            "⚖️ **Choose the new Law of the Land!** You have 60 seconds.",
            view=inner_view,
            ephemeral=True,
        )
        await inner_view.wait()

        if inner_result["rule_id"] is not None:
            self.selected_rule_id = inner_result["rule_id"]
            self.done = True
            self.stop()

    async def on_timeout(self):
        self.stop()


class GoldenMonkeyView(discord.ui.View):
    def __init__(self, active_player_id: int):
        super().__init__(timeout=float(GOLDEN_MONKEY_TIMEOUT_SECONDS))
        self.active_player_id = active_player_id
        self.choice: Optional[str] = None
        self.timed_out = False

    @discord.ui.button(label="🫃 BELLY", style=discord.ButtonStyle.success, custom_id="monkey_belly")
    async def belly(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.active_player_id:
            await interaction.response.send_message(
                "⚠️ This monkey isn't yours!", ephemeral=True
            )
            return
        self.choice = "belly"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="🐒 TAIL", style=discord.ButtonStyle.danger, custom_id="monkey_tail")
    async def tail(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.active_player_id:
            await interaction.response.send_message(
                "⚠️ This monkey isn't yours!", ephemeral=True
            )
            return
        self.choice = "tail"
        self.stop()
        await interaction.response.defer()

    async def on_timeout(self):
        self.timed_out = True
        self.stop()


# ─────────────────────────────────────────────────────────────
# Game Controls View (host-only buttons pinned in the game thread)
# ─────────────────────────────────────────────────────────────


class GameControlsView(discord.ui.View):
    """Small view pinned to the first message of the game thread.
    Gives the host a visible button to force-end the game mid-run."""

    def __init__(self, game: "GameState", cog: "BamboozledCog"):
        super().__init__(timeout=None)
        self.game = game
        self.cog = cog

    @discord.ui.button(label="🛑 End Game", style=discord.ButtonStyle.danger, row=0)
    async def end_game_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        game = self.game
        if interaction.user.id != game.host_id:
            await interaction.response.send_message(
                "⚠️ Only the host can force-end the game!", ephemeral=True
            )
            return
        if not game.active:
            await interaction.response.send_message(
                "⚠️ No game is running.", ephemeral=True
            )
            return
        game.active = False
        self.stop()
        # Respond immediately — cleanup (which sleeps 2s for thread archive) runs in background
        await interaction.response.edit_message(view=None)
        await interaction.followup.send(
            "🛑 **GAME ENDED EARLY.** The producer pulls the plug. No results saved. "
            "The audience leaves in stunned silence."
        )
        asyncio.create_task(self.cog._cleanup_game(game))


# ─────────────────────────────────────────────────────────────
# Lobby View (button-based join/leave/start/disband)
# ─────────────────────────────────────────────────────────────


class LobbyView(discord.ui.View):
    """Persistent view attached to the lobby embed message.

    Buttons are visible to everyone; auth checks run on click.
    The view is stopped (buttons disabled) when the game starts or the lobby is disbanded.
    """

    def __init__(self, game: "GameState", cog: "BamboozledCog"):
        super().__init__(timeout=None)
        self.game = game
        self.cog = cog

    # ── Embed builder ────────────────────────────────────────

    def lobby_embed(self) -> discord.Embed:
        game = self.game
        if game.active:
            embed = discord.Embed(
                title="🎬 Bamboozled! — Game in Progress",
                color=discord.Color.red(),
            )
            roster = "\n".join(f"• {game.player_names[pid]}" for pid in game.players)
            embed.description = f"🎮 A game is running! Check the game thread.\n\n**Players:**\n{roster}"
        else:
            embed = discord.Embed(
                title="🎬 Bamboozled! — Lobby Open",
                color=discord.Color.green(),
            )
            if game.players:
                roster = "\n".join(f"• {game.player_names[pid]}" for pid in game.players)
                host_name = game.player_names.get(game.host_id, "Unknown")
                embed.description = (
                    f"**Players ({len(game.players)}/6):**\n{roster}\n\n"
                    f"👑 Host: **{host_name}**\n\n"
                    f"Press **Join** to join or **Leave** to exit. "
                    f"The host can **Start** or **End** the lobby.\n"
                    f"⏰ *Auto-closes after 10 minutes of inactivity.*"
                )
            else:
                embed.description = "Lobby is empty."
        return embed

    # ── Helpers ───────────────────────────────────────────────

    async def _refresh(self, interaction: discord.Interaction) -> None:
        """Edit the lobby message in-place with the updated embed."""
        try:
            await interaction.message.edit(embed=self.lobby_embed(), view=self)
        except Exception:
            pass

    # ── Buttons ───────────────────────────────────────────────

    @discord.ui.button(label="🎮 Join", style=discord.ButtonStyle.success, row=0)
    async def join_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        game = self.game
        if game.active:
            await interaction.response.send_message(
                "⚠️ A game is already running in this lobby!", ephemeral=True
            )
            return
        if interaction.user.id in game.players:
            await interaction.response.send_message(
                "⚠️ You're already in this lobby!", ephemeral=True
            )
            return
        if len(game.players) >= 6:
            await interaction.response.send_message(
                "⚠️ This lobby is full (6 players max)!", ephemeral=True
            )
            return

        game.players.append(interaction.user.id)
        game.player_names[interaction.user.id] = interaction.user.display_name
        game.scores[interaction.user.id] = STARTING_POINTS
        game.last_join_time = time.time()
        await upsert_player(str(interaction.user.id), interaction.user.display_name)

        await interaction.response.edit_message(embed=self.lobby_embed(), view=self)
        await interaction.followup.send(
            f"🎬 **{interaction.user.display_name}** joined the lobby! "
            f"({len(game.players)}/6 players)"
        )

    @discord.ui.button(label="👋 Leave", style=discord.ButtonStyle.secondary, row=0)
    async def leave_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        game = self.game
        if interaction.user.id not in game.players:
            await interaction.response.send_message(
                "⚠️ You're not in this lobby.", ephemeral=True
            )
            return
        if game.active:
            await interaction.response.send_message(
                "⚠️ Game is running. Use `/bamboozled forfeit` or ask the host "
                "to use `/bamboozled endgame`.",
                ephemeral=True,
            )
            return

        pname = interaction.user.display_name
        uid = interaction.user.id
        game.players.remove(uid)
        game.player_names.pop(uid, None)
        game.scores.pop(uid, None)

        if not game.players:
            await self.cog._cleanup_game(game)
            self.stop()
            await interaction.response.edit_message(
                content="🛑 **Lobby closed** — all players left.",
                embed=None,
                view=None,
            )
            return

        host_msg = ""
        if uid == game.host_id:
            game.host_id = game.players[0]
            host_msg = f" **{game.player_names[game.host_id]}** is now the host!"

        await interaction.response.edit_message(embed=self.lobby_embed(), view=self)
        await interaction.followup.send(f"👋 **{pname}** left the lobby.{host_msg}")

    @discord.ui.button(label="▶️ Start", style=discord.ButtonStyle.primary, row=1)
    async def start_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        game = self.game
        if interaction.user.id != game.host_id:
            await interaction.response.send_message(
                "⚠️ Only the host can start the game!", ephemeral=True
            )
            return
        if game.active:
            await interaction.response.send_message(
                "⚠️ A game is already running!", ephemeral=True
            )
            return
        if not game.players:
            await interaction.response.send_message(
                "⚠️ No players in the lobby!", ephemeral=True
            )
            return

        config_view = GameConfigView()
        await interaction.response.send_message(
            "⚙️ **Configure your game!** Select your settings, then click **Confirm & Start**.\n"
            "You have 30 seconds — defaults apply if you don't confirm.",
            view=config_view,
            ephemeral=True,
        )
        await config_view.wait()

        game.total_rounds = config_view.total_rounds
        game.question_difficulty = config_view.question_difficulty
        game.question_categories = config_view.question_categories

        if interaction.guild:
            game.test_mode = await get_test_mode(str(interaction.guild.id))

        game.active = True
        game.session_token = await fetch_session_token()
        await register_active_channel(str(game.channel_id))

        # Disable lobby view buttons and update embed
        self.stop()
        try:
            await interaction.message.edit(embed=self.lobby_embed(), view=None)
        except Exception:
            pass

        channel = interaction.channel
        roster = "\n".join(
            f"{i + 1}. {game.player_names[pid]}" for i, pid in enumerate(game.players)
        )
        test_note = (
            "\n\n⚠️ **TEST MODE ACTIVE** — no Boli points or stats will be recorded."
            if game.test_mode
            else ""
        )
        announce_text = (
            f"🎬🎉 **BAMBOOZLED BEGINS!** 🎉🎬\n\n"
            f"**{len(game.players)} player(s) take the stage:**\n{roster}\n\n"
            f"Everyone starts with **{STARTING_POINTS:,} points**. "
            f"**Settings: {config_view.config_summary()}**\n\n"
            f"*The studio audience goes absolutely FERAL...*{test_note}"
        )

        game_channel = channel
        try:
            announce_msg = await channel.send(announce_text)
            thread = await announce_msg.create_thread(
                name=f"🎬 Bamboozled! — {datetime.now().strftime('%b %d %I:%M%p')}",
                auto_archive_duration=60,
            )
            game.thread_id = thread.id
            _active_games[thread.id] = game
            game_channel = thread
            await thread.send(
                "🎮 **The game is running in this thread!** "
                "Use `/bamboozled forfeit` to skip your turn or `/bamboozled scores` to check scores.",
                view=GameControlsView(game, self.cog),
            )
        except discord.Forbidden:
            await channel.send(
                "⚠️ I don't have permission to create threads — running in this channel instead."
            )
        except Exception as exc:
            logger.warning("Failed to create game thread: %s", exc)

        asyncio.create_task(self.cog._run_game(game_channel, game))

    @discord.ui.button(label="🛑 End Lobby", style=discord.ButtonStyle.danger, row=1)
    async def end_lobby_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        game = self.game
        if interaction.user.id != game.host_id:
            await interaction.response.send_message(
                "⚠️ Only the host can end the lobby!", ephemeral=True
            )
            return
        if game.active:
            await interaction.response.send_message(
                "⚠️ A game is running. Use `/bamboozled endgame` to force-end it.",
                ephemeral=True,
            )
            return

        await self.cog._cleanup_game(game)
        self.stop()
        await interaction.response.edit_message(
            content="🛑 **Lobby disbanded.** See you next time!",
            embed=None,
            view=None,
        )


# ─────────────────────────────────────────────────────────────
# Boli Points helpers
# ─────────────────────────────────────────────────────────────

def calculate_boli_award(
    player_id: int,
    game: "GameState",
    correct_answers: dict,
    winner_id: int,
    sorted_players: list,
) -> tuple:
    """Returns (boli_amount, reason_string). Amount can be negative."""
    dm = DIFFICULTY_MULTIPLIER.get(game.question_difficulty, 1.0)

    participation = round(20 * dm)
    correct_count = correct_answers.get(player_id, 0)
    correct_reward = correct_count * 3

    position = sorted_players.index(player_id) if player_id in sorted_players else len(sorted_players)
    if player_id == winner_id:
        placement_bonus = round(50 * dm)
        placement_label = "winner"
    elif position == 1 and len(game.players) >= 3:
        placement_bonus = round(20 * dm)
        placement_label = "2nd place"
    elif position == 2 and len(game.players) >= 5:
        placement_bonus = round(10 * dm)
        placement_label = "3rd place"
    else:
        placement_bonus = 0
        placement_label = f"{position + 1}th place"

    final_score = game.scores.get(player_id, 0)
    score_bonus = max(0, final_score) // 50

    penalty = 0
    if final_score < 0:
        penalty = min(0, final_score // 100)
        penalty = max(penalty, -20)

    total = participation + correct_reward + placement_bonus + score_bonus + penalty
    diff_str = game.question_difficulty.title() if game.question_difficulty else "Mixed"
    reason = (
        f"Bamboozled: {placement_label}, {correct_count} correct, "
        f"{diff_str}, score {final_score}"
    )
    return total, reason


# ─────────────────────────────────────────────────────────────
# Cog
# ─────────────────────────────────────────────────────────────


class BamboozledCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    bamboozled = app_commands.Group(name="bamboozled", description="Play Bamboozled!")

    # ── /bamboozled join ─────────────────────────────────────

    @bamboozled.command(name="join", description="Create a new Bamboozled lobby!")
    async def join(self, interaction: discord.Interaction):
        cid = interaction.channel_id

        # Prevent a user from being in two lobbies in the same channel
        for g in _channel_games.get(cid, []):
            if interaction.user.id in g.players:
                await interaction.response.send_message(
                    "⚠️ You're already in a lobby in this channel! "
                    "Use the lobby message buttons to manage your spot.",
                    ephemeral=True,
                )
                return

        game = GameState(channel_id=cid, host_id=interaction.user.id)
        game.players.append(interaction.user.id)
        game.player_names[interaction.user.id] = interaction.user.display_name
        game.scores[interaction.user.id] = STARTING_POINTS

        await upsert_player(str(interaction.user.id), interaction.user.display_name)

        # Post the lobby embed with interactive buttons
        view = LobbyView(game, self)
        msg = await interaction.channel.send(embed=view.lobby_embed(), view=view)
        game.lobby_id = msg.id
        _active_games[msg.id] = game
        _channel_games.setdefault(cid, []).append(game)

        await interaction.response.send_message(
            f"🎬 **Lobby created!** Others can click **Join** on the message above.",
            ephemeral=True,
        )

        asyncio.create_task(self._lobby_timeout_watch(game))
        _lobby_watchers.add(game.lobby_id)

    # ── /bamboozled leave ────────────────────────────────────

    @bamboozled.command(name="leave", description="Leave the lobby before the game starts.")
    async def leave(self, interaction: discord.Interaction):
        game = self._find_player_game(interaction.channel_id, interaction.user.id)
        if not game:
            await interaction.response.send_message(
                "⚠️ You're not in any lobby here.", ephemeral=True
            )
            return
        if game.active:
            await interaction.response.send_message(
                "⚠️ The game is already running! Use `/bamboozled forfeit` to skip your turn, "
                "or ask the host to use `/bamboozled endgame`.",
                ephemeral=True,
            )
            return
        # Delegate to the lobby button logic by directly mutating state
        pname = interaction.user.display_name
        uid = interaction.user.id
        game.players.remove(uid)
        game.player_names.pop(uid, None)
        game.scores.pop(uid, None)

        if not game.players:
            await self._cleanup_game(game)
            await interaction.response.send_message(
                f"👋 **{pname}** left. No players remaining — lobby closed."
            )
            return

        host_msg = ""
        if uid == game.host_id:
            game.host_id = game.players[0]
            host_msg = f" **{game.player_names[game.host_id]}** is now the host!"

        # Refresh the lobby embed if we can find the lobby message
        await self._refresh_lobby_embed(game)
        await interaction.response.send_message(f"👋 **{pname}** left the lobby.{host_msg}")

    # ── /bamboozled start ────────────────────────────────────

    @bamboozled.command(name="start", description="Start the game (host only). Prefer the lobby Start button.")
    async def start(self, interaction: discord.Interaction):
        game = self._find_host_game(interaction.channel_id, interaction.user.id)
        if not game:
            await interaction.response.send_message(
                "⚠️ No lobby found where you are the host. Use `/bamboozled join` first!",
                ephemeral=True,
            )
            return
        if game.active:
            await interaction.response.send_message(
                "⚠️ The game is already running!", ephemeral=True
            )
            return
        # Redirect to the lobby Start button flow — avoids duplicating the thread-creation logic
        await interaction.response.send_message(
            "⚙️ Use the **▶️ Start** button on the lobby message above to configure and start the game.",
            ephemeral=True,
        )

    # ── /bamboozled scores ───────────────────────────────────

    @bamboozled.command(name="scores", description="Check current scores (obfuscated during Mist).")
    async def scores(self, interaction: discord.Interaction):
        game = self._find_player_game(interaction.channel_id, interaction.user.id)
        if not game or not game.active:
            await interaction.response.send_message(
                "⚠️ No active game found for you in this channel.", ephemeral=True
            )
            return
        embed = self._scores_embed(game)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /bamboozled leaderboard ──────────────────────────────

    @bamboozled.command(name="leaderboard", description="All-time Bamboozled win leaderboard.")
    async def leaderboard(self, interaction: discord.Interaction):
        rows = await get_leaderboard()
        if not rows:
            await interaction.response.send_message(
                "No games on record yet!", ephemeral=True
            )
            return
        embed = discord.Embed(title="🏆 BAMBOOZLED ALL-TIME LEADERBOARD", color=discord.Color.gold())
        for i, (username, wins, played, pts) in enumerate(rows, 1):
            embed.add_field(
                name=f"{i}. {username}",
                value=f"🏆 {wins} wins · 🎮 {played} games · ⭐ {pts:,} pts earned",
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    # ── /bamboozled stats ────────────────────────────────────

    @bamboozled.command(name="stats", description="View a player's all-time stats.")
    @app_commands.describe(user="The player to look up")
    async def stats(self, interaction: discord.Interaction, user: discord.Member):
        row = await get_player_stats(str(user.id))
        if not row:
            await interaction.response.send_message(
                f"No stats found for {user.display_name}.", ephemeral=True
            )
            return
        username, played, wins, pts = row
        win_rate = f"{wins / played * 100:.1f}%" if played > 0 else "N/A"
        embed = discord.Embed(title=f"📊 Stats — {username}", color=discord.Color.blue())
        embed.add_field(name="Games Played", value=str(played), inline=True)
        embed.add_field(name="Games Won", value=str(wins), inline=True)
        embed.add_field(name="Win Rate", value=win_rate, inline=True)
        embed.add_field(name="Total Points Earned", value=f"{pts:,}", inline=True)
        await interaction.response.send_message(embed=embed)

    # ── /bamboozled reset_scores (admin only) ───────────────

    @bamboozled.command(
        name="reset_scores",
        description="[Admin] Wipe all Bamboozled player stats and game history.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def reset_scores(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        affected = await reset_all_scores()
        await interaction.followup.send(
            f"✅ All Bamboozled scores have been reset. {affected} player record(s) cleared.",
            ephemeral=True,
        )

    @reset_scores.error
    async def reset_scores_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "⛔ Only server admins can reset scores.", ephemeral=True
            )
        else:
            raise error

    # ── /bamboozled forfeit ──────────────────────────────────

    @bamboozled.command(name="forfeit", description="Forfeit your current turn (treated as timeout).")
    async def forfeit(self, interaction: discord.Interaction):
        game = self._find_player_game(interaction.channel_id, interaction.user.id)
        if not game or not game.active:
            await interaction.response.send_message(
                "⚠️ No active game found for you in this channel.", ephemeral=True
            )
            return
        if interaction.user.id != game.current_player_id():
            await interaction.response.send_message(
                "⚠️ It's not your turn!", ephemeral=True
            )
            return
        game.forfeit_requested = True
        await interaction.response.send_message(
            f"🏳️ **{game.player_display_name(interaction.user.id)}** forfeits their turn!",
        )

    # ── /bamboozled endgame ──────────────────────────────────

    @bamboozled.command(name="endgame", description="Force-end the current game or disband the lobby (host only).")
    async def endgame(self, interaction: discord.Interaction):
        game = self._find_host_game(interaction.channel_id, interaction.user.id)
        if not game:
            await interaction.response.send_message(
                "⚠️ No active game or lobby found where you are the host.", ephemeral=True
            )
            return
        was_active = game.active
        game.active = False
        await self._cleanup_game(game)
        if was_active:
            await interaction.response.send_message(
                "🛑 **GAME ENDED EARLY.** The producer pulls the plug. No results saved. "
                "The audience leaves in stunned silence."
            )
        else:
            await interaction.response.send_message(
                "🛑 **LOBBY DISBANDED.** The show is cancelled before it began. Everyone go home."
            )

    # ── /bamboozled testmode ─────────────────────────────────

    @bamboozled.command(
        name="testmode",
        description="[Admin] Toggle test mode — no Boli points or stats recorded.",
    )
    @app_commands.describe(enabled="Enable or disable test mode for this server")
    @app_commands.checks.has_permissions(administrator=True)
    async def testmode(self, interaction: discord.Interaction, enabled: bool):
        if not interaction.guild:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return
        await set_test_mode(str(interaction.guild.id), enabled)
        if enabled:
            await interaction.response.send_message(
                "⚠️ **Test mode ENABLED** for this server.\n"
                "Future games will not record Boli points or player stats.\n"
                "Disable with `/bamboozled testmode enabled:False`.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "✅ **Test mode DISABLED** for this server. Games will record normally.",
                ephemeral=True,
            )

    @testmode.error
    async def testmode_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "⛔ Only server admins can toggle test mode.", ephemeral=True
            )
        else:
            raise error

    # ── /bamboozled help ─────────────────────────────────────

    @bamboozled.command(name="help", description="How to play Bamboozled.")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎬 Bamboozled — Chaos Trivia Game",
            description=(
                "A multiplayer trivia game where answering correctly just gets the chaos started. "
                "Supports 1–6 players per game, fully bot-run with slash commands.\n\n"
                "**How a turn works:**\n"
                "Answer a trivia question → Correct gets you a **Chance Card**, wrong gets you a "
                "**Wicked Wango Card**. Cards can send you to the **Wheel of Mayhem**, which has 8 "
                "outcomes including the **Ladder of Chance** — which leads to the **Golden Monkey**, "
                "who will ask you to choose Belly or Tail. Choose wrong and you also draw a Wango "
                "card. The **Mystic Mist** can descend at any time and hide everyone's scores for "
                "2 turns. The **Sombrero** passes between players and adds an extra penalty on wrong "
                "answers. The **Bamboozle** card lets a player select a mechanically-enforced rule "
                "that applies to everyone for a full round.\n\n"
                "**Before each game starts, the host picks:**\n"
                "• **Rounds:** 3, 5, 7, or 10\n"
                "• **Difficulty:** Easy, Medium, Hard, or Mixed\n"
                "• **Category:** All, or one of several curated topic areas\n\n"
                "**Boli Points** are awarded at the end based on placement, correct answers, "
                "difficulty, and your final in-game score. Hard difficulty pays more. A negative "
                "final score costs a small Boli penalty — the chaos is your problem."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="📋 How to join",
            value=(
                "1. One player uses `/bamboozled join` to open a **lobby** with an interactive message.\n"
                "2. Others click **🎮 Join** on that message to join the same lobby. "
                "Multiple lobbies can run in parallel — each `/join` creates a new one.\n"
                "3. The host clicks **▶️ Start** to configure and begin. "
                "Players can click **👋 Leave** at any time before the game starts.\n"
                "4. The host can click **🛑 End Lobby** to disband before the game starts, "
                "or use `/bamboozled endgame` once a game is running."
            ),
            inline=False,
        )
        embed.add_field(
            name="📋 Commands",
            value=(
                "`/bamboozled join` — Create a new lobby\n"
                "`/bamboozled leave` — Leave the lobby *(slash fallback; use the button)*\n"
                "`/bamboozled scores` — Check current scores mid-game *(hidden during Mist)*\n"
                "`/bamboozled leaderboard` — All-time win leaderboard\n"
                "`/bamboozled stats @user` — A specific player's all-time stats\n"
                "`/bamboozled forfeit` — Skip your current turn *(treated as timeout)*\n"
                "`/bamboozled endgame` — Force-end the game with no results saved *(host only)*\n"
                "`/bamboozled testmode` — Toggle test mode *(admin only)*"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔥 Hot Streak",
            value="Answer **3 or more in a row** correctly and earn a 🔥 **HOT STREAK!** badge.",
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    # ─────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────

    def _scores_embed(self, game: GameState, title: str = "📊 CURRENT SCORES") -> discord.Embed:
        embed = discord.Embed(title=title, color=discord.Color.blurple())
        if game.mist_active:
            embed.description = "🌫️ *The Mystic Mist obscures all scores...*"

        display = game.scores_display()
        ordered = sorted(game.players, key=lambda p: game.scores.get(p, 0), reverse=True)
        medals = ["🥇", "🥈", "🥉"]

        for i, pid in enumerate(ordered):
            name = game.player_display_name(pid)
            badges = ""
            if game.golden_pass.get(pid):
                badges += "🎫"
            if game.silenced.get(pid):
                badges += "🤫"
            medal = medals[i] if i < 3 and not game.mist_active else ""
            score_str = display[pid] if game.mist_active else f"**{game.scores.get(pid, 0):,}** pts"
            embed.add_field(name=f"{medal} {name} {badges}".strip(), value=score_str, inline=False)

        if game.active_bamboozle_rule:
            rule = game.active_bamboozle_rule
            perm_note = " *(permanent)*" if rule["is_permanent"] else ""
            embed.add_field(
                name="⚖️ ACTIVE LAW",
                value=f"**{rule['name']}**{perm_note} — *{rule['description']}*",
                inline=False,
            )
        embed.set_footer(text=f"Round {game.current_round}/{game.total_rounds}")
        return embed

    def _still_active(self, _cid: int, game: GameState) -> bool:
        """True while the game has not been force-ended by /endgame."""
        return game.active

    def _find_player_game(self, channel_id: int, user_id: int) -> Optional["GameState"]:
        """Find a game/lobby that this user belongs to, starting from a channel or thread ID."""
        # Direct hit: channel_id might be a thread_id or lobby message ID
        g = _active_games.get(channel_id)
        if g and user_id in g.players:
            return g
        # Search all games registered in this channel
        for g in _channel_games.get(channel_id, []):
            if user_id in g.players:
                return g
        return None

    def _find_host_game(self, channel_id: int, user_id: int) -> Optional["GameState"]:
        """Find a game/lobby hosted by this user, starting from a channel or thread ID."""
        g = _active_games.get(channel_id)
        if g and g.host_id == user_id:
            return g
        for g in _channel_games.get(channel_id, []):
            if g.host_id == user_id:
                return g
        return None

    async def _refresh_lobby_embed(self, game: "GameState") -> None:
        """Re-fetch and update the lobby embed message if possible."""
        if not game.lobby_id or not game.channel_id:
            return
        channel = self.bot.get_channel(game.channel_id)
        if not channel:
            return
        try:
            msg = await channel.fetch_message(game.lobby_id)
            view = LobbyView(game, self)
            await msg.edit(embed=view.lobby_embed(), view=view)
        except Exception:
            pass

    async def _draw_card_interaction(
        self,
        channel: discord.TextChannel,
        player_id: int,
        prompt: str,
    ) -> None:
        """Send a prompt with a button; wait for the player to click or auto-proceed."""
        view = DrawCardView(player_id, "▶️ Continue")
        msg = await channel.send(prompt, view=view)
        await view.wait()
        for item in view.children:
            item.disabled = True
        try:
            await msg.edit(view=view)
        except discord.HTTPException:
            pass

    async def _cleanup_game(self, game: GameState) -> None:
        """Remove game from all registries and archive any game thread."""
        if game.lobby_id:
            _active_games.pop(game.lobby_id, None)
        if game.thread_id:
            _active_games.pop(game.thread_id, None)
        chan_list = _channel_games.get(game.channel_id)
        if chan_list and game in chan_list:
            chan_list.remove(game)
            if not chan_list:
                _channel_games.pop(game.channel_id, None)
        await unregister_active_channel(str(game.channel_id))
        if game.thread_id:
            thread = self.bot.get_channel(game.thread_id)
            if isinstance(thread, discord.Thread):
                try:
                    await asyncio.sleep(2)
                    await thread.edit(archived=True, locked=True)
                except Exception as exc:
                    logger.warning("Failed to archive game thread: %s", exc)

    async def _lobby_timeout_watch(self, game: GameState) -> None:
        """Auto-disband the lobby if no game starts within 10 minutes of the last player joining."""
        lobby_id = game.lobby_id
        try:
            while True:
                await asyncio.sleep(60)
                # Exit if game was already cleaned up or started
                if _active_games.get(lobby_id) is not game:
                    return
                if game.active:
                    return
                if time.time() - game.last_join_time > 600:
                    game.active = False
                    channel = self.bot.get_channel(game.channel_id)
                    if channel and lobby_id:
                        # Try to update the lobby message, fall back to a plain message
                        try:
                            msg = await channel.fetch_message(lobby_id)
                            closed_embed = discord.Embed(
                                title="🎬 Bamboozled! — Lobby Closed",
                                description="⏰ **Lobby auto-disbanded** — no game started within 10 minutes.",
                                color=discord.Color.dark_gray(),
                            )
                            await msg.edit(embed=closed_embed, view=None)
                        except Exception:
                            await channel.send(
                                "⏰ **Lobby auto-disbanded!** No game started within 10 minutes. "
                                "Use `/bamboozled join` to open a new lobby."
                            )
                    await self._cleanup_game(game)
                    return
        finally:
            _lobby_watchers.discard(lobby_id)

    # ─────────────────────────────────────────────────────────
    # Game Loop
    # ─────────────────────────────────────────────────────────

    async def _run_game(self, channel: discord.TextChannel, game: GameState):
        cid = channel.id

        # Build a shuffled full-rotation only when no specific categories were chosen.
        # When specific categories are selected, pick_question_category() samples them directly.
        if not game.question_categories:
            rotation = list(SAFE_OPENTDB_CATEGORY_IDS) * 4  # enough for a long game
            random.shuffle(rotation)
            game.category_rotation = rotation
            game.category_rotation_index = 0

        while game.current_round <= game.total_rounds and self._still_active(cid, game):
            player_id = game.current_player_id()

            if game.silenced.get(player_id):
                game.silenced[player_id] = False
                await channel.send(
                    f"🤫 <@{player_id}>'s turn is **SKIPPED** — "
                    f"The Silence reigns supreme. Not a peep."
                )
                await asyncio.sleep(2)
            else:
                await self._run_turn(channel, game, player_id)
                if not self._still_active(cid, game):
                    return

            mist_lifted = game.decrement_mist()

            prev_rule = game.active_bamboozle_rule
            rule_expired = game.advance_turn()

            if mist_lifted:
                await asyncio.sleep(1)
                await channel.send(
                    "🌫️ **The Mist clears.** Reality reasserts itself. Behold — the REAL scores:"
                )
                await channel.send(embed=self._scores_embed(game))

            if rule_expired and prev_rule:
                await channel.send(
                    f"📜 **THE LAW HAS EXPIRED!** ⚖️ *{prev_rule['name']}* is no more. "
                    f"Anarchy resumes. Act normally. Whatever that means."
                )

            # Post round summary (not after the final round — endgame handles that)
            if game.current_turn_index == 0 and game.current_round <= game.total_rounds:
                completed = game.current_round - 1
                await asyncio.sleep(1)
                await channel.send(
                    f"🏁 **End of Round {completed}!** Here's the damage:"
                )
                await channel.send(embed=self._scores_embed(game))
                await asyncio.sleep(2)

        if self._still_active(cid, game):
            await self._run_endgame(channel, game)

    # ─────────────────────────────────────────────────────────
    # Turn
    # ─────────────────────────────────────────────────────────

    async def _run_turn(self, channel: discord.TextChannel, game: GameState, player_id: int):
        cid = channel.id
        player_name = game.player_display_name(player_id)
        game.forfeit_requested = False
        game.answer_time_seconds = 0.0

        await channel.send(cmnt.turn_intro(player_id))
        await asyncio.sleep(1.0)

        if game.active_bamboozle_rule:
            rule = game.active_bamboozle_rule
            perm_note = " *(permanent)*" if rule["is_permanent"] else ""
            await asyncio.sleep(0.5)
            await channel.send(
                f"⚖️ **ACTIVE LAW: {rule['name']}**{perm_note} — *{rule['description']}*"
            )

        # Start-of-turn rule enforcement (Rules 5, 6)
        if game.active_bamboozle_rule:
            sot_msg = apply_start_of_turn(game.active_bamboozle_rule, game, player_id)
            if sot_msg:
                await asyncio.sleep(0.5)
                await channel.send(sot_msg)

        await asyncio.sleep(1)

        question, new_token = await fetch_question(
            game.session_token,
            difficulty=game.question_difficulty,
            category=game.pick_question_category(),
        )
        game.session_token = new_token

        answers, correct_idx = shuffle_answers(question)
        embed = discord.Embed(
            title=f"❓ {question['question']}", color=discord.Color.orange()
        )
        embed.set_footer(
            text=f"Category: {question['category']} · "
                 f"Difficulty: {question['difficulty'].title()} · "
                 f"⏱️ {QUESTION_TIMEOUT_SECONDS}s"
        )

        view = AnswerView(answers, correct_idx, player_id)
        question_sent_at = time.monotonic()
        msg = await channel.send(
            f"🎯 **{player_name}**, you have **{QUESTION_TIMEOUT_SECONDS} seconds!**",
            embed=embed,
            view=view,
        )

        # Wait for answer or forfeit signal
        async def _forfeit_watch():
            while not view.is_finished():
                if game.forfeit_requested:
                    view.stop()
                    return
                await asyncio.sleep(0.3)

        await asyncio.gather(view.wait(), _forfeit_watch())

        # Record answer time before anything else
        if not view.timed_out and not game.forfeit_requested:
            game.answer_time_seconds = time.monotonic() - question_sent_at
        else:
            game.answer_time_seconds = float(QUESTION_TIMEOUT_SECONDS)

        # Highlight correct/wrong buttons and disable all
        correct_text = question["correct_answer"]
        for i, item in enumerate(view.children):
            if not isinstance(item, discord.ui.Button):
                continue
            item.disabled = True
            if i == correct_idx:
                item.style = discord.ButtonStyle.success   # green = correct answer
            elif view.chosen_idx is not None and i == view.chosen_idx and view.chosen_idx != correct_idx:
                item.style = discord.ButtonStyle.danger    # red = player's wrong pick
        try:
            await msg.edit(view=view)
        except discord.HTTPException:
            pass

        if not self._still_active(cid, game):
            return

        # ── Timeout / Forfeit ────────────────────────────────
        if view.timed_out or game.forfeit_requested:
            active_rule = game.active_bamboozle_rule
            timeout_penalty = get_effective_timeout_penalty(active_rule)
            game.apply_points(player_id, timeout_penalty)
            game.consecutive_correct[player_id] = 0

            score = game.scores[player_id]
            terror_note = (
                f" ⚠️ *Timeout Terror: -{BAMBOOZLE_TIMEOUT_TERROR_COST} pts!*"
                if active_rule and active_rule["id"] == 7 and not game.forfeit_requested
                else ""
            )
            if game.mist_active:
                await channel.send(cmnt.timeout_mist(player_name))
            elif game.forfeit_requested:
                await channel.send(cmnt.forfeit(player_name, timeout_penalty, score, correct_text))
            else:
                await channel.send(cmnt.timeout(player_name, timeout_penalty, score, correct_text, terror=terror_note))

            # Rule 2 (Slow Burn) — timeout always exceeds 20s threshold
            if active_rule:
                slow_burn_msg = apply_after_wrong_or_timeout(active_rule, game, player_id)
                if slow_burn_msg:
                    await channel.send(slow_burn_msg)
            return

        # ── Correct ──────────────────────────────────────────
        if view.chosen_idx == correct_idx:
            game.apply_points(player_id, CORRECT_ANSWER_POINTS)
            streak = game.consecutive_correct.get(player_id, 0) + 1
            game.consecutive_correct[player_id] = streak
            game.correct_answer_count[player_id] = game.correct_answer_count.get(player_id, 0) + 1
            score = game.scores[player_id]

            if game.mist_active:
                await channel.send(cmnt.correct_mist(player_name))
            else:
                await channel.send(cmnt.correct(player_name, CORRECT_ANSWER_POINTS, score, correct_text))

            # Hot Streak visual badge (3+ correct in a row, independent of any Bamboozle rule)
            if streak >= 3 and not game.mist_active:
                await channel.send(
                    f"🔥 **HOT STREAK!** **{player_name}** has answered "
                    f"**{streak} in a row** correctly! The crowd is losing its mind!"
                )

            # Rule enforcement for correct answers (Rules 1, 4, 8)
            active_rule = game.active_bamboozle_rule
            if active_rule:
                rule_msg = apply_after_correct(active_rule, game, player_id)
                if rule_msg:
                    await channel.send(rule_msg)

            # Rule 9 (Streak Breaker) — intercept before Chance Card is drawn
            if active_rule and active_rule["id"] == 9:
                max_score = max(game.scores.values(), default=0)
                if game.scores.get(player_id, 0) >= max_score:
                    await channel.send(
                        f"⚡ **STREAK BREAKER!** **{player_name}** is in first place — "
                        f"the Chance Card is **DISCARDED** and replaced with a Wicked Wango Card!"
                    )
                    await asyncio.sleep(1)
                    await self._draw_card_interaction(
                        channel, player_id,
                        cmnt.wango_card_draw_prompt(player_name, DRAW_CARD_TIMEOUT_SECONDS),
                    )
                    wango_card = draw_wango_card()
                    await channel.send(WANGO_CARD_FLAVOUR[wango_card])
                    await asyncio.sleep(1)
                    await self._apply_wango_card(channel, game, player_id, wango_card, chain_depth=0)
                    return

            # Interactive card draw prompt
            await self._draw_card_interaction(
                channel, player_id,
                cmnt.chance_card_draw_prompt(player_name, DRAW_CARD_TIMEOUT_SECONDS),
            )
            card = draw_chance_card()
            await channel.send(f"🃏 **{player_name}** draws a **Chance Card**...")
            await asyncio.sleep(1)
            await channel.send(CHANCE_CARD_FLAVOUR[card])
            await asyncio.sleep(1)
            await self._apply_chance_card(channel, game, player_id, card)

        # ── Wrong ────────────────────────────────────────────
        else:
            active_rule = game.active_bamboozle_rule
            wrong_base = get_effective_wrong_penalty(active_rule)
            penalty = wrong_base
            if game.sombrero_holder == player_id:
                penalty -= game.sombrero_penalty
            game.apply_points(player_id, penalty)
            game.consecutive_correct[player_id] = 0

            score = game.scores[player_id]
            sombrero_note = (
                f" 🪅 (+{game.sombrero_penalty} Sombrero penalty)"
                if game.sombrero_holder == player_id
                else ""
            )
            dj_note = (
                " ⚠️ *Double Jeopardy: -100 pts!*"
                if active_rule and active_rule["id"] == 3
                else ""
            )
            if game.mist_active:
                await channel.send(cmnt.wrong_mist(player_name))
            else:
                await channel.send(cmnt.wrong(player_name, penalty, score, correct_text, sombrero_note, dj_note))

            # Rule 2 (Slow Burn) check on wrong answer
            if active_rule:
                slow_burn_msg = apply_after_wrong_or_timeout(active_rule, game, player_id)
                if slow_burn_msg:
                    await channel.send(slow_burn_msg)

            # Golden Pass check
            if game.golden_pass.get(player_id):
                game.golden_pass[player_id] = False
                await channel.send(
                    f"🎫 **GOLDEN PASS ACTIVATED!** **{player_name}** burns their Golden Pass! "
                    f"The Wicked Wango Card... **DISINTEGRATES**. Not today, chaos. NOT TODAY."
                )
                return

            # Interactive card draw prompt
            await self._draw_card_interaction(
                channel, player_id,
                cmnt.wango_card_draw_prompt(player_name, DRAW_CARD_TIMEOUT_SECONDS),
            )
            card = draw_wango_card()
            await channel.send(f"🎴 **{player_name}** draws a **Wicked Wango Card**...")
            await asyncio.sleep(1)
            await channel.send(WANGO_CARD_FLAVOUR[card])
            await asyncio.sleep(1)
            game._wango_chain = 0
            await self._apply_wango_card(channel, game, player_id, card, chain_depth=0)

    # ─────────────────────────────────────────────────────────
    # Chance Cards
    # ─────────────────────────────────────────────────────────

    async def _apply_chance_card(
        self,
        channel: discord.TextChannel,
        game: GameState,
        player_id: int,
        card: ChanceCard,
    ):
        cid = channel.id
        pname = game.player_display_name(player_id)

        if not self._still_active(cid, game):
            return

        if card == ChanceCard.LUCKY_LLAMA:
            game.apply_points(player_id, LUCKY_LLAMA_BONUS)
            score = game.scores[player_id]
            await channel.send(
                f"🦙 The llama blesses **{pname}** with **+{LUCKY_LLAMA_BONUS}** bonus points! "
                f"*(Score: {score:,})*"
            )

        elif card == ChanceCard.SWITCHEROO:
            if game.is_solo():
                # No opponent in solo — convert to Lucky Llama consolation
                game.apply_points(player_id, LUCKY_LLAMA_BONUS)
                score = game.scores[player_id]
                await channel.send(
                    f"🔀 **SWITCHEROO!** *No victims available...* The chaos fizzles — "
                    f"but the llama takes pity. 🦙 **+{LUCKY_LLAMA_BONUS} pts!** "
                    f"*(Score: {score:,})*"
                )
            elif game.switcheroo_used.get(player_id):
                # Already used their one Switcheroo this game — consolation prize
                game.apply_points(player_id, LUCKY_LLAMA_BONUS)
                score = game.scores[player_id]
                await channel.send(
                    f"🔀 **SWITCHEROO!** *Already used this game!* The card fizzles into a "
                    f"consolation llama. 🦙 **+{LUCKY_LLAMA_BONUS} pts!** *(Score: {score:,})*"
                )
            else:
                game.switcheroo_used[player_id] = True
                view = PlayerSelectView(
                    game, player_id,
                    placeholder="Choose a player to swap scores with...",
                    timeout=float(SWITCHEROO_PICK_TIMEOUT_SECONDS),
                    allow_skip=True,
                    skip_label="🚫 No thanks, keep my score",
                )
                await channel.send(
                    f"🔀 **{pname}**, pick your victim! Whose score are you stealing?\n"
                    f"*(Or skip if you'd rather not swap.)*",
                    view=view,
                )
                await view.wait()
                if view.skipped:
                    await channel.send(
                        f"🔀 **{pname}** decides against the Switcheroo. The chaos is left on the table. Wise? Maybe."
                    )
                elif view.timed_out or view.target_id is None:
                    await channel.send(
                        "⏰ **Switcheroo timed out!** The moment of chaos passes uneventfully."
                    )
                else:
                    tid = view.target_id
                    tname = game.player_display_name(tid)
                    old_p, old_t = game.scores[player_id], game.scores[tid]
                    game.scores[player_id], game.scores[tid] = old_t, old_p
                    await channel.send(
                        f"🔀 **SCORES SWAPPED!** "
                        f"**{pname}** ({old_p:,} → **{old_t:,}**) ↔️ "
                        f"**{tname}** ({old_t:,} → **{old_p:,}**). BEAUTIFUL CHAOS."
                    )

        elif card == ChanceCard.DOUBLE_DOWN:
            # 30% chance of a True/False question for variety
            dd_type = "boolean" if random.random() < 0.30 else "multiple"
            tf_note = " *(True or False!)*" if dd_type == "boolean" else ""
            await channel.send(
                f"⬇️⬇️ **{pname}**, the Double Down demands **another question**!{tf_note} "
                f"Correct = **+{DOUBLE_DOWN_BONUS} pts**. Wrong = **{DOUBLE_DOWN_PENALTY} pts**. No cards drawn after."
            )
            await asyncio.sleep(1)

            question, new_token = await fetch_question(
                game.session_token,
                difficulty=game.question_difficulty,
                category=game.pick_question_category(),
                question_type=dd_type,
            )
            game.session_token = new_token
            answers, correct_idx = shuffle_answers(question)

            embed = discord.Embed(
                title=f"❓ DOUBLE DOWN: {question['question']}",
                color=discord.Color.red(),
            )
            embed.set_footer(text=f"DOUBLE DOWN · ⏱️ {QUESTION_TIMEOUT_SECONDS}s")

            view = AnswerView(answers, correct_idx, player_id)
            msg = await channel.send(
                f"🎯 **{pname}**, answer NOW! ⏱️", embed=embed, view=view
            )
            await view.wait()

            correct_text = question["correct_answer"]
            for i, item in enumerate(view.children):
                if not isinstance(item, discord.ui.Button):
                    continue
                item.disabled = True
                if i == correct_idx:
                    item.style = discord.ButtonStyle.success
                elif view.chosen_idx is not None and i == view.chosen_idx and view.chosen_idx != correct_idx:
                    item.style = discord.ButtonStyle.danger
            try:
                await msg.edit(view=view)
            except discord.HTTPException:
                pass

            if view.timed_out:
                game.apply_points(player_id, TIMEOUT_POINTS)
                await channel.send(
                    f"⏰ **TIMED OUT on the Double Down!** Answer was **{correct_text}**. "
                    f"**{pname}** loses **{abs(TIMEOUT_POINTS)} pts**. *(Score: {game.scores[player_id]:,})*"
                )
            elif view.chosen_idx == correct_idx:
                game.apply_points(player_id, DOUBLE_DOWN_BONUS)
                await channel.send(
                    f"✅ **DOUBLE DOWN CORRECT!!** It was **{correct_text}**! "
                    f"**{pname}** earns **+{DOUBLE_DOWN_BONUS} pts**! "
                    f"ABSOLUTELY LEGENDARY. *(Score: {game.scores[player_id]:,})*"
                )
            else:
                game.apply_points(player_id, DOUBLE_DOWN_PENALTY)
                await channel.send(
                    f"❌ **DOUBLE DOWN WRONG!!** The answer was **{correct_text}**. "
                    f"**{pname}** loses **{abs(DOUBLE_DOWN_PENALTY)} pts**. Devastating. "
                    f"*(Score: {game.scores[player_id]:,})*"
                )

        elif card == ChanceCard.SPIN_THE_WHEEL:
            await channel.send(cmnt.send_to_wheel(pname))
            await asyncio.sleep(1)
            await self._spin_wheel(channel, game, player_id, wheel_depth=0)

        elif card == ChanceCard.GOLDEN_PASS:
            game.golden_pass[player_id] = True
            await channel.send(
                f"🎫 **{pname}** receives the **GOLDEN PASS!** "
                f"One free escape from the next Wicked Wango Card. "
                f"Cherish it. Protect it. Love it."
            )

        elif card == ChanceCard.BAMBOOZLE:
            await channel.send(
                f"🃏 **{pname}** drew the **Bamboozle** card! They're choosing a new law of the land..."
            )
            btn_view = BamboozleRuleTriggerButton(player_id)
            await channel.send(
                f"⚖️ <@{player_id}>, click below to choose your law! You have 35 seconds to click.",
                view=btn_view,
            )
            await btn_view.wait()

            if btn_view.done and btn_view.selected_rule_id is not None:
                rule_def = next(
                    (r for r in BAMBOOZLE_RULES if r.id == btn_view.selected_rule_id), None
                )
                if rule_def:
                    active_rule = make_active_rule(rule_def.id, len(game.players))
                    game.active_bamboozle_rule = active_rule

                    if rule_def.is_permanent:
                        perm_note = " This rule is **permanent** for the rest of the game."
                    else:
                        perm_note = (
                            f" This rule is in effect for **1 full round** "
                            f"({len(game.players)} turns)."
                        )
                    await channel.send(
                        f"⚖️ **THE NEW LAW: {rule_def.name}** — {rule_def.description}."
                        f" This is now in effect.{perm_note}"
                    )

                    # Apply permanent rule effects immediately
                    if rule_def.is_permanent:
                        on_set_msg = apply_on_set(active_rule, game)
                        if on_set_msg:
                            await channel.send(on_set_msg)
            else:
                await channel.send(
                    f"⏰ **{pname}** failed to choose a rule in time. The cosmos remain lawless."
                )

    # ─────────────────────────────────────────────────────────
    # Wango Cards
    # ─────────────────────────────────────────────────────────

    async def _apply_wango_card(
        self,
        channel: discord.TextChannel,
        game: GameState,
        player_id: int,
        card: WangoCard,
        chain_depth: int = 0,
    ):
        cid = channel.id
        pname = game.player_display_name(player_id)

        if not self._still_active(cid, game):
            return

        if card == WangoCard.WANGO_CLASSIC:
            await channel.send(cmnt.send_to_wheel(pname))
            await asyncio.sleep(1)
            await self._spin_wheel(channel, game, player_id, wheel_depth=0)

        elif card == WangoCard.THE_SILENCE:
            game.silenced[player_id] = True
            await channel.send(
                f"🤫 **THE SILENCE** descends on **{pname}**! "
                f"Their next turn is automatically skipped. Do not speak. Do not move."
            )

        elif card == WangoCard.REVERSE_UNO:
            if game.is_solo():
                await channel.send(
                    f"🔄 **REVERSE UNO!** Solo mode — the penalty vanishes into the void. Dodged."
                )
            else:
                target_id = game.next_player_in_order(player_id)
                tname = game.player_display_name(target_id)
                game.apply_points(target_id, -REVERSE_UNO_PENALTY)
                await channel.send(
                    f"🔄 **REVERSE UNO!** **{tname}** suffers **-{REVERSE_UNO_PENALTY} pts** "
                    f"thanks to **{pname}'s** misfortune! *(Score: {game.scores[target_id]:,})*"
                )

        elif card == WangoCard.THE_SOMBRERO:
            if game.sombrero_holder is None:
                game.sombrero_holder = player_id
                await channel.send(
                    f"🪅 **THE SOMBRERO** lands on **{pname}'s** head! "
                    f"Every wrong answer now costs an extra **{game.sombrero_penalty} pts**. "
                    f"Wear it with... whatever the opposite of pride is."
                )
            elif game.sombrero_holder == player_id:
                if game.is_solo():
                    game.sombrero_penalty += SOMBRERO_EXTRA_PENALTY
                    await channel.send(
                        f"🪅 **{pname}** draws The Sombrero AGAIN! "
                        f"Solo mode: the penalty rises to **{game.sombrero_penalty} extra pts** per wrong answer!"
                    )
                else:
                    next_id = game.next_player_in_order(player_id)
                    next_name = game.player_display_name(next_id)
                    game.sombrero_holder = next_id
                    await channel.send(
                        f"🪅 **{pname}** already has The Sombrero and draws it AGAIN! "
                        f"It **FLIES** across the room and lands on **{next_name}**! 🪅"
                    )
            else:
                old_holder = game.player_display_name(game.sombrero_holder)
                game.sombrero_holder = player_id
                await channel.send(
                    f"🪅 **THE SOMBRERO** is ripped from **{old_holder}** "
                    f"and slapped onto **{pname}**! Extra penalty: **{game.sombrero_penalty} pts** per wrong answer."
                )

        elif card == WangoCard.DOUBLE_WANGO:
            remaining = DOUBLE_WANGO_CHAIN_LIMIT - chain_depth
            if remaining <= 0:
                await channel.send(
                    "🃏🃏 **DOUBLE WANGO** — but the chain limit is reached. The universe shows mercy."
                )
                return
            cards_to_draw = min(2, remaining)
            await channel.send(
                f"🃏🃏 **{pname}** must draw **{cards_to_draw}** more Wicked Wango Card(s)! "
                f"CHAOS BEGETS CHAOS!"
            )
            for i in range(cards_to_draw):
                await asyncio.sleep(1)
                extra = draw_wango_card()
                # Prevent Double Wango from re-chaining within this chain
                if extra == WangoCard.DOUBLE_WANGO:
                    extra = draw_wango_card()
                    if extra == WangoCard.DOUBLE_WANGO:
                        await channel.send(
                            f"🃏 Chain card {i + 1}: *Double Wango again — the chain recoils. Skipped.*"
                        )
                        continue
                await channel.send(f"🎴 **Chain card {i + 1}:** {WANGO_CARD_FLAVOUR[extra]}")
                await asyncio.sleep(1)
                await self._apply_wango_card(
                    channel, game, player_id, extra, chain_depth=chain_depth + 1
                )
                if not self._still_active(cid, game):
                    return

        elif card == WangoCard.MYSTIC_MIST:
            game.activate_mist()
            await channel.send(
                f"🌫️ **THE MYSTIC MIST DESCENDS. No one can see anything.**\n"
                f"Scores are hidden for the next **{MIST_TURN_DURATION} turns**. "
                f"Reality is optional."
            )

    # ─────────────────────────────────────────────────────────
    # Wheel of Mayhem
    # ─────────────────────────────────────────────────────────

    async def _spin_wheel(
        self,
        channel: discord.TextChannel,
        game: GameState,
        player_id: int,
        wheel_depth: int = 0,
    ):
        # Interactive spin button — player clicks or it auto-spins
        pname = game.player_display_name(player_id)
        spin_view = DrawCardView(player_id, "🎡 SPIN THE WHEEL!")
        spin_msg = await channel.send(
            cmnt.spin_prompt(pname, DRAW_CARD_TIMEOUT_SECONDS),
            view=spin_view,
        )
        await spin_view.wait()
        for item in spin_view.children:
            item.disabled = True
        try:
            await spin_msg.edit(view=spin_view)
        except discord.HTTPException:
            pass

        for line in SPIN_SUSPENSE:
            await channel.send(line)
            await asyncio.sleep(0.8)

        segment = spin_wheel()

        if segment == WheelSegment.MONKEYS_CHOICE:
            await channel.send(WHEEL_FLAVOUR[segment])
            await asyncio.sleep(1)
            segment = monkey_choice_segment()
            await channel.send(
                f"🐵 The monkey reaches into the void and pulls out... **{segment.value}**!"
            )
            await asyncio.sleep(1)
        else:
            await channel.send(WHEEL_FLAVOUR[segment])
            await asyncio.sleep(1)

        await self._apply_wheel_segment(channel, game, player_id, segment, wheel_depth)

    async def _apply_wheel_segment(
        self,
        channel: discord.TextChannel,
        game: GameState,
        player_id: int,
        segment: WheelSegment,
        wheel_depth: int = 0,
    ):
        cid = channel.id
        pname = game.player_display_name(player_id)

        if not self._still_active(cid, game):
            return

        if segment == WheelSegment.LADDER_OF_CHANCE:
            await self._golden_monkey(channel, game, player_id)

        elif segment == WheelSegment.TAX_SEASON:
            current = game.scores.get(player_id, 0)
            tax = max(TAX_MINIMUM, int(abs(current) * TAX_RATE))
            game.scores[player_id] = current - tax  # bypass cap — percentage mechanic
            await channel.send(
                f"💸 **TAX SEASON!** **{pname}** loses **{tax:,} pts** "
                f"({TAX_RATE * 100:.0f}% of score, min {TAX_MINIMUM}). "
                f"*(Score: {game.scores[player_id]:,})*"
            )

        elif segment == WheelSegment.GIFT_OF_THE_BAMBOOZLE:
            if game.is_solo():
                game.scores[player_id] = game.scores.get(player_id, 0) + GIFT_STEAL_AMOUNT
                await channel.send(
                    f"🎁 **GIFT OF THE BAMBOOZLE!** **{pname}** steals **{GIFT_STEAL_AMOUNT} pts** "
                    f"from The Bank! *(Score: {game.scores[player_id]:,})*"
                )
            else:
                view = PlayerSelectView(
                    game, player_id,
                    placeholder=f"Choose someone to steal {GIFT_STEAL_AMOUNT} pts from...",
                    timeout=float(SWITCHEROO_PICK_TIMEOUT_SECONDS),
                )
                await channel.send(
                    f"🎁 **{pname}**, pick your mark! Who are you robbing?", view=view
                )
                await view.wait()
                if view.timed_out or view.target_id is None:
                    await channel.send(
                        "⏰ **Gift timed out!** The gift is returned unopened. How anticlimactic."
                    )
                else:
                    tid = view.target_id
                    tname = game.player_display_name(tid)
                    game.scores[tid] = game.scores.get(tid, 0) - GIFT_STEAL_AMOUNT
                    game.scores[player_id] = game.scores.get(player_id, 0) + GIFT_STEAL_AMOUNT
                    await channel.send(
                        f"🎁 **{pname}** STEALS **{GIFT_STEAL_AMOUNT} pts** from **{tname}**! "
                        f"Bold. Brazen. Beautiful."
                    )

        elif segment == WheelSegment.FULL_REVERSAL:
            if game.is_solo():
                old = game.scores.get(player_id, 0)
                game.scores[player_id] = -old
                await channel.send(
                    f"🔃 **FULL REVERSAL!** Solo mode: **{pname}'s** score flips from "
                    f"**{old:,}** to **{game.scores[player_id]:,}**!"
                )
            else:
                ranked = sorted(game.players, key=lambda p: game.scores.get(p, 0), reverse=True)
                old_scores = {p: game.scores.get(p, 0) for p in ranked}
                new_score_values = list(reversed([old_scores[p] for p in ranked]))
                await channel.send(
                    "🔃 **FULL REVERSAL!** Scores are being swapped in reverse rank order!"
                )
                await asyncio.sleep(1)
                for p, new_val in zip(ranked, new_score_values):
                    game.scores[p] = new_val
                    await channel.send(
                        f"  • **{game.player_display_name(p)}**: {old_scores[p]:,} → **{new_val:,}**"
                    )
                    await asyncio.sleep(0.5)

        elif segment == WheelSegment.MYSTIC_MIST:
            game.activate_mist()
            await channel.send(
                f"🌫️ **THE MYSTIC MIST DESCENDS** via the Wheel! "
                f"Scores hidden for **{MIST_TURN_DURATION} turns**."
            )

        elif segment == WheelSegment.BONUS_ROUND:
            game.apply_points(player_id, BONUS_ROUND_POINTS)
            await channel.send(
                f"🎉 **BONUS ROUND!** **{pname}** earns **+{BONUS_ROUND_POINTS} pts**! "
                f"The universe smiles! *(Score: {game.scores[player_id]:,})*"
            )

        elif segment == WheelSegment.WANGO_AGAIN:
            if wheel_depth >= WANGO_AGAIN_WHEEL_DEPTH_LIMIT:
                await channel.send(
                    "😅 **Wango Again** — but we're already in a chain. "
                    "The laws of chaos prevent further recursion. Bonus Round instead!"
                )
                game.apply_points(player_id, BONUS_ROUND_POINTS)
                await channel.send(
                    f"🎉 Consolation **+{BONUS_ROUND_POINTS} pts** for **{pname}**! "
                    f"*(Score: {game.scores[player_id]:,})*"
                )
                return

            card = draw_wango_card()
            await channel.send(
                f"😱 **WANGO AGAIN!** **{pname}** draws another Wicked Wango Card!"
            )
            await asyncio.sleep(1)
            await channel.send(WANGO_CARD_FLAVOUR[card])
            await asyncio.sleep(1)

            if card == WangoCard.WANGO_CLASSIC:
                # This wheel spin cannot trigger Wango Again
                await channel.send(cmnt.send_to_wheel(pname))
                await asyncio.sleep(1)
                await self._spin_wheel_capped(channel, game, player_id)
            else:
                await self._apply_wango_card(channel, game, player_id, card, chain_depth=0)

    async def _spin_wheel_capped(
        self, channel: discord.TextChannel, game: GameState, player_id: int
    ):
        """Spin at wheel_depth=1: Wango Again is converted to Bonus Round."""
        pname = game.player_display_name(player_id)
        spin_view = DrawCardView(player_id, "🎡 SPIN THE WHEEL!")
        spin_msg = await channel.send(
            cmnt.spin_prompt(pname, DRAW_CARD_TIMEOUT_SECONDS),
            view=spin_view,
        )
        await spin_view.wait()
        for item in spin_view.children:
            item.disabled = True
        try:
            await spin_msg.edit(view=spin_view)
        except discord.HTTPException:
            pass

        for line in SPIN_SUSPENSE:
            await channel.send(line)
            await asyncio.sleep(0.8)

        segment = spin_wheel()

        if segment == WheelSegment.MONKEYS_CHOICE:
            await channel.send(WHEEL_FLAVOUR[segment])
            await asyncio.sleep(1)
            segment = monkey_choice_segment()
            await channel.send(f"🐵 The monkey chooses... **{segment.value}**!")
            await asyncio.sleep(1)
        else:
            await channel.send(WHEEL_FLAVOUR[segment])
            await asyncio.sleep(1)

        # Enforce cap: Wango Again becomes Bonus Round
        if segment == WheelSegment.WANGO_AGAIN:
            await channel.send(
                "*The wheel tries Wango Again — but the chain cap blocks it. Bonus Round instead!*"
            )
            segment = WheelSegment.BONUS_ROUND

        await self._apply_wheel_segment(channel, game, player_id, segment, wheel_depth=1)

    # ─────────────────────────────────────────────────────────
    # Golden Monkey
    # ─────────────────────────────────────────────────────────

    async def _golden_monkey(
        self, channel: discord.TextChannel, game: GameState, player_id: int
    ):
        pname = game.player_display_name(player_id)

        # Randomly decide which side is lucky this turn — the monkey knows, the player doesn't
        belly_is_lucky = random.random() < 0.5
        lucky_pts = GOLDEN_MONKEY_BELLY    # +300
        unlucky_pts = GOLDEN_MONKEY_TAIL   # -200

        await channel.send(
            f"🐒 **{pname}** has climbed the **Ladder of Chance** "
            f"and faces the **GOLDEN MONKEY**... 🐒\n"
            f"*The studio falls completely silent. The monkey could go either way today.*"
        )
        await asyncio.sleep(1)

        view = GoldenMonkeyView(player_id)
        await channel.send(
            f"🐒 <@{player_id}> **The Golden Monkey waits. BELLY or TAIL?** "
            f"One is fortune. One is misfortune. The monkey has already decided — have you? "
            f"**{GOLDEN_MONKEY_TIMEOUT_SECONDS} seconds.** *(Only your click counts!)*",
            view=view,
        )
        await view.wait()

        if view.timed_out or view.choice is None:
            # Default to tail on timeout — still random outcome
            default_pts = unlucky_pts if belly_is_lucky else lucky_pts
            if default_pts >= 0:
                await channel.send(
                    f"⏳ *The Monkey grew impatient.* **TAIL** by default! "
                    f"**{pname}** earns **+{default_pts} pts**! Wait — the monkey is... confused?"
                )
                game.apply_points(player_id, default_pts)
                await channel.send(f"*(Score: {game.scores[player_id]:,})*")
            else:
                await channel.send(
                    f"⏳ *The Monkey grew impatient.* **TAIL** by default! "
                    f"**{pname}** loses **{abs(default_pts)} pts**! The monkey is... disappointed."
                )
                game.apply_points(player_id, default_pts)
                await channel.send(f"*(Score: {game.scores[player_id]:,})*")
                await asyncio.sleep(1)
                card = draw_wango_card()
                await channel.send(f"🎴 The Monkey's parting gift: {WANGO_CARD_FLAVOUR[card]}")
                await asyncio.sleep(1)
                await self._apply_wango_card(channel, game, player_id, card, chain_depth=0)

        elif view.choice == "belly":
            pts = lucky_pts if belly_is_lucky else unlucky_pts
            if pts >= 0:
                game.apply_points(player_id, pts)
                await channel.send(
                    f"🫃 **BELLY!** THE MONKEY IS PLEASED! "
                    f"**{pname}** earns **+{pts} pts**! "
                    f"Today the belly was blessed! *(Score: {game.scores[player_id]:,})*"
                )
                await asyncio.sleep(1.0)
            else:
                game.apply_points(player_id, pts)
                await channel.send(
                    f"🫃 **BELLY!** The monkey... *winces*. "
                    f"**{pname}** loses **{abs(pts)} pts**! "
                    f"Belly was the wrong call today. *(Score: {game.scores[player_id]:,})*"
                )
                await asyncio.sleep(1)
                card = draw_wango_card()
                await channel.send(f"🎴 The Monkey's displeasure: {WANGO_CARD_FLAVOUR[card]}")
                await asyncio.sleep(1)
                await self._apply_wango_card(channel, game, player_id, card, chain_depth=0)

        else:  # tail
            pts = unlucky_pts if belly_is_lucky else lucky_pts
            if pts >= 0:
                game.apply_points(player_id, pts)
                await channel.send(
                    f"🐒 **TAIL!** The monkey does a little dance! "
                    f"**{pname}** earns **+{pts} pts**! "
                    f"The tail was sacred today! *(Score: {game.scores[player_id]:,})*"
                )
                await asyncio.sleep(1.0)
            else:
                game.apply_points(player_id, pts)
                await channel.send(
                    f"🐒 **TAIL!** The monkey SCREECHES! "
                    f"**{pname}** loses **{abs(pts)} pts**! "
                    f"Should've gone belly. *(Score: {game.scores[player_id]:,})*"
                )
                await asyncio.sleep(1)
                card = draw_wango_card()
                await channel.send(f"🎴 And as punishment: {WANGO_CARD_FLAVOUR[card]}")
                await asyncio.sleep(1)
                await self._apply_wango_card(channel, game, player_id, card, chain_depth=0)

    # ─────────────────────────────────────────────────────────
    # Endgame
    # ─────────────────────────────────────────────────────────

    async def _run_endgame(self, channel: discord.TextChannel, game: GameState):
        cid = channel.id

        await channel.send(cmnt.endgame_wrap(game.total_rounds))
        await asyncio.sleep(2)

        # Lift any residual mist for the final reveal
        if game.mist_active:
            game.mist_active = False
            await channel.send("🌫️ *The Mist clears for the final reveal...*")
            await asyncio.sleep(1)

        await channel.send("📊 **FINAL SCORES INCOMING...**")
        await asyncio.sleep(1)
        await channel.send(embed=self._scores_embed(game, title="🏆 FINAL LEADERBOARD"))
        await asyncio.sleep(2)

        sorted_players = sorted(game.players, key=lambda p: game.scores.get(p, 0), reverse=True)
        top_score = game.scores[sorted_players[0]]
        winners = [p for p in sorted_players if game.scores[p] == top_score]

        # Tiebreaker — Sudden Bamboozle
        while len(winners) > 1 and self._still_active(cid, game):
            tied_names = ", ".join(game.player_display_name(w) for w in winners)
            await channel.send(
                f"⚡ **IT'S A TIE!** {tied_names} are ALL at **{top_score:,} pts**!\n"
                f"🎲 **SUDDEN BAMBOOZLE!** Each tied player spins the Wheel AND answers a question!"
            )
            await asyncio.sleep(2)

            for w in winners:
                wname = game.player_display_name(w)
                await channel.send(f"🎡 **{wname}** spins the Wheel of Mayhem!")
                await asyncio.sleep(1)
                await self._spin_wheel(channel, game, w, wheel_depth=0)
                await asyncio.sleep(1)

            for w in winners:
                if not self._still_active(cid, game):
                    return
                wname = game.player_display_name(w)
                await channel.send(f"❓ **{wname}** faces a tiebreaker question!")
                await asyncio.sleep(1)

                question, new_token = await fetch_question(
                    game.session_token,
                    difficulty=game.question_difficulty,
                    category=game.pick_question_category(),
                )
                game.session_token = new_token
                answers, correct_idx = shuffle_answers(question)

                embed = discord.Embed(
                    title=f"❓ TIEBREAKER: {question['question']}",
                    color=discord.Color.red(),
                )
                view = AnswerView(answers, correct_idx, w)
                msg = await channel.send(
                    f"🎯 **{wname}**, tiebreaker! {QUESTION_TIMEOUT_SECONDS}s!",
                    embed=embed,
                    view=view,
                )
                await view.wait()

                correct_text = question["correct_answer"]
                for i, item in enumerate(view.children):
                    if not isinstance(item, discord.ui.Button):
                        continue
                    item.disabled = True
                    if i == correct_idx:
                        item.style = discord.ButtonStyle.success
                    elif view.chosen_idx is not None and i == view.chosen_idx and view.chosen_idx != correct_idx:
                        item.style = discord.ButtonStyle.danger
                try:
                    await msg.edit(view=view)
                except discord.HTTPException:
                    pass

                if view.timed_out:
                    game.apply_points(w, TIMEOUT_POINTS)
                    await channel.send(
                        f"⏰ **{wname}** timed out! Answer: **{correct_text}**. "
                        f"**{abs(TIMEOUT_POINTS):,} pts**. *(Score: {game.scores[w]:,})*"
                    )
                elif view.chosen_idx == correct_idx:
                    game.apply_points(w, CORRECT_ANSWER_POINTS)
                    await channel.send(
                        f"✅ **CORRECT!** It was **{correct_text}**! "
                        f"**{wname}** earns **+{CORRECT_ANSWER_POINTS} pts**! "
                        f"*(Score: {game.scores[w]:,})*"
                    )
                else:
                    game.apply_points(w, WRONG_ANSWER_POINTS)
                    await channel.send(
                        f"❌ **WRONG!** The answer was **{correct_text}**. "
                        f"**{wname}** loses **{abs(WRONG_ANSWER_POINTS)} pts**. "
                        f"*(Score: {game.scores[w]:,})*"
                    )

            await asyncio.sleep(1)

            new_top = max(game.scores[w] for w in winners)
            winners = [w for w in winners if game.scores[w] == new_top]
            top_score = new_top

        if not self._still_active(cid, game):
            return

        champion_id = winners[0]
        champion_name = game.player_display_name(champion_id)
        champion_score = game.scores[champion_id]

        await channel.send(cmnt.winner_announce(champion_name.upper(), champion_score))

        if game.test_mode:
            await channel.send(
                "⚠️ **TEST MODE** — results not recorded and no Boli points awarded."
            )
        else:
            # Save results
            final_scores = {str(pid): game.scores.get(pid, 0) for pid in game.players}
            await save_game_result(
                channel_id=str(game.channel_id),
                winner_id=str(champion_id),
                player_count=len(game.players),
                final_scores=final_scores,
            )
            for pid in game.players:
                await update_player_stats(str(pid), pid == champion_id, game.scores.get(pid, 0))

            # Boli Points bridge
            if NAVI_DB_PATH:
                final_sorted = sorted(game.players, key=lambda p: game.scores.get(p, 0), reverse=True)
                boli_results = []
                for pid in final_sorted:
                    amount, reason = calculate_boli_award(
                        pid, game, game.correct_answer_count, champion_id, final_sorted
                    )
                    boli_results.append((pid, amount, reason))

                boli_tasks = [
                    award_boli(pid, amount, reason)
                    for pid, amount, reason in boli_results
                    if amount > 0
                ]
                if boli_tasks:
                    await asyncio.gather(*boli_tasks, return_exceptions=True)

                medals = ["🥇", "🥈", "🥉"]
                boli_embed = discord.Embed(
                    title="🍮 Boli Points Awarded (powered by Navi)",
                    color=discord.Color.gold(),
                )
                has_negative = any(amount < 0 for _, amount, _ in boli_results)
                for i, (pid, amount, _) in enumerate(boli_results):
                    pname = game.player_display_name(pid)
                    medal = medals[i] if i < 3 else "  "
                    amount_str = f"+{amount}" if amount >= 0 else str(amount)
                    boli_embed.add_field(
                        name=f"{medal} {pname}",
                        value=f"{amount_str} Boli",
                        inline=False,
                    )
                if has_negative:
                    boli_embed.set_footer(
                        text="Negative Boli reflects a negative final score — the chaos got you this time."
                    )
                await channel.send(embed=boli_embed)

        # ── Play-again: reset to lobby state, keeping the same players ──
        game.active = False
        old_thread_id = game.thread_id
        old_lobby_id = game.lobby_id

        # Archive the completed game thread
        if isinstance(channel, discord.Thread):
            try:
                await channel.edit(archived=True, locked=True)
            except Exception as exc:
                logger.warning("Failed to archive game thread: %s", exc)

        # Clear all volatile game state (resets thread_id and lobby_id too)
        game.reset_for_new_game()

        # Remove stale registry entries
        if old_thread_id:
            _active_games.pop(old_thread_id, None)
        if old_lobby_id:
            _active_games.pop(old_lobby_id, None)

        await unregister_active_channel(str(game.channel_id))

        # Post a fresh lobby embed with join/start buttons in the original channel
        lobby_channel = self.bot.get_channel(game.channel_id) or channel
        view = LobbyView(game, self)
        msg = await lobby_channel.send(
            "🎬 **Game over! Same players — ready for another round?**",
            embed=view.lobby_embed(),
            view=view,
        )
        game.lobby_id = msg.id
        _active_games[msg.id] = game

        # Restart lobby timeout watcher
        asyncio.create_task(self._lobby_timeout_watch(game))
        _lobby_watchers.add(game.lobby_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(BamboozledCog(bot))
