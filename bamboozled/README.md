# Bamboozled — Deployment Guide

Bamboozled lives in the `bamboozled/` sub-directory of the same `disco_bot` repo as AstRobot. It runs as a **separate bot process** with its own Discord application and token, on the same GCP VM. The two bots are completely independent.

---

## What's new since initial build

| Area | What changed |
|---|---|
| OpenTDB questions | Restricted to 12 SFW category IDs — entertainment categories excluded |
| Bamboozle Rule input | Player-typed rules are screened by a content filter before posting |
| `BAMBOOZLE_RULE_FILTER_ENABLED` | Toggle in `constants.py` — `True` by default |
| `SAFE_OPENTDB_CATEGORY_IDS` | Configurable list of allowed category IDs in `constants.py` |
| Game config UI | Host sees a rounds/difficulty/category picker before the game starts |
| Player mentions | Turn opener and silence-skip now ping the active player |
| Pacing delays | Small sleeps between game events for readability |
| Boli Points bridge | Bamboozled awards Boli Points into Navi's database at game end |

---

## Boli Points bridge

At the end of each game Bamboozled writes Boli Point awards directly into Navi's SQLite database. No Navi code is imported — only raw SQL via `aiosqlite`.

### Setup

Set `NAVI_DB_PATH` in `bamboozled/.env` to the absolute path of Navi's database on the VM:

```env
NAVI_DB_PATH=/home/astrobot/disco_bot/data/astro_bot.db
```

If the variable is empty or the file does not exist, the bridge silently does nothing — Bamboozled will never crash due to a missing Navi database.

### Award formula

| Component | Amount |
|---|---|
| Participation | `round(20 × difficulty_multiplier)` |
| Per correct answer | `correct_answers × 3` (no multiplier) |
| Winner bonus | `round(50 × dm)` |
| 2nd place (3+ players) | `round(20 × dm)` |
| 3rd place (5+ players) | `round(10 × dm)` |
| Score bonus | `max(0, score) // 50` (+1 per 50 positive pts) |
| Negative score penalty | `max(score // 100, -20)` (capped at −20) |

Difficulty multipliers: Easy = 0.75 × · Medium = 1.0 × · Hard = 1.5 × · Mixed = 1.0 ×

Only positive totals are written to Navi's DB. Negative totals appear in the end-of-game embed but are not deducted from Navi.

### Environment variable

| Variable | Default | Effect |
|---|---|---|
| `NAVI_DB_PATH` | *(empty)* | Absolute path to Navi's `astro_bot.db`. Leave empty to disable the bridge. |

---

## Step 0 — One-time: Create a Discord application for Bamboozled

Bamboozled needs its own bot token. Do this once from any browser.

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application** → name it **Bamboozled**
2. Left sidebar → **Bot** → **Add Bot**
3. Under **Token** → **Reset Token** → copy and save it somewhere safe
4. Scroll down to **Privileged Gateway Intents** — no intents are needed, leave all off
5. Left sidebar → **OAuth2 → URL Generator**
   - Scopes: tick **`bot`** and **`applications.commands`**
   - Bot permissions: tick **`Send Messages`**, **`Read Message History`**, **`Use Slash Commands`**
6. Copy the generated URL at the bottom → open it in a browser → select your server → **Authorise**

---

## Step 1 — Push the code from Windows

On your local machine, commit and push the new `bamboozled/` directory:

```powershell
cd "C:\Users\Sree\Documents\GitHub\disco_bot"
git add bamboozled/
git commit -m "feat: add Bamboozled game"
git push origin main
```

> `.env` files are git-ignored and will **not** be pushed — only the `.env.example` template is tracked.

---

## Step 2 — Pull on the GCP VM

SSH into your VM (GCP Console → Compute Engine → click **SSH**), then:

```bash
cd ~/disco_bot
git pull origin main
```

---

## Step 3 — Set up a virtual environment for Bamboozled

Bamboozled needs `discord.py`, `aiosqlite`, and `aiohttp` — versions that may differ from AstRobot's. Give it its own venv to keep them isolated.

```bash
cd ~/disco_bot/bamboozled

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

Verify:

```bash
python -c "import discord, aiosqlite, aiohttp; print('deps OK')"
# Expected: deps OK
```

---

## Step 4 — Create the `.env` file

```bash
cd ~/disco_bot/bamboozled
cp .env.example .env
nano .env
```

Replace the placeholder with your real token:

```env
DISCORD_TOKEN=your_bamboozled_bot_token_here
```

Press `Ctrl+O` → `Enter` to save, `Ctrl+X` to exit.

---

## Step 5 — Run the bot

Open a **new tmux window** (don't touch the existing AstRobot session):

```bash
# Create a new tmux session just for Bamboozled
tmux new -s bamboozled

