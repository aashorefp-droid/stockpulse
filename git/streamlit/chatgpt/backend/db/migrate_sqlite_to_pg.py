"""
One-time migration: copy local SQLite GEX history into the configured
Postgres/Neon store. Idempotent — safe to re-run (ON CONFLICT upsert).

Run from chatgpt/ with the backend's environment (DATABASE_URL set):

    python -m backend.db.migrate_sqlite_to_pg

Why this exists: the backend ran in postgres mode while psycopg2 was
missing, so every write failed and history accrued only in SQLite. This
backfills that SQLite history (e.g. Friday's SPY +1) into Neon so the
streak picks up where it left off instead of restarting from today.
"""
import os
import sqlite3
import sys

_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "gex_history.db")


def main() -> int:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url.startswith(("postgres://", "postgresql://")):
        print("DATABASE_URL is not a Postgres URL — nothing to migrate to.")
        return 1
    try:
        import psycopg2
    except ModuleNotFoundError:
        print("psycopg2 not installed in THIS interpreter — "
              "run `pip install -r backend/requirements.txt` first.")
        return 1

    if not os.path.exists(_SQLITE_PATH):
        print(f"No SQLite file at {_SQLITE_PATH} — nothing to migrate.")
        return 0

    s = sqlite3.connect(_SQLITE_PATH)
    s.row_factory = sqlite3.Row
    spy = s.execute("SELECT day, sign FROM gex_history").fetchall()
    try:
        sym = s.execute(
            "SELECT symbol, day, sign FROM gex_history_sym"
        ).fetchall()
    except sqlite3.OperationalError:
        sym = []
    s.close()

    pg = psycopg2.connect(
        url.replace("postgres://", "postgresql://", 1), connect_timeout=10
    )
    with pg, pg.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS gex_history "
            "(day DATE PRIMARY KEY, sign INTEGER NOT NULL)"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS gex_history_sym "
            "(symbol TEXT, day DATE, sign INTEGER NOT NULL, "
            "PRIMARY KEY (symbol, day))"
        )
        for r in spy:
            cur.execute(
                "INSERT INTO gex_history (day, sign) VALUES (%s, %s) "
                "ON CONFLICT (day) DO UPDATE SET sign = EXCLUDED.sign",
                (r["day"], r["sign"]),
            )
        for r in sym:
            cur.execute(
                "INSERT INTO gex_history_sym (symbol, day, sign) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (symbol, day) DO UPDATE SET sign = EXCLUDED.sign",
                (r["symbol"], r["day"], r["sign"]),
            )
    pg.close()
    print(f"Migrated {len(spy)} SPY row(s) and {len(sym)} sector row(s) "
          f"into Postgres. SQLite left untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
