"""
exceptional_scanner.py — Multi-Timeframe Strategy Exceptional Scanner & EOD Telegram Dispatcher.

Combines tickers from:
  1. Default 50 Watchlist (core liquid large caps & ETFs)
  2. Momentum Watchlist (high-beta momentum & growth leaders)
  3. ThinkOrSwim (TOS) Gmail Watchlist (recent TOS scan alert emails)

Executes the full Multi-Timeframe Strategy Scan (scan_single / full_score_pipeline)
on the combined universe, isolates exceptional Grade S/A setups + 30W Stage 2 curls,
and formats high-definition institutional trade plans for Telegram end-of-day dispatch.
"""

import os
import re
import logging
from datetime import datetime, date
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any

from backend.config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_MESSAGE_THREAD_ID,
)
from backend.services.telegram_svc import send_telegram
from backend.services.scanner import WATCHLISTS, scan_single

logger = logging.getLogger(__name__)
_CST = ZoneInfo("America/Chicago")


def get_exceptional_scan_universe(days_gmail: int = 2, max_gmail_tickers: int = 60) -> List[str]:
    """
    Build the full scanning universe:
      - Default 50 Watchlist
      - Momentum Watchlist
      - ThinkOrSwim (TOS) Gmail Scan Alert tickers (today/recent or latest available date)
    Returns an ordered, deduplicated list of clean ticker symbols.
    """
    candidates: List[str] = []

    # 1. Default 50
    default_wl = WATCHLISTS.get("default", [])
    candidates.extend(default_wl)

    # 2. Momentum
    momentum_wl = WATCHLISTS.get("momentum", [])
    candidates.extend(momentum_wl)

    # 3. Gmail TOS Alerts
    try:
        from backend.services.gmail_watchlist import fetch_today_watchlist, _load_store
        # Attempt to fetch alerts from past N days
        gmail_tickers = fetch_today_watchlist(days=days_gmail)

        # If zero alerts in requested window (e.g. weekend or quiet trading day),
        # pull the most recent active batch from the local JSON store
        if not gmail_tickers:
            store = _load_store()
            if store:
                dates = sorted(list({m.get("date") for m in store if m.get("date")}))
                if dates:
                    latest_date = dates[-1]
                    for m in reversed(store):
                        if m.get("date") == latest_date:
                            gmail_tickers.extend(m.get("tickers", []))

        if gmail_tickers:
            seen_g = set()
            clean_g = []
            for t in gmail_tickers:
                t_clean = str(t).strip().upper()
                if t_clean and t_clean not in seen_g and len(t_clean) <= 5 and t_clean.isalpha():
                    seen_g.add(t_clean)
                    clean_g.append(t_clean)
            candidates.extend(clean_g[:max_gmail_tickers])
    except Exception as e:
        logger.warning(f"[exceptional_scanner] Failed pulling Gmail TOS tickers: {e}")

    # Deduplicate preserving order
    seen: set[str] = set()
    universe: List[str] = []
    for sym in candidates:
        s = str(sym).strip().upper()
        if s and s not in seen and len(s) <= 5 and re.match(r"^[A-Z]{1,5}$", s):
            seen.add(s)
            universe.append(s)

    return universe


