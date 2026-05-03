# bot.py - Navi (disco_bot) — Main Discord bot entrypoint

from __future__ import annotations

import io
import logging
import math
import os
import random
import re
import asyncio
import shutil
from collections import deque
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
import discord
from discord import app_commands

from schema import (
    init_db,
    seed_local_knowledge,
    get_table_counts,
    get_health_stats,
    get_user_profile,
    upsert_user,
    get_last_n_predictions,
    save_user_prediction,
    save_prediction,
    update_boli_points,
    increment_prediction_count,
    log_curse,
    get_leaderboard,
    get_config_float,
    get_config_int,
    get_config_str,
    set_config_float,
    set_config_int,
    set_config_str,
    get_all_configs,
    get_todays_user_prediction,
    get_level_from_points,
    points_for_level,
    has_active_perk,
    grant_perk,
    get_perk_expiry,
    get_user_strikes,
    increment_user_strikes,
    reset_user_strikes,
    reset_all_strikes,
    set_user_points,
    get_daily_action_count,
    increment_daily_action_count,
    get_extra_actions,
    decrement_extra_actions,
    add_extra_actions,
    count_leaderboard_entries,
    DB_PATH,
    save_local_media,
    get_local_media,
    get_user_local_media_count,
    get_global_local_media_count,
    list_user_local_media,
    delete_local_media,
    log_bless,
    get_curse_leaderboard,
    get_bless_leaderboard,
    log_command_event,
    log_session_event,
    log_api_call,
    save_app_emoji,
    get_oldest_app_emoji,
    delete_app_emoji_record,
    update_app_emoji_last_used,
    count_app_emojis,
    _APP_EMOJI_EVICT_THRESHOLD,
    # New feature imports
    get_daily_refill_count,
    increment_daily_refill_count,
    get_game_daily_count,
    increment_game_daily_count,
    _GAME_DAILY_LIMIT,
    get_active_user_ids_previous_day,
    get_top_n_from_user_ids,
)
from glossary import RASHIS
from prompts import (
    get_time_aware_system_prompt,
    get_navi_prompt,
    get_curse_prompt,
    get_qa_prompt,
    get_summ_prompt,
    SUMM_SYSTEM_PROMPT,
    LINK_SUMMARY_SYSTEM_PROMPT,
    get_vibe_check_prompt,
    get_kanmanilla_prompt,
    get_link_summary_prompt,
    get_audit_prompt,
    get_mod_tldr_prompt,
    FALLBACK_MESSAGES,
    WELCOME_MESSAGES,
    MODA_INTROS,
    PENDING_WELCOME_MESSAGES,
    BOT_SELF_CURSE_REPLIES,
    BOT_LOOP_CURSE_REPLIES,
    BOT_SELF_COMPLIMENT_REPLIES,
    BOT_LOOP_COMPLIMENT_REPLIES,
    SPAM_WRAPPERS,
    LEVEL_UP_MESSAGES,
    LINK_USERNAME,
)
from curses import (
    CURSE_WORDS,
    SEVERE_CURSE_WORDS,
    get_random_curse,
    get_random_compliment,
    get_random_doomed_prediction,
    get_random_curse_back,
    get_random_kochi_response,
    contains_boli_trigger,
    contains_kochi_slang,
    contains_curse_word,
)
from services.gemini_service import GeminiService
from services.api_manager import ApiManager
from slang_scorer import load_slang_data, score_message, get_slang_matches
from template_resolver import load_templates, get_template

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("navi")

# ---------------------------------------------------------------------------
# Load environment
# ---------------------------------------------------------------------------

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
FREE_API_KEY = os.getenv("GEMINI_API_KEY_FREE")
# Support old single-key env var as alias for paid key
PAID_API_KEY = os.getenv("GEMINI_API_KEY_PAID") or os.getenv("GEMINI_API_KEY")
FREE_TIER_MODE = os.getenv("FREE_TIER_MODE", "true").lower() == "true"
GENERAL_CHANNEL = os.getenv("GENERAL_CHANNEL", "general")
MOD_CHANNEL_NAME = os.getenv("MOD_CHANNEL_NAME", "mod-log")

# Bot owner's Discord user ID — MUST match the owner of the bot application.
# Set OWNER_ID in .env so no other user can ever invoke /admin commands.
_raw_owner_id = os.getenv("OWNER_ID", "")
OWNER_ID: int | None = int(_raw_owner_id) if _raw_owner_id.isdigit() else None

# ---------------------------------------------------------------------------
# In-Memory Rate Limiting (rolling 60-second window per user)
# ---------------------------------------------------------------------------


_user_usage: dict[int, list[datetime]] = {}  # user_id -> list of timestamps
_RATE_WINDOW_SECONDS = 60


def get_user_minute_count(user_id: int, increment: bool = True) -> int:
    """Return how many times user has used /navi in the last 60 seconds.

    If increment=True, also records this usage (call ONCE per invocation).
    Returns the count AFTER incrementing.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=_RATE_WINDOW_SECONDS)

    # Clean expired timestamps
    _user_usage.setdefault(user_id, [])
    _user_usage[user_id] = [t for t in _user_usage[user_id] if t > cutoff]

    if increment:
        _user_usage[user_id].append(now)

    return len(_user_usage[user_id])

# ---------------------------------------------------------------------------
# Vibe Check — lightweight in-memory channel tracker (Feature 4)
# ---------------------------------------------------------------------------

_VIBE_WINDOW_SECONDS = 15
_VIBE_MSG_THRESHOLD = 10
_VIBE_CAPS_RATIO = 0.30

# channel_id -> deque of (timestamp, content) tuples
_channel_msg_tracker: dict[int, deque] = {}
# channel_id -> timestamp of last vibe-check fire (to avoid spam)
_vibe_last_fired: dict[int, datetime] = {}
_VIBE_COOLDOWN_SECONDS = 60  # don't fire again within 60s for the same channel


def _is_heated_message(content: str) -> bool:
    """Return True if the message is all-caps or contains a severe curse word."""
    lower = content.lower()
    is_allcaps = len(content) > 3 and content == content.upper() and content.strip().isalpha() is False and any(c.isalpha() for c in content)
    has_severe = any(re.search(rf"\b{re.escape(w)}\b", lower) for w in SEVERE_CURSE_WORDS)
    return is_allcaps or has_severe


async def _check_vibe(message: discord.Message) -> None:
    """Track message and fire vibe-check calming message if chat overheats."""
    channel_id = message.channel.id
    now = datetime.now(timezone.utc)

    # Cooldown guard — don't fire twice in 60s per channel
    last = _vibe_last_fired.get(channel_id)
    if last and (now - last).total_seconds() < _VIBE_COOLDOWN_SECONDS:
        return

    # Maintain rolling deque for this channel
    tracker = _channel_msg_tracker.setdefault(channel_id, deque())
    tracker.append((now, message.content))

    # Purge messages older than the window
    cutoff = now.timestamp() - _VIBE_WINDOW_SECONDS
    while tracker and tracker[0][0].timestamp() < cutoff:
        tracker.popleft()

    if len(tracker) < _VIBE_MSG_THRESHOLD:
        return

    heated = sum(1 for _, c in tracker if _is_heated_message(c))
    if heated / len(tracker) < _VIBE_CAPS_RATIO:
        return

    # Threshold met — fire vibe check
    _vibe_last_fired[channel_id] = now
    tracker.clear()

    channel_name = getattr(message.channel, "name", "this channel")
    system_prompt = get_time_aware_system_prompt(db_conn, username=None)  # no persona for vibe check
    user_prompt = get_vibe_check_prompt(channel_name)

    reply, _ = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: api_mgr.call(
            prompt=user_prompt,
            system_prompt=system_prompt,
            cache_type="qa",
            name="VibeCheck",
            fallback_message="Everyone take a breath. Whatever's happening in here, it can wait.",
        ),
    )
    await message.channel.send(reply)
    logger.info("Vibe check fired in #%s", channel_name)


# ---------------------------------------------------------------------------
# Strike system helpers (Feature 7)
# ---------------------------------------------------------------------------

async def _handle_strike(member: discord.Member, channel: discord.abc.Messageable) -> None:
    """Increment strike counter and issue a public warning. At 3 strikes, alert the mod channel and reset."""
    new_strikes = increment_user_strikes(db_conn, member.id)

    if new_strikes == 1:
        await channel.send(f"{member.mention} — Strike 1. Two more and the mod team gets notified.")
        logger.info("Strike 1 issued to %s", member.display_name)

    elif new_strikes == 2:
        await channel.send(f"{member.mention} — Strike 2. One more and this escalates.")
        logger.info("Strike 2 issued to %s", member.display_name)

    elif new_strikes >= 3:
        reset_user_strikes(db_conn, member.id)
        mod_ch = None
        if isinstance(channel, discord.TextChannel):
            mod_ch = discord.utils.get(channel.guild.text_channels, name=MOD_CHANNEL_NAME)
        if mod_ch:
            await mod_ch.send(
                f"🚨 **Mod Alert:** {member.mention} has hit **3 strikes**. "
                f"Strikes have been reset to 0 — further action is up to the mod team."
            )
        await channel.send(f"🚨 {member.mention} — 3 strikes. Mod team has been notified.")
        logger.info("Strike 3 — mod channel alerted for %s, strikes reset", member.display_name)



# ---------------------------------------------------------------------------
# Discord client setup
# ---------------------------------------------------------------------------

intents = discord.Intents.all()
intents.message_content = True
bot = discord.Client(intents=intents)


class _NaviTree(app_commands.CommandTree):
    """CommandTree subclass that enforces the master kill switch globally."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not _feat("master_killswitch"):
            return True  # bot alive — allow all

        # Bot is dead — only owner can reach /admin commands
        is_owner = bool(OWNER_ID and interaction.user.id == OWNER_ID)
        if not is_owner:
            try:
                app_info = await bot.application_info()
                is_owner = interaction.user.id == app_info.owner.id
            except Exception:
                pass

        if is_owner and interaction.command and interaction.command.qualified_name.startswith("admin"):
            return True

        await interaction.response.send_message(
            "Bot is currently disabled. Only the owner can re-enable it.",
            ephemeral=True,
        )
        return False


tree = _NaviTree(bot)

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

db_conn = None          # sqlite3.Connection
gemini_svc = None       # GeminiService
api_mgr = None          # ApiManager
_BOT_START_TIME = datetime.now(timezone.utc)

# In-memory set of user IDs seen this session — avoids upsert_user on every message
_seen_users: set[int] = set()

# ---------------------------------------------------------------------------
# In-memory feature toggle cache (Feature 8)
# Loaded at startup; updated immediately on every /admin toggle command.
# Avoids a DB read on every message/interaction.
# ---------------------------------------------------------------------------

_FEATURE_DEFAULTS: dict[str, int] = {
    "master_killswitch":    0,
    "feature_navi":         1,
    "feature_curses":       1,
    "feature_gambling":     1,
    "feature_local_media":  1,
    "feature_boli_points":  1,
    "feature_kochi_replies": 1,
    "feature_curse_replies": 1,
    "feature_vibe_check":   1,
    "feature_link_summary": 1,
    "feature_kanmanilla":   1,
    "feature_welcome":      1,
    "feature_temp_vc":      1,
    "feature_audit":        1,
    "feature_mod_tldr":     1,
    "feature_strikes":      1,
}

# Populated in on_ready() after db_conn is available
_feature_cache: dict[str, int] = dict(_FEATURE_DEFAULTS)


def _feat(key: str) -> bool:
    """Return True if the feature is enabled in the in-memory cache."""
    return bool(_feature_cache.get(key, _FEATURE_DEFAULTS.get(key, 1)))


def _load_feature_cache() -> None:
    """Reload all toggle values from DB into the in-memory cache."""
    global _feature_cache
    for key, default in _FEATURE_DEFAULTS.items():
        _feature_cache[key] = get_config_int(db_conn, key, default)
    logger.info("Feature toggle cache loaded: %s", _feature_cache)


def _set_feature(key: str, value: int) -> None:
    """Persist a feature toggle to DB and update in-memory cache atomically."""
    set_config_int(db_conn, key, value)
    _feature_cache[key] = value

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _templatize(text: str, name: str, curse: str | None = None) -> str:
    """Replace concrete name/curse with {user}/{curse} placeholders for caching."""
    result = text.replace(name, "{user}")
    if curse:
        result = result.replace(curse, "{curse}")
    return result


def _personalise(template: str, name: str, curse: str | None = None) -> str:
    """Fill {user}/{curse} placeholders with real values."""
    result = template.replace("{user}", name)
    if curse:
        result = result.replace("{curse}", curse)
    return result



# SPAM_WRAPPERS imported from prompts.py

_LEVEL_TITLES: dict[int, str] = {
    0:   "Tourist",
    5:   "Chalai Wanderer",
    10:  "Thampanoor Regular",
    15:  "Auto Stand VIP",
    20:  "Palayam Negotiator",
    25:  "Kowdiar Cruiser",
    30:  "Chaya Kada Analyst",
    35:  "Technopark Hustler",
    40:  "KSRTC Minnal Survivor",
    45:  "Ponmudi Rider",
    50:  "Secretariat Insider",
    55:  "Putharikandam Orator",
    60:  "Padmanabhaswamy Guard",
    65:  "East Fort Navigator",
    70:  "Varkala Drifter",
    75:  "Museum Campus Walker",
    80:  "Trivandrum Oracle",
    85:  "The Southern Sage",
    90:  "Ananthapuri Legend",
    95:  "Cosmic Malayali",
    100: "The Chosen One",
}

# LEVEL_UP_MESSAGES imported from prompts.py


def get_level_title(level: int) -> str:
    """Return the highest title whose threshold is <= level."""
    title = _LEVEL_TITLES[0]
    for threshold in sorted(_LEVEL_TITLES):
        if level >= threshold:
            title = _LEVEL_TITLES[threshold]
    return title


_DAILY_QUOTA = 20  # combined cosmic actions per day (curses + blessings + games)

_OVER_QUOTA_MESSAGES: list[str] = [
    "🌌 {invoker}, you've used all {quota} cosmic actions for today. Head to `/shop buy` to grab a refill (up to 2×10 more).",
    "⭐ Daily quota hit, {invoker}. The cosmos offers refills — `/shop buy` for up to 20 extra actions today.",
    "🌠 {quota} cosmic actions used, {invoker}. Buy a refill from `/shop buy` to keep going.",
    "✨ {invoker}, you're out of free actions for today. Pick up extras in `/shop buy` if you need more.",
    "🔮 {invoker}, the daily {quota}-action limit is reached. `/shop buy` has refills waiting.",
]

_UNIVERSAL_BLESSING_MESSAGES: list[str] = [
    "✨ The cosmos just smiled. {caster} blessed {receiver} so hard the universe joined in — **both get 100 Boli** each!",
    "🌸 A rare cosmic alignment! {caster}'s blessing for {receiver} echoed through the stars. **+100 Boli** to both of you, mwah!",
    "💫 Oh wow. {caster} sent love to {receiver} and the universe sent it back tenfold. **100 Boli** each, as a treat!",
    "🌟 Universal Blessing activated! {caster} and {receiver} are bathed in cosmic light. **+100 Boli** each — the stars are happy today!",
    "🎀 The heavens noticed! {caster} blessed {receiver} with such sincerity that the cosmos rewarded them both. **100 Boli** each!",
]


# Messages shown when a user can't buy because they have unused purchased actions
_HAS_EXTRAS_MESSAGES: list[str] = [
    "🔋 {invoker}, you still have **{extra}** purchased action(s) left. Spend those first before buying more.",
    "You haven't used your purchased actions yet, {invoker}. You have **{extra}** remaining. Use them first!",
    "⚡ {invoker}, **{extra}** extra cosmic action(s) left in your tank. Burn through them before refilling.",
]

# Over-quota game messages (now with countdown)
_OVER_QUOTA_GAME_MESSAGES: list[str] = [
    "You've used all 5 plays for **{game}** today, {user}. Come back after midnight IST!",
    "{user}, the cosmic {game} table is closed for you today. 5 plays max. Reset at midnight IST.",
    "Out of {game} tries, {user}. You've hit the 5/day limit. See you after midnight IST!",
    "{user}, 5 games of {game} per day max. The dealer has cut you off until midnight IST.",
    "The cosmic {game} machine won't spin for you anymore today, {user}. Come back after midnight IST.",
]


async def _check_cosmic_quota(
    invoker: discord.Member | discord.User,
    reply_target: discord.Message,
    action_type: str = "curse",  # for log labelling only
) -> bool:
    """Check if the invoker can perform a cosmic action (curse or bless).

    Returns True if the action is BLOCKED (caller should return early).
    Returns False if the action is ALLOWED (caller may proceed).

    All DB reads are offloaded to a thread executor to avoid blocking the event loop.
    """
    loop = asyncio.get_running_loop()

    def _db_reads():
        daily = get_daily_action_count(db_conn, invoker.id)
        extras = get_extra_actions(db_conn, invoker.id)
        return daily, extras

    daily_count, extra_actions = await loop.run_in_executor(None, _db_reads)

    if daily_count < _DAILY_QUOTA or extra_actions > 0:
        return False

    # Quota exhausted and no extras — direct to shop.
    msg = random.choice(_OVER_QUOTA_MESSAGES).format(
        invoker=invoker.display_name, quota=_DAILY_QUOTA
    )
    await reply_target.reply(msg)
    return True


# ---------------------------------------------------------------------------
# Slang spam prevention
# ---------------------------------------------------------------------------
# Once a user sends 3+ short standalone messages within 60s (regardless of
# whether they change words), they enter a 5-minute slang cooldown during
# which NO Boli points are awarded for any slang message.
# ---------------------------------------------------------------------------

# user_id -> (consecutive_short_msg_count, last_message_time)
_slang_spam_tracker: dict[int, tuple[int, datetime]] = {}
# user_id -> datetime when the cooldown expires (5 minutes after spam triggered)
_slang_cooldown: dict[int, datetime] = {}

_SLANG_SPAM_WINDOW_SECONDS = 60   # window for consecutive short-message counting
_SLANG_SPAM_THRESHOLD = 2          # trigger cooldown after this many (3rd message = index 2)
_SLANG_COOLDOWN_MINUTES = 5        # how long points are suppressed after trigger
_SLANG_SPAM_MAX_WORDS = 2          # only track messages with ≤ this many words
_SLANG_SPAM_MAX_CHARS = 20         # and ≤ this many chars


def _check_slang_spam(user_id: int, content: str) -> bool:
    """Track short standalone messages regardless of word change.

    Returns True (suppress Boli points) if:
    - The user is currently in a 5-minute slang cooldown, OR
    - This message is the 3rd+ consecutive short message within 60s,
      which also starts the 5-minute cooldown.

    Counter increments for ANY short message within the 60s window,
    even if the user switches to a different word.
    """
    now = datetime.now(timezone.utc)

    # Check if already in cooldown
    cooldown_until = _slang_cooldown.get(user_id)
    if cooldown_until and now < cooldown_until:
        return True  # still cooling down — suppress points

    # Count consecutive short messages within the window
    count, last_time = _slang_spam_tracker.get(user_id, (0, now))
    within_window = (now - last_time).total_seconds() <= _SLANG_SPAM_WINDOW_SECONDS

    new_count = count + 1 if within_window else 1
    _slang_spam_tracker[user_id] = (new_count, now)

    if new_count >= _SLANG_SPAM_THRESHOLD + 1:
        # Enter 5-minute cooldown
        _slang_cooldown[user_id] = now + timedelta(minutes=_SLANG_COOLDOWN_MINUTES)
        logger.debug(
            "Slang spam cooldown set for user_id=%d — '%s' was the %d-th short msg in window.",
            user_id, content.strip()[:20], new_count,
        )
        return True

    return False


