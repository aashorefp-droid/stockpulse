"""
gmail_watchlist.py — Read the ThinkOrSwim scan email from Gmail and persist
the tickers locally. This is the durable source of truth for the 7 PM list:

    ThinkOrSwim scan  →  Gmail  →  (this module reads IMAP)  →  local store

Replaces the fragile Telegram round-trip. Gmail has no 24-hr buffer and is
single-consumer safe.

  - poll_and_store()        — IMAP-fetch recent TOS scan email(s), parse tickers,
                              append to a local JSON store (dedup by Message-ID).
  - fetch_today_watchlist() — today's tickers (→ yesterday → most-recent fallback).
"""
import os
import re
import json
import email
import imaplib
import threading
from email.header import decode_header
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from backend.config import (
    GMAIL_USER, GMAIL_APP_PASSWORD, GMAIL_IMAP_HOST,
    TOS_EMAIL_FROM, TOS_EMAIL_SUBJECT, TOS_LOOKBACK_DAYS,
)
from backend.services.telegram_watchlist import _SKIP_WORDS

# ── TOS "Alert: New symbol" parser ──────────────────────────────────────────
# ThinkOrSwim scan alerts are one email per symbol; the ticker is in the
# subject ("Alert: New symbol: AAPL ..."). The body is mostly a legal
# disclaimer full of ALL-CAPS words — we trim it and prefer the subject.
_TICKER = re.compile(r"\b([A-Z]{1,5}(?:\.[A-Z])?)\b")
_DISCLAIMER_RE = re.compile(
    r"(TD Ameritrade|Charles Schwab|Member SIPC|Member FINRA|"
    r"This (?:e-?mail|message)|The information|Do not reply|"
    r"Past performance|All rights reserved|thinkorswim is|©)", re.I)
_TOS_STOP = {
    "ALERT", "ALERTS", "NEW", "SYMBOL", "SYMBOLS", "WAS", "ADDED", "TO",
    "WATCHLIST", "SCAN", "STUDY", "QUERY", "TOS", "TD", "INC", "LLC", "LP",
    "NA", "SEC", "FINRA", "SIPC", "USA", "ET", "AM", "PM", "ID", "FAQ",
    "PDF", "RE", "FW", "FWD",
}
_STOP = _SKIP_WORDS | _TOS_STOP


def _tos_extract(subject: str, body: str) -> list[str]:
    out: list[str] = []
    seen: set = set()

    def _add(s: str):
        for tok in _TICKER.findall((s or "").upper()):
            if tok not in _STOP and tok not in seen:
                seen.add(tok)
                out.append(tok)

    subj = subject or ""
    m = re.search(r"new\s+symbol(?:\(s\)|s)?\s*[:\-]?\s*(.+)", subj, re.I)
    if m:
        # drop trailing "... was added to <watchlist/scan>"
        tail = re.split(r"\b(?:was\s+added|added\s+to)\b",
                         m.group(1), maxsplit=1, flags=re.I)[0]
        _add(tail)
    if not out:                                  # generic "Alert: …: SYM"
        m2 = re.search(r":\s*([A-Za-z.\s,]+)\s*$", subj)
        if m2:
            _add(m2.group(1))
    if not out:                                  # body fallback (trim legal)
        b = body or ""
        cut = _DISCLAIMER_RE.search(b)
        if cut:
            b = b[:cut.start()]
        mb = re.search(r"symbol[s]?\s*[:=]\s*(.+)", b, re.I)
        _add(mb.group(1).splitlines()[0] if mb else b)
    return out

_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(_BASE_DIR, ".gmail_cache", "messages.json")
_CST       = ZoneInfo("America/Chicago")
_POLL_LOCK = threading.Lock()


# ── Local store (same shape as the telegram store) ──────────────────────────

