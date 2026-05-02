import sys
import os
import sqlite3
from dotenv import load_dotenv
load_dotenv()

# Add parent directory to path so we can import schema.py
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

import schema

try:
    import duckdb
except ImportError:
    print("Error: duckdb is not installed. Run 'pip install duckdb' first.")
    sys.exit(1)

def migrate():
    data_dir = os.path.join(parent_dir, "data")
    duck_path = os.getenv("DB_PATH", os.path.join(data_dir, "astro_bot.db"))
    
    # We rename the old duckdb file to duck.db
    backup_path = duck_path + ".duck_backup"
    
    if not os.path.exists(duck_path):
        print(f"Error: Could not find database file at {duck_path}")
        return
        
    # Check if the file is already a valid SQLite database by reading its magic bytes.
    # sqlite3.connect() is NOT reliable here — it succeeds on non-SQLite files and only
    # fails later (e.g. on PRAGMA calls), which caused false-positive "already SQLite" results.
    SQLITE_MAGIC = b'SQLite format 3\x00'
    try:
        with open(duck_path, 'rb') as f:
            header = f.read(16)
        if header == SQLITE_MAGIC:
            # Confirm the file is actually openable and readable as SQLite
            try:
                conn = sqlite3.connect(duck_path)
                conn.execute("PRAGMA integrity_check")
                conn.close()
                print("Database is already in SQLite format. No migration needed.")
                return
            except sqlite3.DatabaseError as e:
                print(f"File has SQLite magic bytes but failed integrity check: {e}")
                print("Treating as corrupted — will back up and recreate.")
        else:
            print(f"File does not have SQLite magic bytes (got: {header[:16]!r}). Treating as DuckDB.")
    except Exception as e:
        print(f"Unexpected error when checking DB format: {e}")
        return

    print(f"Moving {duck_path} -> {backup_path}")
    os.rename(duck_path, backup_path)
    
    # Let schema.py create the empty SQLite DB with perfect schemas
    print(f"Creating fresh SQLite database at {duck_path}...")
    os.environ["DB_PATH"] = duck_path
    sql_conn = schema.init_db()
    
    print(f"Connecting to DuckDB backup at {backup_path}...")
    duck_conn = duckdb.connect(backup_path)
    
    tables = duck_conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()
    
    for (table,) in tables:
        print(f"Migrating table: {table}...")
        try:
            rows = duck_conn.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                print(f"  - Empty table, skipping.")
                continue
                
            columns = duck_conn.execute(f"SELECT column_name FROM information_schema.columns WHERE table_schema='main' AND table_name='{table}'").fetchall()
            col_names = [c[0] for c in columns]
            
            placeholders = ",".join(["?"] * len(col_names))
            insert_query = f"INSERT OR REPLACE INTO {table} ({','.join(col_names)}) VALUES ({placeholders})"
            
            sql_conn.executemany(insert_query, rows)
            sql_conn.commit()
            print(f"  - Copied {len(rows)} rows successfully.")
        except Exception as e:
            print(f"  - ERROR on table {table}: {e}")
            
    duck_conn.close()
    sql_conn.close()
    
    print("\n✅ Migration complete! Your database is now natively SQLite.")
    print(f"The old DuckDB file is saved safely at: {backup_path}")
    print("You can now start the bot!")

if __name__ == '__main__':
    migrate()