def scan_exceptional_tickers(
    universe: Optional[List[str]] = None,
    as_of: Optional[str] = None,
    max_workers: int = 12,
) -> Dict[str, Any]:
    """
    Executes the Multi-Timeframe Strategy Scan across the combined universe
    and filters for Exceptional Bullish, Exceptional Bearish, and 30W Curl setups.
    """
    if not universe:
        universe = get_exceptional_scan_universe()

    scanned_results: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(scan_single, ticker, as_of): ticker for ticker in universe}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                res = fut.result()
                if res and not res.get("error") and (res.get("price") or 0) > 0:
                    scanned_results.append(res)
            except Exception as e:
                logger.warning(f"[exceptional_scanner] Error scanning {ticker}: {e}")

    # Classify results matching frontend stock-analysis filters
    exceptional_bull: List[Dict[str, Any]] = []
    exceptional_bear: List[Dict[str, Any]] = []
    stage2_curls: List[Dict[str, Any]] = []
    tier2_high_quality: List[Dict[str, Any]] = []

    bull_cnt = 0
    bear_cnt = 0
    grade_counts = {"S": 0, "A": 0, "B": 0, "C": 0}

    for r in scanned_results:
        verdict = (r.get("verdict") or "").upper()
        score = r.get("score") or 0
        conf = (r.get("confidence") or "").upper()
        wr = r.get("expected_wr") or 0
        grade = (r.get("entry_grade") or "").upper()
        mtf_sig = r.get("mtf_signal") or ""
        mtf_rk = r.get("mtf_rank") or 0
        direction = (r.get("direction") or "LONG").upper()

        if verdict == "BULLISH":
            bull_cnt += 1
        elif verdict == "BEARISH":
            bear_cnt += 1

        if grade in grade_counts:
            grade_counts[grade] += 1
        elif grade in ("B-", "B+"):
            grade_counts["B"] += 1

        # 1. Exceptional Bullish Setup
        # Criteria: High Confidence, Score >= 4, Expected Win Rate > 80% (Grade S or A), MTF A+ Long or Rank 1
        is_bull_exceptional = (
            direction == "LONG"
            and conf == "HIGH"
            and score >= 4
            and wr >= 80
            and (mtf_sig in ("A+ Long", "Buy Long") or mtf_rk == 1)
        )
        if is_bull_exceptional:
            exceptional_bull.append(r)

        # 2. Exceptional Bearish Setup
        # Criteria: High Confidence, Score <= -4, Expected Win Rate > 80%, MTF A+ Short or Rank 1/2
        is_bear_exceptional = (
            direction == "SHORT"
            and conf == "HIGH"
            and score <= -4
            and wr >= 80
            and (mtf_sig in ("A+ Short", "Sell Short") or mtf_rk in (1, 2))
        )
        if is_bear_exceptional:
            exceptional_bear.append(r)

        # 3. 30W Stage 2 Curls (Curling Up with Volume confirmation)
        if r.get("is_30w_curl") and (r.get("is_fresh_stage2") or r.get("stage2_curl_surge") or r.get("stage2_status") in ("FRESH", "ADVANCING")):
            stage2_curls.append(r)

        # 4. Tier 2 High Quality Candidate (Fallback if quiet market day)
        if not is_bull_exceptional and direction == "LONG" and mtf_rk == 1 and score >= 3 and grade in ("S", "A", "B", "B-"):
            tier2_high_quality.append(r)

    # Sort candidates by Score desc & Win Rate desc
    exceptional_bull.sort(key=lambda x: (x.get("score") or 0, x.get("expected_wr") or 0), reverse=True)
    exceptional_bear.sort(key=lambda x: (abs(x.get("score") or 0), x.get("expected_wr") or 0), reverse=True)
    stage2_curls.sort(key=lambda x: (x.get("stage2_curl_surge", False), x.get("score") or 0), reverse=True)
    tier2_high_quality.sort(key=lambda x: (x.get("score") or 0, x.get("expected_wr") or 0), reverse=True)

    now_cst = datetime.now(_CST)
    return {
        "ok": True,
        "as_of": as_of or now_cst.date().isoformat(),
        "scan_time_cst": now_cst.strftime("%Y-%m-%d %H:%M:%S CST"),
        "total_scanned": len(scanned_results),
        "universe_size": len(universe),
        "bullish_count": bull_cnt,
        "bearish_count": bear_cnt,
        "grade_counts": grade_counts,
        "exceptional_bull": exceptional_bull,
        "exceptional_bear": exceptional_bear,
        "stage2_curls": stage2_curls,
        "tier2_high_quality": tier2_high_quality[:4],  # top 4 fallback
        "all_results": scanned_results,
    }


