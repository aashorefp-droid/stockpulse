"""Build and load saved scanner snapshots for watchlists."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from backend.db import scanner_snapshot_store

ET = ZoneInfo("America/New_York")


def _market_day() -> str:
    return datetime.now(ET).date().isoformat()


def _worker_limit(total: int) -> int:
    try:
        configured = int(os.getenv("SNAPSHOT_SCAN_MAX_WORKERS", "4"))
    except ValueError:
        configured = 4
    return max(1, min(total, configured))


def configured_watchlists() -> list[str]:
    raw = os.getenv("SCANNER_SNAPSHOT_WATCHLISTS", "default,momentum")
    watchlists: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        key = part.strip().lower()
        if key and key not in seen:
            seen.add(key)
            watchlists.append(key)
    return watchlists or ["default"]


def retention_days() -> int:
    try:
        return int(os.getenv("SCANNER_SNAPSHOT_RETENTION_DAYS", "7"))
    except ValueError:
        return 7


def _tickers_for_watchlist(watchlist: str) -> list[str]:
    from backend.db.holdings_store import get_holdings_tickers
    from backend.services.scanner import (
        WATCHLISTS,
        SWING_UNIVERSE_PRESETS,
        get_earnings_watchlist,
        get_swing_universe_tickers,
    )

    key = (watchlist or "").strip().lower()
    if key == "holdings":
        tickers = get_holdings_tickers()
    elif key == "earnings":
        tickers = get_earnings_watchlist()
    elif key in SWING_UNIVERSE_PRESETS:
        tickers = get_swing_universe_tickers(key)
    else:
        tickers = WATCHLISTS.get(key, [])
    seen: set[str] = set()
    out: list[str] = []
    for ticker in tickers:
        sym = (ticker or "").strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def refresh_snapshot(watchlist: str = "default", *, day: Optional[str] = None) -> dict:
    """Scan a watchlist and persist the full result list."""
    from backend.services.scanner import scan_single

    key = (watchlist or "").strip().lower()
    tickers = _tickers_for_watchlist(key)
    if not tickers:
        return {
            "available": False,
            "watchlist": key,
            "date": day or _market_day(),
            "count": 0,
            "results": [],
            "error": f"unknown or empty watchlist: {key}",
        }

    scan_day = day or _market_day()
    results: list[dict | None] = [None] * len(tickers)
    workers = _worker_limit(len(tickers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(scan_single, ticker, scan_day): idx
            for idx, ticker in enumerate(tickers)
        }
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:
                results[idx] = {
                    "ticker": tickers[idx],
                    "error": str(exc)[:120],
                    "score": 0,
                }

    clean_results = [r for r in results if r is not None]
    saved = scanner_snapshot_store.save_snapshot(key, scan_day, clean_results)
    return {"available": True, "store": scanner_snapshot_store.backend_name(), **saved}


def save_snapshot(
    watchlist: str,
    results: list[dict],
    *,
    day: Optional[str] = None,
) -> dict:
    """Persist an already-scanned result set without rerunning the scanner."""
    key = (watchlist or "").strip().lower() or "custom"
    clean_results = [r for r in results if isinstance(r, dict)]
    saved = scanner_snapshot_store.save_snapshot(
        key,
        day or _market_day(),
        clean_results,
    )
    prune_snapshots()
    return {"available": True, "store": scanner_snapshot_store.backend_name(), **saved}


def list_snapshots(watchlist: Optional[str] = None) -> dict:
    key = (watchlist or "").strip().lower() or None
    rows = scanner_snapshot_store.list_snapshots(key)
    return {
        "available": bool(rows),
        "count": len(rows),
        "retention_days": retention_days(),
        "store": scanner_snapshot_store.backend_name(),
        "snapshots": rows,
    }


def refresh_snapshots(
    watchlists: Optional[Iterable[str]] = None,
    *,
    day: Optional[str] = None,
) -> dict:
    keys = list(watchlists or configured_watchlists())
    snapshots = [refresh_snapshot(key, day=day) for key in keys]
    return {
        "available": any(s.get("available") for s in snapshots),
        "date": day or _market_day(),
        "store": scanner_snapshot_store.backend_name(),
        "watchlists": snapshots,
    }


def load_snapshot(watchlist: str, *, day: Optional[str] = None) -> dict:
    key = (watchlist or "").strip().lower()
    snap = scanner_snapshot_store.load_snapshot(key, day)
    if not snap:
        return {
            "available": False,
            "watchlist": key,
            "date": day or date.today().isoformat(),
            "count": 0,
            "results": [],
            "store": scanner_snapshot_store.backend_name(),
        }
    return {"available": True, "store": scanner_snapshot_store.backend_name(), **snap}


def delete_snapshot(watchlist: str, *, day: Optional[str] = None) -> dict:
    key = (watchlist or "").strip().lower()
    deleted = scanner_snapshot_store.delete_snapshot(key, day)
    return {
        "watchlist": key,
        "date": day,
        "deleted": deleted,
        "store": scanner_snapshot_store.backend_name(),
    }


def prune_snapshots(
    *,
    days: Optional[int] = None,
    watchlists: Optional[Iterable[str]] = None,
) -> dict:
    keep_days = retention_days() if days is None else days
    keys = list(watchlists) if watchlists is not None else []
    deleted = scanner_snapshot_store.prune_old_snapshots(keep_days, keys)
    return {
        "retention_days": keep_days,
        "watchlists": keys,
        "deleted": deleted,
        "store": scanner_snapshot_store.backend_name(),
    }
