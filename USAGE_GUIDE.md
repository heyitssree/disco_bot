# AstRobot V2 — Usage Guide

*"The most unnecessarily dramatic astrologer in Thirontharam."*

---

## Slash Commands

### 🔮 `/astro` — Get Your Prediction
The main event. Get a Trivandrum Manglish astrology reading.

```
/astro
/astro user:@someone
```

**What happens:**
- **First time**: AstRobot assigns you a Rashi (Malayalam star sign) that sticks forever
- **Every time after**: AstRobot references one of your past doomed predictions and blames it on something specific ("you got stuck at KD Puram because you skipped Boli and Paal Payasam")
- The prediction changes based on **time of day** — evening predictions obsess over chaya and Pazham Pori; late-night ones hint at Museum Campus ghosts
- **+2 Boli Points** awarded to the person who used the command

**Example outputs:**
> *"Eda Sree, your Kumbham Rashi shows kili poyi vibes near Museum Campus tonight — and yes, last week's Chalai Market disaster was because you skipped Boli."*

> *"Aiyo Arun, 4pm star alignment says go get chaya NOW from thattukada or your evening is shokam."*

---

### 🍮 `/mypoints` — Check Your Boli Points
See your personal AstRobot profile. Only visible to you.

```
/mypoints
```

Shows:
- 🌟 Your Rashi (assigned on first `/astro` use)
- 🍮 Total Boli Points earned
- 🔮 Number of predictions you've received

---

### 🏆 `/rank` — Leaderboard
See the **Top Appis** in the server — ranked by Boli Points.

```
/rank
```

Shows top 10 users with their Rashi, Boli Points, and prediction count.

---

### 🔧 `/health` — System Status *(Owner Only)*
Check AstRobot's internals. Only works if you're the bot owner.

```
/health
```

Shows:
- Which Gemini key is active (`free` or `paid`)
- Circuit breaker state (OPEN 🔴 / CLOSED 🟢)
- Rate limiter: how many of the 10 RPM you've used this minute
- DuckDB row counts per table
- Bot uptime

---

## Earning Boli Points 🍮

Boli Points are earned passively — no command needed.

| Action | Points |
|---|---|
| Use `/astro` | +2 pts |
| Say a Trivandrum word in chat | +5 pts per word |
| Trigger a curse-word reply | +1 pt |

**Words that earn points** (say these naturally in chat):

| Word | Meaning |
|---|---|
| `kidilam` | Absolutely awesome |
| `kidu` | Same — fantastic |
| `pillacha` | Respectful address for older man |
| `appi` | Term of endearment |
| `shokam` | Sad/boring situation |
| `chumma` | Simply, for no reason |
| `kili poyi` | Bird flew away — shocked/confused |
| `vishayam` | Matter/issue |
| `mone` | Son; affectionate address |
| `chetta` | Elder brother |
| `thirontharam` | Correct local name for Trivandrum |
| `boli` | The sacred sweet |
| `paal payasam` | The sacred dessert |

> **Example**: Saying *"that movie was kidilam mone"* earns +10 pts (2 trigger words × 5).

---

## Passive Bot Reactions

AstRobot watches every message and reacts automatically.

### 🗣️ Kochi Slang Detection
Say any of these and AstRobot will condescendingly remind you where you are:

`machane` · `machi` · `sayi` · `da scene` · `yov` · `monae` · `adipoli`

**Example reply:**
> *"Eda Arun, this is Thirontharam. Keep that Kochi talk at Ernakulam South station."*

*85% chance of reply — sometimes AstRobot ignores you, which is also very on-brand.*

---

### 💢 Curse Word Detection
Use any Malayalam curse word and there's a 25% chance AstRobot roasts you back.

**Example reply:**
> *"Eda Sree, nee oru pottan aanu. Ninte future darker than KD Puram at 7pm. Shokam."*

---

### ❓ Tagging AstRobot
Mention `@AstRobot` with a question and get a sarcastic answer.

```
@AstRobot will I get a promotion this year?
@AstRobot what should I eat for lunch?
```

**Example replies:**
> *"Eda Sree, even KD Puram traffic has more potential than your promotion chances."*
> *"Aiyo, go eat Boli. What kind of question is that, mone."*

---

### 🌅 Time-Aware Personality

AstRobot's mood shifts throughout the day:

| Time | Personality |
|---|---|
| 6–10 AM | Grumpy about the KSRTC bus rush at Thampanoor |
| 11 AM–2 PM | Irritable about the unbearable noon heat |
| 2–4 PM | Sleepy post-lunch sluggishness |
| **4–7 PM** | **Obsessed with evening chaya and Pazham Pori** |
| 7–10 PM | Lamenting Technopark traffic and KD Puram gridlock |
| 10 PM+ | Ominous late-night vibes, Museum Campus ghost energy |

---

## 📅 Daily Omen + Weather Briefing

Every day at **7:00 AM IST**, AstRobot posts a combined weather report and city-wide omen to `#off-topic`.

**What it contains:**
- Real Trivandrum weather for the day (temperature, rain forecast) — fetched live from Open-Meteo
- An absurd astrology prediction for the whole city woven around the weather
- A Trivandrum landmark as the day's "focal point"

**Example:**
> *"Namaskaram Thirontharam! Today: 34°C scorching sun, zero rain — basically Thampanoor is a tandoor oven. The stars say if you are near Chalai Market by noon, your chappal will melt and your mood also. Drink buttermilk, not opinions. Shokam day ahead. — AstRobot"*

---

## Admin Controls

These work by typing in chat (no slash command needed). Owner only.

| Message | Effect |
|---|---|
| `astro syros stop` | Silences AstRobot everywhere |
| `astro syros start` | Brings AstRobot back online |

---

## How the API Works (Behind the Scenes)

You don't need to manage this, but good to know:

```
Every Gemini call:
  1. Try free API key first
  2. If busy/failed → try paid key
  3. If both fail 3 times → cache-only mode for 30 min
  4. Max 10 API calls per minute (then falls back to saved responses)
```

- The bot **caches all responses** in a local database and reuses them smartly
- `/health` shows exactly what's happening at any moment

---

## Tips

- **Check `/mypoints` after chatting** — you'll often have points you didn't notice earning
- **The more people use Trivandrum words, the richer the leaderboard** — `/rank` is more fun with a full server
- **The 7 AM post is the highlight** — check `#off-topic` every morning for the omen
- **Tag AstRobot with weird questions** — the more absurd the question, the better the sarcastic answer
