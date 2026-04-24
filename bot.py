# bot.py - Main Discord bot logic for AstRobot

import os
import random
import asyncio
import json
from datetime import datetime
from dotenv import load_dotenv
import discord
from discord import app_commands
from google import genai

from prompts import ASTRO_SYSTEM_PROMPT, get_astro_prompt, get_curse_prompt, FALLBACK_MESSAGE
from curses import CURSE_WORDS, get_random_doomed_prediction, get_random_curse_back, get_random_curse

# Load environment variables
load_dotenv()

# Constants
CURSE_REPLY_CHANCE = 0.50  # 50% chance to reply to curse words
BOT_PREFIX = "."
CACHE_FILE = "response_cache.json"

# Initialize Gemini client
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Set up Discord intents
intents = discord.Intents.all()
intents.message_content = True

# Create bot instance
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


def call_gemini(prompt: str) -> str:
    """Synchronous call to Gemini API. Use with asyncio.to_thread()."""
    print(f"[{datetime.now().isoformat()}] Calling Gemini API with prompt: {prompt[:100]}...")
    
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={
                "system_instruction": ASTRO_SYSTEM_PROMPT,
                "temperature": 0.8,
            }
        )
        result = response.text
        print(f"[{datetime.now().isoformat()}] Gemini API response received")
        return result
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Gemini API error: {e}")
        print(f"[{datetime.now().isoformat()}] Error type: {type(e).__name__}")
        return FALLBACK_MESSAGE


def save_to_cache(response: str, name: str, curse_used: str | None = None):
    """Saves generated LLM responses to a local JSON cache with placeholders."""
    try:
        if response == FALLBACK_MESSAGE or not response:
            return
            
        template_result = response.replace(name, "{user}")
        if curse_used:
            template_result = template_result.replace(curse_used, "{curse}")
            
        cache_data = []
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                try:
                    cache_data = json.load(f)
                except json.JSONDecodeError:
                    cache_data = []
                    
        if template_result not in cache_data:
            cache_data.append(template_result)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Caching error: {e}")


async def get_astro_prediction(name: str) -> str:
    """Get astrology prediction from Gemini and cache it."""
    user_prompt = get_astro_prompt(name)
    
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, call_gemini, user_prompt)
    
    # Save the successful prediction to cache
    save_to_cache(result, name)
    
    return result


def contains_curse_word(message_content: str) -> bool:
    """Check if message contains any curse word (case-insensitive)."""
    content_lower = message_content.lower()
    for curse in CURSE_WORDS:
        if curse in content_lower:
            return True
    return False


@bot.event
async def on_ready():
    """Event triggered when the bot is ready. Used to sync app commands."""
    print(f"[{datetime.now().isoformat()}] Logged in as {bot.user}")
    await tree.sync()
    print("Slash commands synced!")


@tree.command(name="astro", description="Get a dramatic Manglish astrology prediction")
async def astro_slash(interaction: discord.Interaction, user: discord.Member | None = None):
    """Slash command to get a dramatic astrology reading."""
    target = user or interaction.user
    display_name = target.display_name
    mention_str = target.mention
    
    await interaction.response.defer(thinking=True)
    await asyncio.sleep(random.uniform(1.0, 2.0))
    
    prediction = await get_astro_prediction(display_name)
    final_reply = prediction.replace(display_name, mention_str) if mention_str.startswith("<@") else prediction
    
    await interaction.followup.send(final_reply)


@bot.event
async def on_message(message: discord.Message):
    """Handle prefix commands and passive replies."""
    if message.author.bot:
        return
    
    if message.author.id == bot.user.id:
        return
    
    # Check for astro command
    if message.content.lower().startswith("astro"):
        if message.mentions:
            target = message.mentions[0]
            display_name = target.display_name
            mention_str = target.mention
        else:
            parts = message.content.split(None, 1)
            if len(parts) > 1:
                display_name = parts[1]
                mention_str = parts[1]
            else:
                display_name = message.author.display_name
                mention_str = message.author.mention
        
        curse = get_random_curse()
        final_reply = f"{curse} {mention_str}"
        await message.reply(final_reply)
    
    # Check for curse words (passive reply)
    elif contains_curse_word(message.content):
        # 20% chance to reply
        if random.random() < CURSE_REPLY_CHANCE:
            async with message.channel.typing():
                await asyncio.sleep(random.uniform(1.0, 2.0))
                
                username = message.author.display_name
                content_lower = message.content.lower()
                curse_used = next((c for c in CURSE_WORDS if c in content_lower), "oola")
                
                # Try getting dynamic curse response from Gemini
                user_prompt = get_curse_prompt(username, curse_used)
                loop = asyncio.get_event_loop()
                reply = await loop.run_in_executor(None, call_gemini, user_prompt)
                
                if reply == FALLBACK_MESSAGE:
                    if random.random() < 0.5:
                        reply = get_random_doomed_prediction(username)
                    else:
                        reply = get_random_curse_back(username)
                else:
                    save_to_cache(reply, username, curse_used)
                
                await message.reply(reply)


# Run the bot
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN not found in .env file")
        exit(1)
    
    if not os.getenv("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY not found in .env file")
        exit(1)
    
    print(f"[{datetime.now().isoformat()}] Starting AstRobot...")
    bot.run(token)