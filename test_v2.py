# test_v2.py - AstRobot V2 comprehensive feature tests
import os, sys, duckdb, random
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from dotenv import load_dotenv

load_dotenv()

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    results.append((name, condition))

def section(title):
    print(f"\n{'='*55}\n  {title}\n{'='*55}")

# ── 1. SCHEMA ──────────────────────────────────────────────
section("1. Schema / DuckDB")
import schema

conn = duckdb.connect(":memory:")
schema._create_tables(conn)

tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
for t in ["predictions_cache","user_stats","user_prediction_history","curse_logs","daily_omens"]:
    check(f"Table '{t}' exists", t in tables)

# upsert + get user
schema.upsert_user(conn, 1001, "Sree")
profile = schema.get_user_profile(conn, 1001)
check("upsert_user creates new user", profile is not None)
check("Default Rashi is None", profile["rashi"] is None)
check("Default Boli Points = 0", profile["boli_points"] == 0)

# assign rashi
schema.upsert_user(conn, 1001, "Sree", rashi="Chingam (Leo)")
profile = schema.get_user_profile(conn, 1001)
check("Rashi assigned correctly", profile["rashi"] == "Chingam (Leo)")

# boli points
schema.update_boli_points(conn, 1001, 10)
schema.update_boli_points(conn, 1001, 5)
profile = schema.get_user_profile(conn, 1001)
check("Boli Points accumulate correctly", profile["boli_points"] == 15)

# prediction cache
schema.save_prediction(conn, "astro", "Aiyo {user}, shokam day at KD Puram.", user_id=1001)
schema.save_prediction(conn, "astro", "Aiyo {user}, shokam day at KD Puram.")  # duplicate
count = conn.execute("SELECT COUNT(*) FROM predictions_cache").fetchone()[0]
check("Duplicate prediction not saved twice", count == 1)

cached = schema.get_cached_prediction(conn, "astro", min_count=1)
check("get_cached_prediction returns template", cached is not None and "{user}" in cached)

# user prediction history
schema.save_user_prediction(conn, 1001, "You will miss the KSRTC bus.")
schema.save_user_prediction(conn, 1001, "Your wallet will disappear at Chalai.")
schema.save_user_prediction(conn, 1001, "Zam Zam ran out of shawarma for you.")
history = schema.get_last_n_predictions(conn, 1001, n=3)
check("get_last_n_predictions returns 3 items", len(history) == 3)
check("Newest prediction first", "Zam Zam" in history[0])

# increment prediction count
schema.increment_prediction_count(conn, 1001)
profile = schema.get_user_profile(conn, 1001)
check("prediction_count increments", profile["prediction_count"] == 1)

# curse log
schema.log_curse(conn, 1001, "Sree", "pottan")
count = conn.execute("SELECT COUNT(*) FROM curse_logs").fetchone()[0]
check("log_curse saves entry", count == 1)

# leaderboard
schema.upsert_user(conn, 1002, "Arun")
schema.update_boli_points(conn, 1002, 50)
leaders = schema.get_leaderboard(conn, limit=5)
check("Leaderboard returns sorted results", leaders[0]["username"] == "Arun")

# daily omen
check("get_todays_omen returns None initially", schema.get_todays_omen(conn) is None)
schema.save_daily_omen(conn, "Namaskaram Thirontharam! Today is shokam.", "Chalai Market")
check("get_todays_omen returns today's omen", schema.get_todays_omen(conn) is not None)

# table counts
counts = schema.get_table_counts(conn)
check("get_table_counts returns all 5 tables", len(counts) == 5)

# export csv
schema.export_stats_csv(conn, "data/test_export.csv")
check("export_stats_csv creates file", os.path.exists("data/test_export.csv"))
os.remove("data/test_export.csv")

# ── 2. GEMINI SERVICE ──────────────────────────────────────
section("2. GeminiService — Dual-Key + Circuit Breaker")
from services.gemini_service import GeminiService

# No keys → ValueError
try:
    GeminiService(None, None, conn)
    check("No keys raises ValueError", False)
except ValueError:
    check("No keys raises ValueError", True)

# Only free key init
svc = GeminiService(free_api_key="fake_free", paid_api_key=None, db_conn=conn)
check("Init with only free key OK", svc._free_client is not None and svc._paid_client is None)

# Only paid key init
svc2 = GeminiService(free_api_key=None, paid_api_key="fake_paid", db_conn=conn)
check("Init with only paid key OK", svc2._paid_client is not None and svc2._free_client is None)

