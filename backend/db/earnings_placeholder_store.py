"""Weekly placeholder earnings tickers.

Uses Neon/Postgres when DATABASE_URL is set; otherwise a local SQLite DB.
The placeholder table is a controlled fallback for earnings scans when the
calendar provider does not return reporters.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Iterable

_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_IS_PG = _DATABASE_URL.startswith(("postgres://", "postgresql://"))
_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "earnings_placeholders.db")
_initialized = False

_DEFAULT_TICKERS = (
    "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AMD,AVGO,NFLX,JPM,UNH,LLY,COST,WMT"
)


def _pg_conn():
    import psycopg2

    url = _DATABASE_URL.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url, connect_timeout=10)


def _sqlite_conn():
    conn = sqlite3.connect(_SQLITE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _init() -> None:
    global _initialized
    if _initialized:
        return
    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS earnings_placeholders ("
                "day DATE NOT NULL, "
                "ticker TEXT NOT NULL, "
                "source TEXT NOT NULL DEFAULT 'placeholder', "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
                "expires_at DATE NOT NULL, "
                "PRIMARY KEY (day, ticker))"
            )
            conn.commit()
    else:
        with _sqlite_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS earnings_placeholders ("
                "day TEXT NOT NULL, "
                "ticker TEXT NOT NULL, "
                "source TEXT NOT NULL DEFAULT 'placeholder', "
                "created_at TEXT NOT NULL, "
                "expires_at TEXT NOT NULL, "
                "PRIMARY KEY (day, ticker))"
            )
            conn.commit()
    _initialized = True


def _parse_day(value: str | None) -> date:
    if not value:
        return date.today()
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def normalize_tickers(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_parts = re.split(r"[\s,;]+", value)
    elif isinstance(value, Iterable):
        raw_parts = [str(v) for v in value]
    else:
        raw_parts = []

    seen: set[str] = set()
    tickers: list[str] = []
    for raw in raw_parts:
        sym = str(raw or "").strip().upper().lstrip("$")
        sym = re.sub(r"[^A-Z0-9.\-]", "", sym)
        if not sym or sym in seen:
            continue
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", sym):
            continue
        seen.add(sym)
        tickers.append(sym)
    return tickers


def default_placeholder_tickers() -> list[str]:
    return normalize_tickers(os.getenv("EARNINGS_PLACEHOLDER_TICKERS", _DEFAULT_TICKERS))


def _keep_after_days(value: int | None = None) -> int:
    raw = value
    if raw is None:
        raw = os.getenv("EARNINGS_PLACEHOLDER_KEEP_AFTER_DAYS", "2")
    try:
        return max(0, min(int(raw), 30))
    except (TypeError, ValueError):
        return 2


def _upsert_placeholder_rows(rows: list[tuple[str, str, str, str, str]]) -> None:
    if not rows:
        return
    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO earnings_placeholders "
                "(day, ticker, source, created_at, expires_at) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (day, ticker) DO UPDATE SET "
                "source = EXCLUDED.source, "
                "created_at = EXCLUDED.created_at, "
                "expires_at = EXCLUDED.expires_at",
                rows,
            )
            conn.commit()
        return

    with _sqlite_conn() as conn:
        conn.executemany(
            "INSERT INTO earnings_placeholders "
            "(day, ticker, source, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(day, ticker) DO UPDATE SET "
            "source = excluded.source, "
            "created_at = excluded.created_at, "
            "expires_at = excluded.expires_at",
            rows,
        )
        conn.commit()


def seed_weekly_placeholders(
    *,
    start_date: str | None = None,
    days: int = 7,
    tickers: Any = None,
    source: str = "placeholder",
    replace: bool = False,
    keep_after_days: int | None = None,
) -> dict[str, Any]:
    """Insert placeholder rows for start_date through start_date + days - 1."""
    _init()
    days = max(1, min(int(days or 7), 14))
    start = _parse_day(start_date)
    clean = normalize_tickers(tickers) or default_placeholder_tickers()
    keep_days = _keep_after_days(keep_after_days)
    created_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    rows: list[tuple[str, str, str, str, str]] = []
    for offset in range(days):
        row_day = start + timedelta(days=offset)
        expires = row_day + timedelta(days=keep_days)
        rows.extend(
            (row_day.isoformat(), ticker, source, created_at, expires.isoformat())
            for ticker in clean
        )

    if replace:
        delete_placeholders(start_date=start.isoformat(), days=days)
    _upsert_placeholder_rows(rows)

    return {
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=days - 1)).isoformat(),
        "days": days,
        "tickers": clean,
        "count": len(rows),
        "replaced": replace,
        "keep_after_days": keep_days,
        "store": backend_name(),
    }


def save_dated_placeholders(
    rows: Iterable[dict[str, Any]],
    *,
    source: str = "scanner-weekly",
    replace: bool = True,
    keep_after_days: int | None = None,
) -> dict[str, Any]:
    """Insert placeholder rows where each input row has its own earnings date."""
    _init()
    keep_days = _keep_after_days(keep_after_days)
    created_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    target_dates: list[str] = []
    seen_dates: set[str] = set()
    inserted: list[tuple[str, str, str, str, str]] = []
    tickers_seen: set[str] = set()

    for item in rows or []:
        if not isinstance(item, dict):
            continue
        day_raw = item.get("date") or item.get("day")
        try:
            row_day = _parse_day(str(day_raw) if day_raw else None)
        except Exception:
            continue
        day = row_day.isoformat()
        if day not in seen_dates:
            seen_dates.add(day)
            target_dates.append(day)
        clean = normalize_tickers(
            item.get("tickers")
            or item.get("symbols")
            or item.get("ticker")
            or item.get("symbol")
        )
        expires = row_day + timedelta(days=keep_days)
        for ticker in clean:
            tickers_seen.add(ticker)
            inserted.append((day, ticker, source, created_at, expires.isoformat()))

    if replace and target_dates:
        for day in target_dates:
            delete_placeholders(start_date=day, days=1)
    _upsert_placeholder_rows(inserted)

    return {
        "dates": target_dates,
        "tickers": sorted(tickers_seen),
        "count": len(inserted),
        "replaced": replace,
        "keep_after_days": keep_days,
        "store": backend_name(),
    }


def delete_placeholders(start_date: str | None = None, days: int = 7) -> dict[str, Any]:
    _init()
    days = max(1, min(int(days or 7), 31))
    start = _parse_day(start_date)
    end = start + timedelta(days=days - 1)

    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM earnings_placeholders WHERE day BETWEEN %s AND %s",
                (start.isoformat(), end.isoformat()),
            )
            conn.commit()
            deleted = max(cur.rowcount, 0)
    else:
        with _sqlite_conn() as conn:
            cur = conn.execute(
                "DELETE FROM earnings_placeholders WHERE day BETWEEN ? AND ?",
                (start.isoformat(), end.isoformat()),
            )
            conn.commit()
            deleted = max(cur.rowcount, 0)

    return {
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=days - 1)).isoformat(),
        "days": days,
        "deleted": deleted,
        "store": backend_name(),
    }


def purge_past_placeholders(
    *,
    keep_after_days: int | None = 2,
    today: str | None = None,
) -> dict[str, Any]:
    """Delete earnings placeholder rows whose earnings date is older than the grace window."""
    _init()
    keep_days = _keep_after_days(keep_after_days)
    today_date = _parse_day(today)
    cutoff = today_date - timedelta(days=keep_days)

    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM earnings_placeholders WHERE day < %s",
                (cutoff.isoformat(),),
            )
            conn.commit()
            deleted = max(cur.rowcount, 0)
    else:
        with _sqlite_conn() as conn:
            cur = conn.execute(
                "DELETE FROM earnings_placeholders WHERE day < ?",
                (cutoff.isoformat(),),
            )
            conn.commit()
            deleted = max(cur.rowcount, 0)

    return {
        "today": today_date.isoformat(),
        "cutoff_exclusive": cutoff.isoformat(),
        "keep_after_days": keep_days,
        "deleted": deleted,
        "store": backend_name(),
    }


def load_placeholders_for_date(day: str) -> list[str]:
    _init()
    target = _parse_day(day).isoformat()
    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ticker FROM earnings_placeholders "
                "WHERE day = %s AND expires_at >= %s "
                "ORDER BY ticker",
                (target, target),
            )
            return [str(r[0]).upper() for r in cur.fetchall()]

    with _sqlite_conn() as conn:
        rows = conn.execute(
            "SELECT ticker FROM earnings_placeholders "
            "WHERE day = ? AND expires_at >= ? "
            "ORDER BY ticker",
            (target, target),
        ).fetchall()
    return [str(r["ticker"]).upper() for r in rows]


def ensure_weekly_placeholders(day: str, *, days: int = 7) -> dict[str, Any]:
    """Ensure placeholders exist for the requested date and return its tickers."""
    tickers = load_placeholders_for_date(day)
    seeded = False
    if not tickers:
        seed_weekly_placeholders(start_date=day, days=days)
        tickers = load_placeholders_for_date(day)
        seeded = True
    return {
        "date": _parse_day(day).isoformat(),
        "tickers": tickers,
        "count": len(tickers),
        "seeded": seeded,
        "store": backend_name(),
    }


def list_placeholders(start_date: str | None = None, days: int = 7) -> dict[str, Any]:
    _init()
    days = max(1, min(int(days or 7), 31))
    start = _parse_day(start_date)
    end = start + timedelta(days=days - 1)

    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT day, ticker, source, created_at, expires_at "
                "FROM earnings_placeholders "
                "WHERE day BETWEEN %s AND %s "
                "ORDER BY day, ticker",
                (start.isoformat(), end.isoformat()),
            )
            raw_rows = cur.fetchall()
            rows = [
                {
                    "date": str(r[0]),
                    "ticker": str(r[1]).upper(),
                    "source": str(r[2]),
                    "created_at": r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3]),
                    "expires_at": str(r[4]),
                }
                for r in raw_rows
            ]
    else:
        with _sqlite_conn() as conn:
            raw_rows = conn.execute(
                "SELECT day, ticker, source, created_at, expires_at "
                "FROM earnings_placeholders "
                "WHERE day BETWEEN ? AND ? "
                "ORDER BY day, ticker",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        rows = [
            {
                "date": str(r["day"]),
                "ticker": str(r["ticker"]).upper(),
                "source": str(r["source"]),
                "created_at": str(r["created_at"]),
                "expires_at": str(r["expires_at"]),
            }
            for r in raw_rows
        ]

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "count": len(rows),
        "rows": rows,
        "store": backend_name(),
    }


def backend_name() -> str:
    return "postgres" if _IS_PG else "sqlite"