# ---------------------------------------------------------------------------
# Daily Lucky Draw (7am IST — 200 Boli to a random top-10 active user)
# ---------------------------------------------------------------------------

_LUCKY_DRAW_AMOUNT = 200
_LUCKY_DRAW_MESSAGES: list[str] = [
    "🪐 **Daily Navi Lucky Draw!** The cosmos took one look at the leaderboard, scratched its head, and said: {winner_mention}, it's your lucky day! **+{amount} Boli** for doing absolutely nothing productive. 🎉",
    "🎰 **Ding ding ding!** Today's cosmic jackpot winner is {winner_mention}! The stars rolled the dice, picked a name, and landed on yours. Congrats, you beautifully undeserving human. **+{amount} Boli** added! 🦄",
    "🌈 **Navi Lucky Draw Results:** After careful scientific analysis (aka pure random chance), the universe has decided {winner_mention} deserves **{amount} free Boli**. No skill required. The cosmos is chaotic like that. 🍀",
    "✨ **Lucky Draw o’clock!** {winner_mention} woke up, existed, and was randomly chosen by Navi. The reward? **+{amount} Boli**. The lesson? Sometimes just being here is enough. Spend it on something silly. 🪄",
    "💫 **Breaking news from the Cosmic Lottery Bureau:** {winner_mention} is today’s winner! Out of everyone who was active yesterday, Navi spun the wheel and — yep, it’s you. **{amount} Boli** deposited into your cosmic wallet. Go gamble it immediately. 🎨",
    "🔮 **The oracle has spoken!** Navi closed her eyes, pointed at the leaderboard, and her finger landed on {winner_mention}. **+{amount} Boli** for being in the right chat at the right time. May the Boli bless your evening. 🙏",
]


async def _lucky_draw_loop() -> None:
    """Background loop that fires a lucky draw at 7:00am IST every day.

    Picks one random user from the top-10 most-active previous day members
    and awards _LUCKY_DRAW_AMOUNT Boli Points. Announced in the general channel.
    """
    _IST = timezone(timedelta(hours=5, minutes=30))

    while True:
        # Calculate seconds until next 7:00am IST
        now_ist = datetime.now(_IST)
        target = now_ist.replace(hour=7, minute=0, second=0, microsecond=0)
        if now_ist >= target:
            target = target + timedelta(days=1)
        wait_seconds = (target - now_ist).total_seconds()
        logger.info("Lucky draw sleeping for %.0f seconds (until 7:00am IST).", wait_seconds)
        await asyncio.sleep(wait_seconds)

        try:
            # Deduplicate: check we haven't already run today
            today_str = datetime.now(_IST).strftime("%Y-%m-%d")
            last_run = get_config_str(db_conn, "lucky_draw_last_date", "")
            if last_run == today_str:
                logger.info("Lucky draw already ran today (%s) — skipping.", today_str)
                await asyncio.sleep(60)  # small extra sleep to avoid re-triggering
                continue

            result = await _run_lucky_draw()
            logger.info("Lucky draw loop: %s", result)

        except Exception as exc:
            logger.error("Lucky draw loop error: %s", exc)

        await asyncio.sleep(60)  # small buffer before recalculating next target


async def _run_lucky_draw(announce_channel: discord.abc.Messageable | None = None) -> str:
    """Core lucky-draw logic. Returns a status string for logging/feedback.

    Can be called from the scheduled loop or the /lucky_draw slash command.
    If announce_channel is provided it overrides the default channel resolution.
    """
    _IST = timezone(timedelta(hours=5, minutes=30))
    today_str = datetime.now(_IST).strftime("%Y-%m-%d")

    active_ids = get_active_user_ids_previous_day(db_conn)
    if not active_ids:
        return "No active users from the previous day — draw skipped."

    candidates = get_top_n_from_user_ids(db_conn, active_ids, n=10)
    if not candidates:
        return "No top-10 candidates found — draw skipped."

    winner = random.choice(candidates)
    update_boli_points(db_conn, winner["user_id"], _LUCKY_DRAW_AMOUNT)
    set_config_str(db_conn, "lucky_draw_last_date", today_str)

    winner_mention = f"<@{winner['user_id']}>"
    msg = random.choice(_LUCKY_DRAW_MESSAGES).format(
        winner_mention=winner_mention,
        amount=_LUCKY_DRAW_AMOUNT,
    )

    if announce_channel is None:
        _NAVI_GAMES_ID = 1499827359585665104
        _BOT_COMMANDS_ID = 1372128042058780783
        announce_channel = (
            bot.get_channel(_NAVI_GAMES_ID)
            or discord.utils.find(
                lambda c: c.name == "navi-games",
                (ch for g in bot.guilds for ch in g.text_channels),
            )
            or bot.get_channel(_BOT_COMMANDS_ID)
        )

    if announce_channel:
        await announce_channel.send(msg)
        logger.info(
            "Lucky draw winner: %s (user_id=%d) +%d Boli — announced in #%s.",
            winner["username"], winner["user_id"], _LUCKY_DRAW_AMOUNT,
            getattr(announce_channel, "name", "?"),
        )
        return f"Winner: {winner['username']} (+{_LUCKY_DRAW_AMOUNT} Boli)"
    else:
        logger.warning("Lucky draw: could not find any announce channel.")
        return f"Winner: {winner['username']} (+{_LUCKY_DRAW_AMOUNT} Boli) — no announce channel found."


async def _maybe_announce_levelup(
    mention: str,
    old_points: int,
    new_points: int,
    channel: discord.abc.Messageable,
) -> None:
    """Send a level-up notification if the points delta crossed a level or tier boundary."""
    old_level = get_level_from_points(old_points)
    new_level = get_level_from_points(new_points)
    if new_level <= old_level:
        return

    old_title = get_level_title(old_level)
    new_title = get_level_title(new_level)

    # Primary level-up line in the requested format
    msg = f"🔥 **Level Up!** {mention} reached **Level {new_level}**! Keep the boli flowing!"

    # Tier crossing: append a witty title unlock line
    if new_title != old_title:
        flavour = random.choice(LEVEL_UP_MESSAGES).format(user=mention, level=new_level)
        msg = (
            f"🔥 **Level Up!** {mention} reached **Level {new_level}**! "
            f"Earned title: [**{new_title}**]. Keep the boli flowing!\n"
            f"*{flavour}*"
        )

    await channel.send(msg)


def _format_cached_spam_reply(cached: str, name: str) -> str:
    """Strip the 'Eda/Aiyo [name],' opener from a cached prediction and
    wrap it in a varied snarky spam-reply message."""
    # Remove leading opener like "Eda Link," / "Aiyo Link," (case-insensitive)
    stripped = re.sub(
        rf"^(Hey|Oh|Eda|Aiyo)\s+{re.escape(name)}\s*[,!.]?\s*",
        "",
        cached,
        flags=re.IGNORECASE,
    ).strip()
    # Capitalise first letter of remainder
    if stripped:
        stripped = stripped[0].upper() + stripped[1:]
    prediction_text = stripped or cached  # fall back to full text if strip failed
    wrapper = random.choice(SPAM_WRAPPERS)
    return wrapper.format(prediction=prediction_text)


# ---------------------------------------------------------------------------
# Core prediction logic
# ---------------------------------------------------------------------------

async def get_navi_prediction(user_id: int, name: str, usage_count: int = 1) -> str:
    """Get a Navi prediction.

    Routing by usage_count within the current minute:
      1st call  → normal flow (50% daily cache reuse, otherwise Gemini)
      2nd call  → 35% daily cache, 65% Gemini
      3rd+ call → always daily/pool cache with artificial delay (no API)
    """
    profile = get_user_profile(db_conn, user_id)

    # Assign Rashi if missing (new user, or existing user from before Rashi was introduced)
    rashi: str | None = None if profile is None else profile.get("rashi")
    if not rashi:
        rashi = random.choice(RASHIS)
        upsert_user(db_conn, user_id, name, rashi=rashi)
        logger.info("Assigned Rashi to %s: %s", name, rashi)

    todays_pred = get_todays_user_prediction(db_conn, user_id)

    # ---- 3rd+ usage: always serve from cache, never call Gemini ----
    if usage_count >= 3:
        if todays_pred:
            await asyncio.sleep(random.uniform(1.5, 3.0))  # feel like AI
            return todays_pred
        # No daily cache yet — fall through to pool cache only (no Gemini)
        cached_pool, _ = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: api_mgr.call_cache_only(
                cache_type="navi", name=name, fallback_message=random.choice(FALLBACK_MESSAGES)
            ),
        )
        await asyncio.sleep(random.uniform(1.5, 3.0))
        return cached_pool

    # ---- 2nd usage: 35% daily cache, 65% Gemini ----
    if usage_count == 2 and todays_pred:
        if random.random() < 0.35:
            logger.info("2nd call: serving cached prediction for %s", name)
            await asyncio.sleep(random.uniform(1.0, 2.5))
            return todays_pred

    # ---- 1st usage (or 2nd fell through): normal 50% daily cache check ----
    if usage_count == 1 and todays_pred:
        cache_chance = get_config_float(db_conn, "cache_reuse_chance", 0.50)
        if random.random() < cache_chance:
            logger.info("Recycling today's prediction from cache for %s", name)
            await asyncio.sleep(random.uniform(1.0, 2.5))
            return todays_pred

    # ---- Gemini call ----
    past_predictions = get_last_n_predictions(db_conn, user_id, n=3)
    system_prompt = get_time_aware_system_prompt(db_conn, username=name)
    user_prompt = get_navi_prompt(name, rashi=rashi, past_predictions=past_predictions)

    result, from_cache = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: api_mgr.call(
            prompt=user_prompt,
            system_prompt=system_prompt,
            cache_type="navi",
            name=name,
            fallback_message=random.choice(FALLBACK_MESSAGES),
        ),
    )

    if not from_cache and result not in FALLBACK_MESSAGES:
        template = _templatize(result, name)
        save_prediction(db_conn, "navi", template, user_id=user_id, original_prompt=user_prompt)
        save_user_prediction(db_conn, user_id, result)
        increment_prediction_count(db_conn, user_id)

    return result


# ---------------------------------------------------------------------------
# Application emoji LRU helpers
# ---------------------------------------------------------------------------

_APP_EMOJI_RE = re.compile(r"<a?:\w+:(\d+)>")


# Matches custom emojis: <:name:id> or <a:name:id>
_CUSTOM_EMOJI_RE = re.compile(r"<(a?):(\w+):(\d+)>")

# Local media shortcut trigger (;;name)
_LOCAL_SHORTCUT_RE = re.compile(r"^;;(\w+)", re.IGNORECASE)
_LOCAL_MEDIA_DIR = "data/local_media"





# ---------------------------------------------------------------------------
# Local Media Stealing — context menus + ;;shortcut trigger
# ---------------------------------------------------------------------------

def _sanitize_shortcut(raw: str) -> str | None:
    """Lowercase, replace spaces/hyphens with underscores, strip non-word chars.
    Returns None if the result is too short to be useful."""
    cleaned = re.sub(r"[\s\-]+", "_", raw.lower())
    cleaned = re.sub(r"[^\w]", "", cleaned).strip("_")
    return cleaned if len(cleaned) >= 2 else None

async def _evict_and_upload_app_emoji(
    name: str,
    image_bytes: bytes,
    animated: bool = False,
) -> tuple[str, str] | None:
    """Upload image_bytes as a Discord Application Emoji, evicting LRU if at limit.

    Returns (emoji_id, emoji_name) on success, or None on failure.
    The name is sanitized to comply with Discord's emoji name rules.
    """
    # Sanitize: only word chars, 2-32 length
    safe_name = re.sub(r"[^\w]", "_", name)[:32]
    if len(safe_name) < 2:
        safe_name = "emoji_" + safe_name

    # Evict if at limit
    if count_app_emojis(db_conn) >= _APP_EMOJI_EVICT_THRESHOLD:
        oldest = get_oldest_app_emoji(db_conn)
        if oldest:
            try:
                await bot.delete_application_emoji(int(oldest["emoji_id"]))
                logger.info("Evicted LRU app emoji %s (%s)", oldest["emoji_id"], oldest["name"])
            except Exception as exc:
                logger.warning("Could not delete app emoji %s: %s", oldest["emoji_id"], exc)
            delete_app_emoji_record(db_conn, oldest["emoji_id"])

    try:
        new_emoji = await bot.create_application_emoji(name=safe_name, image=image_bytes)
        save_app_emoji(db_conn, str(new_emoji.id), new_emoji.name, animated=animated)
        return str(new_emoji.id), new_emoji.name
    except Exception as exc:
        logger.error("Failed to create application emoji '%s': %s", safe_name, exc)
        return None


async def _auto_save_media(
    interaction: discord.Interaction,
    storage_type: str,
    media_type: str,
    suggested_name: str,
    download_url: str,
    file_ext: str = "png",
) -> None:
    """Fully-automated save: defer → resolve name → download → upload → confirm.

    No modal is shown. The name is taken from the source (emoji/sticker name) and
    a numeric suffix is appended automatically if the shortcut is already taken.
    Users type ;;name in chat to replay the saved media; the ;; is just the chat
    trigger prefix and is never entered by the user during saving.
    """
    user_id = interaction.user.id

    # ---- Limits ----
    user_count = get_user_local_media_count(db_conn, user_id)
    max_per_user = get_config_int(db_conn, "local_media_max_per_user", 20)
    if user_count >= max_per_user:
        await interaction.followup.send(
            f"⚠️ You’ve hit your media limit ({max_per_user} items). "
            f"Delete some with `/my_media` first.",
            ephemeral=True,
        )
        return

    global_count = get_global_local_media_count(db_conn)
    max_global = get_config_int(db_conn, "local_media_max_global", 200)
    if global_count >= max_global:
        await interaction.followup.send(
            f"⚠️ The global media storage is full ({max_global} items). Contact an admin.",
            ephemeral=True,
        )
        return

    # ---- Resolve unique name (auto-suffix on collision) ----
    base = _sanitize_shortcut(suggested_name) or "saved_emoji"
    name = base
    suffix = 1
    while get_local_media(db_conn, name):  # name taken by anyone
        name = f"{base}_{suffix}"
        suffix += 1
        if suffix > 99:
            await interaction.followup.send(
                "⚠️ Couldn’t find a free shortcut name. "
                f"Try deleting an old `;;{base}*` entry with `/my_media`.",
                ephemeral=True,
            )
            return

    # ---- Download ----
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        f"⚠️ Couldn’t download the media (CDN returned {resp.status}).",
                        ephemeral=True,
                    )
                    return
                data = await resp.read()
    except Exception as exc:
        logger.error("_auto_save_media: download failed for %s: %s", download_url, exc)
        await interaction.followup.send("⚠️ Download failed. Check the logs.", ephemeral=True)
        return

    # ---- Upload as Application Emoji ----
    result = await _evict_and_upload_app_emoji(name, data, animated=(file_ext == "gif"))
    if result is None:
        await interaction.followup.send(
            "⚠️ Failed to upload as a Discord emoji. "
            "The image may be too large (max 256 KB) or an unsupported format.",
            ephemeral=True,
        )
        return

    emoji_id, emoji_name = result
    save_local_media(
        db_conn,
        user_id=user_id,
        shortcut=name,
        file_path="",
        media_type=media_type,
        source_url=download_url,
        storage_type=storage_type,
        discord_id=emoji_id,
        discord_name=emoji_name,
        animated=(file_ext == "gif"),
    )

    animated = file_ext == "gif"
    emoji_str = f"<{'a' if animated else ''}:{emoji_name}:{emoji_id}>"
    await interaction.followup.send(
        f"✅ Saved {emoji_str} as `;;{name}` — "
        f"type `;;{name}` in any channel and I’ll post it!",
        ephemeral=True,
    )
    logger.info(
        "User %d auto-saved %s ';;%s' (emoji_id=%s)",
        user_id, media_type, name, emoji_id,
    )


@tree.context_menu(name="Save Emoji/Sticker")
async def save_emoji_sticker_context(interaction: discord.Interaction, message: discord.Message) -> None:
    """Right-click → Apps → Save Emoji/Sticker.

    Auto-detects emoji or sticker from the message, downloads it, uploads it as
    a Discord Application Emoji, and saves it under an auto-generated shortcut
    name — no modal, no user input required. Users type ;;name in chat to replay.
    """
    if not _feat("feature_local_media"):
        await interaction.response.send_message("Local media shortcuts are currently disabled.", ephemeral=True)
        return

    # Defer immediately — download + upload can take a second
    await interaction.response.defer(ephemeral=True, thinking=True)

    # --- Detect: custom emoji in message content ---
    matches = _CUSTOM_EMOJI_RE.findall(message.content)
    if matches:
        if len(matches) > 1:
            await interaction.followup.send(
                f"That message has **{len(matches)}** custom emojis — pick a message with exactly one.",
                ephemeral=True,
            )
            return
        animated_flag, emoji_name, emoji_id = matches[0]
        ext = "gif" if animated_flag else "png"
        emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}?size=128"
        await _auto_save_media(
            interaction,
            storage_type="app_emoji",
            media_type="emoji",
            suggested_name=emoji_name,
            download_url=emoji_url,
            file_ext=ext,
        )
        return

    # --- Detect: sticker on the message ---
    if message.stickers:
        sticker = message.stickers[0]
        sticker_url = str(sticker.url)
        ext_part = sticker_url.rsplit(".", 1)[-1].split("?")[0].lower()
        if ext_part == "json":
            await interaction.followup.send(
                "⚠️ That sticker uses an animated Lottie format which can’t be saved as a bot emoji. "
                "Try a standard PNG or GIF sticker.",
                ephemeral=True,
            )
            return
        await _auto_save_media(
            interaction,
            storage_type="app_emoji_sticker",
            media_type="sticker",
            suggested_name=sticker.name,
            download_url=sticker_url,
            file_ext="png",
        )
        return

    await interaction.followup.send(
        "No custom emoji or sticker found in that message.\n"
        "*Tip: The emoji must be a **custom** emoji (not a standard Unicode one).*",
        ephemeral=True,
    )


