# bot.py - AstRobot V2 — Main Discord bot entrypoint

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
    DB_PATH,
)
from glossary import RASHIS
from prompts import (
    get_time_aware_system_prompt,
    get_astro_prompt,
    get_curse_prompt,
    get_qa_prompt,
    get_summ_prompt,
    SUMM_SYSTEM_PROMPT,
    get_vibe_check_prompt,
    get_kanmanilla_prompt,
    get_link_summary_prompt,
    get_audit_prompt,
    get_mod_tldr_prompt,
    FALLBACK_MESSAGE,
    WELCOME_MESSAGES,
)
from curses import (
    CURSE_WORDS,
    SEVERE_CURSE_WORDS,
    get_random_curse,
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
logger = logging.getLogger("astrobot")

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
JAIL_ROLE_NAME = os.getenv("JAIL_ROLE_NAME", "Thampanoor Jail")

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
    """Return how many times user has used /astro in the last 60 seconds.

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
    system_prompt = get_time_aware_system_prompt(db_conn)
    user_prompt = get_vibe_check_prompt(channel_name)

    reply, _ = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: api_mgr.call(
            prompt=user_prompt,
            system_prompt=system_prompt,
            cache_type="qa",
            name="VibeCheck",
            fallback_message="Aiyo, everyone chill please. This is not KSRTC Thampanoor — no need to fight like this mone.",
        ),
    )
    await message.channel.send(reply)
    logger.info("Vibe check fired in #%s", channel_name)


# ---------------------------------------------------------------------------
# Strike system helpers (Feature 7)
# ---------------------------------------------------------------------------

_JAIL_TIMEOUT_MINUTES = 5


async def _handle_strike(member: discord.Member, channel: discord.abc.Messageable) -> None:
    """Increment strike counter and apply the appropriate penalty."""
    new_strikes = increment_user_strikes(db_conn, member.id)

    if new_strikes == 1:
        await channel.send(
            f"Eda {member.mention}, that's Strike 1. Watch your language mone. "
            f"Two more and AstRobot will personally arrange your cosmic punishment at Thampanoor."
        )
        logger.info("Strike 1 issued to %s", member.display_name)

    elif new_strikes == 2:
        await channel.send(
            f"Aiyo {member.mention}, Strike 2! You have been sent to **Thampanoor Jail** for {_JAIL_TIMEOUT_MINUTES} minutes. "
            f"Sit quietly and reflect on your vocabulary choices."
        )
        # Assign jail role if it exists on the guild
        if isinstance(channel, discord.TextChannel):
            jail_role = discord.utils.get(channel.guild.roles, name=JAIL_ROLE_NAME)
            if jail_role:
                try:
                    await member.add_roles(jail_role, reason="AstRobot Strike 2 — Thampanoor Jail")
                    asyncio.create_task(_release_from_jail(member, jail_role, _JAIL_TIMEOUT_MINUTES * 60))
                except discord.Forbidden:
                    logger.warning("Missing permissions to assign jail role to %s", member.display_name)
        logger.info("Strike 2 — %s jailed for %d min", member.display_name, _JAIL_TIMEOUT_MINUTES)

    elif new_strikes >= 3:
        reset_user_strikes(db_conn, member.id)
        # Alert mod channel
        mod_ch = None
        if isinstance(channel, discord.TextChannel):
            mod_ch = discord.utils.get(channel.guild.text_channels, name=MOD_CHANNEL_NAME)
        if mod_ch:
            await mod_ch.send(
                f"🚨 **Mod Alert:** {member.mention} just hit **3 strikes**. "
                f"Time to consider the ban hammer. Their strikes have been reset to 0."
            )
        await channel.send(
            f"🚨 {member.mention}, that's **3 strikes**. Mod team has been notified. Shokam situation mone."
        )
        logger.info("Strike 3 alert sent for %s — strikes reset", member.display_name)


async def _release_from_jail(member: discord.Member, role: discord.Role, delay_seconds: int) -> None:
    """Remove jail role after delay_seconds."""
    await asyncio.sleep(delay_seconds)
    try:
        await member.remove_roles(role, reason="AstRobot jail sentence served")
        logger.info("Released %s from Thampanoor Jail", member.display_name)
    except Exception as exc:
        logger.warning("Could not remove jail role from %s: %s", member.display_name, exc)


def _contains_severe_curse(text: str) -> bool:
    """Return True if text contains any SEVERE_CURSE_WORDS."""
    lower = text.lower()
    return any(re.search(rf"\b{re.escape(w)}\b", lower) for w in SEVERE_CURSE_WORDS)


# ---------------------------------------------------------------------------
# Discord client setup
# ---------------------------------------------------------------------------

intents = discord.Intents.all()
intents.message_content = True
bot = discord.Client(intents=intents)


class _AstRobotTree(app_commands.CommandTree):
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
            "AstRobot is currently in sleep mode. Only the owner can wake it up. Chumma wait mone.",
            ephemeral=True,
        )
        return False


tree = _AstRobotTree(bot)

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
    "feature_astro":        1,
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



_SPAM_WRAPPERS = [
    "Mone, I told you already — {prediction}",
    "Still asking? Shokam. {prediction}",
    "Aiyo, same question again? The stars already decided — {prediction}",
    "Eda, the planets haven't changed since 5 minutes ago. {prediction}",
    "Chumma asking again is it? Fine. {prediction}",
    "I only have one doom per customer, mone. {prediction}",
    "You think fate changes every 5 minutes? {prediction}",
]

_LEVEL_TITLES: list[tuple[int, str]] = [
    (0,  "Tourist"),
    (6,  "Thampanoor Regular"),
    (11, "Chalai Veteran"),
    (21, "Kowdiar Insider"),
    (36, "Thirontharam Native"),
    (51, "Neyyattinkara Gopan"),
    (71, "Cosmic Sage of Thirontharam"),
    (91, "The Chosen One"),
]

_LEVEL_UP_MESSAGES: list[str] = [
    "Aiyo {user}! Level **{level}** achieved! The stars have taken note. Reluctantly.",
    "Eda {user}, Level **{level}** unlocked! Your Thirontharam energy is growing. Still not enough to beat KD Puram traffic, but still.",
    "{user} has reached Level **{level}**! Even the thattukada pillacha is impressed. Slightly.",
    "Oola! {user} is now Level **{level}**! The cosmos updated your file. Long overdue, honestly.",
    "Shokam to everyone else — {user} just hit Level **{level}**! The universe is watching. And judging the rest.",
    "{user} Level **{level}** achieved! AstRobot acknowledges your slang dedication. Chumma. Keep going.",
    "Aiyo {user}, Level **{level}**! Even the Ponmudi mist parted briefly to recognise this moment. Kidilam.",
    "Eda {user}, you are now Level **{level}**! Indian Coffee House Thampanoor will serve you slightly faster now.",
]


def get_level_title(level: int) -> str:
    title = _LEVEL_TITLES[0][1]
    for threshold, name in _LEVEL_TITLES:
        if level >= threshold:
            title = name
    return title


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
        flavour = random.choice(_LEVEL_UP_MESSAGES).format(user=mention, level=new_level)
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
        rf"^(Eda|Aiyo|Oola|Shokam)\s+{re.escape(name)}\s*[,.]?\s*",
        "",
        cached,
        flags=re.IGNORECASE,
    ).strip()
    # Capitalise first letter of remainder
    if stripped:
        stripped = stripped[0].upper() + stripped[1:]
    prediction_text = stripped or cached  # fall back to full text if strip failed
    wrapper = random.choice(_SPAM_WRAPPERS)
    return wrapper.format(prediction=prediction_text)


# ---------------------------------------------------------------------------
# Core prediction logic
# ---------------------------------------------------------------------------

async def get_astro_prediction(user_id: int, name: str, usage_count: int = 1) -> str:
    """Get an astrology prediction.

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
                cache_type="astro", name=name, fallback_message=FALLBACK_MESSAGE
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
    system_prompt = get_time_aware_system_prompt(db_conn)
    user_prompt = get_astro_prompt(name, rashi=rashi, past_predictions=past_predictions)

    result, from_cache = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: api_mgr.call(
            prompt=user_prompt,
            system_prompt=system_prompt,
            cache_type="astro",
            name=name,
            fallback_message=FALLBACK_MESSAGE,
        ),
    )

    if not from_cache and result != FALLBACK_MESSAGE:
        template = _templatize(result, name)
        save_prediction(db_conn, "astro", template, user_id=user_id, original_prompt=user_prompt)
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
    logger.info("Slash commands synced. AstRobot V2 is live.")


