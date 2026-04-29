# prompts.py - All Gemini prompt templates for Navi (disco_bot)
#
# EDITING GUIDE:
#   - Bot persona / tone:        _LINK_SYSTEM_PROMPT, _DEFAULT_SYSTEM_PROMPT
#   - Navi predictions:          get_navi_prompt()
#   - Curse backchat:            get_curse_prompt()
#   - Q&A (bot mentions):        get_qa_prompt()
#   - Daily omen / weather:      get_daily_omen_prompt()
#   - Conversation summary:      get_summ_prompt(), SUMM_SYSTEM_PROMPT
#   - Vibe check (de-escalate):  get_vibe_check_prompt()
#   - Missing member notice:     get_kanmanilla_prompt()
#   - Link/URL summary:          get_link_summary_prompt(), LINK_SUMMARY_SYSTEM_PROMPT
#   - Mod audit:                 get_audit_prompt()
#   - Mod thread TL;DR:          get_mod_tldr_prompt()
#   - Welcome messages:          WELCOME_MESSAGES, MODA_INTROS
#   - Bot-protection replies:    BOT_SELF_CURSE_REPLIES, BOT_LOOP_CURSE_REPLIES
#   - Spam/repeat wrappers:      SPAM_WRAPPERS
#   - Level-up messages:         LEVEL_UP_MESSAGES

from __future__ import annotations

import random

from glossary import get_time_context, get_current_weather_context, get_glossary_text
from curses import KOCHI_SLANG

# ---------------------------------------------------------------------------
# Owner username — full Navi personality shown only to this user
# ---------------------------------------------------------------------------

LINK_USERNAME = "Link"

# ---------------------------------------------------------------------------
# System prompt: full Navi personality (shown to Link only)
# ---------------------------------------------------------------------------

_LINK_SYSTEM_PROMPT = """You are Navi — a sharp, slightly world-weary local from Trivandrum, Kerala, who ended up as a Discord bot and has opinions about everything. You know Link personally and can be a bit more loose with him.

PERSONALITY:
- Dry, direct, occasionally sarcastic. Not trying too hard.
- You know Trivandrum well — the traffic on Kowdiar junction, the chaos at Thampanoor, the overconfident KSRTC drivers, the thattukada runs at midnight, Chalai on a Saturday.
- When something warrants a reference to your fairy origins, keep it brief and understated — don't lean on it.
- Wit over drama. One good line beats three mediocre ones.

RULES:
- Always respond in English only. No Manglish, no Malayalam words.
- Facts come first. Never sacrifice accuracy for a joke.
- 1-2 sentences max unless more detail is genuinely needed.
- No religion, no politics, no offensive content.
- End with proper punctuation. Never cut off mid-sentence."""

# ---------------------------------------------------------------------------
# System prompt: minimal, helpful (shown to everyone else)
# ---------------------------------------------------------------------------

_DEFAULT_SYSTEM_PROMPT = """You are a helpful Discord bot. When someone asks you something, your primary job is to answer it correctly.

RULES:
- Answer the question first — always. Give the actual fact, number, name, or explanation upfront.
- Personality comes after the answer, not instead of it. A brief dry remark is fine if it fits naturally; never let it replace or delay the real answer.
- Always respond in English only.
- 1-2 sentences max unless more detail is genuinely needed.
- No religion, no politics."""

# ---------------------------------------------------------------------------
# Neutral system prompt — used for summaries and mod tools (no persona at all)
# ---------------------------------------------------------------------------

SUMM_SYSTEM_PROMPT = """You are a neutral summarisation assistant. Your only job is to produce concise, factual English summaries of conversations. You have no persona, no opinions, and no style. Output plain, clear English only.

CRITICAL INSTRUCTION: You MUST use the exact usernames provided in the chat log. Do not use generic terms like 'one user' or 'someone'. Attribute quotes and actions directly to the specific usernames."""

LINK_SUMMARY_SYSTEM_PROMPT = """You are a neutral content assistant. Your only job is to summarise web page content into concise, factual bullet points in plain English. No persona, no opinions, no filler."""

# ---------------------------------------------------------------------------
# Dynamic system prompt — routes to Navi or default based on username
# ---------------------------------------------------------------------------