# Circuit breaker: force 3 failures → opens
svc3 = GeminiService(free_api_key=None, paid_api_key="fake_paid", db_conn=conn)
svc3.failure_count = 2
with patch.object(svc3, "_try_key", return_value=None):
    svc3.call("test", "sys")
check("Circuit opens after 3 failures", svc3.is_circuit_open)
check("open_until is set", svc3.open_until is not None)

# Circuit auto-reset when timer expires
svc3.open_until = datetime.now() - timedelta(seconds=1)
check("Circuit auto-resets when timer expired", not svc3.is_circuit_open)
check("failure_count reset after auto-reset", svc3.failure_count == 0)

# Free key tried first
svc4 = GeminiService(free_api_key="f", paid_api_key="p", db_conn=conn)
call_order = []
def mock_try_key(client, prompt, system_prompt, key_name):
    call_order.append(key_name)
    return "response" if key_name == "free" else None
with patch.object(svc4, "_try_key", side_effect=mock_try_key):
    result = svc4.call("p", "s")
check("Free key attempted first", call_order[0] == "free")
check("Paid key not called when free succeeds", len(call_order) == 1)
check("active_key set to 'free' on success", svc4.active_key == "free")

# Free fails → paid used
call_order2 = []
def mock_try_key2(client, prompt, system_prompt, key_name):
    call_order2.append(key_name)
    return "paid_response" if key_name == "paid" else None
with patch.object(svc4, "_try_key", side_effect=mock_try_key2):
    result = svc4.call("p", "s")
check("Falls back to paid when free fails", "paid" in call_order2)
check("active_key set to 'paid' on fallback", svc4.active_key == "paid")

# status_dict
status = svc4.status_dict()
check("status_dict has required keys",
    all(k in status for k in ["circuit_open","failure_count","active_key","free_key_available","paid_key_available"]))

# ── 3. API MANAGER ─────────────────────────────────────────
section("3. ApiManager — Rate Limiter + Cache Fallback")
from services.api_manager import ApiManager

mock_gemini = MagicMock()
mock_gemini.is_circuit_open = False
mock_gemini.call.return_value = "Aiyo Mone, shokam stars today at Thampanoor."

mgr = ApiManager(mock_gemini, conn, rpm_limit=3, free_tier_mode=True)
check("ApiManager initialises OK", mgr.rpm_limit == 3)
check("free_tier_mode flag set", mgr.free_tier_mode is True)

# Normal call goes through
schema.save_prediction(conn, "astro", "Aiyo {user}, shokam at Palayam.", user_id=1001)
text, from_cache = mgr.call("p", "s", "astro", "Sree")
check("Normal call returns Gemini result", not from_cache)

# Hit rate limit (exhaust remaining 2 calls)
mgr.call("p", "s", "astro", "Sree")
mgr.call("p", "s", "astro", "Sree")
text4, from_cache4 = mgr.call("p", "s", "astro", "Sree")
check("Rate limit triggers cache fallback", from_cache4)

# Circuit open → cache
mgr2 = ApiManager(mock_gemini, conn, rpm_limit=100)
mock_gemini.is_circuit_open = True
text5, from_cache5 = mgr2.call("p", "s", "astro", "Sree")
check("Circuit open triggers cache fallback", from_cache5)
mock_gemini.is_circuit_open = False

# Window reset
mgr3 = ApiManager(mock_gemini, conn, rpm_limit=2)
mgr3._request_count = 2
mgr3._window_start = datetime.now() - timedelta(seconds=61)
mgr3.call("p", "s", "astro", "Sree")
check("Window resets after 60s", mgr3._request_count == 1)

status = mgr.status_dict()
check("status_dict has required keys",
    all(k in status for k in ["rpm_used","rpm_limit","window_resets_in_seconds","free_tier_mode"]))

# ── 4. GLOSSARY ────────────────────────────────────────────
section("4. Glossary — Time Context + Weather")
from glossary import (
    get_time_context, get_current_weather_context,
    get_daily_weather_forecast, _decode_wmo, RASHIS, LANDMARKS
)

# Time periods
from unittest.mock import patch as mpatch
from datetime import datetime as dt

