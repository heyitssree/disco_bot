"""Rotating witty commentary pools for Bamboozled!"""
import random


_TURN_INTROS = [
    "🎬 It's <@{pid}>'s turn. The studio lights dim. The audience holds its collective breath.",
    "🎬 <@{pid}> steps into the spotlight. No turning back. The question is loading.",
    "🎭 All eyes on <@{pid}>! The pressure is palpable. The snacks are getting cold.",
    "🎯 The universe has a question for <@{pid}>. Ready or not, here it comes.",
    "🎬 <@{pid}> strides to the podium. The audience forgets to breathe.",
    "📺 Lights, camera, <@{pid}>! Your 30 seconds of fame begin now.",
    "🎪 <@{pid}> has arrived at the hot seat! Even the interns stopped scrolling.",
    "🔔 Ding ding — it's <@{pid}>'s turn! The question has been waiting. It's impatient.",
    "🎭 <@{pid}>, the studio needs you. The question *definitely* needs you.",
    "🎬 Fate has pointed its finger at <@{pid}>. The question approaches like a freight train.",
    "🎯 <@{pid}> steps up to the plate. The crowd stirs. The question is already judging.",
    "🎪 And now, ladies and gentlemen — <@{pid}>! *polite applause, mild dread*",
]

_CORRECT_MSGS = [
    "✅ **CORRECT!** **{pname}** nails it for **+{pts}** points! The answer was **{correct}**. *(Score: {score:,})*",
    "✅ **YES!!** It was **{correct}** — and **{pname}** knew it! **+{pts}** points! *(Score: {score:,})*",
    "✅ **ABSOLUTELY RIGHT!** The answer: **{correct}**. **{pname}** earns **+{pts}** points! *(Score: {score:,})*",
    "✅ **CORRECT!** **{pname}** — turns out they actually know things! Answer: **{correct}**. **+{pts}** pts! *(Score: {score:,})*",
    "✅ **BRILLIANT!** **{pname}** got it! **{correct}** — exactly right! **+{pts}** points! *(Score: {score:,})*",
    "✅ **{pname}** GETS IT! Answer: **{correct}**. **+{pts}** points! The llama nods approvingly. *(Score: {score:,})*",
    "✅ **SPOT ON!** **{correct}** was the answer. **{pname}** earns **+{pts}** points! *(Score: {score:,})*",
    "✅ **CORRECT!** **{correct}** — yes! **{pname}** earns **+{pts}** points! *(Score: {score:,})*",
    "✅ **{pname}** READ THE ROOM! The answer was **{correct}**. **+{pts}** points! *(Score: {score:,})*",
    "✅ THE JUDGES SAY YES! **{correct}** is correct. **{pname}** gets **+{pts}** points! *(Score: {score:,})*",
]

_WRONG_MSGS = [
    "❌ **WRONG!** The correct answer was **{correct}**. **{pname}** loses **{pts}** pts{sombrero}{dj}. *(Score: {score:,})*",
    "❌ **NOPE!** It was **{correct}**. **{pname}** drops **{pts}** pts{sombrero}{dj}. *(Score: {score:,})*",
    "❌ **OH DEAR.** **{correct}** was right there. **{pname}** loses **{pts}** pts{sombrero}{dj}. *(Score: {score:,})*",
    "❌ **NOT EVEN CLOSE!** Answer: **{correct}**. **{pname}** pays **{pts}** pts{sombrero}{dj}. *(Score: {score:,})*",
    "❌ **BZZT!** The crowd groans. It was **{correct}**. **{pname}** loses **{pts}** pts{sombrero}{dj}. *(Score: {score:,})*",
    "❌ **INCORRECT!** **{correct}** was the answer. **{pname}** suffers **{pts}** pts{sombrero}{dj}. *(Score: {score:,})*",
    "❌ **WRONG!** **{correct}** — that's what they should have said. **{pname}** loses **{pts}** pts{sombrero}{dj}. *(Score: {score:,})*",
    "❌ The judges shake their heads. Answer: **{correct}**. **{pname}** drops **{pts}** pts{sombrero}{dj}. *(Score: {score:,})*",
    "❌ **YIKES.** It was **{correct}**. **{pname}** is down **{pts}** pts{sombrero}{dj}. *(Score: {score:,})*",
    "❌ **{pname}** confidently got it wrong. The answer was **{correct}**. **{pts}** pts{sombrero}{dj}. *(Score: {score:,})*",
]

