# bot.py - AstRobot V2 — Main Discord bot entrypoint

from __future__ import annotations

import logging
import os
import random
import asyncio
from datetime import datetime, timezone, time as dt_time

from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import tasks

from schema import (
    init_db,
    get_table_counts,
    get_user_profile,
    upsert_user,
    get_last_n_predictions,
    save_user_prediction,
    save_prediction,
    update_boli_points,
    increment_prediction_count,
    log_curse,
    get_leaderboard,
    get_todays_omen,
    save_daily_omen,
    export_stats_csv,
    get_config_float,
    get_config_int,
    set_config_float,
    set_config_int,
    get_all_configs,
    get_todays_user_prediction,
)
from glossary import LANDMARKS, RASHIS, get_daily_weather_forecast
from prompts import (
    get_time_aware_system_prompt,
    get_astro_prompt,
    get_curse_prompt,
    get_qa_prompt,
    get_daily_omen_prompt,
    FALLBACK_MESSAGE,
    WELCOME_MESSAGES,
)
from curses import (
    CURSE_WORDS,
    get_random_curse,
    get_random_doomed_prediction,
    get_random_curse_back,
    get_random_kochi_response,
    contains_boli_trigger,
    contains_kochi_slang,
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
HORRIBLESCOPE_CHANNEL = os.getenv("HORRIBLESCOPE_CHANNEL", "off-topic")
# ---------------------------------------------------------------------------
# In-Memory Spam Control
# ---------------------------------------------------------------------------
_user_cooldowns: dict[int, datetime] = {}

def check_spam_cooldown(user_id: int) -> int:
    """Returns seconds remaining if on cooldown, else 0."""
    now = datetime.now(timezone.utc)
    if user_id in _user_cooldowns:
        cooldown_seconds = get_config_int(db_conn, "astro_cooldown_seconds", 60)
        time_since = (now - _user_cooldowns[user_id]).total_seconds()
        if time_since < cooldown_seconds:
            return int(cooldown_seconds - time_since)
    _user_cooldowns[user_id] = now
    return 0

# ---------------------------------------------------------------------------
# Bot startup time
# ---------------------------------------------------------------------------

_BOT_START_TIME: datetime = datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# Discord client setup
# ---------------------------------------------------------------------------

intents = discord.Intents.all()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

db_conn = None          # duckdb.DuckDBPyConnection
gemini_svc = None       # GeminiService
api_mgr = None          # ApiManager
_BOT_START_TIME = datetime.now(timezone.utc)

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


def contains_curse_word(content: str) -> bool:
    content_lower = content.lower()
    return any(c in content_lower for c in CURSE_WORDS)


# ---------------------------------------------------------------------------
# Core prediction logic
# ---------------------------------------------------------------------------

async def get_astro_prediction(user_id: int, name: str) -> str:
    """Get an astrology prediction, using memory + cache + Gemini as needed."""
    profile = get_user_profile(db_conn, user_id)

    # Assign Rashi on first use
    rashi: str | None = None
    if profile is None:
        rashi = random.choice(RASHIS)
        upsert_user(db_conn, user_id, name, rashi=rashi)
        logger.info("New user %s assigned Rashi: %s", name, rashi)
    else:
        rashi = profile.get("rashi")

    # 1. Check if the user already had a prediction TODAY
    todays_pred = get_todays_user_prediction(db_conn, user_id)
    if todays_pred:
        cache_chance = get_config_float(db_conn, "cache_reuse_chance", 0.50)
        if random.random() < cache_chance:
            logger.info("Recycling today's prediction from cache for %s", name)
            # Simulate Gemini thinking time
            await asyncio.sleep(random.uniform(1.0, 2.5))
            return todays_pred

    past_predictions = get_last_n_predictions(db_conn, user_id, n=3)
    system_prompt = get_time_aware_system_prompt()
    user_prompt = get_astro_prompt(name, rashi=rashi, past_predictions=past_predictions)

    result, from_cache = await asyncio.get_event_loop().run_in_executor(
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

    # Start scheduled tasks
    if not daily_omen_and_weather.is_running():
        daily_omen_and_weather.start()

    await tree.sync()
    logger.info("Slash commands synced. AstRobot V2 is live.")


@bot.event
async def on_member_join(member: discord.Member) -> None:
    channel = (
        discord.utils.get(member.guild.text_channels, name="general")
        or member.guild.system_channel
    )
    if channel:
        msg = random.choice(WELCOME_MESSAGES).format(user=member.mention)
        await channel.send(msg)
        logger.info("Welcomed new member %s in %s", member.display_name, channel.name)


@bot.event
async def on_message(message: discord.Message) -> None:
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

    # ---- Boli Points: local slang triggers ----
    triggered_words = contains_boli_trigger(message.content)
    if triggered_words:
        points = len(triggered_words) * 5
        update_boli_points(db_conn, message.author.id, points)
        logger.debug(
            "%s triggered Boli words %s → +%d pts",
            message.author.display_name, triggered_words, points
        )

    # ---- Kochi slang detection ----
    kochi_chance = get_config_float(db_conn, "kochi_reply_chance", 0.28)
    if contains_kochi_slang(message.content) and random.random() < kochi_chance:
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
        if content_without_ping and not contains_curse_word(message.content):
            system_prompt = get_time_aware_system_prompt()
            user_prompt = get_qa_prompt(message.author.display_name, content_without_ping)

            if FREE_TIER_MODE:
                async with message.channel.typing():
                    await asyncio.sleep(random.uniform(1.0, 2.0))
                    reply, _ = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: api_mgr.call(
                            prompt=user_prompt,
                            system_prompt=system_prompt,
                            cache_type="qa",
                            name=message.author.display_name,
                            fallback_message=FALLBACK_MESSAGE,
                        ),
                    )
            else:
                await asyncio.sleep(random.uniform(1.0, 2.0))
                reply, _ = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: api_mgr.call(
                        prompt=user_prompt,
                        system_prompt=system_prompt,
                        cache_type="qa",
                        name=message.author.display_name,
                        fallback_message=FALLBACK_MESSAGE,
                    ),
                )
            await message.reply(reply)
            return

    # ---- Legacy prefix "astro" command ----
    if message.content.lower().startswith("astro"):

        # Spam Check
        cooldown = check_spam_cooldown(message.author.id)
        if cooldown > 0:
            cached_pred = get_todays_user_prediction(db_conn, message.author.id)
            if cached_pred:
                await message.reply(f"Eda mone chill... I already told you: {cached_pred}")
            return

        # "astro @username" → curse/roast the mentioned user (instant, no API)
        if message.mentions:
            target = message.mentions[0]
            # Don't curse the bot itself
            if target.id == bot.user.id:
                await message.reply("Eda, you think I can curse myself? Oola idea.")
                return

            curse_word = get_random_curse()

            app_info = await bot.application_info()
            owner_reversal_chance = get_config_float(db_conn, "reversal_chance_owner", 0.45)
            reversal_chance = owner_reversal_chance if target.id == app_info.owner.id else 0.10

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

        if FREE_TIER_MODE:
            async with message.channel.typing():
                prediction = await get_astro_prediction(target.id, display_name)
        else:
            prediction = await get_astro_prediction(target.id, display_name)

        final_reply = prediction.replace(display_name, mention_str)
        await message.reply(final_reply)
        return

    # ---- Passive curse word reply ----
    curse_chance = get_config_float(db_conn, "curse_reply_chance", 0.25)
    if contains_curse_word(message.content) and random.random() < curse_chance:
        username = message.author.display_name
        user_id = message.author.id
        curse_used = next((c for c in CURSE_WORDS if c in content_lower), "oola")

        log_curse(db_conn, user_id, username, curse_used)
        update_boli_points(db_conn, user_id, 1)  # +1 Boli pt for curse event

        system_prompt = get_time_aware_system_prompt()
        user_prompt = get_curse_prompt(username, curse_used)

        async with message.channel.typing():
            await asyncio.sleep(random.uniform(1.0, 2.0))
            reply, from_cache = await asyncio.get_event_loop().run_in_executor(
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
# Slash Commands
# ---------------------------------------------------------------------------

@tree.command(name="astro", description="Get a dramatic Manglish astrology prediction")
async def astro_slash(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
) -> None:
    cooldown = check_spam_cooldown(interaction.user.id)
    if cooldown > 0:
        cached_pred = get_todays_user_prediction(db_conn, interaction.user.id)
        if cached_pred:
            # We don't make it ephemeral, we just reply normally from cache with the funny prefix!
            await interaction.response.send_message(f"Eda mone chill... I already told you: {cached_pred}")
        else:
            await interaction.response.send_message(
                f"Eda mone, chill! Wait {cooldown} seconds before asking again.", ephemeral=True
            )
        return

    target = user or interaction.user
    display_name = target.display_name
    mention_str = target.mention

    await interaction.response.defer(thinking=True)
    await asyncio.sleep(random.uniform(1.0, 2.0))

    prediction = await get_astro_prediction(target.id, display_name)
    final_reply = prediction.replace(display_name, mention_str)
    await interaction.followup.send(final_reply)

    # +2 Boli Points for using /astro
    update_boli_points(db_conn, interaction.user.id, 2)
    logger.info("%s used /astro → +2 Boli Points", interaction.user.display_name)


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
        embed.add_field(
            name=f"{medal} {entry['username']}{rashi_str}",
            value=f"🍮 **{entry['boli_points']} Boli Points** · {entry['prediction_count']} readings",
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

    await interaction.response.send_message(
        f"**Your AstRobot Profile**\n"
        f"🌟 Rashi: **{rashi}**\n"
        f"🍮 Boli Points: **{pts}**\n"
        f"🔮 Predictions received: **{count}**\n\n"
        f"*Keep using Thirontharam slang to earn more points. Kidilam!*",
        ephemeral=True,
    )


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

    embed = discord.Embed(
        title="🔧 AstRobot V2 — Health Status",
        color=discord.Color.red() if gemini_status["circuit_open"] else discord.Color.green(),
    )
    embed.add_field(name="⏱️ Uptime", value=uptime_str, inline=False)
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
    embed.add_field(
        name="⚡ Circuit Breaker",
        value=(
            f"State: **{'OPEN 🔴' if gemini_status['circuit_open'] else 'CLOSED 🟢'}**\n"
            f"Failures: {gemini_status['failure_count']}/3\n"
            f"Opens until: {gemini_status['open_until']}"
        ),
        inline=True,
    )
    embed.add_field(
        name="🚦 Rate Limiter",
        value=(
            f"Used: **{rate_status['rpm_used']}/{rate_status['rpm_limit']} RPM**\n"
            f"Window resets in: {rate_status['window_resets_in_seconds']}s\n"
            f"Free Tier Mode: {'ON' if rate_status['free_tier_mode'] else 'OFF'}\n"
            f"Free-Only Mode (Killswitch): **{'ON 🔴' if gemini_svc.free_only else 'OFF 🟢'}**"
        ),
        inline=False,
    )
    counts_str = "\n".join(f"  `{t}`: {n}" for t, n in table_counts.items())
    embed.add_field(name="🗄️ DuckDB Row Counts", value=counts_str, inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="help", description="Learn how to interact with AstRobot")
async def help_slash(interaction: discord.Interaction) -> None:
    """Show the help menu for AstRobot."""
    embed = discord.Embed(
        title="🤖 AstRobot V2 — Help & Features",
        description="I am your friendly neighbourhood Trivandrum Astrologer. Here is what I can do:",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="🔮 Predictions (`astro`)",
        value="Type `astro` in chat to get a personalised, Trivandrum-style daily astrological prediction.",
        inline=False,
    )
    embed.add_field(
        name="🤬 Proxy Cursing (`astro @user`)",
        value="Mention someone with the astro command to instantly send them a local curse or roast.",
        inline=False,
    )
    embed.add_field(
        name="💬 Q&A (`@AstRobot question?`)",
        value="Tag me with a question and I'll give you a highly sarcastic, culturally accurate answer.",
        inline=False,
    )
    embed.add_field(
        name="🏆 Boli Points & Profile (`/profile`)",
        value="Use Trivandrum slang (like *kidilam*, *shokam*) to earn Boli Points! Check your stats using the `/profile` slash command.",
        inline=False,
    )
    embed.add_field(
        name="👀 Passive Aggression",
        value="Be careful what you say... If you use Kochi slang (*machane*), I will judge you. If you curse, I might curse you back.",
        inline=False,
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# Admin Slash Commands
# ---------------------------------------------------------------------------

@app_commands.default_permissions(administrator=True)
class AdminGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="admin", description="AstRobot owner configuration controls")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        app_info = await bot.application_info()
        if interaction.user.id != app_info.owner.id:
            await interaction.response.send_message("Eda mone, only my owner can use this.", ephemeral=True)
            return False
        return True

    @app_commands.command(name="config_view", description="View all active probabilities and cooldowns")
    async def config_view(self, interaction: discord.Interaction) -> None:
        configs = get_all_configs(db_conn)
        embed = discord.Embed(title="⚙️ AstRobot Configuration", color=discord.Color.dark_grey())
        embed.add_field(name="Free-Only Mode (Killswitch)", value=f"**{'ON 🔴' if gemini_svc.free_only else 'OFF 🟢'}**", inline=False)
        for k, v in configs.items():
            val_str = f"{v:.0%}" if isinstance(v, float) else str(v)
            embed.add_field(name=k, value=f"`{val_str}`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="set_cooldown", description="Set anti-spam cooldown in seconds")
    async def set_cooldown(self, interaction: discord.Interaction, seconds: int) -> None:
        set_config_int(db_conn, "astro_cooldown_seconds", seconds)
        await interaction.response.send_message(f"Spam cooldown set to **{seconds} seconds**.", ephemeral=True)

    @app_commands.command(name="set_chances", description="Set reaction probabilities (0.0 to 1.0)")
    async def set_chances(
        self, interaction: discord.Interaction, 
        cache_reuse: float = None, kochi: float = None, curse: float = None, owner_reversal: float = None
    ) -> None:
        updates = []
        if cache_reuse is not None:
            set_config_float(db_conn, "cache_reuse_chance", cache_reuse)
            updates.append(f"cache_reuse_chance={cache_reuse:.0%}")
        if kochi is not None:
            set_config_float(db_conn, "kochi_reply_chance", kochi)
            updates.append(f"kochi_reply_chance={kochi:.0%}")
        if curse is not None:
            set_config_float(db_conn, "curse_reply_chance", curse)
            updates.append(f"curse_reply_chance={curse:.0%}")
        if owner_reversal is not None:
            set_config_float(db_conn, "reversal_chance_owner", owner_reversal)
            updates.append(f"reversal_chance_owner={owner_reversal:.0%}")
        
        msg = f"Updated configs: {', '.join(updates)}" if updates else "No changes made."
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="toggle_killswitch", description="Toggle Free-Only API mode (circuit breaker bypass)")
    async def toggle_killswitch(self, interaction: discord.Interaction) -> None:
        gemini_svc.free_only = not gemini_svc.free_only
        status = "ON 🔴 (Free API + Cache only)" if gemini_svc.free_only else "OFF 🟢 (Paid Fallback enabled)"
        await interaction.response.send_message(f"Killswitch is now **{status}**.", ephemeral=True)

tree.add_command(AdminGroup())


# ---------------------------------------------------------------------------
# Scheduled Task: Daily Omen + Weather Briefing (7:00 AM IST = 01:30 UTC)
# ---------------------------------------------------------------------------

@tasks.loop(time=dt_time(hour=1, minute=30, tzinfo=timezone.utc))
async def daily_omen_and_weather() -> None:
    """Post the daily Trivandrum omen + weather briefing to #off-topic at 7 AM IST."""

    # Idempotent check
    if get_todays_omen(db_conn):
        logger.info("Daily omen already posted today. Skipping.")
        return

    logger.info("Generating daily omen + weather briefing...")
    forecast = get_daily_weather_forecast()
    landmark = random.choice(LANDMARKS)
    system_prompt = get_time_aware_system_prompt()
    user_prompt = get_daily_omen_prompt(
        condition=forecast["condition"],
        max_temp=forecast["max_temp"],
        min_temp=forecast["min_temp"],
        rain_mm=forecast["rain_mm"],
        landmark=landmark,
    )

    result, from_cache = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: api_mgr.call(
            prompt=user_prompt,
            system_prompt=system_prompt,
            cache_type="daily_omen",
            name="Thirontharam",
            fallback_message=FALLBACK_MESSAGE,
        ),
    )

    save_daily_omen(db_conn, result, landmark)

    # Find the configured channel across all guilds
    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name=HORRIBLESCOPE_CHANNEL)
        if channel:
            await channel.send(result)
            logger.info("Daily omen posted to #%s in %s", HORRIBLESCOPE_CHANNEL, guild.name)
        else:
            logger.warning(
                "Channel #%s not found in guild %s. Skipping.", HORRIBLESCOPE_CHANNEL, guild.name
            )


@daily_omen_and_weather.before_loop
async def before_daily_omen() -> None:
    await bot.wait_until_ready()


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