def _load_store() -> list[dict]:
    if not os.path.exists(STORE_PATH):
        return []
    try:
        with open(STORE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _save_store(messages: list[dict]):
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
    with open(STORE_PATH, "w") as f:
        json.dump(messages, f, indent=1)


def _cleanup_old(messages: list[dict], keep_days: int = 7) -> list[dict]:
    cutoff = date.today().toordinal() - keep_days
    out = []
    for m in messages:
        try:
            if date.fromisoformat(m["date"]).toordinal() >= cutoff:
                out.append(m)
        except Exception:
            out.append(m)
    return out


# ── Email parsing helpers ───────────────────────────────────────────────────

def _decode(s) -> str:
    if not s:
        return ""
    parts = decode_header(s)
    out = ""
    for txt, enc in parts:
        if isinstance(txt, bytes):
            try:
                out += txt.decode(enc or "utf-8", errors="replace")
            except Exception:
                out += txt.decode("utf-8", errors="replace")
        else:
            out += txt
    return out


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;", " ", html)
    return re.sub(r"\s+", " ", html).strip()


def _body_text(msg: email.message.Message) -> str:
    """Prefer text/plain; fall back to stripped text/html."""
    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if ctype == "text/plain":
                plain += text + "\n"
            elif ctype == "text/html":
                html += text + "\n"
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace") if payload else ""
        except Exception:
            text = ""
        if msg.get_content_type() == "text/html":
            html = text
        else:
            plain = text
    return (plain.strip() or _strip_html(html)).strip()


# ── Public API ──────────────────────────────────────────────────────────────

def poll_and_store() -> int:
    """Fetch recent TOS scan email(s) from Gmail, parse tickers, persist."""
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("⚠️ Gmail watchlist: GMAIL_USER / GMAIL_APP_PASSWORD not set")
        return 0
    if not _POLL_LOCK.acquire(blocking=False):
        return 0
    try:
        return _poll_inner()
    finally:
        _POLL_LOCK.release()


def _poll_inner() -> int:
    try:
        M = imaplib.IMAP4_SSL(GMAIL_IMAP_HOST)
        M.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    except Exception as e:
        print(f"⚠️ Gmail IMAP login failed: {e}")
        return 0

    try:
        M.select("INBOX", readonly=True)
        since = (date.today() - timedelta(days=max(1, TOS_LOOKBACK_DAYS)))
        since_str = since.strftime("%d-%b-%Y")
        criteria = ["SINCE", since_str]
        if TOS_EMAIL_FROM:
            criteria += ["FROM", f'"{TOS_EMAIL_FROM}"']
        if TOS_EMAIL_SUBJECT:
            criteria += ["SUBJECT", f'"{TOS_EMAIL_SUBJECT}"']

        typ, data = M.search(None, *criteria)
        if typ != "OK" or not data or not data[0]:
            print(f"ℹ️ Gmail watchlist: no TOS emails since {since_str} "
                  f"(FROM~{TOS_EMAIL_FROM!r} SUBJECT~{TOS_EMAIL_SUBJECT!r})")
            return 0

        ids = data[0].split()
        store = _load_store()
        seen_ids = {m.get("msg_id") for m in store}
        new_count = 0

        # newest first; keep it bounded
        for num in reversed(ids[-25:]):
            typ, msg_data = M.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])

            msg_id = (msg.get("Message-ID") or "").strip() or f"uid-{num.decode()}"
            if msg_id in seen_ids:
                continue

            subject = _decode(msg.get("Subject"))
            sender  = _decode(msg.get("From"))
            # Belt-and-suspenders: IMAP FROM search can be fuzzy
            if TOS_EMAIL_FROM and TOS_EMAIL_FROM.lower() not in sender.lower():
                continue

            body = _body_text(msg)
            tickers = _tos_extract(subject, body)
            if not tickers:
                continue

            try:
                dt = email.utils.parsedate_to_datetime(msg.get("Date"))
                dt_cst = dt.astimezone(_CST) if dt else datetime.now(_CST)
            except Exception:
                dt_cst = datetime.now(_CST)

            store.append({
                "msg_id":  msg_id,
                "date":    dt_cst.date().isoformat(),
                "time":    dt_cst.strftime("%H:%M:%S"),
                "subject": subject[:120],
                "from":    sender[:120],
                "tickers": tickers,
            })
            seen_ids.add(msg_id)
            new_count += 1

        store = _cleanup_old(store)
        _save_store(store)
        if new_count:
            print(f"📧 Gmail watchlist: saved {new_count} new TOS scan email(s)")
        return new_count
    finally:
        try:
            M.logout()
        except Exception:
            pass


def fetch_today_watchlist(force: bool = False) -> list[str]:
    """today's tickers → yesterday's → most-recent message with tickers."""
    if force:
        poll_and_store()

    store = _load_store()
    if not store:
        return []

    now_cst = datetime.now(_CST)
    today_str = now_cst.date().isoformat()
    yest_str  = (now_cst.date() - timedelta(days=1)).isoformat()

    def _collect(d: str) -> list[str]:
        out, seen = [], set()
        for m in store:
            if m.get("date") == d:
                for t in m.get("tickers", []):
                    if t not in seen:
                        seen.add(t)
                        out.append(t)
        return out

    return (
        _collect(today_str)
        or _collect(yest_str)
        or next((list(m["tickers"]) for m in reversed(store) if m.get("tickers")), [])
    )