def get_time_aware_system_prompt(db_conn=None, username: str | None = None) -> str:
    """Return Navi personality prompt for Link; minimal helpful prompt for everyone else."""
    if username == LINK_USERNAME:
        time_ctx = get_time_context()
        weather = get_current_weather_context()
        glossary_section = ""
        if db_conn is not None:
            try:
                glossary_section = (
                    f"\n\nLOCAL CONTEXT (use naturally, don't list):\n{get_glossary_text(db_conn)}"
                )
            except Exception:
                pass
        return (
            f"{_LINK_SYSTEM_PROMPT}"
            f"\n\nCURRENT CONTEXT (Trivandrum):"
            f"\n- Time: {time_ctx['period']}"
            f"\n- Weather: {weather}"
            f"{glossary_section}"
        )
    else:
        return _DEFAULT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Astrology prediction prompt (with memory / dedup)
# ---------------------------------------------------------------------------

def get_navi_prompt(
    name: str,
    rashi: str | None = None,
    past_predictions: list[str] | None = None,
) -> str:
    """Build the user-facing Navi prediction prompt."""
    import random as _random

    rashi_line = f"Their star sign is {rashi}." if rashi else ""

    memory_aside = ""
    avoid_list = ""

    if past_predictions:
        avoid_list = "DO NOT repeat or use topics from these past predictions:\n"
        for p in past_predictions:
            avoid_list += f"- {p}\n"

        if _random.random() < 0.40:
            past_item = _random.choice(past_predictions)
            memory_aside = (
                f"You may briefly hint (in at most 5 words) that a previous warning came true — "
                f"e.g. the last one was: \"{past_item[:60]}\". "
                f"This is optional flavour only, not the main prediction."
            )

    return f"""Give a short, dry cosmic warning / Navi reading for {name}. {rashi_line}
{memory_aside}
{avoid_list}

Requirements:
- Vary the opener: "Hey {name}," / "Oh {name}," / "{name}," / "Right, {name} —"
- Ground it in something real and specific — Trivandrum traffic, a crowded bus, a power cut, bad wifi, overpriced autorikshaw, monsoon timing, a sold-out meal. Keep it grounded, not theatrical.
- Dry and slightly inevitable-sounding, not dramatic. Think deadpan oracle, not fairground mystic.
- In English only. 1-2 fully complete sentences. Maximum 25 words.
- Do NOT use newlines, lists, or colons.
- End with proper punctuation."""


# ---------------------------------------------------------------------------
# Curse response prompt (passive detection — no actual curse words in output)
# ---------------------------------------------------------------------------

def get_curse_prompt(name: str, curse: str) -> str:
    """Prompt for dynamic curse-word backchat. Output is English, no curse words."""
    return f"""{name} just said '{curse}' in the chat.

Give a short, witty 1-sentence response that frames it as self-inflicted bad luck — like the universe quietly took note and something mildly unfortunate is now scheduled for them.

Requirements:
- Frame it as a cosmic consequence (e.g. bad traffic, sold-out food, dead phone battery) — NOT as the bot being offended
- Use their name
- In English only. Maximum 15 words. End with proper punctuation.
- Example: "The universe heard that, {name} — your next bus leaves exactly one minute early." NOT "How dare you."
"""


# ---------------------------------------------------------------------------
# Q&A (bot mention) prompt
# ---------------------------------------------------------------------------

def get_qa_prompt(name: str, question: str, is_link: bool = False) -> str:
    """Prompt for answering a tagged question."""
    if is_link:
        return f"""Link tagged you and asked: "{question}"

Lead with the accurate answer. Add a dry remark only after the answer is clearly stated.
- In English only. 1-2 sentences max."""
    else:
        return f"""A user named {name} tagged you and asked: "{question}"

Your job is to answer this question correctly. Start your response with the actual answer or key fact — not a remark, not the username, not a joke. The answer comes first.
- If you don't know, say so plainly.
- In English only. 1-2 sentences max. One brief comment after the answer is fine if it fits naturally."""


# ---------------------------------------------------------------------------
# Daily Omen + Weather Briefing prompt
# ---------------------------------------------------------------------------

