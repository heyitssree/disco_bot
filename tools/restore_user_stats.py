"""
tools/restore_user_stats.py — Emergency restore of user_stats from last known-good snapshot.

Only use this if astro_bot.db is confirmed unrecoverable and has been wiped/recreated.
Data captured from show_user_stats.py output on 2026-05-02.

Usage:
    cd ~/disco_bot && source .venv/bin/activate
    python tools/restore_user_stats.py
"""

import sqlite3
import os
import sys

parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

from dotenv import load_dotenv
load_dotenv()

DB_PATH = os.getenv("DB_PATH", "/opt/astrobot/data/astro_bot.db")

# Last known-good snapshot — 2026-05-02
# (user_id, username, rashi, boli_points, prediction_count)
SNAPSHOT = [
    (1166249556988657686, "Anu",                        "Makaram (Capricorn)",   1053, 6),
    (925701772054528030,  "Koda☂️",                     "Mithunam (Gemini)",      687, 5),
    (965230294103900201,  "Kiki",                       None,                     660, 0),
    (820488194344353792,  "Flo",                        "Kumbham (Aquarius)",     403, 1),
    (981249535802245120,  "Zero🌟",                     "Chingam (Leo)",          260, 3),
    (271986361047711744,  "Link",                       "Makaram (Capricorn)",    255, 6),
    (507468776451866624,  "sigmoid",                    "Medam (Aries)",          147, 2),
    (1183030262670561302, "Voda",                       "Karkidakam (Cancer)",    141, 4),
    (1471504426517921915, "ChaiChaiNPC",                "Thulam (Libra)",          98, 1),
    (498881499458961418,  "Moda",                       "Dhanu (Sagittarius)",     92, 8),
    (1049357001655857253, "LucidMonkey",                None,                      69, 0),
    (419544210346213376,  "Lisan Al Aiiss",             "Mithunam (Gemini)",       53, 1),
    (1368844439862116362, "Delulu",                     None,                      51, 0),
    (1495384244363460638, "Advait",                     None,                      30, 0),
    (458661163350097920,  "archemo (bully gang)",       None,                      30, 0),
    (701068872412954694,  "joe",                        "Karkidakam (Cancer)",     22, 1),
    (1362104620121329896, "M I C H Λ Ǝ L",             None,                      21, 0),
    (367266169796952064,  "jvbin",                      "Dhanu (Sagittarius)",     18, 4),
    (761854254276149282,  "Aquila",                     None,                      15, 0),
    (783759918531739718,  "Link",                       "Makaram (Capricorn)",      5, 2),
    (751689254505021492,  "Darkr0ku",                   None,                       5, 0),
    (398396362078552064,  "Juggernaut",                 None,                       5, 0),
    (1240562429671510052, "Contrarian",                 None,                       0, 0),
    (1080838404100599920, "aliena4",                    None,                       0, 0),
    (518783256389222400,  "Wrench",                     None,                       0, 0),
    (1011296630219223082, "aries plex",                 None,                       0, 0),
    (1498756377060049027, "RSR",                        None,                       0, 0),
    (404670612741816320,  "Modnar",                     None,                       0, 0),
    (770271324156985395,  "ełgato~",                    None,                       0, 0),
    (1471532671682351351, "1ce.Tw0",                    None,                       0, 0),
    (781592633172033567,  "Symbol8",                    None,                       0, 0),
    (791556839140884512,  "Voyager380",                 None,                       0, 0),
    (1027206449492938773, "Black butterfly 🦋",          None,                       0, 0),
    (826093490613911582,  "Sandeep",                    None,                       0, 0),
    (1001800633386545217, "XNDR your boy next door",    None,                       0, 0),
    (1489847149649723554, "New Age Monk",               None,                       0, 0),
    (1496556546492989613, "_. N1khiiii",                None,                       0, 0),
    (860458644616904705,  "Bonito",                     None,                       0, 0),
    (694946412554616885,  "papa_allu",                  None,                       0, 0),
    (794242688245301281,  "malulu",                     None,                       0, 0),
    (918342547175276584,  "「 RKB  」",                 None,                       0, 0),
    (732237329397317744,  "OggyNotOp",                  None,                       0, 0),
    (705713307197112342,  "Violet ✨",                  None,                       0, 0),
    (953671735297843220,  "roze_sha",                   None,                       0, 0),
    (848247048422948865,  "J.Stuart",                   None,                       0, 0),
    (762240273266114572,  "Luffy",                      None,                       0, 0),
    (847249810276745217,  "aamil10349",                 None,                       0, 0),
    (836267879925415997,  "Dvader",                     None,                       0, 0),
    (777897529164693575,  "Arjun",                      None,                       0, 0),
]


def restore():
    print(f"Connecting to: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print("ERROR: Database file not found. Create it first with: python tools/recover_db.py --force")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    existing = conn.execute("SELECT COUNT(*) FROM user_stats").fetchone()[0]
    if existing > 0:
        answer = input(f"⚠️  user_stats already has {existing} rows. Overwrite all? [y/N]: ").strip().lower()
        if answer != 'y':
            print("Aborted.")
            conn.close()
            return

    conn.execute("DELETE FROM user_stats")

    for user_id, username, rashi, pts, preds in SNAPSHOT:
        conn.execute(
            """
            INSERT INTO user_stats (user_id, username, rashi, boli_points, prediction_count,
                                    last_seen, strikes, daily_action_count, extra_actions)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 0, 0, 0)
            """,
            [user_id, username, rashi, pts, preds],
        )

    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM user_stats").fetchone()[0]
    conn.close()

    print(f"✅ Restored {count} users successfully.")
    print("Note: rashi values marked 'BAD RASHI' in the snapshot were left as-is.")
    print("      Users can re-register their rashi with /astro when the bot is back up.")


if __name__ == "__main__":
    restore()
