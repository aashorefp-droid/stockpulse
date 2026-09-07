"""
webhook_server.py — Lightweight FastAPI webhook that receives alert text from Make.com
and stores tickers in the same .watchlist_cache/messages.json used by telegram_watchlist.py.

Usage:
    python webhook_server.py          # starts on port 8600
    python webhook_server.py --port 8700

Make.com sends POST to:
    http://<your-ip>:8600/webhook/tickers
    Body: raw alert text (Content-Type: text/plain)
      or  JSON {"text": "..."}  (Content-Type: application/json)
"""

import os
import json
import re
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ── Paths (same store as telegram_watchlist.py) ─────────────────────────────
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(_BASE_DIR, ".watchlist_cache", "messages.json")
_CST = ZoneInfo("America/Chicago")

# ── Ticker extraction (same logic as telegram_watchlist.py) ─────────────────
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

_ALERT_RE = re.compile(r"added\s+to\s+[\w\-]+:\s*(.+?)(?:\.|$)", re.IGNORECASE)
_TICKER_CLEAN_RE = re.compile(r"[A-Z]{1,5}(?:/[A-Z])?")
_FOOTER_RE = re.compile(r"To stop (?:market|all).*", re.IGNORECASE | re.DOTALL)


def _extract_tickers(text: str) -> list[str]:
    text = _FOOTER_RE.sub("", text).strip()
    tickers, seen = [], set()

    for match in _ALERT_RE.finditer(text):
        ticker_str = match.group(1)
        for part in ticker_str.split(","):
            cleaned = part.strip().rstrip(".").upper()
            m = _TICKER_CLEAN_RE.search(cleaned)
            if m:
                sym = m.group(0)
                if sym not in _SKIP_WORDS and sym not in seen:
                    seen.add(sym)
                    tickers.append(sym)

    if tickers:
        return tickers

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

    words = text.upper().split()
    for w in words:
        cleaned = re.sub(r"[^A-Z/]", "", w)
        m = _TICKER_CLEAN_RE.fullmatch(cleaned)
        if m and m.group(0) not in _SKIP_WORDS and m.group(0) not in seen:
            seen.add(m.group(0))
            tickers.append(m.group(0))
    return tickers


# ── Store helpers ───────────────────────────────────────────────────────────

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


# ── FastAPI app ─────────────────────────────────────────────────────────────

app = FastAPI(title="Watchlist Webhook", docs_url="/docs")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/tickers")
async def receive_tickers(request: Request):
    """
    Accepts alert text from Make.com.
    Supports:
      - Content-Type: text/plain  → raw body is the alert text
      - Content-Type: application/json  → {"text": "..."}
    """
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        body = await request.json()
        text = body.get("text", "") if isinstance(body, dict) else ""
    else:
        raw = await request.body()
        text = raw.decode("utf-8", errors="replace")

    if not text.strip():
        return JSONResponse({"error": "empty body"}, status_code=400)

    tickers = _extract_tickers(text)
    if not tickers:
        return JSONResponse({
            "warning": "no tickers extracted",
            "raw_text": text[:200],
        }, status_code=200)

    now_cst = datetime.now(_CST)
    store = _load_store()

    store.append({
        "update_id": f"webhook_{int(now_cst.timestamp())}",
        "date": now_cst.date().isoformat(),
        "time": now_cst.strftime("%H:%M:%S"),
        "text": text.strip(),
        "tickers": tickers,
        "source": "make.com",
    })
    _save_store(store)

    print(f"📩 Webhook received {len(tickers)} tickers: {', '.join(tickers[:10])}")
    return {
        "status": "ok",
        "tickers_saved": len(tickers),
        "tickers": tickers,
    }


@app.get("/tickers/today")
async def get_today_tickers():
    """Returns today's tickers from the store (for debugging)."""
    store = _load_store()
    today_str = datetime.now(_CST).date().isoformat()
    tickers, seen = [], set()
    for m in store:
        if m.get("date") == today_str:
            for t in m.get("tickers", []):
                if t not in seen:
                    seen.add(t)
                    tickers.append(t)
    return {"date": today_str, "count": len(tickers), "tickers": tickers}


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8600)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    print(f"🚀 Webhook server starting on {args.host}:{args.port}")
    print(f"   POST http://localhost:{args.port}/webhook/tickers")
    print(f"   GET  http://localhost:{args.port}/tickers/today")
    print(f"   GET  http://localhost:{args.port}/docs")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