def _format_ticker_card(item: Dict[str, Any], is_tier2: bool = False) -> str:
    """Formats an individual high-definition trade plan card for Telegram HTML."""
    sym = item.get("ticker", "—")
    price = item.get("price") or 0.0
    direction = item.get("direction", "LONG").upper()
    grade = item.get("entry_grade", "—")
    wr = item.get("expected_wr") or 0
    score = item.get("score") or 0
    mtf_sig = item.get("mtf_signal", "—")
    w_bias = item.get("weekly_bias", "—")
    d_bias = item.get("daily_bias", "—")
    h4_bias = item.get("h4_bias", d_bias)
    ma_bias = item.get("ma_bias", "Bull")

    # Trade Levels
    entry = item.get("entry") or price
    stop = item.get("stop_loss")
    t1 = item.get("target1")
    t2 = item.get("target2")
    t1_days = item.get("t1_days", 5)
    t2_days = item.get("t2_days", 14)
    risk_pct = item.get("risk_pct")
    rr_t1 = item.get("rr_t1")
    rr_t2 = item.get("rr_t2")

    t1_diff = ((t1 - entry) / entry * 100) if (t1 and entry) else 0.0
    t2_diff = ((t2 - entry) / entry * 100) if (t2 and entry) else 0.0

    # Volume Profile & 52W Distance
    vol_trend = item.get("vol_trend", "FLAT")
    vol_ratio = item.get("vol_ratio") or 1.0
    vol_surge = item.get("vol_surge") or False
    poc = item.get("poc")
    val = item.get("val")
    vah = item.get("vah")
    hi_52 = item.get("hi_52")
    lo_52 = item.get("lo_52")
    dist_hi_52 = item.get("dist_hi_52")
    dist_lo_52 = item.get("dist_lo_52")

    # 30-Week MA & Stage 2 Curl
    is_30w_curl = item.get("is_30w_curl", False)
    stage2_status = item.get("stage2_status", "NONE")
    dist_sma30 = item.get("dist_from_sma30")
    weeks_curling = item.get("weeks_curling", 0)
    sma30 = item.get("sma30")

    # Final Judgement
    fj = item.get("final_judgement") or {}
    badge = fj.get("badge", "CONFIRMED" if direction == "LONG" else "BREAKDOWN")
    summary = fj.get("summary", "")
    confluence = fj.get("confluence_score", 85)

    # Options
    opt_summary = item.get("opt_summary")
    opt_debit = item.get("opt_debit")
    opt_profit = item.get("opt_profit")

    dir_icon = "🟢 <b>BUY LONG</b>" if direction == "LONG" else "🔴 <b>SELL SHORT</b>"
    star = "⭐" if not is_tier2 else "🔷"
    tier_tag = " [Tier 2]" if is_tier2 else ""

    lines = [
        f"{star} <b>{sym}</b> · <b>${price:.2f}</b> · {dir_icon}{tier_tag}",
        f"🏅 <b>Grade:</b> <b>{grade}</b> ({wr}% WR) · Score: <b>{score:+d}</b>",
        f"🧭 <b>MTF Align:</b> {mtf_sig} (W: {w_bias} | D: {d_bias} | 4H: {h4_bias} | 30MA: {ma_bias})",
    ]

    # Retest Entry Zone
    retest_entry = item.get("retest_entry")
    retest_min = item.get("retest_zone_min")
    retest_max = item.get("retest_zone_max")
    retest_label = item.get("retest_label")
    retest_diff = item.get("retest_diff_pct")

    # Trade Plan
    plan_lines = ["🎯 <b>Trade Plan:</b>"]
    plan_lines.append(f"  • Trigger Entry: <code>${entry:.2f}</code>")
    if retest_entry and retest_min and retest_max:
        diff_str = f" ({retest_diff:+.1f}%)" if retest_diff is not None else ""
        plan_lines.append(f"  • 🔄 Retest Zone: <code>${retest_min:.2f}–${retest_max:.2f}</code> · <i>{retest_label}</i>{diff_str}")
    if stop:
        risk_str = f" ({risk_pct:.1f}% risk)" if risk_pct is not None else ""
        plan_lines.append(f"  • Stop Loss: <code>${stop:.2f}</code>{risk_str}")
    if t1:
        rr_str = f", 1:{rr_t1:.1f} R/R" if rr_t1 is not None else ""
        plan_lines.append(f"  • Target 1: <code>${t1:.2f}</code> ({t1_diff:+.1f}%{rr_str} · ~{t1_days}d)")
    if t2:
        rr2_str = f", 1:{rr_t2:.1f} R/R" if rr_t2 is not None else ""
        plan_lines.append(f"  • Target 2: <code>${t2:.2f}</code> ({t2_diff:+.1f}%{rr2_str} · ~{t2_days}d)")
    lines.append("\n".join(plan_lines))

    # Volume Profile & 52W stats
    vp_surge_str = " 🔥 Surge" if vol_surge else ""
    vp_line = f"📊 <b>Volume Profile:</b> {vol_trend} ({vol_ratio:.1f}x){vp_surge_str}"
    vp_details = []
    if poc and val and vah:
        vp_details.append(f"  • VA Levels: VAL <code>${val:.2f}</code> | POC <code>${poc:.2f}</code> | VAH <code>${vah:.2f}</code>")
    if dist_hi_52 is not None and dist_lo_52 is not None:
        hi_ref = f" (${hi_52:.2f})" if hi_52 else ""
        vp_details.append(f"  • 52W Range: <b>{dist_hi_52:+.1f}%</b> off High{hi_ref} | <b>{dist_lo_52:+.1f}%</b> from Low")
    if vp_details:
        lines.append(vp_line + "\n" + "\n".join(vp_details))
    else:
        lines.append(vp_line)

    # 30-Week MA Stage 2 curl if notable
    if is_30w_curl or (sma30 and dist_sma30 is not None):
        curl_tag = f" · {stage2_status}" if stage2_status != "NONE" else ""
        wks_tag = f" ({weeks_curling} wks curl)" if weeks_curling > 0 else ""
        sma_val_str = f"${sma30:.2f}" if sma30 else "SMA"
        dist_str = f"{dist_sma30:+.1f}%" if dist_sma30 is not None else "0.0%"
        lines.append(f"📈 <b>30W MA:</b> {sma_val_str} ({dist_str}){curl_tag}{wks_tag}")

    # Final Judgement
    if badge or summary:
        fj_hdr = f"⚖️ <b>Final Judgement:</b> <b>{badge}</b>"
        if confluence:
            fj_hdr += f" ({confluence}% Confluence)"
        if summary:
            fj_hdr += f"\n  <i>{summary}</i>"
        lines.append(fj_hdr)

    # Options Strategy
    if opt_summary:
        opt_line = f"💡 <b>Options:</b> {opt_summary}"
        if opt_debit is not None and opt_profit is not None:
            opt_line += f" (Debit: ${opt_debit:.2f} | Max Gain: ${opt_profit:.2f})"
        lines.append(opt_line)

    return "\n".join(lines)