@tree.command(name="my_media", description="List your saved local media shortcuts")
async def my_media_slash(interaction: discord.Interaction) -> None:
    """Show the caller's saved ;;shortcut entries."""
    if not _feat("feature_local_media"):
        await interaction.response.send_message("Local media shortcuts are currently disabled.", ephemeral=True)
        return
    entries = list_user_local_media(db_conn, interaction.user.id)
    if not entries:
        await interaction.response.send_message(
            "You have no saved media. Right-click a message → **Apps** → **Save Emoji/Sticker**.",
            ephemeral=True,
        )
        return

    max_per_user = get_config_int(db_conn, "local_media_max_per_user", 20)
    lines = []
    for e in entries:
        st = e.get("storage_type", "local")
        preview = ""
        if st in ("app_emoji", "app_emoji_sticker"):
            did = e.get("discord_id")
            dname = e.get("discord_name") or e["shortcut"]
            animated = e.get("animated", False)
            if did:
                preview = f"<{'a' if animated else ''}:{dname}:{did}> "
        type_label = e["media_type"].capitalize()
        lines.append(f"{preview}`;;{e['shortcut']}` — {type_label}")

    embed = discord.Embed(
        title="Your Saved Media",
        description="\n".join(lines),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"{len(entries)}/{max_per_user} slots used")
    await interaction.response.send_message(embed=embed, ephemeral=True)


class LocalMediaPickerView(discord.ui.View):
    """Select menu for 'Reply with Media' — lets user pick one of their saved ;;shortcuts."""

    def __init__(self, target_message: discord.Message, entries: list[dict]) -> None:
        super().__init__(timeout=60)
        self._target = target_message

        options = []
        for e in entries[:25]:  # Discord select cap
            # entries now include storage_type, discord_id, discord_name, animated
            st = e.get("storage_type", "local")
            picker_emoji: discord.PartialEmoji | None = None
            if st in ("app_emoji", "app_emoji_sticker", "native_emoji"):
                did = e.get("discord_id")
                dname = e.get("discord_name") or e["shortcut"]
                animated = e.get("animated", False)
                if did:
                    try:
                        picker_emoji = discord.PartialEmoji(
                            name=dname, id=int(did), animated=animated
                        )
                    except Exception:
                        picker_emoji = None
            type_label = e["media_type"].capitalize()  # "Emoji" or "Sticker"
            options.append(
                discord.SelectOption(
                    label=f";;{e['shortcut']}",
                    value=e["shortcut"],
                    description=type_label,
                    emoji=picker_emoji,
                )
            )
        select = discord.ui.Select(placeholder="Pick a saved shortcut to reply with…", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        shortcut_key = interaction.data["values"][0]
        media_entry = get_local_media(db_conn, shortcut_key)
        if media_entry is None:
            await interaction.response.edit_message(content="That shortcut no longer exists.", view=None)
            return

        storage_type = media_entry.get("storage_type", "local")
        attribution = f"-# Sent by {interaction.user.display_name}"

        try:
            if storage_type in ("native_emoji", "app_emoji"):
                animated = media_entry.get("animated", False)
                dname = media_entry.get("discord_name") or shortcut_key
                did = media_entry.get("discord_id")
                emoji_str = f"<{'a' if animated else ''}:{dname}:{did}>"
                if storage_type == "app_emoji" and did:
                    update_app_emoji_last_used(db_conn, did)
                await self._target.reply(content=f"{emoji_str}\n{attribution}")
            elif storage_type == "app_emoji_sticker":
                animated = media_entry.get("animated", False)
                dname = media_entry.get("discord_name") or shortcut_key
                did = media_entry.get("discord_id")
                emoji_str = f"<{'a' if animated else ''}:{dname}:{did}>"
                if did:
                    update_app_emoji_last_used(db_conn, did)
                # Send emoji alone first so Discord enlarges it, then attribution
                await self._target.reply(content=emoji_str)
                await self._target.channel.send(attribution)
            elif storage_type == "native_sticker":
                sticker_url = media_entry.get("source_url")
                if sticker_url:
                    import aiohttp as _aiohttp
                    async with _aiohttp.ClientSession() as session:
                        async with session.get(sticker_url) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                ext = sticker_url.rsplit(".", 1)[-1].split("?")[0] or "png"
                                await self._target.reply(
                                    content=attribution,
                                    file=discord.File(
                                        io.BytesIO(data),
                                        filename=f"{shortcut_key}.{ext}",
                                    ),
                                )
                            else:
                                await self._target.reply(content=f"Sticker CDN unavailable ({resp.status}).")
                else:
                    await interaction.response.edit_message(
                        content=f"Sticker URL for `;;{shortcut_key}` is missing.", view=None
                    )
                    return
            else:
                # local file
                import os as _os
                if not _os.path.isfile(media_entry["file_path"]):
                    await interaction.response.edit_message(
                        content=f"File for `;;{shortcut_key}` is missing from disk.", view=None
                    )
                    return
                await self._target.reply(
                    content=attribution,
                    file=discord.File(media_entry["file_path"]),
                )
            await interaction.response.edit_message(content=f"Replied with `;;{shortcut_key}`!", view=None)
        except Exception as exc:
            err_text = getattr(exc, "text", None) or str(exc)
            await interaction.response.edit_message(content=f"Could not send: {err_text}", view=None)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True



@tree.context_menu(name="Reply with Emoji")
async def reply_with_emoji_context(interaction: discord.Interaction, message: discord.Message) -> None:
    """Right-click → Apps → Reply with Emoji: pick a saved emoji shortcut and reply to this message with it."""
    if not _feat("feature_local_media"):
        await interaction.response.send_message("Local media shortcuts are currently disabled.", ephemeral=True)
        return
    entries = list_user_local_media(db_conn, interaction.user.id, media_type="emoji")
    if not entries:
        await interaction.response.send_message(
            "You have no saved emojis. Right-click a message → **Apps** → **Save Emoji/Sticker** first.",
            ephemeral=True,
        )
        return

    view = LocalMediaPickerView(target_message=message, entries=entries)
    await interaction.response.send_message(
        "Which saved emoji should I reply with?", view=view, ephemeral=True
    )


@tree.context_menu(name="Reply with Sticker")
async def reply_with_sticker_context(interaction: discord.Interaction, message: discord.Message) -> None:
    """Right-click → Apps → Reply with Sticker: pick a saved sticker shortcut and reply to this message with it."""
    if not _feat("feature_local_media"):
        await interaction.response.send_message("Local media shortcuts are currently disabled.", ephemeral=True)
        return
    entries = list_user_local_media(db_conn, interaction.user.id, media_type="sticker")
    if not entries:
        await interaction.response.send_message(
            "You have no saved stickers. Right-click a message → **Apps** → **Save Emoji/Sticker** first.",
            ephemeral=True,
        )
        return

    view = LocalMediaPickerView(target_message=message, entries=entries)
    await interaction.response.send_message(
        "Which saved sticker should I reply with?", view=view, ephemeral=True
    )



# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

_BACKUP_DIR = "data/backups"
_BACKUP_KEEP = 24  # rolling window — oldest is dropped when limit is hit
_BACKUP_INTERVAL_HOURS = 1


def _do_backup() -> str:
    """Copy the DB file. Returns the backup path. Runs in executor.

    SQLite WAL mode makes live file copies safe — no manual checkpoint needed.
    """
    import pathlib
    pathlib.Path(_BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    dest = os.path.join(_BACKUP_DIR, f"astro_bot_{ts}.db")
    shutil.copy2(str(DB_PATH), dest)
    # Prune oldest backups beyond the rolling window
    backups = sorted(
        [f for f in os.listdir(_BACKUP_DIR) if f.startswith("astro_bot_") and f.endswith(".db")]
    )
    for old in backups[:-_BACKUP_KEEP]:
        try:
            os.remove(os.path.join(_BACKUP_DIR, old))
        except OSError:
            pass
    return dest


async def _backup_loop() -> None:
    """Background task: backup the database every hour."""
    await asyncio.sleep(60)  # small initial delay so on_ready finishes first
    while True:
        try:
            path = await asyncio.get_running_loop().run_in_executor(None, _do_backup)
            logger.info("DB backup written to %s", path)
        except Exception as exc:
            logger.error("DB backup failed: %s", exc)
        await asyncio.sleep(_BACKUP_INTERVAL_HOURS * 3600)


@bot.event
async def on_ready() -> None:
    global db_conn, gemini_svc, api_mgr

    logger.info("Logged in as %s", bot.user)

    # Initialise database
    db_conn = init_db()
    seed_local_knowledge(db_conn)
    log_session_event(db_conn, "start")

    # Initialise Gemini service (free key first, paid as fallback)
    gemini_svc = GeminiService(
        free_api_key=FREE_API_KEY,
        paid_api_key=PAID_API_KEY,
        db_conn=db_conn,
    )

    # Initialise rate limiter
    api_mgr = ApiManager(
        gemini_service=gemini_svc,
        db_conn=db_conn,
        rpm_limit=10,
        free_tier_mode=FREE_TIER_MODE,
    )

    # Load feature toggles into memory cache
    _load_feature_cache()

    # Load local slang dictionary and response templates into memory
    load_slang_data()
    load_templates()

    # Ensure local media storage directory exists
    import os as _os
    _os.makedirs(_LOCAL_MEDIA_DIR, exist_ok=True)

    # Start hourly DB backup loop
    asyncio.create_task(_backup_loop())

    # Start daily lucky draw loop (fires at 7:00am IST)
    asyncio.create_task(_lucky_draw_loop())

    await tree.sync()
    logger.info("Slash commands synced. Navi is live. Hey! Listen!")


@bot.event
async def on_close() -> None:
    """Backup then close the DB cleanly on shutdown."""
    if db_conn is not None:
        try:
            log_session_event(db_conn, "stop")
        except Exception:
            pass
        try:
            loop = asyncio.get_event_loop()
            path = await loop.run_in_executor(None, _do_backup)
            logger.info("Shutdown DB backup written to %s", path)
        except Exception as exc:
            logger.warning("Shutdown backup failed: %s", exc)
        try:
            db_conn.close()
            logger.info("SQLite connection closed cleanly.")
        except Exception as exc:
            logger.warning("Error closing SQLite on shutdown: %s", exc)


# MODA_INTROS, BOT_SELF_CURSE_REPLIES, BOT_LOOP_CURSE_REPLIES imported from prompts.py


@bot.event
async def on_interaction(interaction: discord.Interaction) -> None:
    """Log every slash command invocation to command_events for analytics."""
    if interaction.type != discord.InteractionType.application_command:
        return
    if db_conn is None or not interaction.data:
        return
    try:
        log_command_event(
            db_conn,
            interaction.user.id,
            str(interaction.user),
            interaction.data.get("name", "unknown"),
            channel_id=interaction.channel_id,
            guild_id=interaction.guild_id,
        )
    except Exception:
        pass


@bot.event
async def on_member_join(member: discord.Member) -> None:
    """Welcome new member after a 1-minute delay, routing on verification status."""
    channel = (
        discord.utils.get(member.guild.text_channels, name=GENERAL_CHANNEL)
        or discord.utils.get(member.guild.text_channels, name="general")
        or member.guild.system_channel
    )
    if not channel:
        logger.warning("Could not find a welcome channel for new member %s", member.display_name)
        return

    upsert_user(db_conn, member.id, member.display_name)

    if not get_config_int(db_conn, "feature_welcome", 1):
        logger.info("Welcome messages disabled — skipping for %s", member.display_name)
        return

    # 1-minute delay before posting (non-blocking)
    await asyncio.sleep(60)

    # Re-fetch the member so we get the most current state (pending flag, still in guild, etc.)
    try:
        current_member = await member.guild.fetch_member(member.id)
    except discord.NotFound:
        logger.info("Member %s left before welcome message was sent.", member.display_name)
        return

    if current_member.pending:
        # User hasn't verified phone number / accepted screening rules yet
        welcome_line = random.choice(PENDING_WELCOME_MESSAGES).format(user=current_member.mention)
        await channel.send(welcome_line)
        logger.info("Sent pending/verify reminder to %s in #%s", current_member.display_name, channel.name)
    else:
        # Fully verified — standard welcome + Moda intro
        welcome_line = random.choice(WELCOME_MESSAGES).format(user=current_member.mention)
        moda_line = random.choice(MODA_INTROS)
        await channel.send(f"{welcome_line} {moda_line}")
        logger.info("Welcomed verified member %s in #%s", current_member.display_name, channel.name)


@bot.event
async def on_message(message: discord.Message) -> None:
    # Guard against messages arriving before on_ready finishes initialising
    if db_conn is None:
        return

    # Master kill switch — ignore everything (except owner text commands)
    if _feat("master_killswitch"):
        return

    # Ignore other bots
    if message.author.bot:
        return

    # Ignore replies to bot's own messages
    if message.reference:
        resolved = message.reference.resolved or message.reference.cached_message
        if isinstance(resolved, discord.Message) and resolved.author.id == bot.user.id:
            return

    content_lower = message.content.strip().lower()

    # ---- Admin toggle ----
    if content_lower in ("navi syros stop", "navi syros start"):
        app_info = await bot.application_info()
        if message.author.id == app_info.owner.id:
            gemini_svc.free_only = content_lower == "navi syros stop"
            status = "now running in Free-Only Mode (will fallback to cache if free key fails)." if gemini_svc.free_only else "Gemini Paid Tier is back online."
            await message.reply(f"Ok owner, {status}")
        return

    # Upsert user record only on first message per session (in-memory cache to avoid DB write on every message)
    if message.author.id not in _seen_users:
        upsert_user(db_conn, message.author.id, message.author.display_name)
        _seen_users.add(message.author.id)

    # ---- Local media shortcut trigger (;;name) ----
    shortcut_match = _LOCAL_SHORTCUT_RE.match(message.content.strip())
    if shortcut_match and _feat("feature_local_media"):
        shortcut_key = shortcut_match.group(1).lower()
        media_entry = get_local_media(db_conn, shortcut_key)
        if media_entry:
            storage_type = media_entry.get("storage_type", "local")
            # Mirror reply chain if the ;;shortcut message is itself a reply
            reply_target = (
                message.reference.resolved
                if message.reference and isinstance(message.reference.resolved, discord.Message)
                else None
            )

            if storage_type in ("native_emoji", "app_emoji"):
                animated = media_entry.get("animated", False)
                dname = media_entry.get("discord_name") or shortcut_key
                did = media_entry.get("discord_id")
                emoji_str = f"<{'a' if animated else ''}:{dname}:{did}>"
                if storage_type == "app_emoji" and did:
                    update_app_emoji_last_used(db_conn, did)
                if reply_target:
                    await reply_target.reply(content=emoji_str)
                else:
                    await message.channel.send(content=emoji_str)

            elif storage_type == "app_emoji_sticker":
                animated = media_entry.get("animated", False)
                dname = media_entry.get("discord_name") or shortcut_key
                did = media_entry.get("discord_id")
                emoji_str = f"<{'a' if animated else ''}:{dname}:{did}>"
                if did:
                    update_app_emoji_last_used(db_conn, did)
                if reply_target:
                    await reply_target.reply(content=emoji_str)
                else:
                    await message.channel.send(content=emoji_str)

            elif storage_type == "native_sticker":
                sticker_url = media_entry.get("source_url")
                if sticker_url:
                    import aiohttp as _aiohttp
                    try:
                        async with _aiohttp.ClientSession() as session:
                            async with session.get(sticker_url) as resp:
                                if resp.status == 200:
                                    data = await resp.read()
                                    ext = sticker_url.rsplit(".", 1)[-1].split("?")[0] or "png"
                                    f = discord.File(
                                        io.BytesIO(data),
                                        filename=f"{shortcut_key}.{ext}",
                                    )
                                    if reply_target:
                                        await reply_target.reply(file=f)
                                    else:
                                        await message.channel.send(file=f)
                    except Exception as exc:
                        logger.warning(";;%s sticker fetch failed: %s", shortcut_key, exc)
                        await message.reply(f"Could not fetch sticker for `;;{shortcut_key}`.")
                else:
                    await message.reply(f"Sticker URL for `;;{shortcut_key}` is missing.")

            else:
                # local file (original behavior)
                import os as _os
                if _os.path.isfile(media_entry["file_path"]):
                    if reply_target:
                        await reply_target.reply(file=discord.File(media_entry["file_path"]))
                    else:
                        await message.channel.send(file=discord.File(media_entry["file_path"]))
                else:
                    await message.reply(f"Media file for `;;{shortcut_key}` is missing from disk.")

        return  # shortcut messages don't trigger any other processing

    # ---- Vibe Check: lightweight heat tracker — no API cost unless triggered ----
    if _feat("feature_vibe_check"):
        await _check_vibe(message)

    # ---- Slang Spam Prevention: gate Boli awards for repeat short messages ----
    # Check BEFORE awarding points. If this is a repeated standalone word/phrase
    # (same content, <=2 words, sent 3+ times within 60s), suppress all point awards.
    _stripped = message.content.strip()
    _words = _stripped.split()
    _is_slang_spam = (
        len(_words) <= _SLANG_SPAM_MAX_WORDS
        and len(_stripped) <= _SLANG_SPAM_MAX_CHARS
        and bool(_stripped)
        and _check_slang_spam(message.author.id, _stripped)
    )

    # ---- Boli Points: local slang triggers ----
    triggered_words = contains_boli_trigger(message.content) if get_config_int(db_conn, "feature_boli_points", 1) else []
    if triggered_words and not _is_slang_spam:
        points = len(triggered_words) * 5
        profile = get_user_profile(db_conn, message.author.id)
        old_pts = profile["boli_points"] if profile else 0
        update_boli_points(db_conn, message.author.id, points)
        await _maybe_announce_levelup(
            message.author.mention, old_pts, old_pts + points, message.channel
        )
        logger.debug(
            "%s triggered Boli words %s → +%d pts",
            message.author.display_name, triggered_words, points
        )

    # ---- Slang Dictionary Scoring (TVM +pts / Kochi -pts, tiered) ----
    if get_config_int(db_conn, "feature_boli_points", 1) and not _is_slang_spam:
        slang_delta = score_message(message.content)
        if slang_delta != 0:
            profile = get_user_profile(db_conn, message.author.id)
            old_pts = profile["boli_points"] if profile else 0
            update_boli_points(db_conn, message.author.id, slang_delta)
            if slang_delta > 0:
                await _maybe_announce_levelup(
                    message.author.mention, old_pts, old_pts + slang_delta, message.channel
                )
            matches = get_slang_matches(message.content)
            logger.debug(
                "%s slang score %+d pts — matches: %s",
                message.author.display_name,
                slang_delta,
                [(m["token"], m["region"], m["points"]) for m in matches],
            )



    # ---- Kochi slang detection ----
    kochi_chance = get_config_float(db_conn, "kochi_reply_chance", 0.28)
    if get_config_int(db_conn, "feature_kochi_replies", 1) and contains_kochi_slang(message.content) and random.random() < kochi_chance:
        response = get_random_kochi_response(message.author.mention)
        await message.reply(response)
        logger.info("Kochi slang detected from %s — condescending reply sent.", message.author.display_name)
        return

    # ---- Bot mention: Q&A ----
    if bot.user.mentioned_in(message):
        content_without_ping = (
            message.content
            .replace(f"<@{bot.user.id}>", "")
            .replace(f"<@!{bot.user.id}>", "")
            .strip()
        )
        if content_without_ping and not contains_curse_word(message.content)[0]:
            system_prompt = get_time_aware_system_prompt(db_conn, username=message.author.display_name)
            user_prompt = get_qa_prompt(
                message.author.display_name,
                content_without_ping,
                is_link=(message.author.display_name == LINK_USERNAME),
            )

            async def _do_qa_call() -> str:
                await asyncio.sleep(random.uniform(1.0, 2.0))
                reply, _ = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: api_mgr.call(
                        prompt=user_prompt,
                        system_prompt=system_prompt,
                        cache_type="qa",
                        name=message.author.display_name,
                        fallback_message=random.choice(FALLBACK_MESSAGES),
                    ),
                )
                return reply

            if FREE_TIER_MODE:
                async with message.channel.typing():
                    reply = await _do_qa_call()
            else:
                reply = await _do_qa_call()

            await message.reply(reply)
        # Always return after a mention — never double-trigger slang detection
        return

    # ---- Legacy prefix "navi" command — only triggers on the exact word "navi" ----
    if _feat("feature_curses") and re.match(r'^navi\b', message.content, re.IGNORECASE):
        invoker = message.author
        try:
            log_command_event(
                db_conn, invoker.id, str(invoker), "curse",
                channel_id=message.channel.id,
                guild_id=message.guild.id if message.guild else None,
            )
        except Exception:
            pass

        # "navi @username" → curse the mentioned user (tiered points, quota-tracked)
        if message.mentions:
            target = message.mentions[0]
            if target.id == bot.user.id:
                await message.reply(random.choice(BOT_SELF_CURSE_REPLIES))
                return
            if target.bot:
                await message.reply(f"{invoker.mention} {random.choice(BOT_LOOP_CURSE_REPLIES)}")
                return

            # --- Curse Timeout check: invoker may be blocked by a Timeout Ticket ---
            if has_active_perk(db_conn, invoker.id, "curse_timeout"):
                update_boli_points(db_conn, invoker.id, -3)
                await message.reply(
                    f"🚫 {invoker.display_name}, you're under a **Timeout Ticket**! "
                    f"Casting curses is blocked. That stunt cost you **3 Boli**."
                )
                logger.info("%s tried to curse while timed out — -3 Boli penalty", invoker.display_name)
                return

            # --- Daily quota check ---
            if await _check_cosmic_quota(invoker, message, "curse"):
                return

            # --- Vampiric Karma Gamble ---
            curse_data = get_random_curse()
            curse_word = curse_data["word"]

            # Check if target has curse protection or deflector shield
            target_protected = has_active_perk(db_conn, target.id, "curse_protection")
            target_shielded = has_active_perk(db_conn, target.id, "deflector_shield")

            if target_protected or target_shielded:
                reversal_chance = 1.0  # Protected/shielded targets always bounce the curse back
            else:
                _tier_key = {"Mild": "backfire_chance_mild", "Moderate": "backfire_chance_moderate", "Severe": "backfire_chance_severe"}
                reversal_chance = get_config_float(db_conn, _tier_key[curse_data["tier"]], curse_data["backfire_chance"])

            daily_count = increment_daily_action_count(db_conn, invoker.id)
            if daily_count > _DAILY_QUOTA:
                decrement_extra_actions(db_conn, invoker.id)

            first_reversed = random.random() < reversal_chance
            dmg = curse_data["target_damage"]
            if first_reversed:
                # Backfire — symmetric reversal: invoker loses, target gains
                update_boli_points(db_conn, invoker.id, -dmg)
                update_boli_points(db_conn, target.id, dmg)
                log_curse(db_conn, invoker.id, invoker.display_name, f"backfire_{curse_word}")
                if target_protected:
                    perk_label = "Curse Protection"
                elif target_shielded:
                    perk_label = "Deflector Shield"
                else:
                    perk_label = None
                if perk_label:
                    await message.reply(
                        f"The cosmos rejected your negativity, {invoker.display_name}. "
                        f"{target.display_name} has an active **{perk_label}**. "
                        f"The curse reversed — you lose **{dmg} pts**, {target.display_name} gains **{dmg} pts**. {curse_word}!"
                    )
                else:
                    await message.reply(
                        f"The cosmos rejected your negativity, {invoker.display_name}. "
                        f"The curse reversed — you lose **{dmg} pts**, {target.display_name} gains **{dmg} pts**. {curse_word}!"
                    )
                logger.info(
                    "Curse backfired on %s [%s, invoker -%d, target +%d]",
                    invoker.display_name, curse_data["tier"], dmg, dmg,
                )
            else:
                # Curse lands — target loses, invoker gains
                if not target_protected and not target_shielded:
                    update_boli_points(db_conn, target.id, -dmg)
                update_boli_points(db_conn, invoker.id, curse_data["invoker_reward"])
                log_curse(db_conn, target.id, target.display_name, curse_word)
                await message.reply(f"{curse_word} {target.display_name}")
                logger.info(
                    "%s cursed %s [%s, %s-%d target, +%d invoker]",
                    invoker.display_name, target.display_name,
                    curse_data["tier"], "protected, no " if (target_protected or target_shielded) else "-",
                    curse_data["target_damage"],
                    curse_data["invoker_reward"],
                )

            # --- Multiplier Potion: fire a second curse after a delay ---
            if has_active_perk(db_conn, invoker.id, "multiplier_potion"):
                second_curse = get_random_curse()

                async def _fire_second_curse() -> None:
                    delay = random.randint(15, 45)
                    await asyncio.sleep(delay)
                    if first_reversed:
                        potion_dmg = second_curse["target_damage"]
                        update_boli_points(db_conn, invoker.id, -potion_dmg)
                        update_boli_points(db_conn, target.id, potion_dmg)
                        log_curse(db_conn, invoker.id, invoker.display_name, f"potion_backfire_{second_curse['word']}")
                        await message.channel.send(
                            f"⚡ **Potion echo!** `{second_curse['word']}` — the 2nd curse reversed! "
                            f"{invoker.display_name} loses **{potion_dmg} pts**, {target.display_name} gains **{potion_dmg} pts**."
                        )
                    else:
                        if not target_protected and not target_shielded:
                            update_boli_points(db_conn, target.id, -second_curse["target_damage"])
                        update_boli_points(db_conn, invoker.id, second_curse["invoker_reward"])
                        log_curse(db_conn, target.id, target.display_name, f"potion_{second_curse['word']}")
                        await message.channel.send(
                            f"⚡ **Potion echo!** {second_curse['word']} {target.display_name} "
                            f"(2nd curse landed, +{second_curse['invoker_reward']} Boli)"
                        )

                asyncio.create_task(_fire_second_curse())

            return

        # "navi" as a reply to someone → curse the replied-to user
        if message.reference:
            resolved = message.reference.resolved or message.reference.cached_message
            if isinstance(resolved, discord.Message) and not resolved.author.bot:
                target = resolved.author
                if await _check_cosmic_quota(invoker, message, "curse"):
                    return
                curse_data = get_random_curse()
                target_protected = has_active_perk(db_conn, target.id, "curse_protection")
                target_shielded = has_active_perk(db_conn, target.id, "deflector_shield")
                if target_protected or target_shielded:
                    reversal_chance = 1.0
                else:
                    _tier_key = {"Mild": "backfire_chance_mild", "Moderate": "backfire_chance_moderate", "Severe": "backfire_chance_severe"}
                    reversal_chance = get_config_float(db_conn, _tier_key[curse_data["tier"]], curse_data["backfire_chance"])
                daily_count = increment_daily_action_count(db_conn, invoker.id)
                if daily_count > _DAILY_QUOTA:
                    decrement_extra_actions(db_conn, invoker.id)
                dmg = curse_data["target_damage"]
                if random.random() < reversal_chance:
                    update_boli_points(db_conn, invoker.id, -dmg)
                    update_boli_points(db_conn, target.id, dmg)
                    log_curse(db_conn, invoker.id, invoker.display_name, f"backfire_{curse_data['word']}")
                    await message.reply(
                        f"The cosmos rejected your negativity, {invoker.display_name}. "
                        f"The curse reversed — you lose **{dmg} pts**, {target.display_name} gains **{dmg} pts**. {curse_data['word']}!"
                    )
                    logger.info("Curse (reply) backfired on %s [-%d / +%d]", invoker.display_name, dmg, dmg)
                else:
                    if not target_protected and not target_shielded:
                        update_boli_points(db_conn, target.id, -dmg)
                    update_boli_points(db_conn, invoker.id, curse_data["invoker_reward"])
                    log_curse(db_conn, target.id, target.display_name, curse_data["word"])
                    await message.reply(f"{curse_data['word']} {target.display_name}")
                    logger.info("%s cursed %s via navi reply [+%d / -%d]", invoker.display_name, target.display_name, curse_data["invoker_reward"], dmg)
                return

        # "navi" with no mention and no reply → self-curse
        target = invoker
        if await _check_cosmic_quota(invoker, message, "curse"):
            return
        curse_data = get_random_curse()
        update_boli_points(db_conn, target.id, -curse_data["target_damage"])
        daily_count = increment_daily_action_count(db_conn, invoker.id)
        if daily_count > _DAILY_QUOTA:
            decrement_extra_actions(db_conn, invoker.id)
        log_curse(db_conn, target.id, target.display_name, "proxy_navi_self")
        await message.reply(f"{curse_data['word']} {target.display_name}")
        logger.info("%s got self-cursed via navi prefix", target.display_name)
        return

    # ---- "chunk @user" — bless command (quota-tracked, no target mention) ----
    if _feat("feature_curses") and re.match(r'^chunk\b', message.content, re.IGNORECASE):
        invoker = message.author
        try:
            log_command_event(
                db_conn, invoker.id, str(invoker), "bless",
                channel_id=message.channel.id,
                guild_id=message.guild.id if message.guild else None,
            )
        except Exception:
            pass

        def _bless_target(target: discord.Member | discord.User, award_points: bool) -> tuple[str, int, int, str]:
            """Returns (reply_text, target_pts, caster_pts, word)."""
            compliment = get_random_compliment()
            if award_points:
                target_pts = compliment["points"]
                caster_pts = max(1, round(target_pts * compliment["caster_share_pct"] / 100))
            else:
                target_pts = caster_pts = 0
            return f"{compliment['word']} {target.display_name}", target_pts, caster_pts, compliment["word"]

        async def _do_bless(target: discord.Member | discord.User) -> None:
            """Apply blessing: quota check → universal blessing roll → normal blessing."""
            if await _check_cosmic_quota(invoker, message, "bless"):
                return

            new_daily = increment_daily_action_count(db_conn, invoker.id)
            if new_daily > _DAILY_QUOTA:
                decrement_extra_actions(db_conn, invoker.id)

            # Configurable universal blessing chance (default 5%)
            if random.random() < get_config_float(db_conn, "universal_blessing_chance", 0.05):
                _UNIVERSAL_BLESSING_BONUS = 100
                update_boli_points(db_conn, target.id, _UNIVERSAL_BLESSING_BONUS)
                if invoker.id != target.id:
                    update_boli_points(db_conn, invoker.id, _UNIVERSAL_BLESSING_BONUS)
                log_bless(db_conn, target.id, target.display_name, "universal_blessing")
                uni_msg = random.choice(_UNIVERSAL_BLESSING_MESSAGES).format(
                    caster=invoker.display_name, receiver=target.display_name
                )
                await message.reply(uni_msg)
                logger.info(
                    "Universal blessing: %s → %s (+%d each)",
                    invoker.display_name, target.display_name, _UNIVERSAL_BLESSING_BONUS,
                )
                return

            reply, target_pts, caster_pts, bless_word = _bless_target(target, award_points=True)
            update_boli_points(db_conn, target.id, target_pts)
            if invoker.id != target.id:
                update_boli_points(db_conn, invoker.id, caster_pts)
            log_bless(db_conn, target.id, target.display_name, bless_word)
            await message.reply(reply)
            logger.info(
                "%s blessed %s — target +%d, caster +%d",
                invoker.display_name, target.display_name, target_pts, caster_pts,
            )

        # "chunk @mention" → bless the mentioned user
        if message.mentions:
            target = message.mentions[0]
            if target.id == bot.user.id:
                await message.reply(random.choice(BOT_SELF_COMPLIMENT_REPLIES))
                return
            if target.bot:
                await message.reply(f"{invoker.mention} {random.choice(BOT_LOOP_COMPLIMENT_REPLIES)}")
                return
            await _do_bless(target)
            return

        # "chunk" as reply → bless the replied-to user
        if message.reference:
            resolved = message.reference.resolved or message.reference.cached_message
            if isinstance(resolved, discord.Message) and not resolved.author.bot:
                await _do_bless(resolved.author)
                return

        # "chunk" alone → self-bless
        await _do_bless(invoker)
        return


    # ---- Passive curse word reply ----
    curse_chance = get_config_float(db_conn, "curse_reply_chance", 0.25)
    _curse_matched, curse_used = contains_curse_word(message.content)
    if get_config_int(db_conn, "feature_curse_replies", 1) and _curse_matched and random.random() < curse_chance:
        username = message.author.display_name
        user_id = message.author.id
        curse_used = curse_used or "that"

        log_curse(db_conn, user_id, username, curse_used)
        update_boli_points(db_conn, user_id, 1)  # +1 Boli pt for curse event

        system_prompt = get_time_aware_system_prompt(db_conn, username=username)
        user_prompt = get_curse_prompt(username, curse_used)

        async with message.channel.typing():
            await asyncio.sleep(random.uniform(1.0, 2.0))
            reply, from_cache = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: api_mgr.call(
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                    cache_type="curse",
                    name=username,
                    curse_used=curse_used,
                    fallback_message=random.choice(FALLBACK_MESSAGES),
                ),
            )

        if reply in FALLBACK_MESSAGES:
            reply = (
                get_random_doomed_prediction(username)
                if random.random() < 0.5
                else get_random_curse_back(username)
            )
        elif not from_cache:
            template = _templatize(reply, username, curse_used)
            save_prediction(db_conn, "curse", template, user_id=message.author.id)

        await message.reply(reply)