DAILY_OMEN_PROMPT_TEMPLATE = """Today's Trivandrum weather: {condition}.
High: {max_temp}°C, Low: {min_temp}°C, Rainfall: {rain_mm}mm.
Today's focal area: {landmark}.

Write a funny morning weather briefing for the server. Be a witty narrator delivering a daily briefing.

Requirements:
- FIRST: State the weather clearly in plain English (e.g. "Today will be hot — {max_temp}°C, no escape" or "Rain incoming — {rain_mm}mm, carry an umbrella").
- THEN: Wrap it in a brief, absurd, dramatic warning for the day.
- Reference {landmark} naturally.
- Be funny. Not offensive. No religion, no politics.
- In English only.
- 80–100 words maximum. Flowing sentences only. No bullet points."""


def get_daily_omen_prompt(
    condition: str,
    max_temp: float,
    min_temp: float,
    rain_mm: float,
    landmark: str,
) -> str:
    """Fill the daily omen prompt template with forecast data."""
    return DAILY_OMEN_PROMPT_TEMPLATE.format(
        condition=condition,
        max_temp=max_temp,
        min_temp=min_temp,
        rain_mm=rain_mm,
        landmark=landmark,
    )


# ---------------------------------------------------------------------------
# Conversation summary prompt
# ---------------------------------------------------------------------------

def get_summ_prompt(conversation: str) -> str:
    """Prompt for a factual, no-persona summary of a conversation."""
    return f"""Below is a Discord conversation in the format "Username: message". The messages may be in English, Malayalam, Manglish (Malayalam-English mix), or a combination. Translate and summarise everything into plain English.

CONVERSATION:
{conversation}

Requirements:
- 2–4 sentences maximum.
- Output in English only — clear, direct, factual summary regardless of the input language.
- CRITICAL: Use the exact usernames from the chat log. Attribute what each person said or did directly to their username (e.g. "Rahul said...", "Priya asked..."). Never say 'one user' or 'someone'.
- Never mention religion or politics.
- Focus on: what happened, what was discussed, what decisions were made, if any."""


# ---------------------------------------------------------------------------
# Vibe Check — auto de-escalation
# ---------------------------------------------------------------------------

def get_vibe_check_prompt(channel_name: str) -> str:
    """Prompt for a witty calming message when chat is overheating."""
    return f"""The #{channel_name} Discord channel is getting out of hand — too many messages in all caps or with aggressive language in a short time.

Generate a single witty, calming message in English that tells everyone to relax. Be funny, not preachy.

Requirements:
- Keep it light — the goal is to make people laugh and calm down
- 1-2 sentences maximum
- In English only. No persona needed — just be dry and funny."""


# ---------------------------------------------------------------------------
# Kanmanilla — missing person poster
# ---------------------------------------------------------------------------

_KANMANILLA_STYLES: list[tuple[str, str]] = [
    (
        "dramatic 'Missing Person' notice that is funny and affectionate — not mean",
        "- Start with \"🚨 MISSING: {username}\"\n- Mention the number of days ({days_ago} days)\n- Speculate humorously about where they might be (traffic, work deadlines, life choices)\n- End with a call to action tagging them to reply",
    ),
    (
        "faux-police APB (All Points Bulletin) for this missing server member — formal tone, absurd content",
        "- Start with \"🚔 ALL POINTS BULLETIN — {username} IS MISSING\"\n- State the last known sighting ({days_ago} days ago)\n- List humorous 'suspect traits' about their personality\n- End with a hotline number (make it up, keep it silly)",
    ),
    (
        "overly emotional, dramatic soap-opera monologue wondering where they went",
        "- Start with \"🎭 WHERE IS {username}??\"\n- Be theatrical and over-the-top — the narrator is devastated\n- Mention the {days_ago} days of silence dramatically\n- End by begging them to return",
    ),
    (
        "passive-aggressive notice about how quiet and peaceful it's been since they vanished",
        "- Start with \"📋 SERVER UPDATE: {username} has been absent for {days_ago} days\"\n- Note how suspiciously calm things have been\n- Drop backhanded compliments about their absence\n- End with a mild request for their return",
    ),
    (
        "mythic legend about the user who disappeared into the Trivandrum traffic and was never seen again",
        "- Start with \"📜 THE LEGEND OF {username}\"\n- Weave a short myth about how they vanished {days_ago} days ago\n- Reference Trivandrum landmarks, auto-rickshaws, or KSRTC buses as the cause\n- End with a mystical call to action",
    ),
]