def format_exceptional_telegram_messages(scan_res: Dict[str, Any]) -> List[str]:
    """
    Builds rich HTML messages chunked to ensure Telegram's 4,096 character limit
    is never exceeded. Returns a list of message strings to send sequentially.
    """
    total = scan_res.get("total_scanned", 0)
    universe_size = scan_res.get("universe_size", total)
    bull_cnt = scan_res.get("bullish_count", 0)
    bear_cnt = scan_res.get("bearish_count", 0)
    time_str = scan_res.get("scan_time_cst", datetime.now(_CST).strftime("%Y-%m-%d %H:%M CST"))
    date_str = scan_res.get("as_of", datetime.now(_CST).date().isoformat())

    bull_list = scan_res.get("exceptional_bull", [])
    bear_list = scan_res.get("exceptional_bear", [])
    stage2_list = scan_res.get("stage2_curls", [])
    tier2_list = scan_res.get("tier2_high_quality", [])

    messages: List[str] = []

    # ── HEADER CARD ────────────────────────────────────────────────────────────
    header_lines = [
        "🏆 <b>EOD EXCEPTIONAL SCANS — MULTI-TIMEFRAME STRATEGY</b>",
        f"📅 <i>{date_str} · Market Close Scan ({time_str})</i>",
        f"🌐 <b>Universe Scanned:</b> <b>{total} tickers</b> (Default 50 + Momentum + Gmail TOS)",
        f"📊 <b>Market Breadth:</b> 🟢 <b>{bull_cnt} Bullish</b> · 🔴 <b>{bear_cnt} Bearish</b>",
        f"🎯 <b>Grade S/A Matches:</b> ⭐ <b>{len(bull_list)} Bullish</b> · 💀 <b>{len(bear_list)} Bearish</b>",
    ]
    cur_chunk = "\n".join(header_lines) + "\n\n"

    # ── 1. EXCEPTIONAL BULLISH SETUPS ──────────────────────────────────────────
    if bull_list:
        cur_chunk += "━━━━━━━━━━━━━━━━━━━━━━━\n"
        cur_chunk += f"⭐ <b>EXCEPTIONAL BULLISH SETUPS ({len(bull_list)})</b>\n"
        cur_chunk += "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for item in bull_list:
            card = _format_ticker_card(item) + "\n\n"
            if len(cur_chunk) + len(card) > 3700:
                messages.append(cur_chunk.strip())
                cur_chunk = "⭐ <b>EXCEPTIONAL BULLISH SETUPS (Cont.)</b>\n\n"
            cur_chunk += card

    # Fallback to Tier 2 if fewer than 2 exceptional bullish setups
    elif tier2_list:
        cur_chunk += "━━━━━━━━━━━━━━━━━━━━━━━\n"
        cur_chunk += f"🔷 <b>TOP RANK 1 HIGH QUALITY SETUPS ({len(tier2_list)})</b>\n"
        cur_chunk += "<i>Note: Strict Grade S/A criteria zeroed out today; showing top MTF Rank 1 setups.</i>\n"
        cur_chunk += "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for item in tier2_list:
            card = _format_ticker_card(item, is_tier2=True) + "\n\n"
            if len(cur_chunk) + len(card) > 3700:
                messages.append(cur_chunk.strip())
                cur_chunk = "🔷 <b>TOP RANK 1 SETUPS (Cont.)</b>\n\n"
            cur_chunk += card

    # ── 2. EXCEPTIONAL BEARISH SETUPS ──────────────────────────────────────────
    if bear_list:
        section_hdr = "━━━━━━━━━━━━━━━━━━━━━━━\n"
        section_hdr += f"💀 <b>EXCEPTIONAL BEARISH SETUPS ({len(bear_list)})</b>\n"
        section_hdr += "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        if len(cur_chunk) + len(section_hdr) > 3600:
            messages.append(cur_chunk.strip())
            cur_chunk = section_hdr
        else:
            cur_chunk += section_hdr

        for item in bear_list:
            card = _format_ticker_card(item) + "\n\n"
            if len(cur_chunk) + len(card) > 3700:
                messages.append(cur_chunk.strip())
                cur_chunk = "💀 <b>EXCEPTIONAL BEARISH SETUPS (Cont.)</b>\n\n"
            cur_chunk += card

    # ── 3. 30W STAGE 2 CURL LEADERS ────────────────────────────────────────────
    # List fresh 30W curls that aren't already featured above
    featured_syms = {it["ticker"] for it in bull_list + bear_list + tier2_list}
    unique_curls = [c for c in stage2_list if c["ticker"] not in featured_syms][:5]

    if unique_curls:
        curl_hdr = "━━━━━━━━━━━━━━━━━━━━━━━\n"
        curl_hdr += f"📈 <b>30-WEEK MA STAGE 2 CURL RADAR ({len(unique_curls)})</b>\n"
        curl_hdr += "<i>Fresh 150-day / 30-week SMA curls emerging with volume support</i>\n"
        curl_hdr += "━━━━━━━━━━━━━━━━━━━━━━━\n"

        curl_lines = [curl_hdr]
        for c in unique_curls:
            sym = c.get("ticker")
            p = c.get("price", 0.0)
            st = c.get("stage2_status", "FRESH")
            dist = c.get("dist_from_sma30", 0.0)
            wk = c.get("weeks_curling", 0)
            vr = c.get("vol_ratio", 1.0) or 1.0
            surge = " 🔥" if c.get("has_vol_surge") else ""
            curl_lines.append(
                f"• <b>{sym}</b> (${p:.2f}) · <b>{st}</b> ({dist:+.1f}% from 30W MA) · {wk} wks curl · {vr:.1f}x vol{surge}"
            )

        curl_section = "\n".join(curl_lines) + "\n\n"
        if len(cur_chunk) + len(curl_section) > 3700:
            messages.append(cur_chunk.strip())
            cur_chunk = curl_section
        else:
            cur_chunk += curl_section

    # If completely empty across all categories
    if not bull_list and not tier2_list and not bear_list and not unique_curls:
        cur_chunk += "\n<i>No tickers met strict Grade S/A or Stage 2 Curl criteria today. All universe tickers in chop/neutral zones.</i>\n\n"

    # Footer note
    footer = "⚡ <i>Generated by Multi-Timeframe Strategy Scan · StockPulse EOD Engine</i>"
    if len(cur_chunk) + len(footer) > 3800:
        messages.append(cur_chunk.strip())
        messages.append(footer)
    else:
        cur_chunk += footer
        messages.append(cur_chunk.strip())

    return messages


