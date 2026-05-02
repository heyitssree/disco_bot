"""
show_user_stats.py — Read-only view of user_stats. Safe to run at any time.

Usage (on VM):
    cd ~/disco_bot && source .venv/bin/activate
    python tools/show_user_stats.py
"""
import sqlite3, os, sys

VALID_RASHIS = [
    "Medam (Aries)", "Edavam (Taurus)", "Mithunam (Gemini)",
    "Karkidakam (Cancer)", "Chingam (Leo)", "Kanni (Virgo)",
    "Thulam (Libra)", "Vrischikam (Scorpio)", "Dhanu (Sagittarius)",
    "Makaram (Capricorn)", "Kumbham (Aquarius)", "Meenam (Pisces)",
]

DB_PATH = os.getenv("DB_PATH", "/opt/astrobot/data/astro_bot.db")
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT user_id, username, rashi, boli_points, prediction_count, strikes
    FROM user_stats
    ORDER BY boli_points DESC
""").fetchall()

print(f"\nDB: {DB_PATH}   ({len(rows)} users)\n")
print(f"{'#':<4} {'user_id':<22} {'username':<20} {'rashi':<25} {'pts':>6} {'preds':>6} {'stk':>4}  status")
print("-" * 100)

from collections import Counter
seen_names = Counter(r[1] for r in rows)

for i, r in enumerate(rows, 1):
    issues = []
    if r[2] not in VALID_RASHIS:
        issues.append("BAD RASHI")
    if seen_names[r[1]] > 1:
        issues.append("DUP NAME")
    status = ", ".join(issues) if issues else "OK"
    print(f"{i:<4} {r[0]:<22} {str(r[1]):<20} {str(r[2]):<25} {r[3]:>6} {r[4]:>6} {r[5]:>4}  {status}")

print()
bad = [r for r in rows if r[2] not in VALID_RASHIS or seen_names[r[1]] > 1]
if bad:
    print(f"WARNING: {len(bad)} row(s) still need attention (see 'BAD RASHI' / 'DUP NAME' above).")
    sys.exit(1)
else:
    print("All records look clean. Safe to restart the bot.")
    print("  sudo systemctl start navi")
