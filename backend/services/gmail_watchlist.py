"""
gmail_watchlist.py — Read ThinkOrSwim scan alert emails from Gmail via IMAP
and persist the tickers locally. Durable source of truth for the 7 PM TOS scan list:

    ThinkOrSwim scan  →  Gmail  →  (this module reads IMAP)  →  local store

  - poll_and_store()        — IMAP-fetch recent TOS scan email(s), parse tickers,
                              categorize by scan name, append to local JSON store.
  - fetch_today_watchlist() — tickers matching optional comma-separated subjects/scans.
  - get_watchlist_status()  — metadata, available scan categories, counts, and active tickers.
"""
import os
import re
import json
import email
import email.message
import email.utils
from email.message import Message
import imaplib
import threading
from email.header import decode_header
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from backend.config import (
    GMAIL_USER, GMAIL_APP_PASSWORD, GMAIL_IMAP_HOST,
    TOS_EMAIL_FROM, TOS_EMAIL_SUBJECT, TOS_LOOKBACK_DAYS,
)

# ── Skip & Stop words ────────────────────────────────────────────────────────
_SKIP_WORDS = {
    "THE", "FOR", "AND", "BUY", "SELL", "GET", "ADD", "PUT", "CALL",
    "ALL", "BIG", "TOP", "NEW", "SET", "RUN", "USE", "NOT", "BUT",
    "HAS", "HAD", "WAS", "WERE", "ARE", "HIS", "HER", "OUR", "CAN", "MAY",
    "NOW", "DAY", "SEE", "SAY", "WAY", "WHO", "OIL", "DID", "GOT",
    "LET", "SAW", "OLD", "END", "FAR", "RAN", "TRY", "ASK", "MEN",
    "LOW", "OWN", "TOO", "ANY", "BAD", "FEW", "LONG", "SHORT",
    "BULL", "BEAR", "HOLD", "STOP", "LOOK", "SCAN", "WATCH", "THESE",
    "THEM", "THEY", "THIS", "THAT", "WITH", "FROM", "HAVE", "WILL",
    "BEEN", "SOME", "WHAT", "WHEN", "MAKE", "LIKE", "TIME", "JUST",
    "KNOW", "TAKE", "COME", "GOOD", "WELL", "ALSO", "BACK", "ONLY",
    "KEEP", "OPEN", "CLOSE", "HIGH", "ENTRY", "EXIT", "PLAN",
    "FOLLOWING", "LIST", "OF", "DAILY", "WEEKLY", "MONTHLY",
}

_TOS_STOP = {
    "ALERT", "ALERTS", "NEW", "SYMBOL", "SYMBOLS", "WAS", "WERE", "ADDED", "TO",
    "WATCHLIST", "SCAN", "STUDY", "QUERY", "TOS", "TD", "INC", "LLC", "LP",
    "NA", "SEC", "FINRA", "SIPC", "USA", "ET", "AM", "PM", "ID", "FAQ",
    "PDF", "RE", "FW", "FWD",
}

_STOP = _SKIP_WORDS | _TOS_STOP

_TICKER = re.compile(r"\b([A-Z]{1,5}(?:\.[A-Z])?)\b")
_DISCLAIMER_RE = re.compile(
    r"(TD Ameritrade|Charles Schwab|Member SIPC|Member FINRA|"
    r"This (?:e-?mail|message)|The information|Do not reply|"
    r"Past performance|All rights reserved|thinkorswim is|©)", re.I)