_MODA_INTROS: list[str] = [
    "If you have any questions, ask Moda. He is the moderator here. He will pretend to know the answer.",
    "Moda is our moderator. Treat him with respect. He earned it by doing absolutely nothing special.",
    "The server moderator Moda will guide you. Or he will just stare at the screen and nod. Same thing.",
    "Moda is the boss here — in the sense that he has a badge. Whether he uses it wisely is another vishayam entirely.",
    "Our moderator Moda is very capable. At least that is what he tells himself every morning.",
    "If lost, contact Moda. He is the moderator. He will send you in the wrong direction with full confidence.",
    "Moda runs this server. In the same way KSRTC runs on time — technically yes, practically no.",
    "The one called Moda will moderate you. What that means, even the stars are not sure. But he has the role.",
]


@bot.event
async def on_member_join(member: discord.Member) -> None:
    """Welcome new member with a single combined message and fresh astrology prediction."""
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

    async with channel.typing():
        prediction = await get_astro_prediction(member.id, member.display_name)

    welcome_line = random.choice(WELCOME_MESSAGES).format(user=member.mention)
    moda_line = f"\n{random.choice(_MODA_INTROS)}" if random.random() < 0.40 else ""
    await channel.send(f"{welcome_line}{moda_line}\n{prediction}")

    logger.info("Welcomed new member %s with prediction in #%s", member.display_name, channel.name)


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
    if content_lower in ("astro syros stop", "astro syros start"):
        app_info = await bot.application_info()
        if message.author.id == app_info.owner.id:
            gemini_svc.free_only = content_lower == "astro syros stop"
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
            system_prompt = get_time_aware_system_prompt(db_conn)
            user_prompt = get_qa_prompt(message.author.display_name, content_without_ping)

            async def _do_qa_call() -> str:
                await asyncio.sleep(random.uniform(1.0, 2.0))
                reply, _ = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: api_mgr.call(
                        prompt=user_prompt,
                        system_prompt=system_prompt,
                        cache_type="qa",
                        name=message.author.display_name,
                        fallback_message=FALLBACK_MESSAGE,
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

    # ---- Legacy prefix "astro" command ----
    if message.content.lower().startswith("astro"):

        # "astro @username" → curse/roast the mentioned user (instant, no API)
        if message.mentions:
            target = message.mentions[0]
            # Don't curse the bot itself
            if target.id == bot.user.id:
                await message.reply("Eda, you think I can curse myself? Oola idea.")
                return

            # Bot-loop protection: if someone is trying to curse another bot
            if target.bot:
                bot_curses = [
                    f"Aiyo {message.author.mention}, you are trying to make me fight other bots? What is this Thampanoor nonsense.",
                    f"Vayadi {message.author.mention}, trying to start a bot war in this server? The stars curse your WiFi for this.",
                    f"{message.author.mention} Eda, that is a bot. You think bots have feelings? Even I have more feelings than this plan.",
                    f"Shokam {message.author.mention}. Trying to proxy-curse a bot? Go outside. Touch some grass near Shanghumugham.",
                    f"Oola {message.author.mention}, nice try. The universe sees you. And it is judging you very hard right now.",
                ]
                await message.reply(random.choice(bot_curses))
                return

            curse_word = get_random_curse()

            app_info = await bot.application_info()
            owner_reversal_chance = get_config_float(db_conn, "reversal_chance_owner", 0.45)
            if has_active_perk(db_conn, target.id, "curse_protection"):
                reversal_chance = 1.0  # 100% reversal while protected
            elif target.id == app_info.owner.id:
                reversal_chance = owner_reversal_chance
            else:
                reversal_chance = 0.10

            # Chance to reverse the curse back onto the person who sent it
            if random.random() < reversal_chance:
                curse_reply = f"Eda {message.author.mention}, you tried to curse {target.display_name}, but the stars reversed it. {curse_word}!"
                await message.reply(curse_reply)
                log_curse(db_conn, message.author.id, message.author.display_name, "proxy_astro_reverse")
                logger.info("Proxy curse reversed! %s tried to curse %s but got cursed instead.", message.author.display_name, target.display_name)
                return

            curse_reply = f"{curse_word} {target.mention}"
            await message.reply(curse_reply)
            log_curse(db_conn, target.id, target.display_name, "proxy_astro")
            logger.info("%s cursed %s via prefix command", message.author.display_name, target.display_name)
            return

        # "astro" with no mention → full prediction for the sender
        target = message.author
        display_name = target.display_name
        mention_str = target.mention

        # Record usage and get count for tiered cache routing
        usage_count = get_user_minute_count(target.id, increment=True)

        if FREE_TIER_MODE:
            async with message.channel.typing():
                prediction = await get_astro_prediction(target.id, display_name, usage_count=usage_count)
        else:
            prediction = await get_astro_prediction(target.id, display_name, usage_count=usage_count)

        final_reply = prediction.replace(display_name, mention_str)
        await message.reply(final_reply)
        return

    # ---- Strike check: severe curse words ----
    if _feat("feature_strikes") and _contains_severe_curse(message.content):
        if isinstance(message.author, discord.Member):
            await _handle_strike(message.author, message.channel)

    # ---- Passive curse word reply ----
    curse_chance = get_config_float(db_conn, "curse_reply_chance", 0.25)
    _curse_matched, curse_used = contains_curse_word(message.content)
    if get_config_int(db_conn, "feature_curse_replies", 1) and _curse_matched and random.random() < curse_chance:
        username = message.author.display_name
        user_id = message.author.id
        curse_used = curse_used or "oola"

        log_curse(db_conn, user_id, username, curse_used)
        update_boli_points(db_conn, user_id, 1)  # +1 Boli pt for curse event

        system_prompt = get_time_aware_system_prompt(db_conn)
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
                    fallback_message=FALLBACK_MESSAGE,
                ),
            )

        if reply == FALLBACK_MESSAGE:
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

