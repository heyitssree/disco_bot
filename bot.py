# bot.py - Navi (disco_bot) — Main Discord bot entrypoint

from __future__ import annotations

import logging
import os
import random
import re
import asyncio
from collections import deque
from datetime import datetime, timezone

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
    DB_PATH,
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
from datetime import timedelta

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

db_conn = None          # duckdb.DuckDBPyConnection
gemini_svc = None       # GeminiService
api_mgr = None          # ApiManager
_BOT_START_TIME = datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# In-memory feature toggle cache (Feature 8)
# Loaded at startup; updated immediately on every /admin toggle command.
# Avoids a DB read on every message/interaction.
# ---------------------------------------------------------------------------

_FEATURE_DEFAULTS: dict[str, int] = {
    "master_killswitch":    0,
    "feature_navi":         1,
    "feature_vibe_check":   1,
    "feature_kanmanilla":   1,
    "feature_audit":        1,
    "feature_mod_tldr":     1,
    "feature_link_summary": 1,
    "feature_strikes":      1,
    "feature_kochi_replies": 1,
    "feature_curse_replies": 1,
    "feature_boli_points":  1,
    "feature_welcome":      1,
    "feature_temp_vc":      1,
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


_DAILY_QUOTA = 15  # combined curse + bless actions per day

_OVER_QUOTA_CURSE_MESSAGES: list[str] = [
    "The universe is tired of your curses, {invoker}. You've been reverse-cursed for overindulgence. (-5 pts)",
    "15 curses a day is the cosmic limit, {invoker}. The excess comes straight back on you. (-5 pts)",
    "Quota exceeded, {invoker}. The cosmos doesn't appreciate the overtime — curse redirected. (-5 pts)",
    "Too many curses from {invoker} today. The universe is sending one back as a reminder. (-5 pts)",
    "{invoker}, even the stars have limits. You've been billed for the extra curse. (-5 pts)",
]

_OVER_QUOTA_BLESS_INVOKER_CURSE_MESSAGES: list[str] = [
    "{invoker}, you've burned through your daily quota. Even blessings have a limit — the cosmos fines you for the excess.",
    "Over the {quota}/day limit, {invoker}. The universe curses you for pushing it.",
    "{invoker} tried to bless one too many people today. The stars respond with a curse of their own.",
    "Daily action quota exceeded, {invoker}. The cosmos sends its regards — in curse form.",
    "{quota} actions per day, {invoker}. You hit the wall — the universe takes note. And points.",
]


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
# Events
# ---------------------------------------------------------------------------

@bot.event
async def on_ready() -> None:
    global db_conn, gemini_svc, api_mgr

    logger.info("Logged in as %s", bot.user)

    # Initialise database
    db_conn = init_db()
    seed_local_knowledge(db_conn)

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

    await tree.sync()
    logger.info("Slash commands synced. Navi is live. Hey! Listen!")


@bot.event
async def on_close() -> None:
    """Close the DB connection cleanly so DuckDB flushes the WAL on shutdown."""
    if db_conn is not None:
        try:
            db_conn.close()
            logger.info("DuckDB connection closed cleanly.")
        except Exception as exc:
            logger.warning("Error closing DuckDB on shutdown: %s", exc)


# MODA_INTROS, BOT_SELF_CURSE_REPLIES, BOT_LOOP_CURSE_REPLIES imported from prompts.py


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

    # Ignore bots and self
    if message.author.bot or message.author.id == bot.user.id:
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

    # Upsert user record on every message (lightweight)
    upsert_user(db_conn, message.author.id, message.author.display_name)

    # ---- Vibe Check: lightweight heat tracker — no API cost unless triggered ----
    if _feat("feature_vibe_check"):
        await _check_vibe(message)

    # ---- Boli Points: local slang triggers ----
    triggered_words = contains_boli_trigger(message.content) if get_config_int(db_conn, "feature_boli_points", 1) else []
    if triggered_words:
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
    if re.match(r'^navi\b', message.content, re.IGNORECASE):
        invoker = message.author

        # "navi @username" → curse the mentioned user (tiered points, quota-tracked)
        if message.mentions:
            target = message.mentions[0]
            if target.id == bot.user.id:
                await message.reply(random.choice(BOT_SELF_CURSE_REPLIES))
                return
            if target.bot:
                await message.reply(f"{invoker.mention} {random.choice(BOT_LOOP_CURSE_REPLIES)}")
                return

            # --- Daily quota check ---
            daily_count = get_daily_action_count(db_conn, invoker.id)
            extra_actions = get_extra_actions(db_conn, invoker.id)
            if daily_count >= _DAILY_QUOTA + extra_actions:
                update_boli_points(db_conn, invoker.id, -5)
                over_msg = random.choice(_OVER_QUOTA_CURSE_MESSAGES).format(invoker=invoker.display_name)
                await message.reply(over_msg)
                logger.info("%s exceeded daily curse quota — reverse-cursed (-5 pts)", invoker.display_name)
                return

            # --- Vampiric Karma Gamble ---
            curse_data = get_random_curse()
            curse_word = curse_data["word"]

            # Check if target has curse protection
            target_protected = has_active_perk(db_conn, target.id, "curse_protection")

            if not target_protected:
                reversal_chance = curse_data["backfire_chance"]
            else:
                reversal_chance = 0.0  # No backfire for protected targets

            increment_daily_action_count(db_conn, invoker.id)
            if daily_count >= _DAILY_QUOTA:
                decrement_extra_actions(db_conn, invoker.id)

            if random.random() < reversal_chance:
                # Backfire — invoker takes double damage, target unharmed
                points_lost = curse_data["target_damage"] * 2
                update_boli_points(db_conn, invoker.id, -points_lost)
                log_curse(db_conn, invoker.id, invoker.display_name, f"backfire_{curse_word}")
                await message.reply(
                    f"The cosmos rejected your negativity, {invoker.display_name}. "
                    f"The curse bounced back and hit you for {points_lost} points. {curse_word}!"
                )
                logger.info(
                    "Curse backfired on %s [%s, -%d invoker]",
                    invoker.display_name, curse_data["tier"], points_lost,
                )
            else:
                # Curse lands — but protected targets take no damage
                if not target_protected:
                    update_boli_points(db_conn, target.id, -curse_data["target_damage"])
                update_boli_points(db_conn, invoker.id, curse_data["invoker_reward"])
                log_curse(db_conn, target.id, target.display_name, curse_word)
                await message.reply(f"{curse_word} {target.display_name}")
                logger.info(
                    "%s cursed %s [%s, %s-%d target, +%d invoker]",
                    invoker.display_name, target.display_name,
                    curse_data["tier"], "protected, no " if target_protected else "-",
                    curse_data["target_damage"],
                    curse_data["invoker_reward"],
                )
            return

        # "navi" as a reply to someone → curse the replied-to user
        if message.reference:
            resolved = message.reference.resolved or message.reference.cached_message
            if isinstance(resolved, discord.Message) and not resolved.author.bot:
                target = resolved.author
                daily_count = get_daily_action_count(db_conn, invoker.id)
                extra_actions = get_extra_actions(db_conn, invoker.id)
                if daily_count >= _DAILY_QUOTA + extra_actions:
                    update_boli_points(db_conn, invoker.id, -5)
                    over_msg = random.choice(_OVER_QUOTA_CURSE_MESSAGES).format(invoker=invoker.display_name)
                    await message.reply(over_msg)
                    return
                curse_data = get_random_curse()
                update_boli_points(db_conn, target.id, -curse_data["target_damage"])
                update_boli_points(db_conn, invoker.id, curse_data["invoker_reward"])
                increment_daily_action_count(db_conn, invoker.id)
                if daily_count >= _DAILY_QUOTA:
                    decrement_extra_actions(db_conn, invoker.id)
                log_curse(db_conn, target.id, target.display_name, "proxy_navi_reply")
                await message.reply(f"{curse_data['word']} {target.display_name}")
                logger.info("%s cursed %s via navi reply", invoker.display_name, target.display_name)
                return

        # "navi" with no mention and no reply → self-curse
        target = invoker
        daily_count = get_daily_action_count(db_conn, invoker.id)
        extra_actions = get_extra_actions(db_conn, invoker.id)
        if daily_count >= _DAILY_QUOTA + extra_actions:
            update_boli_points(db_conn, invoker.id, -5)
            over_msg = random.choice(_OVER_QUOTA_CURSE_MESSAGES).format(invoker=invoker.display_name)
            await message.reply(over_msg)
            return
        curse_data = get_random_curse()
        update_boli_points(db_conn, target.id, -curse_data["target_damage"])
        increment_daily_action_count(db_conn, invoker.id)
        if daily_count >= _DAILY_QUOTA:
            decrement_extra_actions(db_conn, invoker.id)
        log_curse(db_conn, target.id, target.display_name, "proxy_navi_self")
        await message.reply(f"{curse_data['word']} {target.display_name}")
        logger.info("%s got self-cursed via navi prefix", target.display_name)
        return

    # ---- "chunk @user" — bless command (quota-tracked, no target mention) ----
    if re.match(r'^chunk\b', message.content, re.IGNORECASE):
        invoker = message.author

        def _bless_target(target: discord.Member | discord.User, award_points: bool) -> tuple[str, int]:
            compliment = get_random_compliment()
            pts = compliment["points"] if award_points else 0
            return f"{compliment['word']} {target.display_name}", pts  # display_name, not mention

        # "chunk @mention" → bless the mentioned user
        if message.mentions:
            target = message.mentions[0]
            if target.id == bot.user.id:
                await message.reply(random.choice(BOT_SELF_COMPLIMENT_REPLIES))
                return
            if target.bot:
                await message.reply(f"{invoker.mention} {random.choice(BOT_LOOP_COMPLIMENT_REPLIES)}")
                return

            daily_count = get_daily_action_count(db_conn, invoker.id)
            extra_actions = get_extra_actions(db_conn, invoker.id)
            if daily_count >= _DAILY_QUOTA + extra_actions:
                # Recipient still gets the blessing message, but NO points
                reply, _ = _bless_target(target, award_points=False)
                await message.reply(reply)
                # Invoker gets cursed by bot for exceeding quota
                curse_msg = random.choice(_OVER_QUOTA_BLESS_INVOKER_CURSE_MESSAGES).format(
                    invoker=invoker.display_name, quota=_DAILY_QUOTA
                )
                await message.channel.send(curse_msg)
                logger.info("%s exceeded daily bless quota — no points awarded, invoker cursed", invoker.display_name)
                return

            reply, pts = _bless_target(target, award_points=True)
            update_boli_points(db_conn, target.id, pts)
            update_boli_points(db_conn, invoker.id, 1)
            increment_daily_action_count(db_conn, invoker.id)
            if daily_count >= _DAILY_QUOTA:
                decrement_extra_actions(db_conn, invoker.id)
            await message.reply(reply)
            logger.info("%s blessed %s (+%d pts, invoker +1 karma)", invoker.display_name, target.display_name, pts)
            return

        # "chunk" as reply → bless the replied-to user
        if message.reference:
            resolved = message.reference.resolved or message.reference.cached_message
            if isinstance(resolved, discord.Message) and not resolved.author.bot:
                target = resolved.author
                daily_count = get_daily_action_count(db_conn, invoker.id)
                extra_actions = get_extra_actions(db_conn, invoker.id)
                if daily_count >= _DAILY_QUOTA + extra_actions:
                    reply, _ = _bless_target(target, award_points=False)
                    await message.reply(reply)
                    curse_msg = random.choice(_OVER_QUOTA_BLESS_INVOKER_CURSE_MESSAGES).format(
                        invoker=invoker.display_name, quota=_DAILY_QUOTA
                    )
                    await message.channel.send(curse_msg)
                    return
                reply, pts = _bless_target(target, award_points=True)
                update_boli_points(db_conn, target.id, pts)
                update_boli_points(db_conn, invoker.id, 1)
                increment_daily_action_count(db_conn, invoker.id)
                if daily_count >= _DAILY_QUOTA:
                    decrement_extra_actions(db_conn, invoker.id)
                await message.reply(reply)
                logger.info("%s blessed %s via reply (+%d pts, invoker +1 karma)", invoker.display_name, target.display_name, pts)
                return

        # "chunk" alone → self-bless
        target = invoker
        daily_count = get_daily_action_count(db_conn, invoker.id)
        extra_actions = get_extra_actions(db_conn, invoker.id)
        if daily_count >= _DAILY_QUOTA + extra_actions:
            reply, _ = _bless_target(target, award_points=False)
            await message.reply(reply)
            curse_msg = random.choice(_OVER_QUOTA_BLESS_INVOKER_CURSE_MESSAGES).format(
                invoker=invoker.display_name, quota=_DAILY_QUOTA
            )
            await message.channel.send(curse_msg)
            return
        reply, pts = _bless_target(target, award_points=True)
        update_boli_points(db_conn, target.id, pts)
        update_boli_points(db_conn, invoker.id, 1)
        increment_daily_action_count(db_conn, invoker.id)
        if daily_count >= _DAILY_QUOTA:
            decrement_extra_actions(db_conn, invoker.id)
        await message.reply(reply)
        logger.info("%s self-blessed (+%d pts, +1 karma)", target.display_name, pts)
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


@tree.command(name="rank", description="See the Top Appis — Boli Points leaderboard")
async def rank_slash(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=False)
    leaders = get_leaderboard(db_conn, limit=10)

    if not leaders:
        await interaction.followup.send(
            "Nobody has Boli Points yet. Use **/navi** and start earning."
        )
        return

    embed = discord.Embed(
        title="🍮 Boli Points Leaderboard",
        description="Top ranked members by Boli Points.",
        color=discord.Color.gold(),
    )

    medals = ["🥇", "🥈", "🥉"]
    for i, entry in enumerate(leaders):
        medal = medals[i] if i < 3 else f"**{i + 1}.**"
        rashi_str = f" · {entry['rashi']}" if entry.get("rashi") else ""
        level = get_level_from_points(entry["boli_points"])
        title = get_level_title(level)
        embed.add_field(
            name=f"{medal} {entry['username']}{rashi_str} · Lv.{level}",
            value=f"🍮 **{entry['boli_points']} Boli Points** · {entry['prediction_count']} readings · *{title}*",
            inline=False,
        )

    embed.set_footer(text="Earn points by using /navi and Trivandrum slang.")
    await interaction.followup.send(embed=embed)


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

    await interaction.response.send_message(
        f"**Your Profile**\n"
        f"🌟 Rashi: **{rashi}**\n"
        f"⚔️ Level: **{level}** — *{title}*\n"
        f"{progress_line}\n"
        f"🍮 Boli Points: **{pts}**\n"
        f"🔮 Predictions received: **{count}**",
        ephemeral=True,
    )


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
    if interaction.user.id != app_info.owner.id:
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
    from datetime import timedelta as _td
    cutoff = now_utc - _td(seconds=60)
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

    # ── DuckDB ──────────────────────────────────────────────────────────────
    counts_str = "\n".join(f"  `{t}`: {n}" for t, n in table_counts.items())
    embed.add_field(
        name=f"🗄️ DuckDB  ({db_size_str})",
        value=counts_str,
        inline=False,
    )

    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="help", description="Learn how to interact with Navi")
