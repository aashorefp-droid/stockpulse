import os
import runpy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from backend.services.scanner import WATCHLISTS, scan_single
from backend.services.telegram_svc import send_telegram

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.post("/test")
def telegram_test():
    token = _config_value("TELEGRAM_BOT_TOKEN", "DEFAULT_TELEGRAM_BOT_TOKEN")
    chat_id = _config_value("TELEGRAM_CHAT_ID")
    topic_id = _config_value("TELEGRAM_MESSAGE_THREAD_ID")
    if not token or not chat_id:
        raise HTTPException(status_code=503, detail="Telegram is not configured")

    now = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        "⚡ <b>StockPulse Telegram test alert</b>\n"
        f"Sent from Scanner UI at {now} CT.\n"
        "Default-50 lightning watcher path is reachable."
    )
    if not send_telegram(token, chat_id, msg, message_thread_id=topic_id):
        raise HTTPException(status_code=502, detail="Telegram send failed")
    return {"ok": True, "message": "Telegram test alert sent"}


@router.post("/topic-test")
def telegram_topic_test(
    message_thread_id: int | None = None,
    chat_id: str | None = None,
):
    token = _config_value("TELEGRAM_BOT_TOKEN", "DEFAULT_TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or _config_value("TELEGRAM_GROUP_CHAT_ID", "TELEGRAM_CHAT_ID")
    topic_id = message_thread_id or _config_value("TELEGRAM_MESSAGE_THREAD_ID")
    if not token or not chat_id:
        raise HTTPException(status_code=503, detail="Telegram is not configured")

    msg = (
        "🧪 <b>Telegram topic delivery test</b>\n"
        f"chat_id={chat_id}\n"
        f"message_thread_id={topic_id or 'none'}"
    )
    if not send_telegram(token, chat_id, msg, message_thread_id=topic_id):
        raise HTTPException(status_code=502, detail="Telegram send failed")
    return {
        "ok": True,
        "message": "Telegram topic test alert sent",
        "chat_id": chat_id,
        "message_thread_id": topic_id,
    }


@router.post("/lightning-scan")
def telegram_lightning_scan():
    token = _config_value("TELEGRAM_BOT_TOKEN", "DEFAULT_TELEGRAM_BOT_TOKEN")
    chat_id = _config_value("TELEGRAM_CHAT_ID")
    topic_id = _config_value("TELEGRAM_MESSAGE_THREAD_ID")
    if not token or not chat_id:
        raise HTTPException(status_code=503, detail="Telegram is not configured")

    tickers = _combined_watchlist("default", "momentum")
    hits = []
    scanned = 0
    for ticker in tickers:
        res = scan_single(ticker)
        scanned += 1
        if not res.get("error") and res.get("vol_surge"):
            hits.append(res)

    if not hits:
        return {
            "ok": True,
            "count": 0,
            "scanned": scanned,
            "message": f"Scanned {scanned} Default + Momentum tickers; no lightning found",
        }

    today = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    earnings_today = _same_day_earnings_tickers(hits, today)
    msg = _format_lightning_scan_summary(hits, scanned, earnings_today, today)
    if not send_telegram(token, chat_id, msg, message_thread_id=topic_id):
        raise HTTPException(status_code=502, detail="Telegram send failed")
    return {
        "ok": True,
        "count": len(hits),
        "scanned": scanned,
        "message": f"Telegram sent: {len(hits)} lightning ticker(s)",
    }


def _format_lightning_scan_summary(
    hits: list[dict],
    scanned: int,
    earnings_today: set[str] | None = None,
    earnings_date: str | None = None,
) -> str:
    now = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M CT")
    earnings_today = earnings_today or set()
    lines = [
        f"⚡ <b>Default 50 + Momentum 20 lightning scan</b> · {len(hits)} found / {scanned} scanned",
        now,
    ]
    for idx, r in enumerate(hits[:12]):
        line = (
            f"<b>{str(r.get('ticker', '')).upper()}</b> "
            f"${float(r.get('price') or 0):.2f} · {r.get('verdict', '')} · {r.get('direction', '')}"
        )
        if str(r.get("ticker", "")).upper() in earnings_today:
            line += f"\n   <b>!! EARNINGS TODAY ({earnings_date}) !!</b> same-day earnings volume"
        if r.get("opt_strategy"):
            line += f"\n   Options: {r.get('opt_strategy')}"
            if r.get("opt_summary"):
                line += f"\n   {r.get('opt_summary')}"
            if r.get("opt_alt"):
                line += f"\n   {r.get('opt_alt')}"
        else:
            line += "\n   Options: unavailable"
        liquid = r.get("opt_liquid") or []
        if liquid:
            top = liquid[0]
            line += (
                f"\n   OTM: {top.get('type')} ${float(top.get('strike') or 0):.2f} "
                f"{top.get('expiry')} · vol {top.get('volume')} · OI {top.get('oi')} · IV {top.get('iv')}%"
            )
        lines.append(line)
    if len(hits) > 12:
        lines.append(f"+{len(hits) - 12} more")
    return "\n\n".join(lines)


def _same_day_earnings_tickers(hits: list[dict], today: str) -> set[str]:
    tickers: set[str] = set()
    try:
        from backend.db.earnings_tracker import get_all_for_date
        for row in get_all_for_date(today):
            ticker = str(row.get("ticker") or "").upper().strip()
            if ticker:
                tickers.add(ticker)
    except Exception:
        pass

    for row in hits:
        ticker = str(row.get("ticker") or "").upper().strip()
        next_earnings = str(row.get("next_earnings") or row.get("fib_next_earnings") or "").strip()
        if ticker and next_earnings == today:
            tickers.add(ticker)
    return tickers


def _config_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    cfg = _local_config()
    for name in names:
        value = str(cfg.get(name, "") or "").strip()
        if value:
            return value
    return ""


def _combined_watchlist(*keys: str) -> list[str]:
    seen = set()
    tickers: list[str] = []
    for key in keys:
        for ticker in WATCHLISTS.get(key, []):
            t = str(ticker).strip().upper()
            if not t or t in seen:
                continue
            seen.add(t)
            tickers.append(t)
    return tickers


def _local_config() -> dict:
    root = Path(__file__).resolve().parents[2]
    for path in (root / "go-backend" / "config.py", root / "config.py"):
        if not path.exists():
            continue
        try:
            return runpy.run_path(str(path))
        except Exception:
            continue
    return {}


@router.get("/watchlist")
def get_telegram_watchlist(force: bool = False):
    try:
        from telegram_watchlist import poll_and_store, fetch_today_watchlist
        poll_and_store()
        tickers = fetch_today_watchlist(force=force)
        return {"ok": True, "tickers": tickers or []}
    except Exception as e:
        return {"ok": False, "tickers": [], "error": str(e)}

