"""
telegram_watchlist.py — Poll a Telegram bot for tickers, persist them locally.

Ported from the root stock_pulse project. The scheduler calls poll_and_store()
once at startup and again daily at ~8 PM CST; the scanner's "telegram"
watchlist option then serves fetch_today_watchlist().

  - poll_and_store()        — getUpdates, acknowledge via offset, append to a
                              local JSON store (messages are saved before the
                              24-hr Telegram buffer clears, so they never expire).
  - fetch_today_watchlist() — today's tickers (→ yesterday → most-recent fallback).
"""
import os
import json
import re
import threading
import requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from backend.config import WATCHLIST_BOT_TOKEN, WATCHLIST_CHAT_ID

_BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STORE_PATH  = os.path.join(_BASE_DIR, ".watchlist_cache", "messages.json")
OFFSET_PATH = os.path.join(_BASE_DIR, ".watchlist_cache", "offset.json")
_TICKER_RE  = re.compile(r"[A-Z]{1,5}")
_CST        = ZoneInfo("America/Chicago")
_POLL_LOCK  = threading.Lock()


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
    "BULL", "BEAR", "HOLD", "STOP", "LOOK", "SCAN", "WATCH", "THESE",
    "THEM", "THEY", "THIS", "THAT", "WITH", "FROM", "HAVE", "WILL",
    "BEEN", "SOME", "WHAT", "WHEN", "MAKE", "LIKE", "TIME", "JUST",
    "KNOW", "TAKE", "COME", "GOOD", "WELL", "ALSO", "BACK", "ONLY",
    "KEEP", "OPEN", "CLOSE", "HIGH", "ENTRY", "EXIT", "PLAN",
}


def _extract_tickers(text: str) -> list[str]:
    if "," in text:
        # "Alert: ... IFCBULL: AEHR, ANRO, ..." → last word of each comma part
        tickers = []
        for part in text.split(","):
            word = re.sub(r"[^A-Za-z]", "", part.strip().split()[-1]).upper() if part.strip() else ""
            if word and _TICKER_RE.fullmatch(word) and word not in _SKIP_WORDS:
                tickers.append(word)
        if tickers:
            return tickers
    words = text.upper().split()
    return [re.sub(r"[^A-Z]", "", w) for w in words
            if _TICKER_RE.fullmatch(re.sub(r"[^A-Z]", "", w)) and re.sub(r"[^A-Z]", "", w) not in _SKIP_WORDS]


# ── Telegram polling ────────────────────────────────────────────────────────

def poll_and_store() -> int:
    """Poll getUpdates, save new messages, acknowledge offset. Thread-safe."""
    if not WATCHLIST_BOT_TOKEN or not WATCHLIST_CHAT_ID:
        return 0
    # Prevent concurrent getUpdates calls (Telegram 409 Conflict otherwise)
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
            return 0  # another poll in-flight
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"⚠️ Telegram watchlist poll failed: {e}")
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

    skipped_chat: set = set()
    for upd in updates:
        uid = upd.get("update_id", 0)
        max_update_id = max(max_update_id, uid)
        if uid in existing_ids:
            continue
        # Accept DMs/groups AND channel broadcasts (alert feeds are usually
        # channels → channel_post, which the old code silently dropped).
        msg = (upd.get("message") or upd.get("edited_message")
               or upd.get("channel_post") or upd.get("edited_channel_post"))
        if not msg:
            continue
        chat_id = str(msg.get("chat", {}).get("id", ""))
        # If WATCHLIST_CHAT_ID is set, enforce it; if blank, accept any chat.
        if WATCHLIST_CHAT_ID and chat_id != str(WATCHLIST_CHAT_ID):
            skipped_chat.add(chat_id)
            continue
        text = msg.get("text", "") or msg.get("caption", "")
        if not text.strip():
            continue
        msg_dt = datetime.fromtimestamp(msg.get("date", 0), tz=_CST)
        store.append({
            "update_id": uid,
            "date": msg_dt.date().isoformat(),
            "time": msg_dt.strftime("%H:%M:%S"),
            "text": text.strip(),
            "tickers": _extract_tickers(text),
        })
        new_count += 1

    if max_update_id:
        _save_offset(max_update_id + 1)

    store = _cleanup_old_messages(store)
    _save_store(store)

    if new_count:
        print(f"📩 Saved {new_count} new watchlist message(s) from Telegram")
    if skipped_chat:
        print(f"⚠️ Telegram: {len(updates)} update(s) but none matched "
              f"WATCHLIST_CHAT_ID={WATCHLIST_CHAT_ID!r}; saw chat id(s): "
              f"{sorted(skipped_chat)} — set WATCHLIST_CHAT_ID to one of these.")
    return new_count


# ── Public API ──────────────────────────────────────────────────────────────

def fetch_today_watchlist(force: bool = False) -> list[str]:
    """
    Active watchlist tickers from the local store.
      1) today's messages  2) yesterday's  3) most-recent message with tickers
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
        tickers, seen = [], set()
        for m in store:
            if m.get("date") == target_date:
                for t in m.get("tickers", []):
                    if t not in seen:
                        seen.add(t)
                        tickers.append(t)
        return tickers

    return (
        _collect(today_str)
        or _collect(yesterday_str)
        or next((list(m["tickers"]) for m in reversed(store) if m.get("tickers")), [])
    )
