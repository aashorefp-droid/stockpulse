"""
SQLite store for earnings-day scheduler:
  - Tickers discovered at 8:30 AM CST
  - Pre-earnings and post-earnings notification status
  - Trade result once EPS is confirmed
"""
import sqlite3
import os
from datetime import date
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "earnings_tracker.db")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS earnings_today (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker         TEXT    NOT NULL,
            date           TEXT    NOT NULL,
            eps_estimate   REAL,
            eps_actual     REAL,
            surprise_pct   REAL,
            eps_beat       INTEGER,
            pre_notified   INTEGER DEFAULT 0,
            post_notified  INTEGER DEFAULT 0,
            pre_drift      REAL,
            gap_pct        REAL,
            day_pct        REAL,
            direction      TEXT,
            pnl_pct        REAL,
            vol_ratio      REAL,
            reason         TEXT,
            updated_at     TEXT    DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ticker, date)
        )
        """)
        c.commit()


def upsert_ticker(ticker: str, date_str: str, eps_estimate: Optional[float] = None):
    with _conn() as c:
        c.execute("""
        INSERT INTO earnings_today (ticker, date, eps_estimate)
        VALUES (?, ?, ?)
        ON CONFLICT(ticker, date) DO UPDATE SET
            eps_estimate = COALESCE(?, eps_estimate),
            updated_at   = CURRENT_TIMESTAMP
        """, (ticker, date_str, eps_estimate, eps_estimate))
        c.commit()


def mark_pre_notified(ticker: str, date_str: str, pre_drift: Optional[float] = None):
    with _conn() as c:
        c.execute("""
        UPDATE earnings_today
           SET pre_notified = 1,
               pre_drift    = COALESCE(?, pre_drift),
               updated_at   = CURRENT_TIMESTAMP
         WHERE ticker = ? AND date = ?
        """, (pre_drift, ticker, date_str))
        c.commit()


def mark_post_notified(ticker: str, date_str: str, *, eps_actual: Optional[float],
                       surprise_pct: Optional[float], eps_beat: bool,
                       gap_pct: float, day_pct: float, direction: str,
                       pnl_pct: float, vol_ratio: Optional[float], reason: str):
    with _conn() as c:
        c.execute("""
        UPDATE earnings_today
           SET post_notified = 1,
               eps_actual    = ?,
               surprise_pct  = ?,
               eps_beat      = ?,
               gap_pct       = ?,
               day_pct       = ?,
               direction     = ?,
               pnl_pct       = ?,
               vol_ratio     = ?,
               reason        = ?,
               updated_at    = CURRENT_TIMESTAMP
         WHERE ticker = ? AND date = ?
        """, (eps_actual, surprise_pct, int(eps_beat),
              gap_pct, day_pct, direction, pnl_pct, vol_ratio, reason,
              ticker, date_str))
        c.commit()


def get_pending_post(date_str: str) -> list[dict]:
    """Tickers discovered today that haven't had a post-earnings notification yet."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM earnings_today WHERE date = ? AND post_notified = 0",
            (date_str,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_for_date(date_str: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM earnings_today WHERE date = ? ORDER BY ticker",
            (date_str,)
        ).fetchall()
    return [dict(r) for r in rows]


def today_str() -> str:
    return str(date.today())


# ── Watchlist CRUD ─────────────────────────────────────────────────────────────

def init_watchlist():
    with _conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            ticker     TEXT PRIMARY KEY,
            timing     TEXT DEFAULT 'Unknown',
            added_at   TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        c.commit()


def get_watchlist() -> list[str]:
    with _conn() as c:
        rows = c.execute("SELECT ticker FROM watchlist ORDER BY ticker").fetchall()
    return [r["ticker"] for r in rows]


def add_watchlist_tickers(tickers: list[str]):
    with _conn() as c:
        c.executemany(
            "INSERT OR IGNORE INTO watchlist (ticker) VALUES (?)",
            [(t.upper(),) for t in tickers],
        )
        c.commit()


def remove_watchlist_ticker(ticker: str):
    with _conn() as c:
        c.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker.upper(),))
        c.commit()


def clear_watchlist():
    with _conn() as c:
        c.execute("DELETE FROM watchlist")
        c.commit()