def _tos_extract(subject: str, body: str) -> tuple[str, list[str]]:
    """
    Extract scan name and clean tickers from ThinkOrSwim subject & body.
    Handles:
      - 'Alert: Following list of symbols were added to FIB-STRONGBUY: CNH, CRDO, ...'
      - 'Alert: New symbols: AFL, ALLT, AMCR ... were added to options-mispriced.'
      - 'Alert: New symbol: HYMB was added to options-mispriced.'
    """
    subj = (subject or "").strip().replace("\r\n", " ").replace("\n", " ")
    out: list[str] = []
    seen: set = set()

    # 1. Identify scan / watchlist name (e.g. 'added to <SCAN>')
    scan_name = ""
    m_scan = re.search(r"added to\s+([A-Za-z0-9_\-]+)", subj, re.I)
    if m_scan:
        scan_name = m_scan.group(1).strip()

    # 2. Pattern 1: 'symbols ... added to <SCAN>: T1, T2, ...'
    m1 = re.search(r"symbols?\s+(?:were|was)?\s*added\s+to\s+[^:]+:\s*(.+)", subj, re.I)
    if m1:
        tail = m1.group(1)
        for tok in _TICKER.findall(tail.upper()):
            if tok not in _STOP and tok not in seen:
                seen.add(tok)
                out.append(tok)

    # 3. Pattern 2: 'New symbol(s): T1, T2, ... were/was added to ...'
    if not out:
        m2 = re.search(r"new\s+symbols?\s*[:\-]?\s*(.+)", subj, re.I)
        if m2:
            tail = re.split(r"\b(?:were\s+added|was\s+added|added\s+to)\b",
                            m2.group(1), maxsplit=1, flags=re.I)[0]
            for tok in _TICKER.findall(tail.upper()):
                if tok not in _STOP and tok not in seen:
                    seen.add(tok)
                    out.append(tok)

    # 4. Pattern 3: Generic 'Alert: ... : SYM'
    if not out:
        m3 = re.search(r":\s*([A-Za-z.\s,]+)\s*$", subj)
        if m3:
            for tok in _TICKER.findall(m3.group(1).upper()):
                if tok not in _STOP and tok not in seen:
                    seen.add(tok)
                    out.append(tok)

    # 5. Pattern 4: Body fallback (trim legal disclaimer first)
    if not out:
        b = body or ""
        cut = _DISCLAIMER_RE.search(b)
        if cut:
            b = b[:cut.start()]
        mb = re.search(r"symbols?\s*[:=]\s*(.+)", b, re.I)
        target = mb.group(1).splitlines()[0] if mb else b
        for tok in _TICKER.findall(target.upper()):
            if tok not in _STOP and tok not in seen:
                seen.add(tok)
                out.append(tok)

    return (scan_name or "GENERAL", out)


_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(_BASE_DIR, ".gmail_cache", "messages.json")
_CST       = ZoneInfo("America/Chicago")
_POLL_LOCK = threading.Lock()


# ── Local store ─────────────────────────────────────────────────────────────

def _load_store() -> list[dict]:
    if not os.path.exists(STORE_PATH):
        return []
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_store(messages: list[dict]):
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
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
            out += str(txt)
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
        print("[!] Gmail watchlist: GMAIL_USER / GMAIL_APP_PASSWORD not set")
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
        print(f"[!] Gmail IMAP login failed: {e}")
        return 0

    try:
        M.select("INBOX", readonly=True)
        since = (date.today() - timedelta(days=max(1, TOS_LOOKBACK_DAYS)))
        since_str = since.strftime("%d-%b-%Y")
        criteria = ["SINCE", since_str]
        if TOS_EMAIL_FROM:
            criteria += ["FROM", f'"{TOS_EMAIL_FROM}"']
        
        # Match alerts by default; if custom subject set in env, use it
        if TOS_EMAIL_SUBJECT and TOS_EMAIL_SUBJECT.lower() != "alert:":
            criteria += ["SUBJECT", f'"{TOS_EMAIL_SUBJECT}"']
        else:
            criteria += ["SUBJECT", '"Alert:"']

        typ, data = M.search(None, *criteria)
        if typ != "OK" or not data or not data[0]:
            print(f"[INFO] Gmail watchlist: no TOS emails since {since_str} "
                  f"(FROM~{TOS_EMAIL_FROM!r} SUBJECT~{TOS_EMAIL_SUBJECT!r})")
            return 0

        ids = data[0].split()
        store = _load_store()
        seen_ids = {m.get("msg_id") for m in store}
        new_count = 0

        # newest first; fetch up to 150 recent alerts
        for num in reversed(ids[-150:]):
            typ, msg_data = M.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])

            msg_id = (msg.get("Message-ID") or "").strip() or f"uid-{num.decode()}"
            if msg_id in seen_ids:
                continue

            subject = _decode(msg.get("Subject"))
            sender  = _decode(msg.get("From"))
            if TOS_EMAIL_FROM and TOS_EMAIL_FROM.lower() not in sender.lower():
                continue

            body = _body_text(msg)
            scan_name, tickers = _tos_extract(subject, body)
            if not tickers:
                continue

            try:
                dt = email.utils.parsedate_to_datetime(msg.get("Date"))
                dt_cst = dt.astimezone(_CST) if dt else datetime.now(_CST)
            except Exception:
                dt_cst = datetime.now(_CST)

            store.append({
                "msg_id":    msg_id,
                "date":      dt_cst.date().isoformat(),
                "time":      dt_cst.strftime("%H:%M:%S"),
                "subject":   subject[:160],
                "scan_name": scan_name,
                "from":      sender[:120],
                "tickers":   tickers,
            })
            seen_ids.add(msg_id)
            new_count += 1

        store = _cleanup_old(store)
        _save_store(store)
        if new_count:
            print(f"[OK] Gmail watchlist: saved {new_count} new TOS scan email(s)")
        return new_count
    finally:
        try:
            M.logout()
        except Exception:
            pass


