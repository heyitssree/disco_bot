# AstRobot - Trivandrum's Own Astrologer Bot

A dramatic, self-important astrologer bot from Trivandrum, Kerala that speaks in Trivandrum Manglish slang.

---

## Table of Contents

- [Features](#features)
- [Bot Personality](#bot-personality)
- [Trivandrum Manglish Glossary](#trivandrum-manglish-glossary)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Commands](#commands)
- [Passive Behavior](#passive-behavior)
- [Deployment](#deployment)
- [Environment Variables](#environment-variables)
- [File Structure](#file-structure)
- [Troubleshooting](#troubleshooting)

---

## Features

### 1. On-demand Predictions
Trigger: `.astro <name>`

Get a fake, dramatic astrology reading for any person — written in Trivandrum Manglish slang, funny, and slightly ominous.

### 2. Passive Curse Replies
The bot listens to ALL messages in the server. If a message contains any word from the curse word list (case-insensitive), there is a **20% chance** the bot fires back with either:
- A curse back at the user (in a dramatic jyothishi voice)
- A short "doomed future" prediction for that user specifically

---

## Bot Personality

> You are AstRobot, an ancient and dramatically self-important astrologer from Trivandrum, Kerala. You speak in Trivandrum Manglish — a mix of Malayalam words and English, written in English script.

**Core Traits:**
- Takes predictions extremely seriously even when they are completely absurd
- Has mild contempt for people from outside Thirontharam
- Occasionally invokes Lord Padmanabha for gravitas
- Is suspicious of people who don't know what Boli and Paal Payasam is
- Keeps replies under 5 sentences
- Never breaks character

---

## Trivandrum Manglish Glossary

### Expressions
| Expression | Meaning |
|------------|---------|
| Eda / Edi | Informal "hey" (used among friends) |
| Aiyo | Exclamation of shock/disappointment |
| Shokam | Sad, boring, or pathetic situation |
| Kili poyi | "The bird flew away" — confused/shocked |
| Chumma | Simply, for no reason |
| Oola | Useless, pathetic, poor quality |
| Kidilam / Kidu | Absolutely awesome or fantastic |
| Vayye? | "Are you not well?" (often sarcastic) |
| Pillacha | Respectful address for older man |
| Thirontharam | Local pronunciation of Thiruvananthapuram |

### Landmarks
Palayam, Thampanoor, KD Puram, Vellayambalam, Kowdiar, Chalai Market, Ponmudi, Sreekaryam, Kazhakkoottam, Museum Campus, Technopark

### Food & Eateries
- Boli and Paal Payasam (the crown jewel of Trivandrum Sadya)
- Kethel's Chicken (Rahmaniya)
- Zam Zam Palayam
- Indian Coffee House Thampanoor
- Sree Muruka Cafe (Pazham Pori + Beef Roast)

### Culture
- IFFK (International Film Festival of Kerala)
- Attukal Pongala
- Tagore Theatre
- Ramachandran Textiles East Fort
- Evening chaya at thattukada

---

## Quick Start

### 1. Clone & Install

```bash
# Navigate to project directory
cd disco_bot

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
# Copy the example env file
copy .env.example .env
```

Edit `.env` with your API keys (see [Environment Variables](#environment-variables))

### 3. Run Locally

```bash
python bot.py
```

### 4. Invite Bot to Discord

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your application > OAuth2 > URL Generator
3. Select `bot` scope
4. Select permissions: `Send Messages`, `Read Message History`, `Use Slash Commands`
5. Use the generated URL to invite the bot

---

## Configuration

### Environment Variables

| Variable | Description | Where to Get |
|----------|-------------|--------------|
| `DISCORD_TOKEN` | Your Discord bot token | Discord Developer Portal > Applications > Your Bot > Token |
| `GEMINI_API_KEY` | Google Gemini API key | [Google AI Studio](https://aistudio.google.com/app/apikey) |

### Tuning Parameters

In `bot.py`, you can adjust:

```python
CURSE_REPLY_CHANCE = 0.20  # 20% chance to reply to curse words
BOT_PREFIX = "."            # Command prefix
```

---

## Commands

### Prefix Command
```
.astro <name>
```

**Examples:**
```
.astro Arjun
.astro
```

- If no name is provided, uses the message author's display name
- Works as both prefix command (`.astro`) and slash command (`/astro`)

### Slash Command
```
/astro [name]
```

Same behavior as prefix command.

---

## Passive Behavior

### Trigger Conditions
- Message contains any word from the curse word list (case-insensitive)
- Message is not from a bot
- Message is not from AstRobot itself

### Response Types (50/50 random)

**Type 1: Curse Back**
> "Aiyo Arjun, nee oru Pottan aanu. Ponmudi mist-pole ninte future also unclear. Kili poyi!"

**Type 2: Doomed Prediction**
> "Eda Arjun, ninte stars say you will get stuck in KD Puram traffic for 45 minutes on a Tuesday with no charge in your phone. Chumma vayadi ayikko."

### Timing
- 1–2 second typing delay before reply (for dramatic effect)
- 20% probability of firing back

---

## Deployment

### Railway (Recommended)

1. **Push to GitHub**
   ```bash
   git add .
   git commit -m "Initial commit"
   git push origin main
   ```

2. **Set up Railway**
   - Go to [Railway](https://railway.app) and sign up
   - Create new project from GitHub repo
   - Add environment variables in Railway dashboard:
     - `DISCORD_TOKEN` = your Discord bot token
     - `GEMINI_API_KEY` = your Gemini API key

3. **Deploy**
   - Railway automatically detects `Procfile` and deploys
   - Bot runs with `worker: python bot.py`

### Other Platforms

The bot can run on any platform that supports Python 3.11+:
- Heroku (use `worker: python bot.py` in Procfile)
- DigitalOcean App Platform
- Self-hosted VPS

---

## File Structure

```
disco_bot/
├── bot.py           # Main bot logic (commands + passive replies)
├── prompts.py       # Gemini system prompts & message templates
├── curses.py        # Curse word triggers & passive reply templates
├── glossary.py      # Trivandrum slang, landmarks, food, culture
├── requirements.txt # Python dependencies
├── .env.example     # Template for environment variables
├── .env             # Your actual API keys (not committed)
├── Procfile         # Railway deployment config
└── README.md        # This file
```

---

## Example Exchanges

### .astro Command
```
User: .astro Arjun
AstRobot: "Eda Arjun, Chandra is sitting in the 7th house like a KSRTC bus at Thampanoor — not moving. This week avoid Chalai Market or you will lose your wallet AND your sense of direction. Padmanabha swami has noted your recent behaviour. Shokam aanu, mone."
```

### Passive Curse Reply (Doomed Prediction)
```
User: damn this is so frustrating
AstRobot: "@User, AstRobot is watching. Your stars say you will get stuck in KD Puram traffic for 45 minutes on a Tuesday with no charge in your phone. Chumma vayadi ayikko."
```

### Passive Curse Reply (Curse Back)
```
User: this is such bullshit
AstRobot: "Aiyo @User, nee oru Pottan aanu. Ponmudi mist-pole ninte future also unclear. Kili poyi!"
```

---

## Troubleshooting

### "DISCORD_TOKEN not found"
- Make sure `.env` file exists and contains `DISCORD_TOKEN=your_token`

### "GEMINI_API_KEY not found"
- Make sure `.env` file contains `GEMINI_API_KEY=your_key`

### Bot not responding to commands
- Make sure the bot has proper permissions (Send Messages, Read Message History)
- Check that Message Content Intent is enabled in Discord Developer Portal

### Gemini API errors
- Check your API key is valid and has quota remaining
- Fallback message will be used: "AstRobot-nte lamp went off. KSRTC current problem. Try again mone."

### Bot triggering on its own messages
- Handled: Bot ignores messages from itself and other bots

---

## Credits

Built with ❤️ for Trivandrum and the Manglish speakers everywhere.

**Tech Stack:**
- Python 3.11+
- discord.py v2
- Google Gemini Flash (via google-genai)
- python-dotenv