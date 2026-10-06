"""
breakout_scanner.py — Breakout Scanner & Telegram Dispatcher for Default 50 & Momentum 50.

Scans the complete combined universe:
  1. Default 50 Watchlist (core liquid large caps & ETFs)
  2. Momentum 50 Watchlist (high-beta momentum & growth leaders)

Detects:
  - Fresh breakouts triggered today (Close > 20D/50D/52W high with volume expansion)
  - Active breakout triggers holding above buy zone with volume surge
  - High conviction breakout setups with institutional volume accumulation

Formats and dispatches high-definition trade plans to Telegram.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from zoneinfo import ZoneInfo

from backend.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_MESSAGE_THREAD_ID,
)
from backend.services.scanner import WATCHLISTS, scan_single
from backend.services.telegram_svc import send_telegram

logger = logging.getLogger(__name__)


def get_breakout_universe() -> Dict[str, str]:
    """
    Assembles unique tickers from Default 50 and Momentum 50 watchlists,
    mapping each ticker to its source watchlist tag.
    """
    default_wl = WATCHLISTS.get("default", [])
    momentum_wl = WATCHLISTS.get("momentum", [])

    sources: Dict[str, str] = {}
    for t in default_wl:
        sym = t.strip().upper()
        if sym:
            sources[sym] = "Default 50"

    for t in momentum_wl:
        sym = t.strip().upper()
        if sym in sources:
            sources[sym] = "Default 50 & Momentum 50"
        else:
            sources[sym] = "Momentum 50"

    return sources


def scan_breakout_universe(max_workers: int = 10) -> Dict[str, Any]:
    """
    Executes a parallel scan across the combined Default 50 & Momentum 50 universe.
    Filters setups that qualify for a Breakout Alert.
    """
    sources = get_breakout_universe()
    tickers = list(sources.keys())
    total_scanned = len(tickers)

    logger.info(f"[breakout_scanner] Starting scan of {total_scanned} tickers (Default 50 + Momentum 50)")

    raw_results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(scan_single, t): t for t in tickers}
        for fut in as_completed(future_map):
            t = future_map[fut]
            try:
                res = fut.result()
                if res and not res.get("error"):
                    res["source_watchlist"] = sources.get(t, "Watchlist")
                    raw_results.append(res)
            except Exception as e:
                logger.warning(f"[breakout_scanner] Failed scanning {t}: {e}")

    # Categorize qualifying breakout setups
    today_breakouts: List[Dict[str, Any]] = []
    active_triggers: List[Dict[str, Any]] = []
    recent_breakouts: List[Dict[str, Any]] = []

    for item in raw_results:
        price = item.get("price") or 0.0
        entry = item.get("entry") or price
        direction = str(item.get("direction", "LONG")).upper()
        verdict = str(item.get("verdict", "")).upper()
        vol_surge = bool(item.get("vol_surge") or item.get("has_vol_surge"))
        vol_ratio = float(item.get("vol_ratio") or 1.0)
        breakout_score = int(item.get("breakout_score") or 0)
        entry_grade = str(item.get("entry_grade", "C"))

        days_ago = item.get("last_breakout_days_ago")
        b_type = item.get("last_breakout_type") or "20D Pivot Breakout"

        is_bullish = direction == "LONG" or "BULL" in verdict
        if not is_bullish or price <= 0:
            continue

        # Condition 1: Breakout occurred TODAY (days_ago == 0)
        if days_ago == 0:
            today_breakouts.append(item)
            continue

        # Condition 2: Active Breakout Trigger at or above entry zone with volume surge
        entry_zone_min = float(item.get("retest_zone_min") or round(entry * 0.995, 2))
        is_at_or_above_trigger = price >= entry_zone_min
        has_volume_expansion = vol_surge or vol_ratio >= 1.15

        if is_at_or_above_trigger and has_volume_expansion and (breakout_score >= 2 or entry_grade in ("S", "A", "B+", "B")):
            active_triggers.append(item)
            continue

        # Condition 3: Recent breakout 1 session ago holding strong
        if days_ago == 1 and (vol_ratio >= 1.0 or entry_grade in ("S", "A", "B+")):
            recent_breakouts.append(item)

    # Sort each category by volume ratio and score descending
    def _rank_key(x: Dict[str, Any]) -> float:
        score = float(x.get("score") or 0)
        vr = float(x.get("vol_ratio") or 1.0)
        b_score = float(x.get("breakout_score") or 0)
        return score * 2.0 + vr * 3.0 + b_score * 5.0

    today_breakouts.sort(key=_rank_key, reverse=True)
    active_triggers.sort(key=_rank_key, reverse=True)
    recent_breakouts.sort(key=_rank_key, reverse=True)

    all_breakout_matches = today_breakouts + active_triggers + recent_breakouts

    return {
        "total_scanned": total_scanned,
        "total_matches": len(all_breakout_matches),
        "today_breakouts": today_breakouts,
        "active_triggers": active_triggers,
        "recent_breakouts": recent_breakouts,
        "all_matches": all_breakout_matches,
    }


def _format_breakout_card(item: Dict[str, Any], idx: int) -> str:
    """Formats an individual high-definition breakout trade card for Telegram HTML."""
    sym = str(item.get("ticker", "—")).upper()
    price = item.get("price") or 0.0
    entry = item.get("entry") or price
    stop = item.get("stop_loss")
    t1 = item.get("target1")
    t2 = item.get("target2")
    risk_pct = item.get("risk_pct")
    rr = item.get("rr_t1") or 2.0
    grade = item.get("entry_grade", "A")
    source = item.get("source_watchlist", "Watchlist")

    t1_gain = round(((t1 - entry) / entry * 100), 1) if (t1 and entry) else 0.0
    t2_gain = round(((t2 - entry) / entry * 100), 1) if (t2 and entry) else 0.0

    # Breakout details
    days_ago = item.get("last_breakout_days_ago")
    b_date = item.get("last_breakout_date_label") or item.get("last_breakout_date")
    b_level = item.get("last_breakout_level") or round(entry * 0.99, 2)
    b_type = item.get("last_breakout_type") or "20D Pivot Breakout"
    b_gain = item.get("last_breakout_gain_pct") or 0.0

    if days_ago == 0:
        timing_badge = "⚡ <b>BREAKOUT TODAY</b>"
    elif days_ago == 1:
        timing_badge = f"✨ <b>YESTERDAY ({b_date})</b>"
    elif days_ago is not None:
        timing_badge = f"🕒 <b>{days_ago}d ago ({b_date})</b>"
    else:
        timing_badge = "🎯 <b>ACTIVE TRIGGER</b>"

    # Volume details
    vol_ratio = float(item.get("vol_ratio") or 1.0)
    vol_surge = bool(item.get("vol_surge") or item.get("has_vol_surge"))
    vol_text = f"🔥 <b>{vol_ratio:.1f}x Vol Surge</b>" if vol_surge else f"<b>{vol_ratio:.1f}x Vol</b>"

    # Buy zone
    zone_min = item.get("retest_zone_min") or round(entry * 0.995, 2)
    zone_max = item.get("retest_zone_max") or round(entry * 1.005, 2)

    lines = [
        f"<b>#{idx} 🟢 {sym}</b> · <b>${price:.2f}</b> ({timing_badge})",
        f"• 💥 <b>Pattern:</b> {b_type} | Pivot: <b>${b_level:.2f}</b> (+{b_gain:.1f}%)",
        f"• 📊 <b>Volume:</b> {vol_text} · Grade: <b>{grade}</b>",
        f"• 🎯 <b>Buy Zone:</b> <b>${zone_min:.2f} – ${zone_max:.2f}</b>",
    ]

    if stop and t1:
        stop_line = f"• 🛡️ <b>Stop Loss:</b> ${stop:.2f}"
        if risk_pct:
            stop_line += f" (-{risk_pct:.1f}%)"
        stop_line += f" | <b>R:R:</b> {rr:.1f}×"
        lines.append(stop_line)

        target_line = f"• 🏁 <b>Targets:</b> T1 <b>${t1:.2f}</b> (+{t1_gain:.1f}%)"
        if t2:
            target_line += f" · T2 <b>${t2:.2f}</b> (+{t2_gain:.1f}%)"
        lines.append(target_line)

    lines.append(f"• 🏷️ <b>Watchlist:</b> <i>{source}</i>")

    # Options suggestion if present
    opt_strategy = item.get("opt_strategy")
    opt_summary = item.get("opt_summary")
    if opt_strategy or opt_summary:
        opt_text = opt_summary or opt_strategy
        lines.append(f"• 💡 <b>Options:</b> <code>{opt_text}</code>")

    lines.append("")
    return "\n".join(lines)


def format_breakout_telegram_messages(scan_res: Dict[str, Any]) -> List[str]:
    """
    Builds rich Telegram HTML messages chunked to stay within Telegram's 4,096 char limit.
    """
    now_cst = datetime.now(ZoneInfo("America/Chicago"))
    date_str = now_cst.strftime("%A, %b %d, %Y")
    time_str = now_cst.strftime("%I:%M %p CT")

    total_scanned = scan_res.get("total_scanned", 0)
    total_matches = scan_res.get("total_matches", 0)
    today_breakouts = scan_res.get("today_breakouts", [])
    active_triggers = scan_res.get("active_triggers", [])
    recent_breakouts = scan_res.get("recent_breakouts", [])

    header = (
        "🚀 <b>STOCKPULSE BREAKOUT ALERT — DEFAULT 50 & MOMENTUM 50</b>\n"
        f"📅 <i>{date_str} · {time_str}</i>\n"
        f"🎯 <b>{total_matches}</b> breakout setup(s) triggered across <b>{total_scanned}</b> scanned tickers\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    )

    if total_matches == 0:
        msg = (
            header
            + "<i>No tickers currently qualify for fresh breakout alerts in Default 50 or Momentum 50.</i>\n\n"
            + "🔒 <i>Monitoring watchlists for upcoming pivot breakouts & volume expansion.</i>"
        )
        return [msg]

    sections: List[str] = []
    card_idx = 1

    if today_breakouts:
        sections.append("🔥 <b>FRESH BREAKOUTS TRIGGERED TODAY</b>\n")
        for item in today_breakouts:
            sections.append(_format_breakout_card(item, card_idx))
            card_idx += 1

    if active_triggers:
        sections.append("⚡ <b>ACTIVE BREAKOUT TRIGGERS (HOLDING BUY ZONE)</b>\n")
        for item in active_triggers:
            sections.append(_format_breakout_card(item, card_idx))
            card_idx += 1

    if recent_breakouts:
        sections.append("📐 <b>RECENT BREAKOUTS HOLDING ABOVE PIVOT</b>\n")
        for item in recent_breakouts:
            sections.append(_format_breakout_card(item, card_idx))
            card_idx += 1

    footer = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <i>Rules: Price breaking out above 20D/50D/52W resistance with volume expansion. Trade zone pullbacks with defined risk.</i>"
    )

    # Chunk into <= 3800 character messages
    messages: List[str] = []
    current_chunk = header

    for sec in sections:
        if len(current_chunk) + len(sec) + len(footer) > 3800:
            messages.append(current_chunk.strip())
            current_chunk = f"🚀 <b>STOCKPULSE BREAKOUT ALERT (Cont.)</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n" + sec
        else:
            current_chunk += sec

    current_chunk += footer
    messages.append(current_chunk.strip())

    return messages


def dispatch_breakout_telegram_alert(
    send_msg: bool = True,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    message_thread_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Executes the Default 50 + Momentum 50 breakout scan and dispatches the alert to Telegram.
    """
    token = (bot_token or TELEGRAM_BOT_TOKEN or "").strip()
    cid = (chat_id or TELEGRAM_CHAT_ID or "").strip()
    th_id = message_thread_id if message_thread_id is not None else (
        int(TELEGRAM_MESSAGE_THREAD_ID) if TELEGRAM_MESSAGE_THREAD_ID and str(TELEGRAM_MESSAGE_THREAD_ID).isdigit() else None
    )

    scan_res = scan_breakout_universe(max_workers=10)
    messages = format_breakout_telegram_messages(scan_res)

    sent_count = 0
    errors: List[str] = []

    if send_msg:
        if token and cid:
            for idx, msg_text in enumerate(messages):
                try:
                    ok = send_telegram(token, cid, msg_text, message_thread_id=th_id)
                    if ok:
                        sent_count += 1
                        logger.info(f"[breakout_scanner] Telegram message chunk {idx + 1}/{len(messages)} sent")
                    else:
                        errors.append(f"Telegram rejected chunk {idx + 1}")
                except Exception as e:
                    errors.append(str(e))
        else:
            errors.append("Telegram credentials not configured in environment")

    return {
        "ok": len(errors) == 0,
        "sent": sent_count > 0,
        "chunks_sent": sent_count,
        "chunks_total": len(messages),
        "total_scanned": scan_res.get("total_scanned", 0),
        "total_matches": scan_res.get("total_matches", 0),
        "today_breakouts_count": len(scan_res.get("today_breakouts", [])),
        "active_triggers_count": len(scan_res.get("active_triggers", [])),
        "recent_breakouts_count": len(scan_res.get("recent_breakouts", [])),
        "matches": [m.get("ticker") for m in scan_res.get("all_matches", [])],
        "errors": errors,
    }