periods = {6:"morning", 12:"noon", 15:"afternoon", 17:"evening", 20:"night", 23:"late night"}
for hour, expected in periods.items():
    fake_now = dt(2026, 4, 24, hour, 0, tzinfo=timezone(timedelta(hours=5,minutes=30)))
    with mpatch("glossary.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: dt(*a, **kw)
        ctx = get_time_context()
    check(f"Time period at {hour:02d}:00 -> '{expected}'", ctx["period"] == expected)

# WMO code decoding
check("WMO 0 -> clear sky", "clear" in _decode_wmo(0))
check("WMO 51 -> drizzle", "drizzle" in _decode_wmo(51))
check("WMO 63 -> rain", "rain" in _decode_wmo(63).lower())
check("WMO 95 -> thunderstorm", "thunderstorm" in _decode_wmo(95))
check("Unknown WMO code -> fallback string", len(_decode_wmo(999)) > 0)

# Live weather (with fallback tolerance)
weather_str = get_current_weather_context()
check("get_current_weather_context returns non-empty string", isinstance(weather_str, str) and len(weather_str) > 5)

forecast = get_daily_weather_forecast()
check("get_daily_weather_forecast returns dict", isinstance(forecast, dict))
check("Forecast has max_temp", "max_temp" in forecast)
check("Forecast has rain_mm", "rain_mm" in forecast)
check("Forecast has condition string", isinstance(forecast.get("condition"), str))

check("RASHIS list has 12 entries", len(RASHIS) == 12)
check("LANDMARKS list non-empty", len(LANDMARKS) > 5)

# Weather 15-min cache
from glossary import _weather_cache
check("Weather cache populated after call", _weather_cache is not None)

# ── 5. PROMPTS ─────────────────────────────────────────────
section("5. Prompts — Templates & Composition")
from prompts import (
    get_astro_prompt, get_curse_prompt, get_qa_prompt,
    get_daily_omen_prompt, get_time_aware_system_prompt, FALLBACK_MESSAGE, WELCOME_MESSAGES
)
import random
random.seed(42)  # Force predictable random behaviour for prompt generation testing

prompt1 = get_astro_prompt("Sree", "Kumbham")
check("Astro prompt contains name", "Sree" in prompt1)
check("Astro prompt — new user has no history block", "hint" not in prompt1.lower())
check("Astro prompt includes Rashi", "Kumbham" in prompt1)

# At seed(42), random.random() is ~0.63, so it WON'T include history.
# Let's mock random temporarily for the test to ensure it hits the 40% branch AND picks a known item
import random as test_random
original_random = test_random.random
original_choice = test_random.choice
test_random.random = lambda: 0.1  # Force history branch
test_random.choice = lambda seq: seq[0]  # Force picking the first item ("traffic")

prompt2 = get_astro_prompt("Arun", past_predictions=["You got stuck in traffic", "Lost chappal at Chalai", "Forgot umbrella"])

test_random.random = original_random  # Restore
test_random.choice = original_choice  # Restore

check("Astro prompt includes past predictions hint when triggered", "hint" in prompt2.lower())
check("Astro prompt includes past predictions", "traffic" in prompt2.lower())

# Curse prompt
cp = get_curse_prompt("Sree", "pottan")
check("Curse prompt includes name", "Sree" in cp)
check("Curse prompt includes curse word", "pottan" in cp)

# QA prompt
qp = get_qa_prompt("Arun", "Will I get a promotion?")
check("QA prompt includes name and question", "Arun" in qp and "promotion" in qp)

# Daily omen prompt
dp = get_daily_omen_prompt("thunderstorm, AstRobot advises staying home", 34.0, 25.0, 12.5, "Chalai Market")
check("Daily omen prompt includes condition", "thunderstorm" in dp)
check("Daily omen prompt includes temp", "34.0" in dp)
check("Daily omen prompt includes landmark", "Chalai Market" in dp)

# Time-aware system prompt
sp = get_time_aware_system_prompt()
check("Time-aware system prompt is non-empty", len(sp) > 200)
check("System prompt contains base personality", "AstRobot" in sp)
check("System prompt contains time period", any(q in sp for q in ["morning","noon","afternoon","evening","night"]))

check("FALLBACK_MESSAGE is non-empty string", isinstance(FALLBACK_MESSAGE, str) and len(FALLBACK_MESSAGE) > 5)
check("WELCOME_MESSAGES has 6 entries", len(WELCOME_MESSAGES) == 6)
check("All welcome messages have {user} placeholder", all("{user}" in m for m in WELCOME_MESSAGES))

# ── 6. CURSES ──────────────────────────────────────────────
section("6. Curses — Triggers, Kochi Slang, Helpers")
from curses import (
    contains_boli_trigger, contains_kochi_slang, get_random_kochi_response,
    get_random_curse, get_random_doomed_prediction, get_random_curse_back,
    BOLI_TRIGGER_WORDS, KOCHI_SLANG, CURSE_WORDS
)

check("BOLI_TRIGGER_WORDS non-empty", len(BOLI_TRIGGER_WORDS) > 5)
check("KOCHI_SLANG non-empty", len(KOCHI_SLANG) > 3)
check("CURSE_WORDS non-empty", len(CURSE_WORDS) > 10)

check("contains_boli_trigger detects 'kidilam'", "kidilam" in contains_boli_trigger("That was kidilam!"))
check("contains_boli_trigger detects multiple words", len(contains_boli_trigger("kidilam mone vishayam")) >= 2)
check("contains_boli_trigger returns empty for no match", contains_boli_trigger("hello world") == [])
check("contains_boli_trigger is case-insensitive", "kidilam" in contains_boli_trigger("KIDILAM"))

check("contains_kochi_slang detects 'machane'", contains_kochi_slang("machane what is this"))
check("contains_kochi_slang detects 'sayi'", contains_kochi_slang("sayi come here"))
check("contains_kochi_slang returns False for Trivandrum slang", not contains_kochi_slang("kidilam mone"))

resp = get_random_kochi_response("Sree")
check("get_random_kochi_response formats user", "Sree" in resp)
check("get_random_kochi_response references Thirontharam/Kochi", any(w in resp for w in ["Thirontharam","Kochi","Ernakulam"]))

curse = get_random_curse()
check("get_random_curse returns a string from list", curse in CURSE_WORDS)

doom = get_random_doomed_prediction("Arun")
check("get_random_doomed_prediction returns non-empty string", isinstance(doom, str) and len(doom) > 10)

back = get_random_curse_back("Sree")
check("get_random_curse_back contains username", "Sree" in back)

# ── 7. CACHE CLEANER ───────────────────────────────────────
section("7. Cache Cleaner")
from tools.cache_cleaner import generalize_cache, remove_duplicate_templates

test_conn = duckdb.connect(":memory:")
schema._create_tables(test_conn)

schema.upsert_user(test_conn, 2001, "Rajesh")
schema.save_prediction(test_conn, "astro", "Aiyo Rajesh, shokam day at KD Puram.", user_id=2001)
schema.save_prediction(test_conn, "astro", "Eda Rajesh, go drink chaya.", user_id=2001)

updated = generalize_cache(test_conn, ["Rajesh"])
check("generalize_cache updates rows with name", updated == 2)

rows = test_conn.execute("SELECT template_text FROM predictions_cache").fetchall()
check("Names replaced with {user}", all("{user}" in r[0] for r in rows))
check("Original name removed from templates", all("Rajesh" not in r[0] for r in rows))

# Duplicate removal
schema.save_prediction(test_conn, "astro", "Aiyo {user}, shokam day at KD Puram.")  # same as first after generalize
removed = remove_duplicate_templates(test_conn)
check("remove_duplicate_templates runs without error", True)

# ── 8. LIVE GEMINI API (real keys) ─────────────────────────
section("8. Live Gemini API — Dual-Key Fallback (Real Keys)")
from services.gemini_service import GeminiService as GS

FREE_KEY = os.getenv("GEMINI_API_KEY_FREE")
PAID_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY_PAID")

if not FREE_KEY and not PAID_KEY:
    print("  [SKIP] No API keys found in .env")
else:
    live_conn = duckdb.connect(":memory:")
    live_svc = GS(free_api_key=FREE_KEY, paid_api_key=PAID_KEY, db_conn=live_conn)
    from prompts import get_astro_prompt as gap, get_time_aware_system_prompt as gtsp
    result = live_svc.call(gap("TestUser", rashi="Kumbham (Aquarius)"), gtsp())
    check("Live API returns non-empty response", result is not None and len(result) > 10)
    check("active_key is 'free' or 'paid'", live_svc.active_key in ("free","paid"))
    print(f"    active_key={live_svc.active_key}  |  response: {result[:80]}...")

# ── SUMMARY ───────────────────────────────────────────────
section("SUMMARY")
passed = sum(1 for _, v in results if v)
failed = sum(1 for _, v in results if not v)
total = len(results)
print(f"\n  Total: {total}  |  {PASS}: {passed}  |  {FAIL}: {failed}\n")
if failed:
    print("  FAILED TESTS:")
    for name, v in results:
        if not v:
            print(f"    ✗ {name}")
sys.exit(0 if failed == 0 else 1)