def _matches_filter(m: dict, tokens: list[str]) -> bool:
    if not tokens:
        return True
    subj = (m.get("subject") or "").upper()
    scan = (m.get("scan_name") or "").upper()
    for tok in tokens:
        t = tok.strip().upper()
        if t and (t in subj or t in scan):
            return True
    return False


def fetch_today_watchlist(subjects: str | list[str] | None = None, days: int = 1, force: bool = False) -> list[str]:
    """
    Return tickers filtered by:
      - days: number of days back (default: 1 = same day / today only; 2 = today + yesterday, etc.)
      - subjects: comma-separated subjects or scan names (e.g. 'FIB-STRONGBUY, IFC-BULLISH')
    """
    if force:
        poll_and_store()

    store = _load_store()
    if not store:
        return []

    tokens = []
    if isinstance(subjects, str) and subjects.strip():
        tokens = [t.strip() for t in subjects.split(",") if t.strip() and t.strip().upper() != "ALL"]
    elif isinstance(subjects, (list, set, tuple)):
        tokens = [str(t).strip() for t in subjects if str(t).strip() and str(t).strip().upper() != "ALL"]

    now_cst = datetime.now(_CST)
    today_date = now_cst.date()
    cutoff_ordinal = (today_date - timedelta(days=max(0, days - 1))).toordinal()

    matched_tickers: list[str] = []
    seen: set = set()

    # Search messages in reverse chronological order (newest first)
    for m in reversed(store):
        try:
            m_date = date.fromisoformat(m["date"]).toordinal()
            if m_date < cutoff_ordinal:
                continue
        except Exception:
            continue

        if not _matches_filter(m, tokens):
            continue

        for t in m.get("tickers", []):
            if t not in seen:
                seen.add(t)
                matched_tickers.append(t)

    return matched_tickers


def get_watchlist_status(subjects: str | None = None, days: int = 1) -> dict:
    """Return status metadata, available scan categories, and filtered tickers."""
    store = _load_store()
    tickers = fetch_today_watchlist(subjects=subjects, days=days)
    configured = bool(GMAIL_USER and GMAIL_APP_PASSWORD)
    latest = store[-1] if store else None

    # Aggregate available scan categories within the requested days window
    now_cst = datetime.now(_CST)
    cutoff_ordinal = (now_cst.date() - timedelta(days=max(0, days - 1))).toordinal()

    scan_counts: dict[str, int] = {}
    for m in store:
        try:
            m_date = date.fromisoformat(m["date"]).toordinal()
            if m_date < cutoff_ordinal:
                continue
        except Exception:
            pass

        sn = m.get("scan_name") or "GENERAL"
        if sn and sn != "GENERAL":
            scan_counts[sn] = scan_counts.get(sn, 0) + len(m.get("tickers", []))

    sorted_scans = [
        {"name": k, "count": v}
        for k, v in sorted(scan_counts.items(), key=lambda x: -x[1])
    ]

    return {
        "status": "configured" if configured else "credentials_missing",
        "configured": configured,
        "gmail_user": GMAIL_USER if GMAIL_USER else None,
        "count": len(tickers),
        "tickers": tickers,
        "days": days,
        "filter": subjects or "",
        "available_scans": sorted_scans,
        "total_messages": len(store),
        "latest_date": latest.get("date") if latest else None,
        "latest_time": latest.get("time") if latest else None,
    }
