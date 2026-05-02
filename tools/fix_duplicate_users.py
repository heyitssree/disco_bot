"""
fix_duplicate_users.py — Run this on the GCP VM to resolve duplicate user_stats entries.

Usage:
    cd ~/disco_bot
    source .venv/bin/activate
    python tools/fix_duplicate_users.py

IMPORTANT: Stop the bot before running:
    sudo systemctl stop navi
Restart after:
    sudo systemctl start navi
"""

import duckdb
import os
import sys

# ---------------------------------------------------------------------------
# Valid Rashis as defined in glossary.py
# ---------------------------------------------------------------------------
VALID_RASHIS = [
    "Medam (Aries)", "Edavam (Taurus)", "Mithunam (Gemini)",
    "Karkidakam (Cancer)", "Chingam (Leo)", "Kanni (Virgo)",
    "Thulam (Libra)", "Vrischikam (Scorpio)", "Dhanu (Sagittarius)",
    "Makaram (Capricorn)", "Kumbham (Aquarius)", "Meenam (Pisces)",
]

# ---------------------------------------------------------------------------
# DB path — reads from env var just like the bot does
# ---------------------------------------------------------------------------
DB_PATH = os.getenv("DB_PATH", "data/astro_bot.db")

conn = duckdb.connect(DB_PATH)
print(f"[+] Connected to: {DB_PATH}\n")

# ---------------------------------------------------------------------------
# Step 1: Show all rows, ordered by username so duplicates are obvious
# ---------------------------------------------------------------------------
print("=" * 70)
print("CURRENT user_stats TABLE")
print("=" * 70)
rows = conn.execute("""
    SELECT user_id, username, rashi, boli_points, prediction_count, strikes
    FROM user_stats
    ORDER BY username, user_id
""").fetchall()

print(f"{'user_id':<22} {'username':<20} {'rashi':<25} {'pts':>6} {'preds':>6} {'strikes':>7}")
print("-" * 90)
for r in rows:
    rashi_flag = "  ⚠ NOT IN GLOSSARY" if r[2] not in VALID_RASHIS else ""
    print(f"{r[0]:<22} {str(r[1]):<20} {str(r[2]):<25} {r[3]:>6} {r[4]:>6} {r[5]:>7}{rashi_flag}")

print()

# ---------------------------------------------------------------------------
# Step 2: Detect same username appearing under different user_ids
# ---------------------------------------------------------------------------
from collections import defaultdict
by_name: dict[str, list] = defaultdict(list)
for r in rows:
    by_name[r[1]].append(r)

duplicates = {name: entries for name, entries in by_name.items() if len(entries) > 1}

if not duplicates:
    print("[✓] No username duplicates found. Nothing to fix.")
    conn.close()
    sys.exit(0)

print("=" * 70)
print("DETECTED DUPLICATES (same username, different user_id)")
print("=" * 70)
for name, entries in duplicates.items():
    print(f"\n  Username: {name!r}")
    for e in entries:
        rashi_ok = "✓" if e[2] in VALID_RASHIS else "✗ INVALID"
        print(f"    user_id={e[0]}  rashi={e[2]!r} [{rashi_ok}]  points={e[3]}  preds={e[4]}")

# ---------------------------------------------------------------------------
# Step 3: Interactive merge
# ---------------------------------------------------------------------------
print()
print("=" * 70)
print("MERGE WIZARD")
print("=" * 70)
print("For each duplicate group you will be asked:")
print("  - Which user_id to KEEP (the real Discord ID for that person)")
print("  - Which Rashi to assign (must be a valid glossary Rashi)")
print("Points and prediction_count will be SUMMED across both rows.")
print()

for name, entries in duplicates.items():
    print(f"--- Merging: {name!r} ---")
    for i, e in enumerate(entries):
        print(f"  [{i}] user_id={e[0]}  rashi={e[2]!r}  points={e[3]}  preds={e[4]}")

    # Pick the keeper user_id
    while True:
        raw = input(f"  Enter the user_id to KEEP for {name!r}: ").strip()
        try:
            keep_id = int(raw)
        except ValueError:
            print("  Not a valid integer. Try again.")
            continue
        if keep_id not in [e[0] for e in entries]:
            print("  That user_id is not in the duplicate list. Try again.")
            continue
        break

    remove_ids = [e[0] for e in entries if e[0] != keep_id]

    # Sum points and preds across all rows for this username
    total_points = sum(e[3] for e in entries)
    total_preds  = sum(e[4] for e in entries)
    total_strikes = max(e[5] for e in entries)  # keep the highest strike count

    # Pick the correct Rashi
    print()
    print("  Valid Rashis:")
    for i, r in enumerate(VALID_RASHIS):
        print(f"    [{i:2d}] {r}")
    while True:
        raw = input("  Enter the Rashi number (or type the full name exactly): ").strip()
        if raw.isdigit() and 0 <= int(raw) < len(VALID_RASHIS):
            correct_rashi = VALID_RASHIS[int(raw)]
            break
        if raw in VALID_RASHIS:
            correct_rashi = raw
            break
        print("  Not recognised. Enter the number from the list above.")

    # Confirm before writing
    print()
    print(f"  About to:")
    print(f"    UPDATE user_id={keep_id}  →  rashi={correct_rashi!r}, points={total_points}, preds={total_preds}")
    for rid in remove_ids:
        print(f"    DELETE user_id={rid}")
    confirm = input("  Confirm? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("  Skipped.")
        continue

    # Apply the merge
    conn.execute(
        """
        UPDATE user_stats
        SET rashi=?, boli_points=?, prediction_count=?, strikes=?, last_seen=NOW()
        WHERE user_id=?
        """,
        [correct_rashi, total_points, total_preds, total_strikes, keep_id],
    )
    for rid in remove_ids:
        conn.execute("DELETE FROM user_stats WHERE user_id=?", [rid])

    conn.commit()
    conn.execute("CHECKPOINT")
    print(f"  [✓] Merged. user_id={keep_id} now has {total_points} pts, rashi={correct_rashi!r}\n")

# ---------------------------------------------------------------------------
# Final state
# ---------------------------------------------------------------------------
print()
print("=" * 70)
print("FINAL user_stats TABLE")
print("=" * 70)
final = conn.execute("""
    SELECT user_id, username, rashi, boli_points, prediction_count
    FROM user_stats ORDER BY boli_points DESC
""").fetchall()
print(f"{'user_id':<22} {'username':<20} {'rashi':<25} {'pts':>6} {'preds':>6}")
print("-" * 80)
for r in final:
    print(f"{r[0]:<22} {str(r[1]):<20} {str(r[2]):<25} {r[3]:>6} {r[4]:>6}")

conn.close()
print("\n[✓] Done. Restart the bot: sudo systemctl start navi")
