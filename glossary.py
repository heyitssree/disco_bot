# glossary.py - Trivandrum Manglish slang, food, landmarks, and expressions
# Structured for prompt injection into Gemini

# Expressions with meanings
EXPRESSIONS = {
    "Appi": "Term of endearment for a baby/child",
    "Kili poyi": "Literally 'the bird flew away' - when someone is confused or shocked",
    "Oola": "Useless, pathetic, or poor quality",
    "Shokam": "Sad, boring, or pathetic situation",
    "Chumma": "Simply, for no reason",
    "Eda": "Informal 'hey' (used among friends)",
    "Edi": "Informal 'hey' (used among friends)",
    "Vayye?": "Are you not well? or Can't you do it? (often sarcastic)",
    "Pillacha": "Respectful address for older man (shopkeeper/neighbor)",
    "Kidilam": "Absolutely awesome or fantastic",
    "Kidu": "Absolutely awesome or fantastic",
    "Thirontharam": "Local pronunciation of Thiruvananthapuram",
}

# Landmarks and locations
LANDMARKS = [
    "Palayam",
    "Thampanoor",
    "KD Puram",
    "Vellayambalam",
    "Kowdiar",
    "Chalai Market",
    "Ponmudi",
    "Sreekaryam",
    "Kazhakkoottam",
    "Museum Campus",
    "Technopark",
    "Connemara Market",
    "East Fort",
    "Sasthamangalam",
    "Pongumoodu",
]

# Food and eateries
FOOD = [
    "Boli and Paal Payasam",
    "Kethel's Chicken (Rahmaniya)",
    "Zam Zam Palayam",
    "Indian Coffee House Thampanoor",
    "Sree Muruka Cafe",
    "Rasavadai",
    "Pazham Pori and Beef Roast",
    "Maha Boly",
]

# Culture references
CULTURE = [
    "IFFK (International Film Festival of Kerala)",
    "Tagore Theatre",
    "Attukal Pongala",
    "Ramachandran Textiles East Fort",
    "Technopark",
    "Thattukada (street tea stall)",
    "KSRTC bus stand",
    "Napier Museum",
    "Kanakakkunnu Palace",
]

# All expressions as a formatted string for prompts
def get_glossary_text():
    """Returns formatted glossary for prompt injection."""
    exprs = ", ".join([f"{k} ({v})" for k, v in EXPRESSIONS.items()])
    landmarks = ", ".join(LANDMARKS)
    foods = ", ".join(FOOD)
    culture = ", ".join(CULTURE)
    return f"""
EXPRESSIONS (Trivandrum Manglish): {exprs}
LANDMARKS: {landmarks}
FOOD & EATERIES: {foods}
CULTURE: {culture}
"""