_URL_RE = re.compile(r"https?://[^\s]+")


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

    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except discord.NotFound:
        return

    urls = _URL_RE.findall(message.content)
    if not urls:
        return

    url = urls[0]

    try:
        import aiohttp
        from bs4 import BeautifulSoup

        async with aiohttp.ClientSession(
            headers={"User-Agent": "Mozilla/5.0 AstRobot/2.0 link-summariser"}
        ) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    await message.reply(f"Aiyo, couldn't fetch that link (HTTP {resp.status}). Shokam.")
                    return
                html = await resp.text(errors="replace")

        soup = BeautifulSoup(html, "html.parser")
        paragraphs = [p.get_text(separator=" ", strip=True) for p in soup.find_all("p")]
        page_text = " ".join(paragraphs)[:4000]  # cap to avoid token overrun

        if not page_text.strip():
            await message.reply("Eda, that page has no readable text. Maybe a paywall or JS-only site. Chumma.")
            return

        user_prompt = get_link_summary_prompt(page_text, url)
        system_prompt = get_time_aware_system_prompt(db_conn)

        summary, _ = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: api_mgr.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                cache_type="qa",
                name="LinkSummary",
                fallback_message="AstRobot-nte lamp went off. KSEB current problem. Try again mone.",
            ),
        )
        await message.reply(f"📰 **Link Summary:**\n{summary}")
        logger.info("Link summary sent for %s", url)

    except Exception as exc:
        logger.warning("Link summary failed for %s: %s", url, exc)
        await message.reply("Aiyo, something went wrong while reading that link. KSEB-style failure.")