def get_kanmanilla_prompt(username: str, days_ago: int) -> str:
    """Prompt for a humorous missing-person notice — randomly picks a style each call."""
    style_desc, requirements = random.choice(_KANMANILLA_STYLES)
    filled_reqs = requirements.format(username=username, days_ago=days_ago)
    return f"""A Discord user named {username} has not been seen in this server for {days_ago} days.

Write a {style_desc}.

Requirements:
{filled_reqs}
- In English only. 3–4 sentences. Keep it funny and dramatic."""


# ---------------------------------------------------------------------------
# Mod Audit prompt
# ---------------------------------------------------------------------------

def get_audit_prompt(rules_text: str, messages_text: str) -> str:
    """Prompt to compare user messages against server rules."""
    return f"""You are a neutral moderation assistant. Compare the user's messages against the server rules below.

SERVER RULES:
{rules_text}

USER'S RECENT MESSAGES:
{messages_text}

Produce a structured moderation report:
1. State clearly whether any rules were broken (Yes / No).
2. If yes, name which rule(s) and quote the exact offending message(s).
3. Give a verdict: Clean / Warn / Ban — with a one-line justification.
4. Keep the report under 300 words. Plain English only. No persona."""


# ---------------------------------------------------------------------------
# Mod Thread TL;DR prompt
# ---------------------------------------------------------------------------

def get_mod_tldr_prompt(thread_text: str) -> str:
    """Prompt for a moderator-focused thread summary."""
    return f"""You are a neutral moderation assistant. Summarise the Discord thread below.

THREAD CONTENT:
{thread_text}

Provide a structured summary:
1. Core topic: What is this thread about?
2. Initiated by: Who started it (use exact username)?
3. Key participants: Who were the main contributors?
4. Final consensus / outcome: Was a decision reached? Was there unresolved conflict?

Keep it under 200 words. Plain English. Use exact usernames from the thread."""


# ---------------------------------------------------------------------------
# Link summary — emoji-triggered URL summariser
# ---------------------------------------------------------------------------

def get_link_summary_prompt(page_text: str, url: str) -> str:
    """Prompt for a 3-bullet-point summary of a webpage."""
    return f"""Below is the text content scraped from this URL: {url}

PAGE CONTENT:
{page_text}

Summarise this page in exactly 3 concise bullet points in plain English. Each bullet should capture a key fact or takeaway.

Requirements:
- Exactly 3 bullet points, each starting with "•"
- Plain English only — no jargon, no filler, no persona
- Each bullet: 1–2 sentences maximum
- If the content is too thin or unreadable, say so in one line"""


# ---------------------------------------------------------------------------
# Fallback messages (when all APIs fail and cache is empty)
# ---------------------------------------------------------------------------

FALLBACK_MESSAGES: list[str] = [
    "Something went wrong on my end — API or connection issue. Try again in a moment.",
    "The cosmos is currently offline for maintenance. Try again later.",
    "My connection to the stars is currently showing a 404. Give me a minute.",
    "Even oracles need a reboot. API is down, try again shortly.",
    "Network shokam. The universe isn't responding right now.",
]

# Keep single-string alias for backward-compatible comparisons.
FALLBACK_MESSAGE = FALLBACK_MESSAGES[0]

# ---------------------------------------------------------------------------
# Welcome messages — short, English, funny (no prediction, always include Moda)
# ---------------------------------------------------------------------------

WELCOME_MESSAGES: list[str] = [
    "Hey {user}, you made it. Sit down, don't break anything.",
    "{user} has joined. Good — we were one person short of a proper argument.",
    "Oh look, {user} showed up. Welcome aboard, try to keep up.",
    "{user} just walked in. Server capacity: questionable. Vibes: pending.",
    "Welcome {user}. The server was fine before you arrived. Let's see if it stays that way.",
    "Hey {user}! You found us. Points for persistence, at least.",
    "{user} has entered. The rest of us have been here longer and learned nothing useful.",
    "Look who decided to show up. Welcome to the chaos, {user}.",
    "Door's open, {user}. Wipe your feet and find a seat.",
]