# ---------------------------------------------------------------------------
# Emoji-triggered link summarizer (Feature 3)
# ---------------------------------------------------------------------------

# Strips trailing punctuation that gets swept into a URL match (e.g. "url.")
_URL_RE = re.compile(r"https?://[^\s]+")
_URL_TRAIL_RE = re.compile(r"[)\].,;!?\"'>]+$")

# Tracks message IDs already summarised to prevent duplicate replies when
# multiple users react with the summary emoji in quick succession.
_summarized_messages: set[int] = set()
_SUMMARIZED_MAX = 500  # cap so it never grows unbounded

_SCRAPE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _extract_article_text(html: str) -> str:
    """Return the most article-like text content from raw HTML, capped at 4 000 chars."""
    import importlib
    bs4 = importlib.import_module("bs4")
    BeautifulSoup = bs4.BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # Strip noise nodes that add no article content
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()

    # Prefer a semantic article container; fall back to <main>, then whole body
    container = soup.find("article") or soup.find("main") or soup

    paragraphs = [
        p.get_text(separator=" ", strip=True)
        for p in container.find_all("p")
        if len(p.get_text(strip=True)) > 40  # drop nav labels / button text
    ]

    # If the article container had too little text, widen to the whole document
    if len(" ".join(paragraphs)) < 300:
        paragraphs = [
            p.get_text(separator=" ", strip=True)
            for p in soup.find_all("p")
            if len(p.get_text(strip=True)) > 40
        ]

    return " ".join(paragraphs)[:4000]


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    """Summarise a linked article when a user reacts with the configured emoji."""
    if db_conn is None or payload.user_id == bot.user.id:
        return

    if not _feat("feature_link_summary"):
        return

    summary_emoji = get_config_str(db_conn, "link_summary_emoji", "📰")
    if str(payload.emoji) != summary_emoji:
        return

    # Deduplicate: ignore if another reaction already triggered a summary
    if payload.message_id in _summarized_messages:
        return

    # get_channel misses uncached channels (threads, recently created channels)
    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(payload.channel_id)
        except (discord.NotFound, discord.Forbidden):
            return

    try:
        message = await channel.fetch_message(payload.message_id)
    except (discord.NotFound, discord.Forbidden):
        return

    raw_urls = _URL_RE.findall(message.content)
    if not raw_urls:
        return

    # Strip trailing punctuation that the regex sweeps up (e.g. "https://x.com.")
    url = _URL_TRAIL_RE.sub("", raw_urls[0])

    # Mark as in-progress before the async fetch so concurrent reactions are dropped
    _summarized_messages.add(payload.message_id)
    if len(_summarized_messages) > _SUMMARIZED_MAX:
        try:
            _summarized_messages.pop()
        except KeyError:
            pass

    try:
        import aiohttp

        async with aiohttp.ClientSession(
            headers={"User-Agent": _SCRAPE_UA}
        ) as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(connect=5, total=15),
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    await message.reply(
                        f"Couldn't fetch that link (HTTP {resp.status}). Try again later."
                    )
                    return
                html = await resp.text(errors="replace")

        page_text = _extract_article_text(html)

        if not page_text.strip():
            await message.reply(
                "That page has no readable text — probably a paywall or JS-only site."
            )
            return

        user_prompt = get_link_summary_prompt(page_text, url)

        summary, _ = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: api_mgr.call(
                prompt=user_prompt,
                system_prompt=LINK_SUMMARY_SYSTEM_PROMPT,
                cache_type="qa",
                name="LinkSummary",
                fallback_message=random.choice(FALLBACK_MESSAGES),
            ),
        )
        await message.reply(f"📰 **Link Summary:**\n{summary}")
        logger.info("Link summary sent for %s", url)

    except Exception as exc:
        logger.warning("Link summary failed for %s: %s", url, exc)
        await message.reply("Something went wrong reading that link. Try again.")


# ---------------------------------------------------------------------------
# Slash Commands
# ---------------------------------------------------------------------------

@tree.command(name="navi", description="Get a Navi prediction")
async def navi_slash(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
) -> None:
    if not _feat("feature_navi"):
        await interaction.response.send_message(
            "Navi predictions are currently disabled.", ephemeral=True
        )
        return

    # Bot-loop protection: reject attempts to get predictions for other bots
    if user is not None and user.bot:
        await interaction.response.send_message(
            f"{interaction.user.mention} {random.choice(BOT_LOOP_CURSE_REPLIES)}"
        )
        return

    # Record usage and get per-minute count BEFORE checking cache
    usage_count = get_user_minute_count(interaction.user.id, increment=True)

    target = user or interaction.user
    display_name = target.display_name
    mention_str = target.mention

    # Tiered response for 2nd+ use within the minute
    if usage_count > 1:
        cached_pred = get_todays_user_prediction(db_conn, interaction.user.id)
        if cached_pred:
            if usage_count == 2 and random.random() >= 0.35:
                # 65% chance: fall through to Gemini (handled below)
                pass
            else:
                # 2nd use (35% case): short delay. 3rd+ use: longer delay to feel AI-generated.
                reply = _format_cached_spam_reply(cached_pred, display_name)
                delay = random.uniform(1.5, 3.5) if usage_count >= 3 else random.uniform(1.0, 2.5)
                await interaction.response.defer(thinking=True)
                await asyncio.sleep(delay)
                await interaction.followup.send(reply)
                return

    await interaction.response.defer(thinking=True)
    await asyncio.sleep(random.uniform(1.0, 2.0))

    prediction = await get_navi_prediction(target.id, display_name, usage_count=usage_count)
    final_reply = prediction.replace(display_name, mention_str)
    await interaction.followup.send(final_reply)

    # +2 Boli Points for using /navi (only on first fresh call)
    if usage_count == 1:
        profile = get_user_profile(db_conn, interaction.user.id)
        old_pts = profile["boli_points"] if profile else 0
        update_boli_points(db_conn, interaction.user.id, 2)
        logger.info("%s used /navi → +2 Boli Points", interaction.user.display_name)
        if interaction.channel:
            await _maybe_announce_levelup(
                interaction.user.mention, old_pts, old_pts + 2, interaction.channel
            )