_TIMEOUT_MSGS = [
    "⏰ **TIME'S UP** for **{pname}**! The answer was **{correct}**. **{pts:,} pts** gone{terror}. *(Score: {score:,})*",
    "⏰ **TIMED OUT!** It was **{correct}**. **{pname}** stood there silently. **{pts:,} pts** deducted{terror}. *(Score: {score:,})*",
    "⏰ **THE BUZZER SOUNDS!** NOTHING from **{pname}**! Answer: **{correct}**. **{pts:,} pts** gone{terror}. *(Score: {score:,})*",
    "⏰ **SILENCE IS NOT AN ANSWER, {pname}!** It was **{correct}**. **{pts:,} pts** lost{terror}. *(Score: {score:,})*",
    "⏰ **TIME EXPIRED!** **{correct}** was the answer. **{pname}** loses **{pts:,} pts**{terror}. *(Score: {score:,})*",
    "⏰ **{pname}** froze like a deer in headlights. Answer: **{correct}**. **{pts:,} pts** docked{terror}. *(Score: {score:,})*",
]

_FORFEIT_MSGS = [
    "🏳️ **{pname}** waves the white flag! Answer was **{correct}**. **{pts:,} pts** gone. *(Score: {score:,})*",
    "🏳️ **{pname}** chose to sit this one out. Answer: **{correct}**. **{pts:,} pts**. *(Score: {score:,})*",
    "🏳️ **FORFEIT!** **{pname}** taps out. The answer was **{correct}**. **{pts:,} pts** deducted. *(Score: {score:,})*",
    "🏳️ **{pname}** retreats! Strategically dubious. Answer was **{correct}**. **{pts:,} pts** lost. *(Score: {score:,})*",
]

_MIST_CORRECT_MSGS = [
    "✅ **{pname}** answered... *something. Points may have been awarded.*",
    "✅ **{pname}** pressed a button. The Mist approves. Probably.",
    "✅ *In the Mist, a correct answer echoes. **{pname}** gains something.*",
    "✅ **{pname}** answered. The cosmos react. Points have moved in someone's favour.",
    "✅ *Scores shift beneath the Mist.* **{pname}** did a thing.",
]

_MIST_WRONG_MSGS = [
    "❌ **{pname}** answered... *Hmm. Sure. Points have been adjusted. Maybe downward.*",
    "❌ **{pname}** chose something. The Mist judged silently. Points were probably lost.",
    "❌ *Something happened. The Mist absorbed the consequences.*",
    "❌ **{pname}** answered something. The cosmos didn't love it. Points adjusted.",
    "❌ *The Mist conceals the damage.* **{pname}** has a bad feeling about this.",
]

_MIST_TIMEOUT_MSGS = [
    "⏰ **{pname}** ran out of time... somewhere in the Mist, points vanished.",
    "⏰ **TIME'S UP** for **{pname}**! The Mist absorbed the penalty.",
    "⏰ **{pname}** said nothing. In the Mist, silence has a price.",
    "⏰ *The clock expired on **{pname}**. The Mist does not forgive tardiness.*",
]

_CHANCE_CARD_DRAW_PROMPTS = [
    "🃏 **{pname}**, your fate awaits! Draw your **Chance Card**! *(auto-draws in {timeout}s)*",
    "🃏 Something interesting is coming, **{pname}**... probably. Draw your card! *(auto-draws in {timeout}s)*",
    "🃏 The Chance Card is ready, **{pname}**. Are you? *(auto-draws in {timeout}s)*",
    "🃏 **{pname}**, what does fortune have in store? Draw your **Chance Card**! *(auto-draws in {timeout}s)*",
    "🃏 The card is face-down, **{pname}**. Flip it! *(auto-draws in {timeout}s)*",
    "🃏 **{pname}**, reach out and take your destiny! Or just click the button. *(auto-draws in {timeout}s)*",
]

_WANGO_CARD_DRAW_PROMPTS = [
    "🎴 **{pname}**... you have to draw a **Wicked Wango Card**. Sorry. *(auto-draws in {timeout}s)*",
    "🎴 The chaos deck awaits, **{pname}**. Don't fight it. *(auto-draws in {timeout}s)*",
    "🎴 **{pname}**, please draw your **Wicked Wango Card**. We're all watching. *(auto-draws in {timeout}s)*",
    "🎴 The **Wicked Wango Card** is waiting, **{pname}**. Specifically for you. *(auto-draws in {timeout}s)*",
    "🎴 It could be worse, **{pname}**. (It could also be worse.) Draw your card. *(auto-draws in {timeout}s)*",
    "🎴 **{pname}** must face the Wango deck. The deck is not nervous. *(auto-draws in {timeout}s)*",
]

_SPIN_PROMPTS = [
    "🎡 Destiny is one click away, **{pname}**... *(auto-spins in {timeout}s)*",
    "🎡 The Wheel of Mayhem is ready. **{pname}**, do you dare? *(auto-spins in {timeout}s)*",
    "🎡 **{pname}**, spin the wheel. Embrace the chaos. *(auto-spins in {timeout}s)*",
    "🎡 The Wheel hungers. Feed it, **{pname}**. *(auto-spins in {timeout}s)*",
    "🎡 **{pname}** approaches the Wheel of Mayhem. The audience leans forward. *(auto-spins in {timeout}s)*",
    "🎡 Time to learn your fate, **{pname}**. The Wheel is already judging you. *(auto-spins in {timeout}s)*",
]

