# AstRobot Usage & Transition Guide

## 🔮 Bot Features

AstRobot is a dramatically self-important astrologer from Trivandrum, built to entertain your Discord server in Trivandrum Manglish.

### 1. The Astronomy Command
AstRobot offers two different behaviors depending on how you invoke the command:

* **Slash Command (`/astro [user]`):** Gives an absurd, hyper-specific astrology reading featuring local Trivandrum landmarks. It 'thinks' for 1-2 seconds with a typing indicator, calls the Gemini LLM securely using its Trivandrum persona, and outputs 1-2 sentences of a roasting prediction.
* **Prefix Command (`.astro [user]`):** A fast proxy that immediately targets the user with a random Trivandrum Manglish curse from the local list (e.g., `kumpidi @user`).

### 2. Spontaneous Curse Reactions (20% chance)
* **Trigger:** Any word matching the list in `curses.py` (e.g., *patti*, *myre*).
* **Behavior:** The bot listens passively to the chat. If someone curses, it rolls a dice. 20% of the time, the bot will interrupt with an angry, 1-sentence backchat predicting a doomed inconvenience for them (like getting stuck in traffic at Palayam).

---

## 🔄 Transitioning from LLM to Saved Responses

Right now, AstRobot generates responses lively via the **Google Gemini API**. To prevent duplicate outputs and build an offline corpus, every successfully generated response is generalized (user names become `{user}` and curse words become `{curse}`) and appended to a local `response_cache.json` file.

Once your `response_cache.json` has accumulated a good catalog of hilarious outputs, you may want to turn off the Gemini API (to save money or improve speed). Here's how:

### Step 1: Update `get_astro_prediction()`
Instead of calling the LLM, update the astrologer logic to pull from the cache:

```python
import json
import random

async def get_astro_prediction(name: str) -> str:
    """Pull prediction from local cache instead of LLM"""
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache_data = json.load(f)
            
        if not cache_data:
            return f"Eda {name}, the stars are silent today. Try again later."
            
        # Pick a random cached response and inject the display name
        prediction = random.choice(cache_data)
        return prediction.replace("{user}", name)
        
    except Exception as e:
        return FALLBACK_MESSAGE
```

### Step 2: Update the Curse Handler in `on_message`
In the `contains_curse_word` block inside `bot.py`, remove the `call_gemini` thread logic and replace it with:

```python
# Assuming you separate purely curse responses into another cache or use the same local cache
try:
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        cache_data = json.load(f)
        
    if cache_data:
        reply_template = random.choice(cache_data)
        reply = reply_template.replace("{user}", username).replace("{curse}", curse_used)
    else:
        reply = get_random_doomed_prediction(username)
except Exception:
    reply = get_random_doomed_prediction(username)

await message.reply(reply)
```

### Step 3: Remove LLM Dependencies
Once you have fully switched the two entry points over to using `CACHE_FILE`, you can safely delete the `call_gemini()` and `save_to_cache()` functions, and remove the `google.genai` import and client initialization at the top of the file.