# Inside the new session
cd ~/disco_bot/bamboozled
source .venv/bin/activate
python bot.py
```

On a successful start you should see:

```
2026-05-11 [INFO] __main__: Synced 7 slash command(s).
2026-05-11 [INFO] __main__: Logged in as Bamboozled#1234 (ID: ...)
2026-05-11 [INFO] __main__: Bamboozled bot is ready!
```

**Detach from tmux** (bot keeps running after you close SSH):
Press `Ctrl+B`, release, then press `D`.

> **Slash command propagation**: Global command sync can take up to 1 hour on first run. For instant registration during testing, see [Fast dev sync](#fast-dev-sync-optional) below.

---

## Step 6 — Verify it's working

In Discord, type `/bamboozled` — the command group should appear. Run through:

| Action | Expected |
|---|---|
| `/bamboozled join` | Bot posts lobby message with your name |
| `/bamboozled start` | Game begins, first question posted |
| Answer a question | Points awarded, card drawn |
| `/bamboozled scores` | Ephemeral score list |
| `/bamboozled leaderboard` | All-time wins embed |
| `/bamboozled endgame` | Game cancelled, no results saved |

---

## Checking logs / reattaching

```bash
# Reattach to the running bot
tmux attach -t bamboozled

# Stop the bot
# (inside tmux) Ctrl+C

# Restart after a code update
tmux attach -t bamboozled
# Ctrl+C to stop, then:
git pull origin main
python bot.py
```

---

## Updating the bot after a code change

```bash
# On your local machine
git add bamboozled/
git commit -m "your message"
git push origin main

# On the VM
tmux attach -t bamboozled
# Ctrl+C to stop the bot
cd ~/disco_bot
git pull origin main
cd bamboozled
source .venv/bin/activate
python bot.py
# Ctrl+B then D to detach
```

---

## Database backup

The SQLite database lives at `bamboozled/db/bamboozled.db` on the VM. It is not committed to git. To copy it to your local machine:

```powershell
# Run on your local machine (Windows PowerShell)
# Replace YOUR_VM_NAME and YOUR_ZONE with your GCP instance details
gcloud compute scp YOUR_VM_NAME:~/disco_bot/bamboozled/db/bamboozled.db ./bamboozled_backup.db --zone=YOUR_ZONE
```

To find your VM name and zone: GCP Console → Compute Engine → VM Instances.

---

## Configuring content safety settings

Both settings live in `bamboozled/game_engine/constants.py`. Edit them on the VM:

```bash
nano ~/disco_bot/bamboozled/game_engine/constants.py
```

| Setting | Default | Effect |
|---|---|---|
| `BAMBOOZLE_RULE_FILTER_ENABLED` | `True` | Set to `False` to allow any player-typed rule text through unfiltered |
| `SAFE_OPENTDB_CATEGORY_IDS` | 12 category IDs | Add or remove OpenTDB category IDs to change which topics questions can come from |

After editing, restart the bot (Ctrl+C in tmux, then `python bot.py`).

---

## Customising fallback questions

If OpenTDB is unreachable, the bot uses `bamboozled/data/custom_questions.json`. Add your own questions in this format:

```json
{
  "question": "Your question here?",
  "correct_answer": "The right answer",
  "incorrect_answers": ["Wrong 1", "Wrong 2", "Wrong 3"],
  "category": "Category Name",
  "difficulty": "easy"
}
```

No restart needed — the file is read at fetch time.

---

## Fast dev sync (optional)

The default global command sync takes up to 1 hour. To register commands instantly in one specific server during development, edit `bamboozled/bot.py` and replace the sync line:

```python
# In setup_hook, replace:
synced = await self.tree.sync()

# With (use your server's ID):
GUILD = discord.Object(id=YOUR_GUILD_ID_HERE)
self.tree.copy_global_to(guild=GUILD)
synced = await self.tree.sync(guild=GUILD)
```

Revert this change before final deployment so the commands are available in all servers.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `RuntimeError: DISCORD_TOKEN not set` | Check `bamboozled/.env` exists and contains the token |
| `/bamboozled` command doesn't appear after 1 hour | Check the bot was invited with `applications.commands` scope; re-invite if needed |
| `aiosqlite` or `discord` not found | Make sure you activated `bamboozled/.venv` before running |
| Bot posts restart notice on startup | Expected — means the bot restarted during an active game. Safe to ignore. |
| Questions are all the same category | `SAFE_OPENTDB_CATEGORY_IDS` has only one entry — add more in `constants.py` |
| Bamboozle Rule gets rejected unexpectedly | Rule contains a substring that matches the filter (e.g. "classic" contains "ass"). Set `BAMBOOZLE_RULE_FILTER_ENABLED = False` in `constants.py` to disable |
| `Too Many Requests` from OpenTDB | OpenTDB has a rate limit — the bot retries once after 3 s, then falls back to `custom_questions.json` automatically |