@tree.command(name="ping", description="Check if Navi is awake")
async def ping_slash(interaction: discord.Interaction) -> None:
    latency_ms = round(bot.latency * 1000)
    profile = get_user_profile(db_conn, interaction.user.id)
    pts = profile["boli_points"] if profile else 0
    reply = get_template("ping", pts, latency=latency_ms)
    if not reply:
        reply = f"Pong! Latency is {latency_ms}ms."
    await interaction.response.send_message(reply, ephemeral=True)


def _build_rank_embed(page: int, total_pages: int, leaders: list, offset: int) -> discord.Embed:
    """Build the leaderboard embed for a given page."""
    embed = discord.Embed(
        title="🍮 Boli Points Leaderboard",
        description=f"Page {page} of {total_pages}",
        color=discord.Color.gold(),
    )
    medals = ["🥇", "🥈", "🥉"]
    for i, entry in enumerate(leaders):
        global_rank = offset + i + 1
        if page == 1 and i < 3:
            medal = medals[i]
        else:
            medal = f"**{global_rank}.**"
        rashi_str = f" · {entry['rashi']}" if entry.get("rashi") else ""
        level = get_level_from_points(entry["boli_points"])
        title = get_level_title(level)
        embed.add_field(
            name=f"{medal} {entry['username']}{rashi_str} · Lv.{level}",
            value=f"🍮 **{entry['boli_points']} Boli Points** · {entry['prediction_count']} readings · *{title}*",
            inline=False,
        )
    embed.set_footer(text="Use the buttons below to navigate · Earn points via /navi and Trivandrum slang")
    return embed


class RankView(discord.ui.View):
    """Paginated leaderboard view with Previous / Next buttons."""

    _PAGE_SIZE = 10

    def __init__(self, page: int, total_pages: int) -> None:
        super().__init__(timeout=120)
        self.page = page
        self.total_pages = total_pages
        # Disable buttons that are not applicable
        self.prev_button.disabled = page <= 1
        self.next_button.disabled = page >= total_pages

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = max(1, self.page - 1)
        await self._update(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.page = min(self.total_pages, self.page + 1)
        await self._update(interaction)

    async def _update(self, interaction: discord.Interaction) -> None:
        offset = (self.page - 1) * self._PAGE_SIZE
        leaders = get_leaderboard(db_conn, limit=self._PAGE_SIZE, offset=offset)
        embed = _build_rank_embed(self.page, self.total_pages, leaders, offset)
        self.prev_button.disabled = self.page <= 1
        self.next_button.disabled = self.page >= self.total_pages
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


@tree.command(name="rank", description="See the Boli Points leaderboard")
@app_commands.describe(page="Page number (10 entries per page, default 1)")
async def rank_slash(interaction: discord.Interaction, page: int = 1) -> None:
    _PAGE_SIZE = 10
    total = count_leaderboard_entries(db_conn)

    if total == 0:
        await interaction.response.send_message(
            "Nobody has Boli Points yet. Use **/navi** and start earning.", ephemeral=True
        )
        return

    total_pages = max(1, math.ceil(total / _PAGE_SIZE))
    page = max(1, min(page, total_pages))
    offset = (page - 1) * _PAGE_SIZE

    await interaction.response.defer(thinking=False)
    leaders = get_leaderboard(db_conn, limit=_PAGE_SIZE, offset=offset)
    embed = _build_rank_embed(page, total_pages, leaders, offset)
    view = RankView(page=page, total_pages=total_pages)
    await interaction.followup.send(embed=embed, view=view)


@tree.command(name="leaderboard", description="Fun stats leaderboard: most cursed, most blessed, and richest")
async def leaderboard_slash(interaction: discord.Interaction) -> None:
    """Three-section embed: most cursed, most blessed, highest Boli net worth."""
    await interaction.response.defer(thinking=False)

    curse_board = get_curse_leaderboard(db_conn, limit=5)
    bless_board = get_bless_leaderboard(db_conn, limit=5)
    rich_board = get_leaderboard(db_conn, limit=5, offset=0)

    embed = discord.Embed(
        title="🌌 Cosmic Leaderboards",
        color=discord.Color.dark_purple(),
    )

    medals = ["🥇", "🥈", "🥉", "4.", "5."]

    def _fmt_board(entries: list[dict], count_key: str) -> str:
        if not entries:
            return "*No data yet.*"
        lines = []
        for i, e in enumerate(entries):
            medal = medals[i] if i < len(medals) else f"{i+1}."
            lines.append(f"{medal} **{e['username']}** — {e[count_key]}")
        return "\n".join(lines)

    curse_lines = _fmt_board(curse_board, "hits")
    bless_lines = _fmt_board(bless_board, "hits")
    rich_lines = "\n".join(
        f"{medals[i] if i < len(medals) else f'{i+1}.'} **{e['username']}** — 🍮 {e['boli_points']} pts"
        for i, e in enumerate(rich_board)
    ) if rich_board else "*No data yet.*"

    embed.add_field(name="💀 Most Cursed", value=curse_lines, inline=False)
    embed.add_field(name="✨ Most Blessed", value=bless_lines, inline=False)
    embed.add_field(name="💰 Highest Boli Net Worth", value=rich_lines, inline=False)
    embed.set_footer(text="Curse & bless counts update in real time · /rank for full points board")

    await interaction.followup.send(embed=embed)


@tree.command(name="lucky_draw", description="Manually trigger the daily lucky draw (admin only)")
@app_commands.default_permissions(administrator=True)
async def lucky_draw_slash(interaction: discord.Interaction) -> None:
    """Admin-only manual trigger for the daily lucky draw.

    Checks for duplicate runs within the same IST day and announces the winner
    in the current channel.
    """
    app_info = await bot.application_info()
    is_owner = (
        (bool(OWNER_ID) and interaction.user.id == OWNER_ID)
        or interaction.user.id == app_info.owner.id
    )
    is_admin = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator
    if not (is_owner or is_admin):
        await interaction.response.send_message("This command requires Administrator permission.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    _IST = timezone(timedelta(hours=5, minutes=30))
    today_str = datetime.now(_IST).strftime("%Y-%m-%d")
    last_run = get_config_str(db_conn, "lucky_draw_last_date", "")
    if last_run == today_str:
        await interaction.followup.send(
            f"⚠️ Lucky draw already ran today ({today_str}). "
            "Use `/admin adjust_points` to manually award Boli if needed.",
            ephemeral=True,
        )
        return

    result = await _run_lucky_draw(announce_channel=interaction.channel)
    await interaction.followup.send(f"✅ Lucky draw triggered: {result}", ephemeral=True)
    logger.info("Manual lucky draw triggered by %s: %s", interaction.user.display_name, result)


@tree.command(name="mypoints", description="Check your own Boli Points and Rashi")
async def mypoints_slash(interaction: discord.Interaction) -> None:
    profile = get_user_profile(db_conn, interaction.user.id)
    if not profile:
        await interaction.response.send_message(
            "No profile found. Use **/navi** first to get started.", ephemeral=True
        )
        return

    rashi = profile.get("rashi") or "Not yet assigned"
    pts = profile.get("boli_points", 0)
    count = profile.get("prediction_count", 0)
    level = get_level_from_points(pts)
    title = get_level_title(level)

    if level < 100:
        next_lvl_pts = points_for_level(level + 1)
        curr_lvl_pts = points_for_level(level)
        progress = pts - curr_lvl_pts
        needed = next_lvl_pts - curr_lvl_pts
        filled = int((progress / needed) * 10) if needed > 0 else 10
        bar = "█" * filled + "░" * (10 - filled)
        progress_line = f"📈 `[{bar}]` {progress}/{needed} pts → Level {level + 1}"
    else:
        progress_line = "🌟 Maximum level reached!"

    stats_intro = get_template("stats", pts)
    profile_text = (
        f"**Your Profile**\n"
        f"🌟 Rashi: **{rashi}**\n"
        f"⚔️ Level: **{level}** — *{title}*\n"
        f"{progress_line}\n"
        f"🍮 Boli Points: **{pts}**\n"
        f"🔮 Predictions received: **{count}**"
    )
    full_reply = f"{stats_intro}\n\n{profile_text}" if stats_intro else profile_text
    await interaction.response.send_message(full_reply, ephemeral=True)


@tree.command(name="summ", description="Get a factual summary of recent chat")
@app_commands.describe(
    limit="Number of messages to summarise (1–100)",
    user1="Only include messages from this user (optional)",
    user2="Also include messages from this user (optional)",
    public="Show summary to everyone? (default: only me)",
)
async def summ_slash(
    interaction: discord.Interaction,
    limit: app_commands.Range[int, 1, 100] = 30,
    user1: discord.Member | None = None,
    user2: discord.Member | None = None,
    public: bool = False,
) -> None:
    await interaction.response.defer(ephemeral=not public, thinking=True)

    # Collect history
    raw_messages: list[discord.Message] = []
    async for msg in interaction.channel.history(limit=limit):
        raw_messages.append(msg)
    raw_messages.reverse()  # chronological order

    # Filter by selected users if provided
    filter_ids: set[int] = set()
    if user1:
        filter_ids.add(user1.id)
    if user2:
        filter_ids.add(user2.id)
    if filter_ids:
        raw_messages = [m for m in raw_messages if m.author.id in filter_ids]

    if not raw_messages:
        await interaction.followup.send(
            "No messages found to summarise. Try a larger limit or different user filters.",
            ephemeral=not public,
        )
        return

    # Strip bot messages, commands (starting with /), and mention IDs — keep username prefix
    _mention_re = re.compile(r"<@!?\d+>|<#\d+>|<@&\d+>")
    lines: list[str] = []
    for msg in raw_messages:
        if msg.author.bot:
            continue
        text = _mention_re.sub("[mentioned-user]", msg.content).strip()
        if not text or text.startswith("/"):
            continue
        lines.append(f"{msg.author.display_name}: {text}")

    if not lines:
        await interaction.followup.send(
            "Nothing to summarise — all messages were commands or from bots.",
            ephemeral=not public,
        )
        return

    conversation_text = "\n".join(lines)
    user_prompt = get_summ_prompt(conversation_text)

    summary, _ = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: api_mgr.call(
            prompt=user_prompt,
            system_prompt=SUMM_SYSTEM_PROMPT,
            cache_type="qa",
            name="SummaryRequest",
            fallback_message=random.choice(FALLBACK_MESSAGES),
        ),
    )

    filter_note = ""
    if filter_ids:
        names = [u.display_name for u in [user1, user2] if u]
        filter_note = f" (filtered to {', '.join(names)})"

    await interaction.followup.send(
        f"📜 **Chat Summary — last {len(lines)} messages{filter_note}:**\n{summary}",
        ephemeral=not public,
    )


@tree.command(name="kanmanilla", description="Ping a missing member with a dramatic Missing Person notice")
@app_commands.describe(user="The member you're looking for")
async def kanmanilla_slash(interaction: discord.Interaction, user: discord.Member) -> None:
    if not _feat("feature_kanmanilla"):
        await interaction.response.send_message(
            "Kanmanilla feature is currently disabled.", ephemeral=True
        )
        return

    if user.bot:
        await interaction.response.send_message(
            "Bots don't go missing — they just get turned off.", ephemeral=True
        )
        return

    # Defer early — channel history search can take a moment
    await interaction.response.defer(thinking=True)

    # Search all readable text channels for the user's most recent message
    last_message_time: datetime | None = None
    for ch in interaction.guild.text_channels:
        try:
            async for msg in ch.history(limit=50):
                if msg.author.id == user.id:
                    msg_time = msg.created_at if msg.created_at.tzinfo else msg.created_at.replace(tzinfo=timezone.utc)
                    if last_message_time is None or msg_time > last_message_time:
                        last_message_time = msg_time
                    break  # only the most recent message per channel matters
        except (discord.Forbidden, discord.HTTPException):
            continue

    now = datetime.now(timezone.utc)
    if last_message_time is not None:
        days_ago = max(0, (now - last_message_time).days)
    elif user.joined_at is not None:
        days_ago = max(0, (now - user.joined_at).days)
    else:
        days_ago = 999

    if days_ago < 2:
        await interaction.followup.send(
            f"{user.display_name} was just here recently. Don't ping people for no reason.",
        )
        return

    system_prompt = get_time_aware_system_prompt(db_conn, username=None)
    user_prompt = get_kanmanilla_prompt(user.display_name, days_ago)

    poster, _ = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: api_mgr.call(
            prompt=user_prompt,
            system_prompt=system_prompt,
            cache_type="qa",
            name="KanmanillaRequest",
            fallback_message=f"🚨 MISSING: {user.display_name}. Last seen {days_ago} days ago. {user.mention} — are you still out there? Reply here.",
        ),
    )
    await interaction.followup.send(f"{poster}\n{user.mention}")
    logger.info("Kanmanilla posted for %s (last seen %d days ago)", user.display_name, days_ago)


@tree.command(name="audit", description="Audit a user's messages against server rules (Mods only)")
@app_commands.describe(
    user="The member to audit",
    channel="Channel to fetch messages from (defaults to current channel)",
)
@app_commands.default_permissions(manage_messages=True)
async def audit_slash(
    interaction: discord.Interaction,
    user: discord.Member,
    channel: discord.TextChannel | None = None,
) -> None:
    if not _feat("feature_audit"):
        await interaction.response.send_message("Audit feature is currently disabled.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    # Find rules channel dynamically
    rules_ch = discord.utils.find(
        lambda c: "rule" in c.name.lower(),
        interaction.guild.text_channels,
    ) if interaction.guild else None

    rules_text = "No rules channel found on this server."
    if rules_ch:
        try:
            rule_msgs: list[str] = []
            async for msg in rules_ch.history(limit=50):
                if msg.content.strip():
                    rule_msgs.append(msg.content.strip())
            if rule_msgs:
                rules_text = "\n".join(reversed(rule_msgs))
        except discord.Forbidden:
            rules_text = "(Could not read rules channel — missing permissions)"

    # Fetch user messages
    target_ch = channel or interaction.channel
    user_lines: list[str] = []
    try:
        async for msg in target_ch.history(limit=200):
            if msg.author.id == user.id and msg.content.strip():
                user_lines.append(f"[{msg.created_at.strftime('%H:%M')}] {msg.author.display_name}: {msg.content.strip()}")
            if len(user_lines) >= 100:
                break
    except discord.Forbidden:
        await interaction.followup.send("Cannot read that channel — missing permissions.", ephemeral=True)
        return

    if not user_lines:
        await interaction.followup.send(
            f"No messages found from **{user.display_name}** in that channel.", ephemeral=True
        )
        return

    messages_text = "\n".join(reversed(user_lines))
    user_prompt = get_audit_prompt(rules_text, messages_text)
    system_prompt = "You are a neutral moderation assistant. Be objective and precise."

    report, _ = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: api_mgr.call(
            prompt=user_prompt,
            system_prompt=system_prompt,
            cache_type="qa",
            name="AuditRequest",
            fallback_message="Audit failed — Gemini is unavailable. Check logs.",
        ),
    )

    await interaction.followup.send(
        f"**🔍 Audit Report — {user.display_name}** (from #{target_ch.name})\n\n{report}",
        ephemeral=True,
    )
    logger.info("Audit run by %s for %s in #%s", interaction.user.display_name, user.display_name, target_ch.name)


@tree.command(name="mod_tldr", description="Summarise the current thread for moderators (Mods only)")
@app_commands.default_permissions(manage_messages=True)
async def mod_tldr_slash(interaction: discord.Interaction) -> None:
    if not _feat("feature_mod_tldr"):
        await interaction.response.send_message("Mod TL;DR feature is currently disabled.", ephemeral=True)
        return

    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message(
            "This command only works inside a thread. Run it from within the thread.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    thread: discord.Thread = interaction.channel
    lines: list[str] = []
    _mention_re = re.compile(r"<@!?\d+>|<#\d+>|<@&\d+>")

    async for msg in thread.history(limit=500, oldest_first=True):
        if msg.author.bot:
            continue
        text = _mention_re.sub("[user]", msg.content).strip()
        if text and not text.startswith("/"):
            lines.append(f"{msg.author.display_name}: {text}")

    if not lines:
        await interaction.followup.send("This thread has no readable messages to summarise.", ephemeral=True)
        return

    thread_text = "\n".join(lines)
    user_prompt = get_mod_tldr_prompt(thread_text)
    system_prompt = "You are a neutral moderation assistant. Be concise and factual. Use exact usernames."

    summary, _ = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: api_mgr.call(
            prompt=user_prompt,
            system_prompt=system_prompt,
            cache_type="qa",
            name="ModTldrRequest",
            fallback_message="Thread summary failed — Gemini is unavailable.",
        ),
    )

    await interaction.followup.send(
        f"📋 **Thread TL;DR — #{thread.name}**\n\n{summary}",
        ephemeral=True,
    )
    logger.info("mod_tldr run by %s in thread #%s", interaction.user.display_name, thread.name)


