"""Persistence for saved scanner snapshots.

Uses Neon/Postgres when DATABASE_URL is set; otherwise a local SQLite DB.
Snapshots are keyed by watchlist + market day.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_IS_PG = _DATABASE_URL.startswith(("postgres://", "postgresql://"))
_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "scanner_snapshots.db")

_initialized = False


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
                "CREATE TABLE IF NOT EXISTS scanner_snapshots ("
                "watchlist TEXT NOT NULL, "
                "day DATE NOT NULL, "
                "created_at TIMESTAMPTZ NOT NULL, "
                "count INTEGER NOT NULL, "
                "results JSONB NOT NULL, "
                "PRIMARY KEY (watchlist, day))"
            )
            conn.commit()
    else:
        with _sqlite_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS scanner_snapshots ("
                "watchlist TEXT NOT NULL, "
                "day TEXT NOT NULL, "
                "created_at TEXT NOT NULL, "
                "count INTEGER NOT NULL, "
                "results TEXT NOT NULL, "
                "PRIMARY KEY (watchlist, day))"
            )
            conn.commit()
    _initialized = True


def save_snapshot(watchlist: str, day: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    _init()
    key = (watchlist or "").strip().lower()
    created_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    payload = json.dumps(results, separators=(",", ":"), default=str)
    count = len(results)

    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO scanner_snapshots (watchlist, day, created_at, count, results) "
                "VALUES (%s, %s, %s, %s, %s::jsonb) "
                "ON CONFLICT (watchlist, day) DO UPDATE SET "
                "created_at = EXCLUDED.created_at, "
                "count = EXCLUDED.count, "
                "results = EXCLUDED.results",
                (key, day, created_at, count, payload),
            )
            conn.commit()
    else:
        with _sqlite_conn() as conn:
            conn.execute(
                "INSERT INTO scanner_snapshots (watchlist, day, created_at, count, results) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(watchlist, day) DO UPDATE SET "
                "created_at = excluded.created_at, "
                "count = excluded.count, "
                "results = excluded.results",
                (key, day, created_at, count, payload),
            )
            conn.commit()

    return {
        "watchlist": key,
        "date": day,
        "created_at": created_at,
        "count": count,
        "results": results,
    }


def load_snapshot(watchlist: str, day: Optional[str] = None) -> Optional[dict[str, Any]]:
    _init()
    key = (watchlist or "").strip().lower()
    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            if day:
                cur.execute(
                    "SELECT watchlist, day, created_at, count, results "
                    "FROM scanner_snapshots WHERE watchlist = %s AND day = %s",
                    (key, day),
                )
            else:
                cur.execute(
                    "SELECT watchlist, day, created_at, count, results "
                    "FROM scanner_snapshots WHERE watchlist = %s "
                    "ORDER BY day DESC, created_at DESC LIMIT 1",
                    (key,),
                )
            row = cur.fetchone()
            if not row:
                return None
            results = row[4]
            if isinstance(results, str):
                results = json.loads(results)
            return {
                "watchlist": row[0],
                "date": str(row[1]),
                "created_at": row[2].isoformat() if hasattr(row[2], "isoformat") else str(row[2]),
                "count": int(row[3]),
                "results": results,
            }

    with _sqlite_conn() as conn:
        if day:
            row = conn.execute(
                "SELECT watchlist, day, created_at, count, results "
                "FROM scanner_snapshots WHERE watchlist = ? AND day = ?",
                (key, day),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT watchlist, day, created_at, count, results "
                "FROM scanner_snapshots WHERE watchlist = ? "
                "ORDER BY day DESC, created_at DESC LIMIT 1",
                (key,),
            ).fetchone()
    if not row:
        return None
    return {
        "watchlist": row["watchlist"],
        "date": row["day"],
        "created_at": row["created_at"],
        "count": int(row["count"]),
        "results": json.loads(row["results"]),
    }


def list_snapshots(watchlist: Optional[str] = None) -> list[dict[str, Any]]:
    """Return saved snapshot metadata, newest first."""
    _init()
    key = (watchlist or "").strip().lower()

    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            if key:
                cur.execute(
                    "SELECT watchlist, day, created_at, count "
                    "FROM scanner_snapshots WHERE watchlist = %s "
                    "ORDER BY day DESC, created_at DESC, watchlist ASC",
                    (key,),
                )
            else:
                cur.execute(
                    "SELECT watchlist, day, created_at, count "
                    "FROM scanner_snapshots "
                    "ORDER BY day DESC, created_at DESC, watchlist ASC"
                )
            rows = cur.fetchall()
            return [
                {
                    "watchlist": row[0],
                    "date": str(row[1]),
                    "created_at": row[2].isoformat() if hasattr(row[2], "isoformat") else str(row[2]),
                    "count": int(row[3]),
                }
                for row in rows
            ]

    with _sqlite_conn() as conn:
        if key:
            rows = conn.execute(
                "SELECT watchlist, day, created_at, count "
                "FROM scanner_snapshots WHERE watchlist = ? "
                "ORDER BY day DESC, created_at DESC, watchlist ASC",
                (key,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT watchlist, day, created_at, count "
                "FROM scanner_snapshots "
                "ORDER BY day DESC, created_at DESC, watchlist ASC"
            ).fetchall()
    return [
        {
            "watchlist": row["watchlist"],
            "date": row["day"],
            "created_at": row["created_at"],
            "count": int(row["count"]),
        }
        for row in rows
    ]


def delete_snapshot(watchlist: str, day: Optional[str] = None) -> int:
    """Delete one day for a watchlist, or every saved day when day is omitted."""
    _init()
    key = (watchlist or "").strip().lower()
    if not key:
        return 0

    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            if day:
                cur.execute(
                    "DELETE FROM scanner_snapshots WHERE watchlist = %s AND day = %s",
                    (key, day),
                )
            else:
                cur.execute(
                    "DELETE FROM scanner_snapshots WHERE watchlist = %s",
                    (key,),
                )
            deleted = cur.rowcount
            conn.commit()
            return int(deleted or 0)

    with _sqlite_conn() as conn:
        if day:
            cur = conn.execute(
                "DELETE FROM scanner_snapshots WHERE watchlist = ? AND day = ?",
                (key, day),
            )
        else:
            cur = conn.execute(
                "DELETE FROM scanner_snapshots WHERE watchlist = ?",
                (key,),
            )
        conn.commit()
        return int(cur.rowcount or 0)


def prune_old_snapshots(retention_days: int, watchlists: Optional[list[str]] = None) -> int:
    """Remove snapshots older than retention_days. Set <=0 to keep everything."""
    _init()
    if retention_days <= 0:
        return 0

    cutoff = (datetime.utcnow().date() - timedelta(days=retention_days)).isoformat()
    keys = [
        (w or "").strip().lower()
        for w in (watchlists or [])
        if (w or "").strip()
    ]

    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            if keys:
                cur.execute(
                    "DELETE FROM scanner_snapshots WHERE day < %s AND watchlist = ANY(%s)",
                    (cutoff, keys),
                )
            else:
                cur.execute(
                    "DELETE FROM scanner_snapshots WHERE day < %s",
                    (cutoff,),
                )
            deleted = cur.rowcount
            conn.commit()
            return int(deleted or 0)

    with _sqlite_conn() as conn:
        if keys:
            placeholders = ",".join("?" for _ in keys)
            cur = conn.execute(
                f"DELETE FROM scanner_snapshots WHERE day < ? AND watchlist IN ({placeholders})",
                [cutoff, *keys],
            )
        else:
            cur = conn.execute(
                "DELETE FROM scanner_snapshots WHERE day < ?",
                (cutoff,),
            )
        conn.commit()
        return int(cur.rowcount or 0)


def backend_name() -> str:
    return "postgres" if _IS_PG else "sqlite"