_SEND_TO_WHEEL = [
    "🎡 **{pname}** is heading straight to the **WHEEL OF MAYHEM!**",
    "🎡 **{pname}** takes the dreaded walk to the **WHEEL OF MAYHEM!**",
    "🎡 The Wheel calls! **{pname}** must face the **WHEEL OF MAYHEM!**",
    "🎡 Oh no. **{pname}** is off to the **WHEEL OF MAYHEM!** Pray for them.",
    "🎡 It's Wheel time for **{pname}**! The **WHEEL OF MAYHEM** awaits!",
]

_ENDGAME_WRAP = [
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🎬🎬🎬 **THAT'S A WRAP ON ROUND {rounds}!** 🎬🎬🎬\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n*The studio audience ERUPTS. The confetti cannons fire. Somewhere, a llama weeps with joy.*",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🎬🎬🎬 **AND... CUT! ROUND {rounds} IS DONE!** 🎬🎬🎬\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n*The director yells CUT. The audience goes absolutely feral. The interns scatter.*",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🎬🎬🎬 **GAME OVER! {rounds} ROUNDS OF CHAOS!** 🎬🎬🎬\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n*The Bamboozle machine winds down. Confetti everywhere. Someone's crying. It's fine.*",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🎬🎬🎬 **THAT'S ALL {rounds} ROUNDS, FOLKS!** 🎬🎬🎬\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n*The credits roll. The chaos ends. The studio is a mess. Nobody regrets a thing.*",
]

_WINNER_ANNOUNCES = [
    "🏆🏆🏆 **THE WINNER IS... {champion}!!!** 🏆🏆🏆\nFinal score: **{score:,} points!**\n\n*{champion} is carried off on the shoulders of a grateful nation.*",
    "🏆🏆🏆 **BAMBOOZLED CHAMPION: {champion}!!!** 🏆🏆🏆\nFinal score: **{score:,} points!**\n\n*The crowd goes absolutely wild. {champion} has done the impossible.*",
    "🏆🏆🏆 **{champion} WINS BAMBOOZLED!!!** 🏆🏆🏆\nFinal score: **{score:,} points!**\n\n*{champion} stands victorious amidst the ruins of everyone else's scores.*",
    "🏆🏆🏆 **AND YOUR WINNER IS... {champion}!!!** 🏆🏆🏆\nFinal score: **{score:,} points!**\n\n*A studio audience somewhere screams. Llamas nod. History is made.*",
]


def turn_intro(player_id: int) -> str:
    return random.choice(_TURN_INTROS).format(pid=player_id)


def correct(pname: str, pts: int, score: int, correct_text: str) -> str:
    return random.choice(_CORRECT_MSGS).format(pname=pname, pts=pts, score=score, correct=correct_text)


def wrong(pname: str, pts: int, score: int, correct_text: str, sombrero: str = "", dj: str = "") -> str:
    return random.choice(_WRONG_MSGS).format(
        pname=pname, pts=abs(pts), score=score, correct=correct_text, sombrero=sombrero, dj=dj
    )


def timeout(pname: str, pts: int, score: int, correct_text: str, terror: str = "") -> str:
    return random.choice(_TIMEOUT_MSGS).format(
        pname=pname, pts=abs(pts), score=score, correct=correct_text, terror=terror
    )


def forfeit(pname: str, pts: int, score: int, correct_text: str) -> str:
    return random.choice(_FORFEIT_MSGS).format(pname=pname, pts=abs(pts), score=score, correct=correct_text)


def correct_mist(pname: str) -> str:
    return random.choice(_MIST_CORRECT_MSGS).format(pname=pname)


def wrong_mist(pname: str) -> str:
    return random.choice(_MIST_WRONG_MSGS).format(pname=pname)


def timeout_mist(pname: str) -> str:
    return random.choice(_MIST_TIMEOUT_MSGS).format(pname=pname)


def chance_card_draw_prompt(pname: str, timeout_secs: int) -> str:
    return random.choice(_CHANCE_CARD_DRAW_PROMPTS).format(pname=pname, timeout=timeout_secs)


def wango_card_draw_prompt(pname: str, timeout_secs: int) -> str:
    return random.choice(_WANGO_CARD_DRAW_PROMPTS).format(pname=pname, timeout=timeout_secs)


def spin_prompt(pname: str, timeout_secs: int) -> str:
    return random.choice(_SPIN_PROMPTS).format(pname=pname, timeout=timeout_secs)


def send_to_wheel(pname: str) -> str:
    return random.choice(_SEND_TO_WHEEL).format(pname=pname)


def endgame_wrap(rounds: int) -> str:
    return random.choice(_ENDGAME_WRAP).format(rounds=rounds)


def winner_announce(champion: str, score: int) -> str:
    return random.choice(_WINNER_ANNOUNCES).format(champion=champion, score=score)