@tree.command(name="health", description="Navi system health (owner only)")
@app_commands.default_permissions(administrator=True)
async def health_slash(interaction: discord.Interaction) -> None:
    app_info = await bot.application_info()
    is_owner = bool(OWNER_ID and interaction.user.id == OWNER_ID) or interaction.user.id == app_info.owner.id
    if not is_owner:
        await interaction.response.send_message(
            "This command is owner-only.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    uptime_seconds = int((datetime.now(timezone.utc) - _BOT_START_TIME).total_seconds())
    uptime_str = f"{uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m {uptime_seconds % 60}s"

    gemini_status = gemini_svc.status_dict()
    rate_status = api_mgr.status_dict()
    table_counts = get_table_counts(db_conn)
    health = get_health_stats(db_conn)

    # DB file size
    try:
        db_size_mb = DB_PATH.stat().st_size / (1024 * 1024)
        db_size_str = f"{db_size_mb:.2f} MB"
    except Exception:
        db_size_str = "N/A"

    # Unique users active in the in-memory rate-limit window
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(seconds=60)
    active_in_window = sum(
        1 for timestamps in _user_usage.values()
        if any(t > cutoff for t in timestamps)
    )

    embed = discord.Embed(
        title="🔧 Navi — Health Status",
        color=discord.Color.red() if gemini_status["circuit_open"] else discord.Color.green(),
    )

    # ── Uptime ──────────────────────────────────────────────────────────────
    embed.add_field(name="⏱️ Uptime", value=uptime_str, inline=False)

    # ── Gemini API ──────────────────────────────────────────────────────────
    embed.add_field(
        name="🔑 Gemini API Usage",
        value=(
            f"Free key: {'✅' if gemini_status['free_key_available'] else '❌'}\n"
            f"Paid key: {'✅' if gemini_status['paid_key_available'] else '❌'}\n"
            f"Active key: **{gemini_status['active_key']}**\n\n"
            f"**Lifetime Calls (Since Boot)**\n"
            f"Free: **{gemini_status['free_calls']}** ({gemini_status['free_pct']}%)\n"
            f"Paid: **{gemini_status['paid_calls']}** ({gemini_status['paid_pct']}%)\n"
            f"Fails: {gemini_status['failed_calls']}"
        ),
        inline=True,
    )

    # ── Circuit Breaker ─────────────────────────────────────────────────────
    embed.add_field(
        name="⚡ Circuit Breaker",
        value=(
            f"State: **{'OPEN 🔴' if gemini_status['circuit_open'] else 'CLOSED 🟢'}**\n"
            f"Failures: {gemini_status['failure_count']}/3\n"
            f"Opens until: {gemini_status['open_until']}"
        ),
        inline=True,
    )

    # ── Rate Limiter ────────────────────────────────────────────────────────
    embed.add_field(
        name="🚦 Rate Limiter",
        value=(
            f"Used: **{rate_status['rpm_used']}/{rate_status['rpm_limit']} RPM**\n"
            f"Window resets in: {rate_status['window_resets_in_seconds']}s\n"
            f"Users active in last 60s: **{active_in_window}**\n"
            f"Free Tier Mode: {'ON' if rate_status['free_tier_mode'] else 'OFF'}\n"
            f"Free-Only Killswitch: **{'ON 🔴' if gemini_svc.free_only else 'OFF 🟢'}**"
        ),
        inline=False,
    )

    # ── User Activity ───────────────────────────────────────────────────────
    embed.add_field(
        name="👥 User Activity",
        value=(
            f"Total registered: **{health['total_users']}**\n"
            f"Active today: **{health['active_today']}**\n"
            f"Active this week: **{health['active_week']}**\n"
            f"Total Boli Points in circulation: **{health['total_boli_points']:,}**\n"
            f"Top user: {health['top_user']}"
        ),
        inline=True,
    )

    # ── Prediction Activity ─────────────────────────────────────────────────
    cache_breakdown = " · ".join(
        f"{k}: {v}" for k, v in health["cache_by_type"].items()
    ) or "empty"
    embed.add_field(
        name="🔮 Predictions",
        value=(
            f"Generated today: **{health['predictions_today']}**\n"
            f"Cache pool: {cache_breakdown}"
        ),
        inline=True,
    )

    # ── Curse Log ───────────────────────────────────────────────────────────
    embed.add_field(
        name="🤬 Curses",
        value=(
            f"Today: **{health['curses_today']}**  |  All-time: **{health['curses_total']}**\n"
            f"Top word: {health['top_curse']}\n"
            f"Active Curse Protections: **{health['active_perks']}**"
        ),
        inline=False,
    )

    # ── SQLite ──────────────────────────────────────────────────────────────
    counts_str = "\n".join(f"  `{t}`: {n}" for t, n in table_counts.items())
    embed.add_field(
        name=f"🗄️ SQLite  ({db_size_str})",
        value=counts_str,
        inline=False,
    )

    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="help", description="Learn how to interact with Navi")
async def help_slash(interaction: discord.Interaction) -> None:
    """Show the help menu — only lists features that are currently enabled."""
    embed = discord.Embed(
        title="Navi — Help & Features",
        description="Fairy from Hyrule. Now stationed in Thirontharam. Speaks Manglish. Has seen things.",
        color=discord.Color.dark_purple(),
    )

    # ── Predictions ──────────────────────────────────────────────────────────
    if _feat("feature_navi"):
        embed.add_field(
            name="🔮 Predictions",
            value=(
                "`/navi` — Get your daily cosmic prediction\n"
                "`/navi user:@someone` — Get a prediction for someone else\n"
                "`navi` *(in chat)* — Same as `/navi`\n"
                "`@Navi <question>` — Ask anything: news, scores, how-tos"
            ),
            inline=False,
        )

    # ── Curses & Blessings ───────────────────────────────────────────────────
    if _feat("feature_curses"):
        embed.add_field(
            name="⚡ Curses & Blessings",
            value=(
                "`navi @user` — Curse someone. Lands: **you gain pts, they lose pts**.\n"
                "Backfire reverses it — they gain, you lose. Higher tier = higher risk:\n"
                "Mild **10%** · Moderate **15%** · Severe **20%** backfire chance\n\n"
                "`chunk @user` — Bless someone with Trivandrum slang.\n"
                "Target earns points; you earn **15–25%** of that as karma.\n"
                "**5% chance**: Universal Blessing fires — both of you get **+100 pts** 🌟\n\n"
                "Both commands can also reply to a message or be used alone (self).\n"
                "Protect yourself from curses via `/shop buy`."
            ),
            inline=False,
        )
        embed.add_field(
            name="🌀 Cosmic Actions (daily quota)",
            value=(
                f"Curses, blessings, and gambling all share **{_DAILY_QUOTA} cosmic actions/day**.\n"
                "Resets at **midnight IST**. Buy **+10 extra actions** from the shop to go beyond.\n"
                "Exceed the limit on curses → you get reverse-cursed. On blessings → the cosmos notices."
            ),
            inline=False,
        )

    # ── Boli Economy ─────────────────────────────────────────────────────────
    economy_lines = [
        "`/mypoints` — Your Rashi, Boli Points, level, and cosmic title",
        "`/rank` — Top 10 leaderboard",
        "`/leaderboard` — Most cursed, most blessed, and richest users",
        "`/gift @user amount` — Send Boli Points to someone",
        "`/shop view` / `/shop buy` — Boli Marketplace (perks, protection, extra actions)",
    ]
    embed.add_field(name="🍮 Boli Economy", value="\n".join(economy_lines), inline=False)

    # ── Gambling ─────────────────────────────────────────────────────────────
    if _feat("feature_gambling"):
        embed.add_field(
            name="🎰 Gambling *(uses cosmic actions)*",
            value=(
                "`/flip heads|tails bet` — Coin flip, 1:1 payout\n"
                "`/roll_dice bet` — Dice: over/under 1:1, exact 7 pays 4:1, specific sums up to 35:1\n"
                "`/roulette bet` — 39-pocket wheel: color/odd/even 1:1, dozen 2:1, number 35:1\n"
                "`/slots bet` — Slot machine: 2-match 3:1, 3-match 10:1, jackpot (7️⃣7️⃣7️⃣) 50:1\n"
                "Max bet **50,000 Boli** per game."
            ),
            inline=False,
        )

    # ── Boli Points (passive earning) ────────────────────────────────────────
    if _feat("feature_boli_points"):
        embed.add_field(
            name="🏆 Boli Points — Passive Earning",
            value=(
                "Use Trivandrum slang naturally in chat for +5 pts per unique word:\n"
                "*kidilam, shokam, pillacha, chumma, mone, kili poyi, vishayam, thirontharam, boli, paal payasam…*\n"
                "+2 pts per `/navi` call · Levels: Tourist → Thampanoor Regular → Chalai Veteran → Cosmic Sage"
            ),
            inline=False,
        )

    # ── Media Shortcuts ───────────────────────────────────────────────────────
    if _feat("feature_local_media"):
        embed.add_field(
            name="🖼️ Media Shortcuts",
            value=(
                "Right-click a message → **Apps** → **Save as Emoji** or **Save as Sticker**\n"
                "Custom emojis are saved natively. Images are uploaded as **Application Emojis** (auto-managed, up to 2,000).\n"
                "Saved stickers sent alone appear enlarged — just like a real sticker!\n"
                "Type `;;name` in any channel to post the saved emoji/sticker.\n"
                "Right-click → **Apps** → **Reply with Emoji / Reply with Sticker** to reply with one of your saves.\n"
                "`/my_media` — List all your saved shortcuts (max 20 per user)."
            ),
            inline=False,
        )

    # ── Other Tools ───────────────────────────────────────────────────────────
    other_lines = [
        "`/summ` — Factual summary of recent chat (up to 100 messages)",
        "`/ping` — Check bot latency and your Boli balance",
    ]
    if _feat("feature_kanmanilla"):
        other_lines.append("`/kanmanilla @user` — Dramatic missing-person notice for an absent member")
    if _feat("feature_temp_vc"):
        other_lines.append("`/temp_vc name capacity` — Temp voice channel (self-destructs in 30 min or when empty)")
    embed.add_field(name="🛠️ Other", value="\n".join(other_lines), inline=False)

    # ── Passive Reactions ─────────────────────────────────────────────────────
    passive_lines: list[str] = []
    if _feat("feature_kochi_replies"):
        passive_lines.append("**Kochi slang** *(machane, machi, adipoli…)* → Condescending reply from a true Trivandrumite")
    if _feat("feature_curse_replies"):
        passive_lines.append("**Curse words in chat** → 25% chance of a cosmic doom prediction")
    if _feat("feature_vibe_check"):
        passive_lines.append("**Chat heats up** → Navi de-escalates automatically")
    if _feat("feature_link_summary"):
        passive_lines.append("**React 📰 on a link** → 3-bullet summary of the article")
    if _feat("feature_welcome"):
        passive_lines.append("**New member joins** → Welcome message after 1-minute delay")
    if _feat("feature_strikes"):
        passive_lines.append("**Severe language** → 3-strike system; strike 2 triggers jail role")
    if passive_lines:
        embed.add_field(name="👁️ Passive Reactions (automatic)", value="\n".join(passive_lines), inline=False)

    # ── Mod-only section — only shown to users with manage_messages ───────────
    member = interaction.user
    is_mod = isinstance(member, discord.Member) and member.guild_permissions.manage_messages
    if is_mod:
        mod_lines = []
        if _feat("feature_audit"):
            mod_lines.append("`/audit @user [channel]` — AI audit of a user's messages against server rules")
        if _feat("feature_mod_tldr"):
            mod_lines.append("`/mod_tldr` — TL;DR summary of the current thread")
        if _feat("feature_strikes"):
            mod_lines.append("`/strike @user reason` — Issue a manual strike")
        if mod_lines:
            embed.add_field(name="🛡️ Mod Commands", value="\n".join(mod_lines), inline=False)

    embed.set_footer(text=f"Powered by Gemini · {_DAILY_QUOTA} cosmic actions/day · Resets midnight IST")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# Admin Slash Commands
# ---------------------------------------------------------------------------

class AdminGroup(app_commands.Group):
    """Admin controls — visible and usable only by the bot owner."""
    def __init__(self):
        super().__init__(
            name="admin",
            description="Navi owner configuration controls",
            default_permissions=discord.Permissions(administrator=True),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Primary guard: hardcoded owner ID from .env (fast, no API call)
        if OWNER_ID and interaction.user.id == OWNER_ID:
            return True
        # Fallback: check against Discord's application owner (slower but safe)
        app_info = await bot.application_info()
        if interaction.user.id == app_info.owner.id:
            return True
        # Deny everyone else — ephemeral so it's not publicly embarrassing
        await interaction.response.send_message(
            "Admin commands are owner-only.", ephemeral=True
        )
        return False

    @app_commands.command(name="set_cooldown", description="Set anti-spam cooldown in seconds")
    async def set_cooldown(self, interaction: discord.Interaction, seconds: int) -> None:
        set_config_int(db_conn, "astro_cooldown_seconds", seconds)
        await interaction.response.send_message(f"Spam cooldown set to **{seconds} seconds**.", ephemeral=True)

    @app_commands.command(name="toggle_killswitch", description="Toggle Free-Only API mode (circuit breaker bypass)")
    async def toggle_killswitch(self, interaction: discord.Interaction) -> None:
        gemini_svc.free_only = not gemini_svc.free_only
        status = "ON 🔴 (Free API + Cache only)" if gemini_svc.free_only else "OFF 🟢 (Paid Fallback enabled)"
        await interaction.response.send_message(f"Killswitch is now **{status}**.", ephemeral=True)

    @app_commands.command(name="killswitch", description="Toggle the master kill switch — completely silences the bot")
    async def master_killswitch_cmd(self, interaction: discord.Interaction) -> None:
        new_state = 0 if _feat("master_killswitch") else 1
        _set_feature("master_killswitch", new_state)
        if new_state:
            await interaction.response.send_message(
                "☠️ **Master Kill Switch: ON.** Bot is now silent. Use `/admin killswitch` to re-enable.",
                ephemeral=True,
            )
            logger.warning("MASTER KILL SWITCH ACTIVATED by %s", interaction.user.display_name)
        else:
            await interaction.response.send_message(
                "✅ **Master Kill Switch: OFF.** Bot is back online.",
                ephemeral=True,
            )
            logger.info("Master kill switch deactivated by %s", interaction.user.display_name)

    @app_commands.command(name="toggle_feature", description="Enable or disable a bot feature")
    @app_commands.describe(feature="Feature to toggle", enabled="Turn it on (True) or off (False)")
    @app_commands.choices(feature=[
        app_commands.Choice(name="Navi Predictions", value="feature_navi"),
        app_commands.Choice(name="Curses & Blessings (navi/chunk)", value="feature_curses"),
        app_commands.Choice(name="Gambling (/flip, /roll_dice, /roulette, /slots)", value="feature_gambling"),
        app_commands.Choice(name="Local Media Shortcuts (;;name)", value="feature_local_media"),
        app_commands.Choice(name="Boli Points Tracking", value="feature_boli_points"),
        app_commands.Choice(name="Kochi Slang Detection", value="feature_kochi_replies"),
        app_commands.Choice(name="Passive Curse Replies", value="feature_curse_replies"),
        app_commands.Choice(name="Vibe Check (auto de-escalation)", value="feature_vibe_check"),
        app_commands.Choice(name="Link Summary (emoji reaction)", value="feature_link_summary"),
        app_commands.Choice(name="Kanmanilla (missing person)", value="feature_kanmanilla"),
        app_commands.Choice(name="Welcome Messages", value="feature_welcome"),
        app_commands.Choice(name="Temp VC Generator (/temp_vc)", value="feature_temp_vc"),
        app_commands.Choice(name="Mod Audit (/audit)", value="feature_audit"),
        app_commands.Choice(name="Mod TL;DR (/mod_tldr)", value="feature_mod_tldr"),
        app_commands.Choice(name="3-Strike System", value="feature_strikes"),
    ])
    async def toggle_feature(
        self, interaction: discord.Interaction,
        feature: str,
        enabled: bool,
    ) -> None:
        _set_feature(feature, 1 if enabled else 0)
        label = feature.replace("feature_", "").replace("_", " ").title()
        state = "ENABLED ✅" if enabled else "DISABLED ❌"
        await interaction.response.send_message(
            f"**{label}** is now **{state}**.", ephemeral=True
        )

    @app_commands.command(name="set_chance", description="Set the probability (0.0–1.0) for a specific behaviour")
    @app_commands.describe(feature="Which probability to adjust", value="New value between 0.0 and 1.0")
    @app_commands.choices(feature=[
        app_commands.Choice(name="Cache Reuse (navi predictions)", value="cache_reuse_chance"),
        app_commands.Choice(name="Kochi Slang Reply", value="kochi_reply_chance"),
        app_commands.Choice(name="Passive Curse Reply", value="curse_reply_chance"),
        app_commands.Choice(name="Universal Blessing (5% default)", value="universal_blessing_chance"),
        app_commands.Choice(name="Curse Backfire — Mild tier (10% default)", value="backfire_chance_mild"),
        app_commands.Choice(name="Curse Backfire — Moderate tier (15% default)", value="backfire_chance_moderate"),
        app_commands.Choice(name="Curse Backfire — Severe tier (20% default)", value="backfire_chance_severe"),
    ])
    async def set_chance(
        self, interaction: discord.Interaction,
        feature: str,
        value: float,
    ) -> None:
        if not 0.0 <= value <= 1.0:
            await interaction.response.send_message("Value must be between 0.0 and 1.0.", ephemeral=True)
            return
        set_config_float(db_conn, feature, value)
        label = feature.replace("_", " ").title()
        await interaction.response.send_message(
            f"**{label}** set to **{value:.0%}**.", ephemeral=True
        )

    @app_commands.command(name="set_summary_emoji", description="Set the emoji that triggers link summarisation (default: 📰)")
    @app_commands.describe(emoji="The emoji to use for link summary reactions")
    async def set_summary_emoji(self, interaction: discord.Interaction, emoji: str) -> None:
        set_config_str(db_conn, "link_summary_emoji", emoji.strip())
        await interaction.response.send_message(
            f"Link summary emoji set to **{emoji.strip()}**. React with it on any message containing a URL to get a summary.",
            ephemeral=True,
        )

    @app_commands.command(name="set_points", description="Manually set Boli Points for a user (for data restore)")
    @app_commands.describe(user="The member to update", points="Boli Points to set", readings="Prediction count to set", rashi="Rashi to assign (optional)")
    async def set_points_cmd(self, interaction: discord.Interaction, user: discord.Member, points: int, readings: int = 0, rashi: str | None = None) -> None:
        set_user_points(db_conn, user.id, user.display_name, points, prediction_count=readings, rashi=rashi)
        rashi_str = f" · Rashi: {rashi}" if rashi else ""
        await interaction.response.send_message(
            f"✅ **{user.display_name}** → 🍮 **{points} Boli Points** · {readings} readings{rashi_str}",
            ephemeral=True,
        )
        logger.info("Manual point restore: %s → %d pts, %d readings", user.display_name, points, readings)

    @app_commands.command(name="adjust_points", description="Add or subtract Boli Points for a user")
    @app_commands.describe(
        user="The member to adjust points for",
        operation="Add or subtract points",
        amount="How many points (1–10000)",
    )
    @app_commands.choices(operation=[
        app_commands.Choice(name="Add", value="add"),
        app_commands.Choice(name="Subtract", value="subtract"),
    ])
    async def adjust_points_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        operation: str,
        amount: app_commands.Range[int, 1, 10000],
    ) -> None:
        delta = amount if operation == "add" else -amount
        profile = get_user_profile(db_conn, user.id)
        old_pts = profile["boli_points"] if profile else 0
        update_boli_points(db_conn, user.id, delta)
        new_pts = old_pts + delta
        sign = f"+{amount}" if operation == "add" else f"-{amount}"
        await interaction.response.send_message(
            f"✅ **{user.display_name}**: {sign} Boli Points → now 🍮 **{new_pts}** pts",
            ephemeral=True,
        )
        logger.info(
            "Manual point adjustment: %s %s%d pts (was %d, now %d) by %s",
            user.display_name, "+" if operation == "add" else "-", amount,
            old_pts, new_pts, interaction.user.display_name,
        )

    @app_commands.command(name="reset_all_strikes", description="Reset every user's strike count to 0")
    async def reset_all_strikes_cmd(self, interaction: discord.Interaction) -> None:
        affected = reset_all_strikes(db_conn)
        await interaction.response.send_message(
            f"✅ All strikes cleared. **{affected}** user(s) had their strike count reset to 0.",
            ephemeral=True,
        )
        logger.info("reset_all_strikes executed by %s — %d users affected", interaction.user.display_name, affected)

    @app_commands.command(name="config_view", description="View all active feature flags, probabilities, and cooldowns")
    async def config_view(self, interaction: discord.Interaction) -> None:
        configs = get_all_configs(db_conn)
        embed = discord.Embed(title="⚙️ Navi Configuration", color=discord.Color.dark_grey())

        # API mode
        embed.add_field(
            name="🔌 API Mode",
            value=f"Free-Only (Killswitch): **{'ON 🔴' if gemini_svc.free_only else 'OFF 🟢'}**",
            inline=False,
        )

        # Feature flags
        flags = {k: v for k, v in configs.items() if k.startswith("feature_")}
        if flags:
            flag_lines = []
            for k, v in sorted(flags.items()):
                label = k.replace("feature_", "").replace("_", " ").title()
                icon = "✅" if v else "❌"
                flag_lines.append(f"{icon} {label}")
            embed.add_field(name="🎛️ Feature Flags", value="\n".join(flag_lines), inline=False)

        # Probabilities — show db value or fallback default
        _PROB_DEFAULTS = {
            "cache_reuse_chance":      ("Cache Reuse (navi)",           None),
            "kochi_reply_chance":      ("Kochi Slang Reply",            0.28),
            "curse_reply_chance":      ("Passive Curse Reply",          0.25),
            "universal_blessing_chance": ("Universal Blessing",         0.05),
            "backfire_chance_mild":    ("Backfire — Mild tier",         0.10),
            "backfire_chance_moderate":("Backfire — Moderate tier",     0.15),
            "backfire_chance_severe":  ("Backfire — Severe tier",       0.20),
        }
        prob_keys = set(_PROB_DEFAULTS.keys())
        prob_lines = []
        for k, (label, default) in _PROB_DEFAULTS.items():
            v = configs.get(k)
            if v is not None:
                prob_lines.append(f"`{label}` → **{float(v):.0%}**")
            elif default is not None:
                prob_lines.append(f"`{label}` → **{default:.0%}** *(default)*")
        if prob_lines:
            embed.add_field(name="🎲 Probabilities", value="\n".join(prob_lines), inline=False)

        # Other settings
        other_keys = set(configs.keys()) - set(flags.keys()) - prob_keys
        other_lines = []
        for k in sorted(other_keys):
            if k.startswith("feature_"):
                continue
            other_lines.append(f"`{k}` → **{configs[k]}**")
        if other_lines:
            embed.add_field(name="⚙️ Other", value="\n".join(other_lines), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

tree.add_command(AdminGroup())


@tree.command(name="strike", description="Issue a manual strike to a user (Moderators only)")
@app_commands.describe(user="The member to strike", reason="Why you're issuing the strike")
@app_commands.default_permissions(manage_messages=True)
async def strike_slash(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str = "Manual mod action",
) -> None:
    if user.bot:
        await interaction.response.send_message("You can't strike a bot.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await _handle_strike(user, interaction.channel)
    current = get_user_strikes(db_conn, user.id)
    await interaction.followup.send(
        f"Strike issued to **{user.display_name}**. Reason: *{reason}*. "
        f"They now have **{current} strike(s)**.",
        ephemeral=True,
    )
    logger.info("Manual strike issued to %s by %s — reason: %s", user.display_name, interaction.user.display_name, reason)


# ---------------------------------------------------------------------------
# Gambling Mini-Games
# ---------------------------------------------------------------------------

_ROULETTE_RED: frozenset[int] = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36})

