# curses.py - Curse word trigger list and passive reply templates

# Curse words for trigger detection (case-insensitive matching)
CURSE_WORDS = [
    "pottan",
    "potti",
    "mandan",
    "mandabuddhi",
    "modan",
    "vattan",
    "vatti",
    "oola",
    "thallippoli",
    "vivaramillathavan",
    "buddhisunyan",
    "madiyan",
    "madichi",
    "vayadi",
    "kozhi",
    "kunjamma",
    "paala",
    "enapechi",
    "kanda mrigam",
    "kochu kalla",
    "dushtan",
    "chunk",
    "loose",
    "kumpidi",
    "pokri",
    "veruppirayan",
    "alavalathi",
    "shasi",
    "gundabiju",
    "kuzhappakkaran",
]

# Templates for "doomed future" predictions (randomly selected)
DOOMED_PREDICTIONS = [
    "Nee oru {curse} aanu. Ponmudi mist-pole ninte future also unclear. Kili poyi!",
    "Eda {user}, ninte stars say you will get stuck in KD Puram traffic for 45 minutes on a Tuesday with no charge in your phone. Chumma vayadi ayikko.",
    "Aiyo {user}, nee oru {curse} aanu. This week avoid Chalai Market or you will lose your wallet AND your sense of direction.",
    "{user} ya, AstRobot is watching. Your stars say you will wait at Thampanoor bus stand for 2 hours for a bus that never comes. Shokam aanu.",
    "Eda {user}, ninte rashi meedhi KSRTC bus thana. You will get stuck in Museum Campus traffic during Attukal Pongala. Padmanabha swami has noted your recent behaviour.",
    "{user}, nee oru {curse} aanu. Your palm lines say you will spend all evening at Indian Coffee House waiting for a cutlet that was sold out. Chumma.",
    "Aiyo {user}, ninte future is darker than Chalai Market at night. You will go to Zam Zam and find out they ran out of shawarma. Kidilam aayirunnu!",
]

# Templates for curse-back replies (randomly selected)
CURSE_BACK_REPLIES = [
    "Aiyo {user}, nee oru {curse} aanu. Ponmudi mist-pole ninte future also unclear. Kili poyi!",
    "Eda {user}, nee oru {curse}! Ninte aadu (star) is sitting in the 7th house like a KSRTC bus at Thampanoor — not moving.",
    "{user}, nee oru {curse} aanu. Chumma vayadi ayikko. Padmanabha swami is judging you.",
    "Aiyo {user}, nee oru {curse}! Go drink chaya at thattukada and think about what you said. Oola.",
    "{user}, nee oru {curse} aanu. Ninte future darker than KD Puram at 7pm. Shokam.",
]

def get_random_curse():
    """Returns a random curse word from the list."""
    import random
    return random.choice(CURSE_WORDS)

def get_random_doomed_prediction(username: str) -> str:
    """Returns a random doomed prediction template filled with username and a curse word."""
    import random
    template = random.choice(DOOMED_PREDICTIONS)
    curse = get_random_curse()
    return template.format(user=username, curse=curse)

def get_random_curse_back(username: str) -> str:
    """Returns a random curse-back template filled with username and a curse word."""
    import random
    template = random.choice(CURSE_BACK_REPLIES)
    curse = get_random_curse()
    return template.format(user=username, curse=curse)