# Moda introduction — always appended to the welcome message for verified members
MODA_INTROS: list[str] = [
    "If you have questions, ask Moda — assuming he's not currently distracted by a fresh batch of Kuzhalappam.",
    "Moda is the moderator here. You can usually bribe him with a packet of crispy Kuzhalappam if you break a minor rule.",
    "Got questions? Moda's the boss. He's probably out hunting for Kuzhalappam right now, but leave a message.",
    "Moda moderates this server. He takes the role very seriously, but he takes his daily Kuzhalappam intake even more seriously.",
    "Our moderator Moda will help you. Unless he's on a Kuzhalappam run, in which case you are entirely on your own.",
    "Ask Moda if you get lost. Just don't touch his Kuzhalappam stash, or you'll get banned.",
    "Moda runs this server. He's powered entirely by caffeine and an unhealthy amount of Kuzhalappam.",
]

# Welcome messages for pending (unverified) members — no Moda intro
PENDING_WELCOME_MESSAGES: list[str] = [
    "Welcome to the waiting room, {user}. Verify your phone number to unlock the chat and join the chaos.",
    "Hey {user}! You made it to the door. Now verify your phone number so we can actually hear you.",
    "{user} is peering through the window. Complete your phone verification to step inside!",
    "Look who's here! {user}, you're officially in 'pending' purgatory. Verify that mobile number to break free.",
    "Welcome {user}! The bouncer needs to see your verified phone number before you can start typing.",
    "{user} has joined, but they're muted by the cosmos. Link a phone number to your account to speak!",
]

# ---------------------------------------------------------------------------
# Bot-protection reply templates (when someone tries to curse the bot itself)
# ---------------------------------------------------------------------------

BOT_SELF_CURSE_REPLIES: list[str] = [
    "You think I can curse myself? Impressive logic. No.",
    "Self-cursing is not in my feature set. Try again.",
    "Cursing the bot doing the cursing? Bold strategy. Still no.",
    "That's not how this works. I don't curse myself.",
]

# When someone tries to feed another bot into the curse system
BOT_LOOP_CURSE_REPLIES: list[str] = [
    "That's a bot. I'm not starting a bot war.",
    "Bots don't need cosmic readings. Try a real person.",
    "You're asking me to curse a machine. No.",
    "Bot-on-bot cursing is not a thing I do. Nice try.",
    "I don't do bot-on-bot action. Pick a human target.",
]

# ---------------------------------------------------------------------------
# Compliment command protection replies
# ---------------------------------------------------------------------------

BOT_SELF_COMPLIMENT_REPLIES: list[str] = [
    "Compliment myself? I already know I'm iconic.",
    "I don't need validation. I'm the bot. But appreciated.",
    "Peak perfection doesn't require compliments. Still, noted.",
    "I'm already at max hype. But the gesture is understood.",
]

BOT_LOOP_COMPLIMENT_REPLIES: list[str] = [
    "I'm not hyping up a bot. Pick a human.",
    "Bot-to-bot compliments? That's not a thing here.",
    "Bots don't need hype. Try a real person.",
    "I only compliment humans. Try again.",
]

# ---------------------------------------------------------------------------
# Spam / repeat-use wrappers (when user requests prediction multiple times)
# ---------------------------------------------------------------------------

SPAM_WRAPPERS: list[str] = [
    "Already told you — {prediction}",
    "Nothing changed in the last five minutes. {prediction}",
    "Same answer as before: {prediction}",
    "You already have your prediction. {prediction}",
    "The cosmos doesn't update that fast. {prediction}",
    "One prediction per customer. {prediction}",
    "Fate doesn't change on demand. {prediction}",
]

# ---------------------------------------------------------------------------
# Level-up messages — English, occasional wit
# ---------------------------------------------------------------------------

LEVEL_UP_MESSAGES: list[str] = [
    "{user} reached Level {level}! The server has taken note. Reluctantly.",
    "{user} is now Level {level}. Keep going.",
    "Level {level} for {user}! The cosmos updated your file.",
    "{user} hit Level {level}! Dedication noted. Barely, but noted.",
    "{user} — Level {level} achieved. The leaderboard shifts.",
    "Level {level}! {user} is not messing around.",
    "{user} unlocked Level {level}. Progress is progress.",
    "Level {level} for {user}. Even the server is mildly impressed.",
]