# ---------------------------------------------------------------------------
# Slash Commands
# ---------------------------------------------------------------------------

@tree.command(name="astro", description="Get a dramatic Manglish astrology prediction")
async def astro_slash(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
) -> None:
    if not _feat("feature_astro"):
        await interaction.response.send_message(
            "Astro predictions are currently disabled. Chumma wait mone.", ephemeral=True
        )
        return

    # Bot-loop protection: curse whoever tries to feed a bot into us
    if user is not None and user.bot:
        bot_loop_curses = [
            f"Aiyo {interaction.user.mention}, you are trying to make me talk to another bot? What is this Thampanoor robot conference.",
            f"Eda {interaction.user.mention}, that is a bot. You think bots need cosmic readings? Go touch some grass near Padmanabhaswamy.",
            f"{interaction.user.mention} Shokam. Trying to start a bot loop in this server? The stars curse your internet speed for this nonsense.",
            f"Vayadi {interaction.user.mention}, a bot asking for a bot's horoscope? Even Rahu cannot predict this level of stupidity.",
            f"Oola {interaction.user.mention}, nice try. Bot into bot into bot — I know exactly what you are doing. The universe sees and judges very hard.",
        ]
        await interaction.response.send_message(random.choice(bot_loop_curses))
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

    prediction = await get_astro_prediction(target.id, display_name, usage_count=usage_count)
    final_reply = prediction.replace(display_name, mention_str)
    await interaction.followup.send(final_reply)

    # +2 Boli Points for using /astro (only on first fresh call)
    if usage_count == 1:
        profile = get_user_profile(db_conn, interaction.user.id)
        old_pts = profile["boli_points"] if profile else 0
        update_boli_points(db_conn, interaction.user.id, 2)
        logger.info("%s used /astro → +2 Boli Points", interaction.user.display_name)
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
            "Eda, nobody has Boli Points yet. Use **/astro** and start earning, mone."
        )
        return

    embed = discord.Embed(
        title="🍮 Top Appis — Boli Points Leaderboard",
        description="The most Thirontharam people in this server, ranked by AstRobot.",
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

    embed.set_footer(text="Earn points by using Trivandrum slang and /astro. Shokam to the rest.")
    await interaction.followup.send(embed=embed)


@tree.command(name="mypoints", description="Check your own Boli Points and Rashi")
async def mypoints_slash(interaction: discord.Interaction) -> None:
    profile = get_user_profile(db_conn, interaction.user.id)
    if not profile:
        await interaction.response.send_message(
            "Eda, you have no profile yet. Use **/astro** first, mone.", ephemeral=True
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
        progress_line = "🌟 Maximum level reached! Ninte cosmic destiny is sealed, mone."

    await interaction.response.send_message(
        f"**Your AstRobot Profile**\n"
        f"🌟 Rashi: **{rashi}**\n"
        f"⚔️ Level: **{level}** — *{title}*\n"
        f"{progress_line}\n"
        f"🍮 Boli Points: **{pts}**\n"
        f"🔮 Predictions received: **{count}**\n\n"
        f"*Use Thirontharam slang to earn points and level up. Kidilam!*",
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
            fallback_message=FALLBACK_MESSAGE,
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
            "Eda, bots don't go missing — they just get turned off. Chumma po.", ephemeral=True
        )
        return

    profile = get_user_profile(db_conn, user.id)
    if not profile:
        await interaction.response.send_message(
            f"Eda, {user.mention} hasn't even properly registered with AstRobot yet. Cannot make missing poster for a stranger.",
            ephemeral=True,
        )
        return

    last_seen = profile.get("last_seen")
    if last_seen is None:
        days_ago = 999
    else:
        # DuckDB may return timezone-naive timestamps
        if hasattr(last_seen, "tzinfo") and last_seen.tzinfo is not None:
            now = datetime.now(timezone.utc)
        else:
            now = datetime.now()
        days_ago = max(0, (now - last_seen).days)

    if days_ago < 3:
        await interaction.response.send_message(
            f"Eda, {user.mention} was just here. Chumma pinging people.",
        )
        return

    await interaction.response.defer(thinking=True)

    system_prompt = get_time_aware_system_prompt(db_conn)
    user_prompt = get_kanmanilla_prompt(user.display_name, days_ago)

    poster, _ = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: api_mgr.call(
            prompt=user_prompt,
            system_prompt=system_prompt,
            cache_type="qa",
            name="KanmanillaRequest",
            fallback_message=f"🚨 MISSING: {user.display_name}. Last seen {days_ago} days ago. {user.mention}, are you still alive or did you get stuck in KD Puram traffic? Reply here.",
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
            "Eda, this command only works inside a thread. Go into the thread first, mone.",
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


@tree.command(name="health", description="AstRobot system health (owner only)")
async def health_slash(interaction: discord.Interaction) -> None:
    app_info = await bot.application_info()
    if interaction.user.id != app_info.owner.id:
        await interaction.response.send_message(
            "Eda, this is for the owner only. Chumma po.", ephemeral=True
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
        title="🔧 AstRobot V2 — Health Status",
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


@tree.command(name="help", description="Learn how to interact with AstRobot")
async def help_slash(interaction: discord.Interaction) -> None:
    """Show the help menu — only lists features that are currently enabled."""
    embed = discord.Embed(
        title="AstRobot V2 — Help & Features",
        description="Ancient astrologer from Thirontharam. Speaks Manglish. Judges you constantly. Here is what I can do:",
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
    if _feat("feature_astro"):
        core_cmds.insert(0, "`/astro` — Get a dramatic Manglish astrology prediction (rate-limited per minute)")
        core_cmds.insert(1, "`/astro user:@someone` — Get a prediction for someone else")
    if _feat("feature_kanmanilla"):
        core_cmds.append("`/kanmanilla @user` — Ping a missing member with a dramatic notice")
    if _feat("feature_audit"):
        core_cmds.append("`/audit @user` — Mod: audit a user's messages against server rules")
    if _feat("feature_mod_tldr"):
        core_cmds.append("`/mod_tldr` — Mod: summarise the current thread")
    embed.add_field(name="Slash Commands", value="\n".join(core_cmds), inline=False)

    # Text triggers (only if astro enabled)
    if _feat("feature_astro"):
        embed.add_field(
            name="Text Commands (type in chat)",
            value=(
                "`astro` — Same as `/astro`, triggers a full prediction\n"
                "`astro @user` — Instantly curse or roast someone (10% chance it bounces back on you)"
            ),
            inline=False,
        )

    # Mention QA
    embed.add_field(
        name="Mention Q&A",
        value=(
            "`@AstRobot <question>` — Ask me anything. I will answer factually first, then be sarcastic about it.\n"
            "Works for news, scores, how-tos — or just to hear me judge your question."
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
                "+5 pts per unique trigger word per message · +2 pts per `/astro` call\n"
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
        passive_lines.append("**New member joins** → Welcome message + fresh astrology reading")
    if _feat("feature_vibe_check"):
        passive_lines.append("**Chat heats up** → AstRobot intervenes with a sarcastic calming message")
    if _feat("feature_link_summary"):
        passive_lines.append("**React 📰 on a link** → AstRobot scrapes and summarises the article in 3 bullet points")
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
        super().__init__(name="admin", description="AstRobot owner configuration controls")

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
            "Eda mone, only my owner can use this. Chumma po.", ephemeral=True
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
                "☠️ **Master Kill Switch: ON.** AstRobot is now completely silent. "
                "Only you can wake it up with `/admin killswitch` again.",
                ephemeral=True,
            )
            logger.warning("MASTER KILL SWITCH ACTIVATED by %s", interaction.user.display_name)
        else:
            await interaction.response.send_message(
                "✅ **Master Kill Switch: OFF.** AstRobot is back alive. Kidilam.",
                ephemeral=True,
            )
            logger.info("Master kill switch deactivated by %s", interaction.user.display_name)

    @app_commands.command(name="toggle_feature", description="Enable or disable a bot feature")
    @app_commands.describe(feature="Feature to toggle", enabled="Turn it on (True) or off (False)")
    @app_commands.choices(feature=[
        app_commands.Choice(name="Astro Predictions", value="feature_astro"),
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
        app_commands.Choice(name="Cache Reuse (astro)", value="cache_reuse_chance"),
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

    @app_commands.command(name="config_view", description="View all active feature flags, probabilities, and cooldowns")
    async def config_view(self, interaction: discord.Interaction) -> None:
        configs = get_all_configs(db_conn)
        embed = discord.Embed(title="⚙️ AstRobot Configuration", color=discord.Color.dark_grey())

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
        await interaction.response.send_message("Eda, you can't strike a bot. Chumma po.", ephemeral=True)
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
        "cost": 100,
        "description": "100% reversal of proxy curses for 24 hours. Anyone who tries `astro @you` gets it back.",
        "emoji": "🛡️",
        "duration_hours": 24,
    },
    "custom_rashi": {
        "name": "Customize Rashi",
        "cost": 50,
        "description": "Pick your own Rashi from the cosmic menu. Your destiny, your choice. For now.",
        "emoji": "🌟",
        "duration_hours": 0,  # permanent until next purchase
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

        embed.set_footer(text="Earn Boli Points by using Trivandrum slang and /astro. Shokam to those who can't afford it.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="buy", description="Purchase an item from the Boli Marketplace")
    @app_commands.describe(item="Which item to buy", rashi_choice="Your chosen Rashi (only for custom_rashi)")
    @app_commands.choices(item=[
        app_commands.Choice(name="🛡️ Curse Protection (100 pts)", value="curse_protection"),
        app_commands.Choice(name="🌟 Customize Rashi (50 pts)", value="custom_rashi"),
    ])
    async def buy(
        self,
        interaction: discord.Interaction,
        item: str,
        rashi_choice: str | None = None,
    ) -> None:
        if item not in _SHOP_ITEMS:
            await interaction.response.send_message("Eda, that item doesn't exist. Chumma po.", ephemeral=True)
            return

        shop_item = _SHOP_ITEMS[item]
        profile = get_user_profile(db_conn, interaction.user.id)
        if not profile:
            await interaction.response.send_message(
                "Eda, you have no profile yet. Use /astro first to get started.", ephemeral=True
            )
            return

        pts = profile["boli_points"]
        cost = shop_item["cost"]

        if pts < cost:
            await interaction.response.send_message(
                f"Aiyo {interaction.user.mention}, not enough Boli Points. "
                f"You have 🍮 **{pts}** but need **{cost}**. Earn more by using Trivandrum slang. Shokam.",
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
                f"🛡️ **Curse Protection activated!** {interaction.user.mention}, you are now protected until {ts}. "
                f"Anyone who tries `astro @{interaction.user.display_name}` will have the curse reversed onto them. "
                f"100%. No exceptions. Kidilam.\n🍮 -{cost} Boli Points (remaining: **{pts - cost}**)",
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
                    f"Aiyo, `{rashi_choice}` is not a valid Rashi. Pick from: {rashi_list}",
                    ephemeral=True,
                )
                return

            update_boli_points(db_conn, interaction.user.id, -cost)
            upsert_user(db_conn, interaction.user.id, interaction.user.display_name, rashi=matched)
            await interaction.response.send_message(
                f"🌟 **Rashi updated!** {interaction.user.mention}, your new cosmic sign is **{matched}**. "
                f"The stars have been bribed accordingly. Chumma accept.\n"
                f"🍮 -{cost} Boli Points (remaining: **{pts - cost}**)",
                ephemeral=True,
            )
            logger.info("%s purchased Custom Rashi: %s", interaction.user.display_name, matched)


tree.add_command(ShopGroup())


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
        "Starting AstRobot V2... Free key: %s | Paid key: %s | Free tier mode: %s",
        "✓" if FREE_API_KEY else "✗",
        "✓" if PAID_API_KEY else "✗",
        FREE_TIER_MODE,
    )
    bot.run(DISCORD_TOKEN)