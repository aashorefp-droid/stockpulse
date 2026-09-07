"""
telegram_watchlist.py — Poll a Telegram bot for tickers, persist them locally.

Architecture:
  - poll_and_store()  — called periodically; fetches new messages via getUpdates,
                        acknowledges them (offset), and appends to a local JSON store.
                        Messages never expire because they're saved before the 24-hr
                        Telegram buffer clears.
  - fetch_today_watchlist() — reads the local store and returns today's tickers.

Usage:
    # As a module (called from stock_pulse.py):
    from telegram_watchlist import fetch_today_watchlist, poll_and_store
    poll_and_store()                     # call on startup + periodically
    tickers = fetch_today_watchlist()    # returns list[str] or []

    # Standalone:
    python telegram_watchlist.py          # poll + print today's tickers
    python telegram_watchlist.py --force  # re-poll even if recently polled
"""

import os
import json
import re
import threading
import requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

# ── Config ──────────────────────────────────────────────────────────────────
from config import WATCHLIST_BOT_TOKEN, WATCHLIST_CHAT_ID

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(_BASE_DIR, ".watchlist_cache", "messages.json")
OFFSET_PATH = os.path.join(_BASE_DIR, ".watchlist_cache", "offset.json")
_TICKER_RE = re.compile(r"[A-Z]{1,5}")
_CST = ZoneInfo("America/Chicago")
_POLL_LOCK = threading.Lock()


# ── Local message store ─────────────────────────────────────────────────────

def _ensure_dir():
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)


def _load_store() -> list[dict]:
    if not os.path.exists(STORE_PATH):
        return []
    try:
        with open(STORE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _save_store(messages: list[dict]):
    _ensure_dir()
    with open(STORE_PATH, "w") as f:
        json.dump(messages, f, indent=1)


def _load_offset() -> int | None:
    if not os.path.exists(OFFSET_PATH):
        return None
    try:
        with open(OFFSET_PATH, "r") as f:
            return json.load(f).get("offset")
    except Exception:
        return None


def _save_offset(offset: int):
    _ensure_dir()
    with open(OFFSET_PATH, "w") as f:
        json.dump({"offset": offset}, f)


def _cleanup_old_messages(messages: list[dict], keep_days: int = 7) -> list[dict]:
    cutoff = date.today().toordinal() - keep_days
    return [m for m in messages if date.fromisoformat(m["date"]).toordinal() >= cutoff]


# ── Ticker extraction ───────────────────────────────────────────────────────

_SKIP_WORDS = {
    "THE", "FOR", "AND", "BUY", "SELL", "GET", "ADD", "PUT", "CALL",
    "ALL", "BIG", "TOP", "NEW", "SET", "RUN", "USE", "NOT", "BUT",
    "HAS", "HAD", "WAS", "ARE", "HIS", "HER", "OUR", "CAN", "MAY",
    "NOW", "DAY", "SEE", "SAY", "WAY", "WHO", "OIL", "DID", "GOT",
    "LET", "SAW", "OLD", "END", "FAR", "RAN", "TRY", "ASK", "MEN",
    "LOW", "OWN", "TOO", "ANY", "BAD", "FEW", "LONG", "SHORT",
    "BEAR", "HOLD", "STOP", "LOOK", "SCAN", "WATCH", "THESE",
    "THEM", "THEY", "THIS", "THAT", "WITH", "FROM", "HAVE", "WILL",
    "BEEN", "SOME", "WHAT", "WHEN", "MAKE", "LIKE", "TIME", "JUST",
    "KNOW", "TAKE", "COME", "GOOD", "WELL", "ALSO", "BACK", "ONLY",
    "KEEP", "CLOSE", "HIGH", "ENTRY", "EXIT", "PLAN",
    "ALERT", "FOLLOWING", "LIST", "SYMBOLS", "WERE", "ADDED",
    "MARKET", "REPLY",
}

# Pattern for Schwab/TOS alert format: "... added to CATEGORY: TICKER1, TICKER2, ..."
_ALERT_RE = re.compile(r"added\s+to\s+[\w\-]+:\s*(.+?)(?:\.|$)", re.IGNORECASE)
# Ticker: 1-5 uppercase letters, optionally followed by /A, /B (class shares)
_TICKER_CLEAN_RE = re.compile(r"[A-Z]{1,5}(?:/[A-Z])?")

# Footer patterns to strip before extraction
_FOOTER_RE = re.compile(r"To stop (?:market|all).*", re.IGNORECASE | re.DOTALL)


def _extract_tickers(text: str) -> list[str]:
    # Strip Schwab/TOS footer noise
    text = _FOOTER_RE.sub("", text).strip()

    tickers = []
    seen = set()

    # Try structured alert format first (handles multiple alert lines)
    for match in _ALERT_RE.finditer(text):
        ticker_str = match.group(1)
        for part in ticker_str.split(","):
            cleaned = part.strip().rstrip(".").upper()
            # Handle symbols like PBR/A
            m = _TICKER_CLEAN_RE.search(cleaned)
            if m:
                sym = m.group(0)
                if sym not in _SKIP_WORDS and sym not in seen:
                    seen.add(sym)
                    tickers.append(sym)

    if tickers:
        return tickers

    # Fallback: comma-separated list (no "added to" prefix)
    if "," in text:
        for part in text.split(","):
            cleaned = part.strip().rstrip(".").upper()
            m = _TICKER_CLEAN_RE.search(cleaned.split()[-1] if cleaned.split() else "")
            if m:
                sym = m.group(0)
                if sym not in _SKIP_WORDS and sym not in seen:
                    seen.add(sym)
                    tickers.append(sym)
        if tickers:
            return tickers

    # Fallback: whitespace-separated words
    words = text.upper().split()
    for w in words:
        cleaned = re.sub(r"[^A-Z/]", "", w)
        m = _TICKER_CLEAN_RE.fullmatch(cleaned)
        if m and m.group(0) not in _SKIP_WORDS and m.group(0) not in seen:
            seen.add(m.group(0))
            tickers.append(m.group(0))
    return tickers


# ── Telegram polling ────────────────────────────────────────────────────────

def poll_and_store() -> int:
    """
    Poll getUpdates, save new messages to local store, acknowledge with offset.
    Returns count of new messages saved.  Thread-safe via _POLL_LOCK.
    """
    if not WATCHLIST_BOT_TOKEN or not WATCHLIST_CHAT_ID:
        return 0

    # Prevent concurrent getUpdates calls (causes Telegram 409 Conflict)
    if not _POLL_LOCK.acquire(blocking=False):
        return 0
    try:
        return _poll_and_store_inner()
    finally:
        _POLL_LOCK.release()


def _poll_and_store_inner() -> int:
    url = f"https://api.telegram.org/bot{WATCHLIST_BOT_TOKEN}/getUpdates"
    params = {"limit": 100, "timeout": 0}
    saved_offset = _load_offset()
    if saved_offset is not None:
        params["offset"] = saved_offset

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 409:
            # Another poll in-flight; skip this cycle
            return 0
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"⚠️ Telegram poll failed: {e}")
        return 0

    if not data.get("ok"):
        return 0

    updates = data.get("result", [])
    if not updates:
        return 0

    store = _load_store()
    existing_ids = {m.get("update_id") for m in store}
    new_count = 0
    max_update_id = saved_offset or 0

    for upd in updates:
        uid = upd.get("update_id", 0)
        max_update_id = max(max_update_id, uid)

        if uid in existing_ids:
            continue

        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            continue

        chat_id = str(msg.get("chat", {}).get("id", ""))
        if chat_id != WATCHLIST_CHAT_ID:
            continue

        text = msg.get("text", "") or msg.get("caption", "")
        if not text.strip():
            continue

        msg_ts = msg.get("date", 0)
        msg_dt = datetime.fromtimestamp(msg_ts, tz=_CST)

        store.append({
            "update_id": uid,
            "date": msg_dt.date().isoformat(),
            "time": msg_dt.strftime("%H:%M:%S"),
            "text": text.strip(),
            "tickers": _extract_tickers(text),
        })
        new_count += 1

    # Acknowledge all processed updates
    if max_update_id:
        _save_offset(max_update_id + 1)

    # Clean up old messages and save
    store = _cleanup_old_messages(store)
    _save_store(store)

    if new_count:
        print(f"📩 Saved {new_count} new watchlist message(s) from Telegram")
    return new_count


