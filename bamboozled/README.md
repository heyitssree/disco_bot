# Bamboozled — Discord Trivia Chaos Game

A multiplayer trivia game for 1–6 players inspired by the fictional game from *Friends*. Runs entirely via slash commands in Discord.

---

## Requirements

- Python 3.11+
- A Discord bot application with the **bot** scope and **applications.commands** scope

---

## Setup

### 1. Create a `.env` file

Copy `.env.example` to `.env` inside the `bamboozled/` directory and fill in your token:

```
DISCORD_TOKEN=your_bot_token_here
```

### 2. Install dependencies

From inside the `bamboozled/` directory:

```bash
pip install -r requirements.txt
```

### 3. Invite the bot to your server

Go to the [Discord Developer Portal](https://discord.com/developers/applications), open your application, then navigate to **OAuth2 → URL Generator**.

Required scopes:
- `bot`
- `applications.commands`

Required bot permissions:
- `Send Messages`
- `Read Message History`
- `Use Slash Commands`

Copy the generated URL and open it in your browser to invite the bot.

### 4. Run the bot

From inside the `bamboozled/` directory:

```bash
python bot.py
```

The bot will:
- Initialise the SQLite database (`db/bamboozled.db`) automatically
- Sync slash commands globally (may take up to 1 hour to propagate — for instant registration during development, edit `bot.py` to sync to a specific guild ID)
- Post a restart notice in any channels that had an active game when the bot last stopped

---

## Commands

| Command | Description |
|---|---|
| `/bamboozled join` | Join the lobby for the next game in this channel |
| `/bamboozled start` | Start the game (host/first-joiner only) |
| `/bamboozled scores` | View current scores mid-game |
| `/bamboozled leaderboard` | All-time win counts |
| `/bamboozled stats @user` | A specific player's all-time stats |
| `/bamboozled forfeit` | Forfeit your current turn (treated as timeout) |
| `/bamboozled endgame` | Force-end the game with no results saved (host only) |

---

## Game Overview

- **1–6 players** per game, **5 rounds**, turn order fixed by join order
- Questions fetched from [OpenTDB](https://opentdb.com/) (free, no key required)
- **Correct answer** → +100 pts, draw a **Chance Card**
- **Wrong answer** → -50 pts, draw a **Wicked Wango Card**
- **Timeout/Forfeit** → -100 pts, no card

### Cards & Chaos

**Chance Cards:** Lucky Llama · Switcheroo · Double Down · Spin the Wheel · Golden Pass · Bamboozle

**Wicked Wango Cards:** Wango Classic · The Silence · Reverse Uno · The Sombrero · Double Wango · Mystic Mist

**Wheel of Mayhem (8 segments):** Ladder of Chance · Tax Season · Gift of the Bamboozle · Full Reversal · Mystic Mist · Bonus Round · Wango Again · Monkey's Choice

### Special Mechanics

- 🐒 **Golden Monkey** — Belly (+300) or Tail (−200 + Wango card)
- 🪅 **The Sombrero** — Extra -25 pts per wrong answer while held
- 🌫️ **Mystic Mist** — Scores hidden for 2 turns
- 🃏 **Bamboozle Rule** — Player writes a custom honour-system rule for 1 full round
- 🎫 **Golden Pass** — Skip the next Wango card you'd draw

### Solo Mode

All mechanics work with a single player. Player-targeting effects resolve automatically (Phantom Player for Switcheroo, The Bank for Gift, etc.).

---

## Data

Results are stored in `db/bamboozled.db` (SQLite). The file is created automatically on first run.

Custom fallback questions can be added to `data/custom_questions.json`. They are used if the OpenTDB API is unavailable.
