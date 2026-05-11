import random
from enum import Enum


class WheelSegment(Enum):
    LADDER_OF_CHANCE = "Ladder of Chance"
    TAX_SEASON = "Tax Season"
    GIFT_OF_THE_BAMBOOZLE = "Gift of the Bamboozle"
    FULL_REVERSAL = "Full Reversal"
    MYSTIC_MIST = "Mystic Mist"
    BONUS_ROUND = "Bonus Round"
    WANGO_AGAIN = "Wango Again"
    MONKEYS_CHOICE = "Monkey's Choice"


_ALL_SEGMENTS = list(WheelSegment)
_SEGMENTS_NO_MONKEY = [s for s in WheelSegment if s != WheelSegment.MONKEYS_CHOICE]

WHEEL_FLAVOUR: dict[WheelSegment, str] = {
    WheelSegment.LADDER_OF_CHANCE: "🐒 The wheel lands on... **LADDER OF CHANCE!** The Golden Monkey stirs...",
    WheelSegment.TAX_SEASON: "💸 The wheel lands on... **TAX SEASON!** The IRS of chaos has arrived.",
    WheelSegment.GIFT_OF_THE_BAMBOOZLE: "🎁 The wheel lands on... **GIFT OF THE BAMBOOZLE!** Someone's about to be robbed.",
    WheelSegment.FULL_REVERSAL: "🔃 The wheel lands on... **FULL REVERSAL!** Everything changes. EVERYTHING.",
    WheelSegment.MYSTIC_MIST: "🌫️ The wheel lands on... **MYSTIC MIST!** Can you even trust your own eyes?",
    WheelSegment.BONUS_ROUND: "🎉 The wheel lands on... **BONUS ROUND!** The universe smiles upon you!",
    WheelSegment.WANGO_AGAIN: "😱 The wheel lands on... **WANGO AGAIN!** Draw another Wicked Wango Card!",
    WheelSegment.MONKEYS_CHOICE: "🐵 The wheel lands on... **MONKEY'S CHOICE!** The monkey decides your fate!",
}

SPIN_SUSPENSE = [
    "🎡 The wheel begins to spin...",
    "🌀 It's gaining speed...",
    "😬 Round and round and round...",
    "🎯 It's slowing down...",
    "⏳ Almost... almost...",
]


def spin_wheel() -> WheelSegment:
    return random.choice(_ALL_SEGMENTS)


def monkey_choice_segment() -> WheelSegment:
    return random.choice(_SEGMENTS_NO_MONKEY)