def dispatch_exceptional_telegram_alert(
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    message_thread_id: Optional[str | int] = None,
    send_msg: bool = True,
    universe: Optional[List[str]] = None,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main entry point for scanning the combined universe and dispatching
    the EOD Exceptional Scans alert to Telegram.
    """
    token = (bot_token or TELEGRAM_BOT_TOKEN or "").strip()
    cid = (chat_id or TELEGRAM_CHAT_ID or "").strip()
    th_id = message_thread_id if message_thread_id is not None else TELEGRAM_MESSAGE_THREAD_ID

    logger.info("[exceptional_scanner] Starting EOD Multi-Timeframe Exceptional Scan...")
    scan_res = scan_exceptional_tickers(universe=universe, as_of=as_of)
    messages = format_exceptional_telegram_messages(scan_res)

    sent = False
    send_errors: List[str] = []

    if send_msg and token and cid:
        for idx, msg_text in enumerate(messages):
            try:
                ok = send_telegram(token, cid, msg_text, message_thread_id=th_id)
                if ok:
                    sent = True
                    logger.info(f"[exceptional_scanner] Telegram message chunk {idx + 1}/{len(messages)} sent successfully")
                else:
                    send_errors.append(f"Telegram rejected chunk {idx + 1}")
            except Exception as e:
                send_errors.append(f"Error sending chunk {idx + 1}: {e}")
    elif send_msg and (not token or not cid):
        send_errors.append("Telegram credentials not configured in environment")

    return {
        "ok": True,
        "sent": sent,
        "errors": send_errors,
        "total_scanned": scan_res.get("total_scanned", 0),
        "universe_size": scan_res.get("universe_size", 0),
        "exceptional_bull_count": len(scan_res.get("exceptional_bull", [])),
        "exceptional_bear_count": len(scan_res.get("exceptional_bear", [])),
        "stage2_curl_count": len(scan_res.get("stage2_curls", [])),
        "tier2_fallback_count": len(scan_res.get("tier2_high_quality", [])),
        "messages": messages,
        "results": {
            "bullish": [it["ticker"] for it in scan_res.get("exceptional_bull", [])],
            "bearish": [it["ticker"] for it in scan_res.get("exceptional_bear", [])],
            "curls": [it["ticker"] for it in scan_res.get("stage2_curls", [])],
            "tier2": [it["ticker"] for it in scan_res.get("tier2_high_quality", [])],
        }
    }