# ── Public API ──────────────────────────────────────────────────────────────

def fetch_today_watchlist(force: bool = False) -> list[str]:
    """
    Returns the active watchlist tickers from the local store.
    If force=True, polls Telegram first.

    Logic:
      - Check today's messages first (alerts may arrive any time)
      - If none found, check yesterday's messages
      - Fallback: most recent message regardless of date
    """
    if force:
        poll_and_store()

    store = _load_store()
    if not store:
        return []

    now_cst = datetime.now(_CST)
    today_str = now_cst.date().isoformat()
    yesterday_str = (now_cst.date() - timedelta(days=1)).isoformat()

    def _collect(target_date: str) -> list[str]:
        tickers = []
        seen = set()
        for m in store:
            if m.get("date") == target_date:
                for t in m.get("tickers", []):
                    if t not in seen:
                        seen.add(t)
                        tickers.append(t)
        return tickers

    # 1) Today's messages
    result = _collect(today_str)
    if result:
        return result

    # 2) Yesterday's messages
    result = _collect(yesterday_str)
    if result:
        return result

    # 3) Fallback: most recent message with tickers
    for m in reversed(store):
        if m.get("tickers"):
            return list(m["tickers"])

    return []

    return all_tickers


if __name__ == "__main__":
    import sys
    print("🔄 Polling Telegram for new messages...")
    n = poll_and_store()
    print(f"   → {n} new message(s)")

    tickers = fetch_today_watchlist()
    if tickers:
        print(f"📋 Watchlist ({len(tickers)} tickers): {', '.join(tickers)}")
    else:
        print("ℹ️ No tickers found. Send tickers to the bot (e.g. 'AAPL, MSFT, GOOGL').")

    # Show store contents
    store = _load_store()
    if store:
        print(f"\n📂 Local store ({len(store)} messages):")
        for m in store[-5:]:
            print(f"   {m['date']} {m['time']}  {m['text'][:60]}  → {m['tickers']}")