async def help_slash(interaction: discord.Interaction) -> None:
    """Show the help menu — only lists features that are currently enabled."""
    embed = discord.Embed(
        title="Navi — Help & Features",
        description="Fairy from Hyrule. Now stationed in Thirontharam. Speaks Manglish. Has seen things. Here is what I can do:",
        color=discord.Color.dark_purple(),
    )

    # Core always-on commands
    core_cmds = [
        "`/rank` — Top 10 Boli Points leaderboard",
        "`/mypoints` — Your Rashi, Boli Points, level, and title",
        "`/summ` — Factual English summary of recent chat",
        "`/shop view` / `/shop buy` — Boli Marketplace",
        "`/help` — This menu",
    ]
    if _feat("feature_navi"):
        core_cmds.insert(0, "`/navi` — Get a Navi prediction (rate-limited per minute)")
        core_cmds.insert(1, "`/navi user:@someone` — Get a prediction for someone else")
    if _feat("feature_kanmanilla"):
        core_cmds.append("`/kanmanilla @user` — Ping a missing member with a dramatic notice")
    if _feat("feature_temp_vc"):
        core_cmds.append("`/temp_vc name: capacity:` — Create a temporary voice channel (self-destructs in 30 min or when empty)")
    if _feat("feature_audit"):
        core_cmds.append("`/audit @user` — Mod: audit a user's messages against server rules")
    if _feat("feature_mod_tldr"):
        core_cmds.append("`/mod_tldr` — Mod: summarise the current thread")
    embed.add_field(name="Slash Commands", value="\n".join(core_cmds), inline=False)

    # Text triggers (only if navi enabled)
    if _feat("feature_navi"):
        embed.add_field(
            name="Text Commands (type in chat)",
            value=(
                "`navi` — Same as `/navi`, triggers a full prediction\n"
                "`navi @user` — Curse or roast someone (10% chance it bounces back on you)\n"
                "`chunk @user` — Compliment someone with Trivandrum slang (awards them Boli Points)"
            ),
            inline=False,
        )

    # Mention QA
    embed.add_field(
        name="Mention Q&A",
        value=(
            "`@Navi <question>` — Ask me anything. Factual answer first, then commentary if warranted.\n"
            "Works for news, scores, how-tos, general questions."
        ),
        inline=False,
    )

    # Boli Points
    if _feat("feature_boli_points"):
        embed.add_field(
            name="Boli Points",
            value=(
                "Earn points by using Trivandrum slang naturally in chat:\n"
                "*kidilam, shokam, pillacha, chumma, mone, kili poyi, vishayam, thirontharam, boli, paal payasam...*\n"
                "+5 pts per unique trigger word per message · +2 pts per `/navi` call\n"
                "Level up from Tourist → Thampanoor Regular → Chalai Veteran → Cosmic Sage of Thirontharam"
            ),
            inline=False,
        )

    # Passive reactions
    passive_lines: list[str] = []
    if _feat("feature_kochi_replies"):
        passive_lines.append("**Kochi slang** (*machane, machi, adipoli...*) → Condescending reply from a true Trivandrumite")
    if _feat("feature_curse_replies"):
        passive_lines.append("**Curse words** → 25% chance the cosmos punishes you with a dramatic doom prediction")
    if _feat("feature_welcome"):
        passive_lines.append("**New member joins** → Welcome message (1-min delay)")
    if _feat("feature_vibe_check"):
        passive_lines.append("**Chat heats up** → Navi intervenes with a calming message")
    if _feat("feature_link_summary"):
        passive_lines.append("**React 📰 on a link** → Navi scrapes and summarises the article in 3 bullet points")
    if _feat("feature_strikes"):
        passive_lines.append("**Severe language** → 3-strike system with automatic jail role at strike 2")
    if passive_lines:
        embed.add_field(name="Passive Reactions (automatic)", value="\n".join(passive_lines), inline=False)

    embed.set_footer(text="All responses powered by Gemini · Local knowledge from 85 curated Trivandrum entries")
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
        app_commands.Choice(name="Vibe Check (auto de-escalation)", value="feature_vibe_check"),
        app_commands.Choice(name="Kanmanilla (missing person)", value="feature_kanmanilla"),
        app_commands.Choice(name="Mod Audit (/audit)", value="feature_audit"),
        app_commands.Choice(name="Mod TL;DR (/mod_tldr)", value="feature_mod_tldr"),
        app_commands.Choice(name="Link Summary (emoji reaction)", value="feature_link_summary"),
        app_commands.Choice(name="3-Strike System", value="feature_strikes"),
        app_commands.Choice(name="Kochi Slang Detection", value="feature_kochi_replies"),
        app_commands.Choice(name="Passive Curse Replies", value="feature_curse_replies"),
        app_commands.Choice(name="Boli Points Tracking", value="feature_boli_points"),
        app_commands.Choice(name="Welcome Messages", value="feature_welcome"),
        app_commands.Choice(name="Temp VC Generator (/temp_vc)", value="feature_temp_vc"),
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

    @app_commands.command(name="set_chance", description="Set the probability (0.0–1.0) for a specific reaction")
    @app_commands.describe(feature="Which probability to adjust", value="New value between 0.0 and 1.0")
    @app_commands.choices(feature=[
        app_commands.Choice(name="Cache Reuse (navi)", value="cache_reuse_chance"),
        app_commands.Choice(name="Kochi Slang Reply", value="kochi_reply_chance"),
        app_commands.Choice(name="Passive Curse Reply", value="curse_reply_chance"),
        app_commands.Choice(name="Curse Reversal (owner target)", value="reversal_chance_owner"),
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

        # Probabilities
        prob_keys = {"cache_reuse_chance", "kochi_reply_chance", "curse_reply_chance", "reversal_chance_owner"}
        prob_lines = []
        for k in sorted(prob_keys):
            v = configs.get(k)
            if v is not None:
                prob_lines.append(f"`{k}` → **{float(v):.0%}**")
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
        for item_id, item in _SHOP_ITEMS.items():
            can_afford = "✅" if pts >= item["cost"] else "❌"
            active_note = ""
            if item_id == "curse_protection":
                expiry = get_perk_expiry(db_conn, interaction.user.id, "curse_protection")
                if expiry:
                    active_note = f"\n*(Active until <t:{int(expiry.timestamp())}:t>)*"
            embed.add_field(
                name=f"{item['emoji']} {item['name']} — 🍮 {item['cost']} pts {can_afford}",
                value=f"{item['description']}{active_note}",
                inline=False,
            )

        embed.set_footer(text="Earn Boli Points by using /navi and Trivandrum slang.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="buy", description="Purchase an item from the Boli Marketplace")
    @app_commands.describe(item="Which item to buy", rashi_choice="Your chosen Rashi (only for custom_rashi)")
    @app_commands.choices(item=[
        app_commands.Choice(name="🛡️ Curse Protection (20 pts)", value="curse_protection"),
        app_commands.Choice(name="🌟 Customize Rashi (40 pts)", value="custom_rashi"),
        app_commands.Choice(name="🔋 10x Cosmic Actions Refill (30 pts)", value="action_refill"),
    ])
    async def buy(
        self,
        interaction: discord.Interaction,
        item: str,
        rashi_choice: str | None = None,
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
            ts = f"<t:{int(expiry.timestamp())}:f>" if expiry else "24 hours"
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

        # --- Action Refill ---
        elif item == "action_refill":
            update_boli_points(db_conn, interaction.user.id, -cost)
            add_extra_actions(db_conn, interaction.user.id, 10)
            await interaction.response.send_message(
                f"🔋 Refill successful! {interaction.user.mention}, you have 10 extra cosmic actions added to your quota. Go cause some chaos.\n"
                f"🍮 -{cost} Boli Points (remaining: **{pts - cost}**)",
                ephemeral=True,
            )
            logger.info("%s purchased Action Refill (+10 extra actions)", interaction.user.display_name)


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