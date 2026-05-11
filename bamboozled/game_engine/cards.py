import random
from enum import Enum


class ChanceCard(Enum):
    LUCKY_LLAMA = "Lucky Llama"
    SWITCHEROO = "Switcheroo"
    DOUBLE_DOWN = "Double Down"
    SPIN_THE_WHEEL = "Spin the Wheel"
    GOLDEN_PASS = "Golden Pass"
    BAMBOOZLE = "Bamboozle"


class WangoCard(Enum):
    WANGO_CLASSIC = "Wango Classic"
    THE_SILENCE = "The Silence"
    REVERSE_UNO = "Reverse Uno"
    THE_SOMBRERO = "The Sombrero"
    DOUBLE_WANGO = "Double Wango"
    MYSTIC_MIST = "Mystic Mist"


def draw_chance_card() -> ChanceCard:
    return random.choice(list(ChanceCard))


def draw_wango_card() -> WangoCard:
    return random.choice(list(WangoCard))


CHANCE_CARD_FLAVOUR: dict[ChanceCard, str] = {
    ChanceCard.LUCKY_LLAMA: "🦙 **LUCKY LLAMA!** The llama blesses you with bonus points!",
    ChanceCard.SWITCHEROO: "🔀 **SWITCHEROO!** Time to mess with someone's score...",
    ChanceCard.DOUBLE_DOWN: "⬇️⬇️ **DOUBLE DOWN!** Another question. More risk. More glory.",
    ChanceCard.SPIN_THE_WHEEL: "🎡 **SPIN THE WHEEL!** The Wheel of Mayhem calls your name!",
    ChanceCard.GOLDEN_PASS: "🎫 **GOLDEN PASS!** A get-out-of-wango-free card! Guard it with your life!",
    ChanceCard.BAMBOOZLE: "🃏 **THE BAMBOOZLE!** You get to write THE LAW. Choose wisely... or don't.",
}

WANGO_CARD_FLAVOUR: dict[WangoCard, str] = {
    WangoCard.WANGO_CLASSIC: "🎡 **WANGO CLASSIC!** Straight to the Wheel of Mayhem with you!",
    WangoCard.THE_SILENCE: "🤫 **THE SILENCE!** You shall not speak... or play... next turn.",
    WangoCard.REVERSE_UNO: "🔄 **REVERSE UNO!** Someone near you is about to feel your pain!",
    WangoCard.THE_SOMBRERO: "🪅 **THE SOMBRERO!** Someone's getting a fashionable new hat...",
    WangoCard.DOUBLE_WANGO: "🃏🃏 **DOUBLE WANGO!!** TWO MORE CARDS. The chaos multiplies!",
    WangoCard.MYSTIC_MIST: "🌫️ **MYSTIC MIST!** Reality becomes... optional.",
}