# Dice exact-number payout multipliers (total return including stake)
# Probability: 1/36 for 2&12, 2/36 for 3&11, 3/36 for 4&10, 4/36 for 5&9, 5/36 for 6&8
_DICE_EXACT_PAYOUT: dict[int, int] = {
    2: 36, 3: 18, 4: 12, 5: 9, 6: 7,
    8: 7, 9: 9, 10: 12, 11: 18, 12: 36,
}


def _gambling_guard(
    profile: dict | None,
    bet: int,
) -> str | None:
    """Return an error string if the bet is invalid, else None."""
    if profile is None:
        return "No profile found. Use /navi first to get started."
    if bet < 1:
        return "Bet must be at least 1 Boli."
    if profile["boli_points"] < bet:
        return f"Not enough Boli. You have 🍮 **{profile['boli_points']}** but bet **{bet}**."
    return None


@tree.command(name="flip", description="Coin flip — bet Boli on heads or tails (1:1 payout)")
@app_commands.describe(choice="heads or tails", bet="How many Boli to wager")
@app_commands.choices(choice=[
    app_commands.Choice(name="Heads", value="heads"),
    app_commands.Choice(name="Tails", value="tails"),
])
async def flip_slash(
    interaction: discord.Interaction,
    choice: str,
    bet: app_commands.Range[int, 1, 50000],
) -> None:
    if not _feat("feature_gambling"):
        await interaction.response.send_message("Gambling is currently disabled.", ephemeral=True)
        return
    profile = get_user_profile(db_conn, interaction.user.id)
    err = _gambling_guard(profile, bet)
    if err:
        await interaction.response.send_message(err, ephemeral=True)
        return

    daily_count = get_game_daily_count(db_conn, interaction.user.id, "flip")
    if daily_count >= _GAME_DAILY_LIMIT:
        msg = random.choice(_OVER_QUOTA_GAME_MESSAGES).format(
            user=interaction.user.display_name, game="Coin Flip"
        )
        await interaction.response.send_message(msg, ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    increment_game_daily_count(db_conn, interaction.user.id, "flip")
    update_boli_points(db_conn, interaction.user.id, -bet)
    await asyncio.sleep(1.5)

    result = random.choice(["heads", "tails"])
    coin_emoji = "🪙"
    if result == choice:
        update_boli_points(db_conn, interaction.user.id, bet * 2)
        outcome = f"✅ **{result.capitalize()}!** You win **{bet} Boli**! {coin_emoji}"
    else:
        outcome = f"❌ **{result.capitalize()}.** You lose **{bet} Boli**. {coin_emoji}"

    embed = discord.Embed(
        title=f"{coin_emoji} Cosmic Coin Flip",
        description=f"{interaction.user.mention} bet **{bet} Boli** on **{choice}**.\n\n{outcome}",
        color=discord.Color.green() if result == choice else discord.Color.red(),
    )
    new_pts = (profile["boli_points"] - bet) + (bet * 2 if result == choice else 0)
    embed.set_footer(text=f"Balance: 🍮 {new_pts} Boli")
    await interaction.followup.send(embed=embed)
    logger.info(
        "%s flipped %s on %s, result=%s → %s",
        interaction.user.display_name, bet, choice, result, "win" if result == choice else "lose",
    )


@tree.command(name="roll_dice", description="Roll two dice — bet on over/under/exact 7, or call a specific sum")
@app_commands.describe(bet_type="Your prediction", bet="How many Boli to wager")
@app_commands.choices(bet_type=[
    app_commands.Choice(name="Over 7 (1:1 payout)", value="over7"),
    app_commands.Choice(name="Under 7 (1:1 payout)", value="under7"),
    app_commands.Choice(name="Exact 7 (4:1 payout)", value="exact7"),
    app_commands.Choice(name="Exact 2 — 35:1 payout", value="2"),
    app_commands.Choice(name="Exact 3 — 17:1 payout", value="3"),
    app_commands.Choice(name="Exact 4 — 11:1 payout", value="4"),
    app_commands.Choice(name="Exact 5 — 8:1 payout", value="5"),
    app_commands.Choice(name="Exact 6 — 6:1 payout", value="6"),
    app_commands.Choice(name="Exact 8 — 6:1 payout", value="8"),
    app_commands.Choice(name="Exact 9 — 8:1 payout", value="9"),
    app_commands.Choice(name="Exact 10 — 11:1 payout", value="10"),
    app_commands.Choice(name="Exact 11 — 17:1 payout", value="11"),
    app_commands.Choice(name="Exact 12 — 35:1 payout", value="12"),
])
async def roll_dice_slash(
    interaction: discord.Interaction,
    bet_type: str,
    bet: app_commands.Range[int, 1, 50000],
) -> None:
    if not _feat("feature_gambling"):
        await interaction.response.send_message("Gambling is currently disabled.", ephemeral=True)
        return
    profile = get_user_profile(db_conn, interaction.user.id)
    err = _gambling_guard(profile, bet)
    if err:
        await interaction.response.send_message(err, ephemeral=True)
        return

    daily_count = get_game_daily_count(db_conn, interaction.user.id, "roll_dice")
    if daily_count >= _GAME_DAILY_LIMIT:
        msg = random.choice(_OVER_QUOTA_GAME_MESSAGES).format(
            user=interaction.user.display_name, game="Dice"
        )
        await interaction.response.send_message(msg, ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    increment_game_daily_count(db_conn, interaction.user.id, "roll_dice")
    update_boli_points(db_conn, interaction.user.id, -bet)
    await asyncio.sleep(1.5)

    d1, d2 = random.randint(1, 6), random.randint(1, 6)
    total = d1 + d2

    if bet_type == "over7":
        won = total > 7
        payout_mult = 2
        bet_label = "Over 7"
    elif bet_type == "under7":
        won = total < 7
        payout_mult = 2
        bet_label = "Under 7"
    elif bet_type == "exact7":
        won = total == 7
        payout_mult = 5
        bet_label = "Exact 7"
    else:
        target = int(bet_type)
        won = total == target
        payout_mult = _DICE_EXACT_PAYOUT[target]
        bet_label = f"Exact {target}"

    winnings = bet * payout_mult if won else 0
    if won:
        update_boli_points(db_conn, interaction.user.id, winnings)

    dice_display = f"🎲 **{d1}** + **{d2}** = **{total}**"
    outcome_str = f"✅ **Win!** +**{winnings - bet} Boli**" if won else f"❌ **Loss.** -{bet} Boli"
    embed = discord.Embed(
        title="🎲 Cosmic Dice",
        description=(
            f"{interaction.user.mention} bet **{bet} Boli** on **{bet_label}**.\n\n"
            f"{dice_display}\n\n{outcome_str}"
        ),
        color=discord.Color.green() if won else discord.Color.red(),
    )
    new_pts = (profile["boli_points"] - bet) + winnings
    embed.set_footer(text=f"Balance: 🍮 {new_pts} Boli")
    await interaction.followup.send(embed=embed)
    logger.info(
        "%s rolled dice (%d+%d=%d) on %s, bet=%d → %s",
        interaction.user.display_name, d1, d2, total, bet_type, bet, "win" if won else "lose",
    )


_ROULETTE_DOZENS: dict[str, tuple[int, int]] = {
    "dozen1": (1, 12), "dozen2": (13, 24), "dozen3": (25, 36)
}


@tree.command(name="roulette", description="Space roulette — color (1:1), dozen (2:1), odd/even (1:1), or number 1-36 (35:1)")
@app_commands.describe(
    choice="red/black, odd/even, dozen1/dozen2/dozen3, or a number 1-36",
    bet="How many Boli to wager",
)
async def roulette_slash(
    interaction: discord.Interaction,
    choice: str,
    bet: app_commands.Range[int, 1, 50000],
) -> None:
    if not _feat("feature_gambling"):
        await interaction.response.send_message("Gambling is currently disabled.", ephemeral=True)
        return
    choice = choice.strip().lower()
    valid_colors = {"red", "black"}
    valid_evenodd = {"odd", "even"}
    is_color = choice in valid_colors
    is_evenodd = choice in valid_evenodd
    is_dozen = choice in _ROULETTE_DOZENS
    is_number = choice.isdigit() and 1 <= int(choice) <= 36
    if not (is_color or is_evenodd or is_dozen or is_number):
        await interaction.response.send_message(
            "Invalid choice. Use `red`/`black`, `odd`/`even`, `dozen1`/`dozen2`/`dozen3`, or a number `1`–`36`.",
            ephemeral=True,
        )
        return

    profile = get_user_profile(db_conn, interaction.user.id)
    err = _gambling_guard(profile, bet)
    if err:
        await interaction.response.send_message(err, ephemeral=True)
        return

    daily_count = get_game_daily_count(db_conn, interaction.user.id, "roulette")
    if daily_count >= _GAME_DAILY_LIMIT:
        msg = random.choice(_OVER_QUOTA_GAME_MESSAGES).format(
            user=interaction.user.display_name, game="Roulette"
        )
        await interaction.response.send_message(msg, ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    increment_game_daily_count(db_conn, interaction.user.id, "roulette")
    update_boli_points(db_conn, interaction.user.id, -bet)

    await interaction.followup.send(
        f"🌀 The cosmic wheel is spinning for {interaction.user.mention}…", silent=True
    )
    await asyncio.sleep(2)

    # 39-pocket wheel: 1-36 playable + 0/37/38 green → ~7.7% house edge on all bet types
    landed = random.randint(0, 38)
    _green_pockets = {0, 37, 38}

    if landed in _green_pockets:
        landed_color = "green"
        color_emoji = "🟢"
    else:
        landed_color = "red" if landed in _ROULETTE_RED else "black"
        color_emoji = "🔴" if landed_color == "red" else "⚫"

    if is_color:
        won = landed not in _green_pockets and landed_color == choice
        payout_mult = 2
    elif is_evenodd:
        won = landed not in _green_pockets and (
            landed % 2 == 0 if choice == "even" else landed % 2 == 1
        )
        payout_mult = 2
    elif is_dozen:
        lo, hi = _ROULETTE_DOZENS[choice]
        won = landed not in _green_pockets and lo <= landed <= hi
        payout_mult = 3
    else:
        won = landed == int(choice)
        payout_mult = 36

    winnings = bet * payout_mult if won else 0
    if won:
        update_boli_points(db_conn, interaction.user.id, winnings)

    outcome_str = f"✅ **Win!** +**{winnings - bet} Boli**" if won else f"❌ **Loss.** -{bet} Boli"
    landed_display = f"{landed} (green)" if landed in _green_pockets else f"{landed} ({landed_color})"
    embed = discord.Embed(
        title="🌀 Space Roulette",
        description=(
            f"{interaction.user.mention} bet **{bet} Boli** on **{choice}**.\n\n"
            f"The wheel landed on: {color_emoji} **{landed_display}**\n\n{outcome_str}"
        ),
        color=discord.Color.green() if won else discord.Color.red(),
    )
    new_pts = (profile["boli_points"] - bet) + winnings
    embed.set_footer(text=f"Balance: 🍮 {new_pts} Boli")
    await interaction.followup.send(embed=embed)
    logger.info(
        "%s roulette: bet %d on '%s', landed %d (%s) → %s",
        interaction.user.display_name, bet, choice, landed, landed_color, "win" if won else "lose",
    )


# ---------------------------------------------------------------------------
# /slots — slot machine
# ---------------------------------------------------------------------------

_SLOTS_SYMBOLS: list[str] = ["🍎", "🍒", "🍋", "🔔", "⭐", "💎", "7️⃣"]
_SLOTS_WEIGHTS: list[int]  = [ 30,   25,   20,   15,   7,    5,    3  ]


@tree.command(name="slots", description="Spin the cosmic slot machine — first 2 match 2:1, 3-match 10:1, jackpot 50:1")
@app_commands.describe(bet="How many Boli to wager")
async def slots_slash(
    interaction: discord.Interaction,
    bet: app_commands.Range[int, 1, 50000],
) -> None:
    if not _feat("feature_gambling"):
        await interaction.response.send_message("Gambling is currently disabled.", ephemeral=True)
        return
    profile = get_user_profile(db_conn, interaction.user.id)
    err = _gambling_guard(profile, bet)
    if err:
        await interaction.response.send_message(err, ephemeral=True)
        return

    daily_count = get_game_daily_count(db_conn, interaction.user.id, "slots")
    if daily_count >= _GAME_DAILY_LIMIT:
        msg = random.choice(_OVER_QUOTA_GAME_MESSAGES).format(
            user=interaction.user.display_name, game="Slots"
        )
        await interaction.response.send_message(msg, ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    increment_game_daily_count(db_conn, interaction.user.id, "slots")
    update_boli_points(db_conn, interaction.user.id, -bet)
    await asyncio.sleep(1.5)

    reels = random.choices(_SLOTS_SYMBOLS, weights=_SLOTS_WEIGHTS, k=3)
    r1, r2, r3 = reels
    reel_display = f"[ {r1} | {r2} | {r3} ]"

    jackpot = r1 == r2 == r3 == "7️⃣"
    three_match = r1 == r2 == r3
    two_match = r1 == r2

    if jackpot:
        payout_mult = 51  # 50:1 → 51x return
        outcome_str = f"🎰 **JACKPOT!!!** +**{bet * payout_mult - bet} Boli**"
    elif three_match:
        payout_mult = 11  # 10:1 → 11x return
        outcome_str = f"✅ **Three of a kind!** +**{bet * payout_mult - bet} Boli**"
    elif two_match:
        payout_mult = 3   # 2:1 → 3x return
        outcome_str = f"✅ **Two of a kind (first two)!** +**{bet * payout_mult - bet} Boli**"
    else:
        payout_mult = 0
        outcome_str = f"❌ **No match.** -{bet} Boli"

    winnings = bet * payout_mult
    if winnings:
        update_boli_points(db_conn, interaction.user.id, winnings)

    embed = discord.Embed(
        title="🎰 Cosmic Slots",
        description=(
            f"{interaction.user.mention} bet **{bet} Boli**.\n\n"
            f"## {reel_display}\n\n{outcome_str}"
        ),
        color=discord.Color.gold() if jackpot else (discord.Color.green() if payout_mult else discord.Color.red()),
    )
    new_pts = (profile["boli_points"] - bet) + winnings
    embed.set_footer(text=f"Balance: 🍮 {new_pts} Boli")
    await interaction.followup.send(embed=embed)
    logger.info(
        "%s slots: bet=%d reels=[%s|%s|%s] → %s",
        interaction.user.display_name, bet, r1, r2, r3,
        "jackpot" if jackpot else ("3match" if three_match else ("2match" if two_match else "lose")),
    )


# ---------------------------------------------------------------------------
# /gift — send Boli points to another user
# ---------------------------------------------------------------------------

_GIFT_TEMPLATES: list[str] = [
    "✨ A cosmic breeze just blew **{amount} Boli** from {sender} into {recipient}'s pocket! Don't spend it all in one dimension.",
    "💖 {sender} just sprinkled **{amount} Boli** points onto {recipient}! Friendship level up!",
    "🚀 Delivery! {sender} fired a care package of **{amount} Boli** straight at {recipient}.",
    "🪄 Poof! {sender} magically transferred **{amount} Boli** to {recipient}. Use it wisely!",
    "🎁 {sender} slipped **{amount} Boli** under {recipient}'s pillow. The Boli fairy has arrived!",
]


@tree.command(name="gift", description="Gift Boli points to another user")
@app_commands.describe(recipient="Who to gift points to", amount="How many Boli points to send")
async def gift_slash(
    interaction: discord.Interaction,
    recipient: discord.Member,
    amount: app_commands.Range[int, 1, 10000],
) -> None:
    if recipient.id == interaction.user.id:
        await interaction.response.send_message("You can't gift points to yourself.", ephemeral=True)
        return
    if recipient.bot:
        await interaction.response.send_message("Bots don't need Boli points.", ephemeral=True)
        return

    profile = get_user_profile(db_conn, interaction.user.id)
    if not profile:
        await interaction.response.send_message(
            "No profile found. Use /navi first to get started.", ephemeral=True
        )
        return

    sender_pts = profile["boli_points"]
    if sender_pts < amount:
        await interaction.response.send_message(
            f"Not enough Boli. You have 🍮 **{sender_pts}** but tried to gift **{amount}**.",
            ephemeral=True,
        )
        return

    update_boli_points(db_conn, interaction.user.id, -amount)
    upsert_user(db_conn, recipient.id, recipient.display_name)
    update_boli_points(db_conn, recipient.id, amount)

    msg = random.choice(_GIFT_TEMPLATES).format(
        sender=interaction.user.mention,
        recipient=recipient.mention,
        amount=amount,
    )
    await interaction.response.send_message(msg)
    logger.info("%s gifted %d Boli to %s", interaction.user.display_name, amount, recipient.display_name)


# ---------------------------------------------------------------------------
# Boli Marketplace
# ---------------------------------------------------------------------------

_SHOP_ITEMS: dict[str, dict] = {
    "curse_protection": {
        "name": "Curse Protection",
        "cost": 20,
        "description": "100% reversal of proxy curses for 24 hours. Anyone who tries `navi @you` gets it back.",
        "emoji": "🛡️",
        "duration_hours": 24,
    },
    "custom_rashi": {
        "name": "Customize Rashi",
        "cost": 40,
        "description": "Pick your own Rashi from the cosmic menu. Your destiny, your choice. For now.",
        "emoji": "🌟",
        "duration_hours": 0,  # permanent until next purchase
    },
    "action_refill": {
        "name": "10x Cosmic Actions Refill",
        "cost": 30,
        "description": "Instantly adds 10 extra curses/blessings to your quota. Use them wisely.",
        "emoji": "🔋",
        "duration_hours": 0,
    },
    "deflector_shield": {
        "name": "Deflector Shield",
        "cost": 15,
        "description": "For 30 minutes after activation, any curse aimed at you is automatically reversed back to the caster.",
        "emoji": "🔮",
        "duration_hours": 0.5,
    },
    "timeout_ticket": {
        "name": "Timeout Ticket",
        "cost": 25,
        "description": "Block a target user from casting curses for 30 minutes. They lose 3 Boli per attempt. Requires a target.",
        "emoji": "🚫",
        "duration_hours": 0.5,
        "requires_target": True,
    },
    "multiplier_potion": {
        "name": "Multiplier Potion",
        "cost": 35,
        "description": "For 15 minutes, every curse you cast fires twice. The 2nd curse (15–45s later) mirrors the fate of the 1st.",
        "emoji": "⚡",
        "duration_hours": 0.25,
    },
}


class ShopGroup(app_commands.Group):
    """Boli Marketplace — spend your hard-earned points on cosmic perks."""

    def __init__(self):
        super().__init__(name="shop", description="Boli Marketplace — spend Boli Points on cosmic perks")

    @app_commands.command(name="view", description="Browse available items in the Boli Marketplace")
    async def view(self, interaction: discord.Interaction) -> None:
        profile = get_user_profile(db_conn, interaction.user.id)
        pts = profile["boli_points"] if profile else 0

        embed = discord.Embed(
            title="🏪 Boli Marketplace",
            description=f"Your balance: 🍮 **{pts} Boli Points**\nUse `/shop buy <item>` to purchase.",
            color=discord.Color.dark_gold(),
        )
        _perk_map = {
            "curse_protection": "curse_protection",
            "deflector_shield": "deflector_shield",
            "multiplier_potion": "multiplier_potion",
        }
        for item_id, item in _SHOP_ITEMS.items():
            active_note = ""
            perk_key = _perk_map.get(item_id)
            if perk_key:
                expiry = get_perk_expiry(db_conn, interaction.user.id, perk_key)
                if expiry:
                    expiry_utc = expiry.replace(tzinfo=timezone.utc)
                    active_note = f"\n*(Active until <t:{int(expiry_utc.timestamp())}:t>)*"

            # Dynamic pricing for action_refill
            if item_id == "action_refill":
                refill_count = get_daily_refill_count(db_conn, interaction.user.id)
                extra_remaining = get_extra_actions(db_conn, interaction.user.id)
                if refill_count >= 2:
                    cost_label = "🍮 SOLD OUT (2/2 today)"
                    can_afford = "❌"
                elif extra_remaining > 0:
                    cost_label = f"🍮 Spend your {extra_remaining} remaining first"
                    can_afford = "🔒"
                else:
                    dynamic_cost = 30 if refill_count == 0 else 50
                    can_afford = "✅" if pts >= dynamic_cost else "❌"
                    cost_label = f"🍮 {dynamic_cost} pts {can_afford}"
                embed.add_field(
                    name=f"{item['emoji']} {item['name']} — {cost_label}",
                    value=f"{item['description']} *(1st buy: 30 pts • 2nd buy: 50 pts • max 2/day)*{active_note}",
                    inline=False,
                )
            else:
                can_afford = "✅" if pts >= item["cost"] else "❌"
                embed.add_field(
                    name=f"{item['emoji']} {item['name']} — 🍮 {item['cost']} pts {can_afford}",
                    value=f"{item['description']}{active_note}",
                    inline=False,
                )

        embed.set_footer(text="Earn Boli Points by using /navi and Trivandrum slang.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="buy", description="Purchase an item from the Boli Marketplace")
    @app_commands.describe(
        item="Which item to buy",
        rashi_choice="Your chosen Rashi (only for custom_rashi)",
        target="Target user (only for timeout_ticket)",
    )
    @app_commands.choices(item=[
        app_commands.Choice(name="🛡️ Curse Protection (20 pts)", value="curse_protection"),
        app_commands.Choice(name="🌟 Customize Rashi (40 pts)", value="custom_rashi"),
        app_commands.Choice(name="🔋 10x Cosmic Actions Refill (30 pts)", value="action_refill"),
        app_commands.Choice(name="🔮 Deflector Shield (15 pts)", value="deflector_shield"),
        app_commands.Choice(name="🚫 Timeout Ticket (25 pts)", value="timeout_ticket"),
        app_commands.Choice(name="⚡ Multiplier Potion (35 pts)", value="multiplier_potion"),
    ])
    async def buy(
        self,
        interaction: discord.Interaction,
        item: str,
        rashi_choice: str | None = None,
        target: discord.Member | None = None,
    ) -> None:
        if item not in _SHOP_ITEMS:
            await interaction.response.send_message("That item doesn't exist.", ephemeral=True)
            return

        shop_item = _SHOP_ITEMS[item]
        profile = get_user_profile(db_conn, interaction.user.id)
        if not profile:
            await interaction.response.send_message(
                "No profile found. Use /navi first to get started.", ephemeral=True
            )
            return

        pts = profile["boli_points"]
        cost = shop_item["cost"]

        if pts < cost:
            await interaction.response.send_message(
                f"Not enough Boli Points. You have 🍮 **{pts}** but need **{cost}**.",
                ephemeral=True,
            )
            return

        # --- Curse Protection ---
        if item == "curse_protection":
            update_boli_points(db_conn, interaction.user.id, -cost)
            grant_perk(db_conn, interaction.user.id, "curse_protection", duration_hours=24)
            expiry = get_perk_expiry(db_conn, interaction.user.id, "curse_protection")
            expiry_utc = expiry.replace(tzinfo=timezone.utc) if expiry else None
            ts = f"<t:{int(expiry_utc.timestamp())}:f>" if expiry_utc else "24 hours"
            await interaction.response.send_message(
                f"🛡️ **Curse Protection activated!** {interaction.user.mention}, you're protected until {ts}. "
                f"Any proxy curse attempt will be reversed back at the sender.\n"
                f"🍮 -{cost} Boli Points (remaining: **{pts - cost}**)",
                ephemeral=True,
            )
            logger.info("%s purchased Curse Protection", interaction.user.display_name)

        # --- Custom Rashi ---
        elif item == "custom_rashi":
            from glossary import RASHIS
            if not rashi_choice:
                rashi_list = "\n".join(f"• `{r}`" for r in RASHIS)
                await interaction.response.send_message(
                    f"🌟 **Pick your Rashi!** Use `/shop buy item:custom_rashi rashi_choice:<name>`\n\n"
                    f"Available Rashis:\n{rashi_list}",
                    ephemeral=True,
                )
                return

            # Validate choice
            matched = next((r for r in RASHIS if r.lower() == rashi_choice.strip().lower()), None)
            if not matched:
                rashi_list = ", ".join(RASHIS)
                await interaction.response.send_message(
                    f"`{rashi_choice}` is not a valid Rashi. Pick from: {rashi_list}",
                    ephemeral=True,
                )
                return

            update_boli_points(db_conn, interaction.user.id, -cost)
            upsert_user(db_conn, interaction.user.id, interaction.user.display_name, rashi=matched)
            await interaction.response.send_message(
                f"🌟 **Rashi updated!** {interaction.user.mention}, your sign is now **{matched}**.\n"
                f"🍮 -{cost} Boli Points (remaining: **{pts - cost}**)",
                ephemeral=True,
            )
            logger.info("%s purchased Custom Rashi: %s", interaction.user.display_name, matched)

        # --- Action Refill (tiered pricing: 30 Boli 1st buy, 50 Boli 2nd buy, blocked 3rd+) ---
        elif item == "action_refill":
            refill_count = get_daily_refill_count(db_conn, interaction.user.id)

            # Max 2 refills per day
            if refill_count >= 2:
                await interaction.response.send_message(
                    "🚫 You've already bought the maximum extra actions for today (2 refills). "
                    "Come back after midnight IST.",
                    ephemeral=True,
                )
                return

            # Must spend existing purchased extras before buying more
            extra_remaining = get_extra_actions(db_conn, interaction.user.id)
            if extra_remaining > 0:
                msg = random.choice(_HAS_EXTRAS_MESSAGES).format(
                    invoker=interaction.user.display_name, extra=extra_remaining
                )
                await interaction.response.send_message(msg, ephemeral=True)
                return

            # Dynamic cost: 30 for 1st purchase, 50 for 2nd
            cost = 30 if refill_count == 0 else 50

            if pts < cost:
                await interaction.response.send_message(
                    f"Not enough Boli Points. You have 🍮 **{pts}** but need **{cost}** for this refill.",
                    ephemeral=True,
                )
                return

            update_boli_points(db_conn, interaction.user.id, -cost)
            add_extra_actions(db_conn, interaction.user.id, 10)
            increment_daily_refill_count(db_conn, interaction.user.id)
            next_cost = 50 if refill_count == 0 else "N/A (max reached)"
            await interaction.response.send_message(
                f"🔋 Refill successful! {interaction.user.mention}, **+10 cosmic actions** added to your quota.\n"
                f"🍮 -{cost} Boli Points (remaining: **{pts - cost}**)\n"
                f"*(Next refill today costs: {next_cost} Boli • Spend these before buying again)*",
                ephemeral=True,
            )
            logger.info(
                "%s purchased Action Refill #%d (+10 extra actions, cost=%d)",
                interaction.user.display_name, refill_count + 1, cost,
            )

        # --- Deflector Shield ---
        elif item == "deflector_shield":
            update_boli_points(db_conn, interaction.user.id, -cost)
            grant_perk(db_conn, interaction.user.id, "deflector_shield", duration_hours=0.5)
            expiry = get_perk_expiry(db_conn, interaction.user.id, "deflector_shield")
            expiry_utc = expiry.replace(tzinfo=timezone.utc) if expiry else None
            ts = f"<t:{int(expiry_utc.timestamp())}:f>" if expiry_utc else "30 minutes"
            await interaction.response.send_message(
                f"🔮 **Deflector Shield active!** {interaction.user.mention}, curses will bounce back to their caster until {ts}.\n"
                f"🍮 -{cost} Boli Points (remaining: **{pts - cost}**)",
                ephemeral=True,
            )
            logger.info("%s purchased Deflector Shield", interaction.user.display_name)

        # --- Timeout Ticket ---
        elif item == "timeout_ticket":
            if target is None:
                await interaction.response.send_message(
                    "🚫 **Timeout Ticket** requires a target. Use `/shop buy item:timeout_ticket target:@user`.",
                    ephemeral=True,
                )
                return
            if target.bot:
                await interaction.response.send_message("You can't timeout a bot.", ephemeral=True)
                return
            if target.id == interaction.user.id:
                await interaction.response.send_message("You can't timeout yourself.", ephemeral=True)
                return
            update_boli_points(db_conn, interaction.user.id, -cost)
            upsert_user(db_conn, target.id, target.display_name)
            grant_perk(db_conn, target.id, "curse_timeout", duration_hours=0.5)
            expiry = get_perk_expiry(db_conn, target.id, "curse_timeout")
            expiry_utc = expiry.replace(tzinfo=timezone.utc) if expiry else None
            ts = f"<t:{int(expiry_utc.timestamp())}:f>" if expiry_utc else "30 minutes"
            await interaction.response.send_message(
                f"🚫 **Timeout Ticket used!** {target.mention} is barred from casting curses until {ts}. "
                f"Any attempt will cost them 3 Boli.\n"
                f"🍮 -{cost} Boli Points (remaining: **{pts - cost}**)",
            )
            logger.info(
                "%s used Timeout Ticket on %s", interaction.user.display_name, target.display_name
            )

        # --- Multiplier Potion ---
        elif item == "multiplier_potion":
            update_boli_points(db_conn, interaction.user.id, -cost)
            grant_perk(db_conn, interaction.user.id, "multiplier_potion", duration_hours=0.25)
            expiry = get_perk_expiry(db_conn, interaction.user.id, "multiplier_potion")
            expiry_utc = expiry.replace(tzinfo=timezone.utc) if expiry else None
            ts = f"<t:{int(expiry_utc.timestamp())}:f>" if expiry_utc else "15 minutes"
            await interaction.response.send_message(
                f"⚡ **Multiplier Potion active!** {interaction.user.mention}, every curse you cast until {ts} "
                f"will fire twice — the 2nd following 15–45 seconds later, fate-locked to the 1st.\n"
                f"🍮 -{cost} Boli Points (remaining: **{pts - cost}**)",
                ephemeral=True,
            )
            logger.info("%s purchased Multiplier Potion", interaction.user.display_name)


tree.add_command(ShopGroup())


# ---------------------------------------------------------------------------
# Temporary Voice Channels
# ---------------------------------------------------------------------------

_TEMP_VC_DURATION = 1800  # seconds (30 minutes)

# Tracks channel_id -> asyncio.Task so the voice-state handler can cancel early.
_temp_vc_registry: dict[int, asyncio.Task] = {}


async def _temp_vc_expire(channel: discord.VoiceChannel, delay: int) -> None:
    """Sleep delay seconds then delete the voice channel if it still exists."""
    await asyncio.sleep(delay)
    try:
        await channel.delete(reason="Temp VC expired after 30 minutes.")
        logger.info("Temp VC '%s' auto-deleted after timer expiry.", channel.name)
    except discord.NotFound:
        logger.debug("Temp VC '%s' was already deleted before timer fired.", channel.name)
    except Exception as exc:
        logger.warning("Could not delete temp VC '%s': %s", channel.name, exc)
    finally:
        _temp_vc_registry.pop(channel.id, None)


@tree.command(
    name="temp_vc",
    description="Create a temporary voice channel that self-destructs after 30 minutes",
)
@app_commands.describe(
    name="Name for the voice channel",
    capacity="Max users allowed (2–99)",
)
async def temp_vc_slash(
    interaction: discord.Interaction,
    name: str,
    capacity: app_commands.Range[int, 2, 99],
) -> None:
    if not _feat("feature_temp_vc"):
        await interaction.response.send_message(
            "Temp voice channel creation is currently disabled.",
            ephemeral=True,
        )
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This command only works inside a server.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=False)

    # Place the VC in the same category as the invoking channel when possible.
    category: discord.CategoryChannel | None = None
    if isinstance(interaction.channel, discord.abc.GuildChannel):
        category = interaction.channel.category
    if category is None:
        category = discord.utils.get(guild.categories, name="Temp VCs")

    # Roles with move_members or manage_channels already bypass user limits in
    # Discord natively; adding an explicit connect overwrite makes the intent
    # visible in the channel settings as well.
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(connect=True),
    }
    for role in guild.roles:
        if role.managed:
            continue
        if role.permissions.manage_channels or role.permissions.move_members:
            overwrites[role] = discord.PermissionOverwrite(connect=True)

    try:
        vc = await guild.create_voice_channel(
            name=name,
            category=category,
            user_limit=capacity,
            overwrites=overwrites,
            reason=f"Temp VC requested by {interaction.user.display_name}",
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "I don't have permission to create voice channels. Ask a mod to grant me **Manage Channels**.",
            ephemeral=True,
        )
        return
    except Exception as exc:
        logger.error("Failed to create temp VC: %s", exc)
        await interaction.followup.send(
            "Something went wrong while creating the voice channel. Try again.",
            ephemeral=True,
        )
        return

    task = asyncio.create_task(_temp_vc_expire(vc, _TEMP_VC_DURATION))
    _temp_vc_registry[vc.id] = task

    await interaction.followup.send(
        f"🎙️ Voice channel **{vc.name}** created with a limit of **{capacity}** users.\n"
        f"The stars grant you 30 minutes. Use them wisely."
    )
    logger.info(
        "Temp VC '%s' (id=%d, limit=%d) created by %s.",
        vc.name, vc.id, capacity, interaction.user.display_name,
    )


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    """Delete a temp VC early when it becomes completely empty."""
    if before.channel is None or before.channel.id not in _temp_vc_registry:
        return

    channel = before.channel
    remaining = [m for m in channel.members if not m.bot]
    if remaining:
        return

    # All users gone — cancel the 30-minute timer and delete now.
    task = _temp_vc_registry.pop(channel.id, None)
    if task:
        task.cancel()

    try:
        await channel.delete(reason="Temp VC emptied before the 30-minute timer.")
        logger.info("Temp VC '%s' deleted early (channel empty).", channel.name)
    except discord.NotFound:
        pass
    except Exception as exc:
        logger.warning("Could not early-delete temp VC '%s': %s", channel.name, exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.critical("DISCORD_TOKEN not found in .env. Stopping.")
        raise SystemExit(1)

    if not FREE_API_KEY and not PAID_API_KEY:
        logger.critical(
            "No Gemini API key found. Set GEMINI_API_KEY_FREE or GEMINI_API_KEY_PAID in .env."
        )
        raise SystemExit(1)

    logger.info(
        "Starting Navi... Free key: %s | Paid key: %s | Free tier mode: %s",
        "✓" if FREE_API_KEY else "✗",
        "✓" if PAID_API_KEY else "✗",
        FREE_TIER_MODE,
    )
    bot.run(DISCORD_TOKEN)