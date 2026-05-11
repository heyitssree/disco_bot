# ── OpenTDB SFW category whitelist ────────────────────────────────────────────
# Only fetch from these verified-clean categories.
# Full list: https://opentdb.com/api_category.php
# Excluded: all Entertainment/* categories (can reference mature films/games)
# and Celebrities (can surface tabloid content).
SAFE_OPENTDB_CATEGORY_IDS = [
    9,   # General Knowledge
    17,  # Science & Nature
    18,  # Science: Computers
    19,  # Science: Mathematics
    20,  # Mythology
    21,  # Sports
    22,  # Geography
    23,  # History
    25,  # Art
    27,  # Animals
    28,  # Vehicles
    30,  # Science: Gadgets
]

# ── Bamboozle Rule content filter ─────────────────────────────────────────────
# Set to True to run the Bamboozle Rule text through the profanity filter before
# posting it to the channel. Set to False to disable filtering entirely.
BAMBOOZLE_RULE_FILTER_ENABLED = True

# ── Game mechanics ─────────────────────────────────────────────────────────────
STARTING_POINTS = 500
CORRECT_ANSWER_POINTS = 100
WRONG_ANSWER_POINTS = -50
TIMEOUT_POINTS = -100
LUCKY_LLAMA_BONUS = 50
DOUBLE_DOWN_BONUS = 200
DOUBLE_DOWN_PENALTY = -200
REVERSE_UNO_PENALTY = 100
SOMBRERO_EXTRA_PENALTY = 25
BONUS_ROUND_POINTS = 150
GIFT_STEAL_AMOUNT = 100
TAX_RATE = 0.20
TAX_MINIMUM = 10
GOLDEN_MONKEY_BELLY = 300
GOLDEN_MONKEY_TAIL = -200
WANGO_AGAIN_WHEEL_DEPTH_LIMIT = 1
DOUBLE_WANGO_CHAIN_LIMIT = 2
MAX_SINGLE_SWING_FIXED = 300
QUESTION_TIMEOUT_SECONDS = 30
GOLDEN_MONKEY_TIMEOUT_SECONDS = 15
BAMBOOZLE_RULE_INPUT_TIMEOUT_SECONDS = 60
SWITCHEROO_PICK_TIMEOUT_SECONDS = 20
TOTAL_ROUNDS = 5
MIST_TURN_DURATION = 2

# ── Bamboozle Rule enforcement ─────────────────────────────────────────────────
BAMBOOZLE_SPEED_TAX_THRESHOLD_SECONDS = 5
BAMBOOZLE_SLOW_BURN_THRESHOLD_SECONDS = 20
BAMBOOZLE_KARMA_TAX_POINTS = 50
BAMBOOZLE_UNDERDOG_BOOST_POINTS = 50
BAMBOOZLE_HOT_STREAK_BONUS = 75
BAMBOOZLE_LUCKY_LAST_BONUS = 100
BAMBOOZLE_TIMEOUT_TERROR_COST = 200
BAMBOOZLE_SPEED_TAX_PENALTY = 25
BAMBOOZLE_SLOW_BURN_EXTRA_PENALTY = 50
