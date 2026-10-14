"""Persistence for the user's holdings watchlist.

Uses Neon/Postgres when DATABASE_URL is set; otherwise a local SQLite DB.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime
from typing import Any

_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_IS_PG = _DATABASE_URL.startswith(("postgres://", "postgresql://"))
_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "holdings.db")
_LIST_KEY = "default"
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
                "CREATE TABLE IF NOT EXISTS holdings_lists ("
                "list_key TEXT PRIMARY KEY, "
                "tickers JSONB NOT NULL, "
                "updated_at TIMESTAMPTZ NOT NULL)"
            )
            conn.commit()
    else:
        with _sqlite_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS holdings_lists ("
                "list_key TEXT PRIMARY KEY, "
                "tickers TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )
            conn.commit()
    _initialized = True


def normalize_tickers(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_parts = re.split(r"[\s,;]+", value)
    elif isinstance(value, (list, tuple, set)):
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


def save_holdings(value: Any) -> dict[str, Any]:
    _init()
    tickers = normalize_tickers(value)
    updated_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    payload = json.dumps(tickers, separators=(",", ":"))

    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO holdings_lists (list_key, tickers, updated_at) "
                "VALUES (%s, %s::jsonb, %s) "
                "ON CONFLICT (list_key) DO UPDATE SET "
                "tickers = EXCLUDED.tickers, "
                "updated_at = EXCLUDED.updated_at",
                (_LIST_KEY, payload, updated_at),
            )
            conn.commit()
    else:
        with _sqlite_conn() as conn:
            conn.execute(
                "INSERT INTO holdings_lists (list_key, tickers, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(list_key) DO UPDATE SET "
                "tickers = excluded.tickers, "
                "updated_at = excluded.updated_at",
                (_LIST_KEY, payload, updated_at),
            )
            conn.commit()

    return {
        "tickers": tickers,
        "count": len(tickers),
        "updated_at": updated_at,
        "store": backend_name(),
    }


def load_holdings() -> dict[str, Any]:
    _init()
    if _IS_PG:
        with _pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tickers, updated_at FROM holdings_lists WHERE list_key = %s",
                (_LIST_KEY,),
            )
            row = cur.fetchone()
        if not row:
            return {"tickers": [], "count": 0, "updated_at": None, "store": backend_name()}
        tickers = row[0]
        if isinstance(tickers, str):
            tickers = json.loads(tickers)
        updated_at = row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1])
        return {
            "tickers": normalize_tickers(tickers),
            "count": len(normalize_tickers(tickers)),
            "updated_at": updated_at,
            "store": backend_name(),
        }

    with _sqlite_conn() as conn:
        row = conn.execute(
            "SELECT tickers, updated_at FROM holdings_lists WHERE list_key = ?",
            (_LIST_KEY,),
        ).fetchone()
    if not row:
        return {"tickers": [], "count": 0, "updated_at": None, "store": backend_name()}
    tickers = normalize_tickers(json.loads(row["tickers"]))
    return {
        "tickers": tickers,
        "count": len(tickers),
        "updated_at": row["updated_at"],
        "store": backend_name(),
    }


def get_holdings_tickers() -> list[str]:
    return list(load_holdings().get("tickers") or [])


def backend_name() -> str:
    return "postgres" if _IS_PG else "sqlite"
