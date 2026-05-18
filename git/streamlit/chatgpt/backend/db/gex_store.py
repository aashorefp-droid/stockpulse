"""
Persistence for daily GEX sign history.

Backend selection:
  - DATABASE_URL set (postgres://...)  → cloud Postgres (survives Render deploys/spindown)
  - otherwise                          → local SQLite file (survives local restarts only)

Schema (both backends):
  gex_history(day TEXT/DATE PRIMARY KEY, sign INTEGER)   sign ∈ {-1, 0, 1}
"""
import os
import sqlite3
from datetime import date

_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_IS_PG = _DATABASE_URL.startswith(("postgres://", "postgresql://"))
_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "gex_history.db")

_initialized = False


def _pg_conn():
    import psycopg2
    # Render/Heroku style URLs sometimes use the legacy postgres:// scheme
    url = _DATABASE_URL.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url, connect_timeout=10)


def _sqlite_conn():
    c = sqlite3.connect(_SQLITE_PATH)
    c.row_factory = sqlite3.Row
    return c


def _init() -> None:
    global _initialized
    if _initialized:
        return
    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS gex_history "
                "(day DATE PRIMARY KEY, sign INTEGER NOT NULL)"
            )
            conn.commit()
    else:
        with _sqlite_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS gex_history "
                "(day TEXT PRIMARY KEY, sign INTEGER NOT NULL)"
            )
            conn.commit()
    _initialized = True


def record_sign(sign: int, day: str | None = None) -> None:
    """Upsert today's GEX sign. Idempotent — last write of the day wins."""
    _init()
    d = day or date.today().isoformat()
    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO gex_history (day, sign) VALUES (%s, %s) "
                "ON CONFLICT (day) DO UPDATE SET sign = EXCLUDED.sign",
                (d, sign),
            )
            conn.commit()
    else:
        with _sqlite_conn() as conn:
            conn.execute(
                "INSERT INTO gex_history (day, sign) VALUES (?, ?) "
                "ON CONFLICT(day) DO UPDATE SET sign = excluded.sign",
                (d, sign),
            )
            conn.commit()


def load_history() -> dict[str, int]:
    """Return {iso_date: sign} for the last 120 days."""
    _init()
    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT day, sign FROM gex_history ORDER BY day DESC LIMIT 120"
            )
            return {str(r[0]): int(r[1]) for r in cur.fetchall()}
    with _sqlite_conn() as conn:
        rows = conn.execute(
            "SELECT day, sign FROM gex_history ORDER BY day DESC LIMIT 120"
        ).fetchall()
    return {str(r["day"]): int(r["sign"]) for r in rows}


def backend_name() -> str:
    return "postgres" if _IS_PG else "sqlite"


# ── Per-symbol history (sectors etc.) — separate table; SPY stays on the
#    original gex_history table above so existing data is untouched. ──────────
_sym_initialized = False


def _init_sym() -> None:
    global _sym_initialized
    if _sym_initialized:
        return
    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS gex_history_sym "
                "(symbol TEXT, day DATE, sign INTEGER NOT NULL, "
                "PRIMARY KEY (symbol, day))"
            )
            conn.commit()
    else:
        with _sqlite_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS gex_history_sym "
                "(symbol TEXT, day TEXT, sign INTEGER NOT NULL, "
                "PRIMARY KEY (symbol, day))"
            )
            conn.commit()
    _sym_initialized = True


def record_sign_sym(symbol: str, sign: int, day: str | None = None) -> None:
    """Upsert today's GEX sign for a symbol. Idempotent."""
    _init_sym()
    d = day or date.today().isoformat()
    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO gex_history_sym (symbol, day, sign) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (symbol, day) DO UPDATE SET sign = EXCLUDED.sign",
                (symbol, d, sign),
            )
            conn.commit()
    else:
        with _sqlite_conn() as conn:
            conn.execute(
                "INSERT INTO gex_history_sym (symbol, day, sign) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(symbol, day) DO UPDATE SET sign = excluded.sign",
                (symbol, d, sign),
            )
            conn.commit()


def load_history_sym(symbol: str) -> dict[str, int]:
    """Return {iso_date: sign} for a symbol, last 120 days."""
    _init_sym()
    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT day, sign FROM gex_history_sym WHERE symbol = %s "
                "ORDER BY day DESC LIMIT 120",
                (symbol,),
            )
            return {str(r[0]): int(r[1]) for r in cur.fetchall()}
    with _sqlite_conn() as conn:
        rows = conn.execute(
            "SELECT day, sign FROM gex_history_sym WHERE symbol = ? "
            "ORDER BY day DESC LIMIT 120",
            (symbol,),
        ).fetchall()
    return {str(r["day"]): int(r["sign"]) for r in rows}
