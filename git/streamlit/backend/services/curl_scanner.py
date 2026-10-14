"""
backend.services.curl_scanner
Scans stocks for 30-Week MA Curl Up signals matching TradingView (30W Curl Arrows) indicator.
Detects when the 30W SMA turns positive from flat/declining on weekly bars with volume expansion,
and formats/dispatches clean Telegram alert lists.
"""
import os
import math
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf
import pandas as pd
import numpy as np

from backend.services.telegram_svc import send_telegram

logger = logging.getLogger(__name__)


def _derive_options_contract(price: Optional[float], direction: str = "LONG") -> str:
    """Calculates ATM strike and nearest Friday expiration for swing trades."""
    if not price or price <= 0:
        return "N/A"

    if price >= 200:
        step = 5.0
    elif price >= 50:
        step = 2.5
    elif price >= 20:
        step = 1.0
    else:
        step = 0.5
    atm_strike = round(round(price / step) * step, 2)
    atm_str = f"${atm_strike:.0f}" if atm_strike.is_integer() else f"${atm_strike:.2f}"

    try:
        now_cst = datetime.now(ZoneInfo("America/Chicago"))
    except Exception:
        now_cst = datetime.now()
    days_to_fri = (4 - now_cst.weekday()) % 7
    if days_to_fri == 0 and now_cst.hour >= 15:
        days_to_fri = 7
    exp_fri = (now_cst + timedelta(days=days_to_fri)).strftime("%b %d")

    return f"Buy {atm_str} Call (Exp {exp_fri})" if str(direction).upper() != "SHORT" else f"Buy {atm_str} Put (Exp {exp_fri})"


def check_30w_curl_tradingview(ticker: str, max_weeks_ago: int = 8) -> Optional[Dict[str, Any]]:
    """
    Evaluates 3-year weekly candle data for ticker.
    Detects TradingView 30W Curl Arrows where 30-week SMA turns positive from flat or declining.
    Returns setup dictionary if qualified, else None.
    """
    try:
        tk = ticker.strip().upper()
        if not tk or "^" in tk or "=" in tk:
            return None

        df = yf.Ticker(tk).history(period="3y", interval="1wk")
        if df.empty or len(df) < 32:
            return None
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if len(df) < 32:
            return None

        df["sma30"] = df["Close"].rolling(30).mean()
        df["slope"] = df["sma30"].diff()
        df["vol_sma10"] = df["Volume"].rolling(10).mean() if "Volume" in df.columns else None

        cur_price = round(float(df["Close"].iloc[-1]), 2)
        last_sma = round(float(df["sma30"].iloc[-1]), 2) if not math.isnan(df["sma30"].iloc[-1]) else None
        last_slope = round(float(df["slope"].iloc[-1]), 3) if not math.isnan(df["slope"].iloc[-1]) else 0.0

        if not last_sma or cur_price <= 0:
            return None

        # Price proximity to 30W SMA
        dist_from_sma = round((cur_price - last_sma) / last_sma * 100, 1)

        # Detect historical and recent 30W Curl arrows
        arrows = []
        last_idx = -100
        for i in range(32, len(df)):
            t_str = df.index[i].strftime("%Y-%m-%d")
            c = round(float(df["Close"].iloc[i]), 2)
            vol = float(df["Volume"].iloc[i]) if "Volume" in df.columns else 0
            sma_val = df["sma30"].iloc[i]
            cur_s = df["slope"].iloc[i]
            prev_s = df["slope"].iloc[i-1]
            prev2_s = df["slope"].iloc[i-2]

            # Exact TradingView 30W Curl arrow trigger condition from analysis.py
            if cur_s > 0 and (prev_s <= 0.02 or prev2_s <= 0) and c >= (sma_val * 0.98):
                if i - last_idx >= 6:  # at least 6 weeks spacing between distinct arrows
                    last_idx = i
                    v_avg = float(df["vol_sma10"].iloc[i]) if df["vol_sma10"] is not None else 0
                    v_ratio = (vol / v_avg) if v_avg > 0 else 1.0
                    is_vol = bool(v_ratio >= 1.25)
                    weeks_ago = len(df) - 1 - i
                    arrows.append({
                        "date": t_str,
                        "weeks_ago": weeks_ago,
                        "price_at_arrow": c,
                        "sma30_at_arrow": round(float(sma_val), 2),
                        "slope_at_arrow": round(float(cur_s), 3),
                        "vol_ratio": round(v_ratio, 2),
                        "is_vol_surge": is_vol,
                    })

        if not arrows:
            return None

        recent_arrow = arrows[-1]
        weeks_ago = recent_arrow["weeks_ago"]

        # Qualify if arrow is within max_weeks_ago
        if weeks_ago > max_weeks_ago:
            return None

        # Filter out severely broken charts (more than 8% below 30W SMA)
        if cur_price < last_sma * 0.92:
            return None

        # Risk management levels
        trailing_stop = round(last_sma * 0.95, 2)
        swing_low_8w = round(float(df["Low"].tail(8).min()), 2)
        stop_loss = max(trailing_stop, swing_low_8w)
        if stop_loss >= cur_price:
            stop_loss = round(cur_price * 0.95, 2)

        risk_pct = round((cur_price - stop_loss) / cur_price * 100, 1)
        target1 = round(cur_price * (1.0 + max(0.10, risk_pct * 2.0 / 100)), 2)
        reward_pct = round((target1 - cur_price) / cur_price * 100, 1)
        rr = round(reward_pct / risk_pct, 1) if risk_pct > 0 else 2.0
        opt_contract = _derive_options_contract(cur_price, "LONG")

        # Classification
        if weeks_ago <= 3 and -2.0 <= dist_from_sma <= 10.0:
            category = "FRESH"
            category_badge = "⚡ FRESH CURL"
            category_desc = "Party Just Started (Arrow 0–3w ago, close to 30W SMA)"
            rank_score = 95 - (weeks_ago * 3) + (10 if recent_arrow["is_vol_surge"] else 0)
        elif recent_arrow["is_vol_surge"] and weeks_ago <= 6:
            category = "SURGE"
            category_badge = "🚀 CURL + VOLUME SURGE"
            category_desc = f"Institutional accumulation ({recent_arrow['vol_ratio']}x vol)"
            rank_score = 90 - (weeks_ago * 2) + 5
        elif weeks_ago <= 6:
            category = "BASE"
            category_badge = "🎯 CONSTRUCTIVE BASE"
            category_desc = "Coiled & holding above rising 30W SMA"
            rank_score = 80 - (weeks_ago * 2)
        else:
            category = "ADVANCING"
            category_badge = "📈 ADVANCING TREND"
            category_desc = "Established stage 2 uptrend"
            rank_score = 70 - weeks_ago

        return {
            "ticker": tk,
            "price": cur_price,
            "sma30": last_sma,
            "dist_from_sma30": dist_from_sma,
            "slope": last_slope,
            "weeks_ago": weeks_ago,
            "arrow_date": recent_arrow["date"],
            "arrow_price": recent_arrow["price_at_arrow"],
            "vol_ratio": recent_arrow["vol_ratio"],
            "is_vol_surge": recent_arrow["is_vol_surge"],
            "category": category,
            "category_badge": category_badge,
            "category_desc": category_desc,
            "rank_score": rank_score,
            "stop_loss": stop_loss,
            "target1": target1,
            "risk_pct": risk_pct,
            "reward_pct": reward_pct,
            "rr": rr,
            "options": opt_contract,
        }
    except Exception as e:
        logger.debug(f"Error checking 30W curl for {ticker}: {e}")
        return None


def scan_30w_curl_candidates(
    tickers: Optional[List[str]] = None,
    watchlist: str = "tos_email",
    days: int = 1,
    subjects: str = "",
    max_weeks_ago: int = 8,
    max_workers: int = 10,
) -> Dict[str, Any]:
    """
    Scans candidate tickers for 30W MA Curl Up arrows.
    Returns ranked list of matches and scan summary.
    """
    source_label = "TOS Scan"
    if tickers:
        ticker_list = [t.strip().upper() for t in tickers if t.strip()]
        source_label = "Custom List"
    elif watchlist in ("tos_email", "tos", "gmail"):
        source_label = f"TOS Email Alerts ({days}d)"
        try:
            from backend.services.gmail_watchlist import fetch_today_watchlist
            ticker_list = fetch_today_watchlist(subjects=subjects, days=days)
        except Exception as e:
            logger.warning(f"Error fetching TOS watchlist for 30W curl: {e}")
            ticker_list = []
        if not ticker_list:
            from backend.services.scanner import WATCHLISTS
            ticker_list = WATCHLISTS.get("default", [])
            source_label = "Default 50 (TOS Fallback)"
    elif watchlist == "all":
        from backend.services.scanner import WATCHLISTS
        from backend.services.gmail_watchlist import fetch_today_watchlist
        combined = []
        try:
            combined.extend(fetch_today_watchlist(subjects=subjects, days=days)[:50])
        except Exception:
            pass
        for k in ("default", "tech", "momentum", "etfs"):
            combined.extend(WATCHLISTS.get(k, []))
        ticker_list = list(dict.fromkeys(combined))
        source_label = "All Watchlists"
    else:
        from backend.services.scanner import WATCHLISTS
        ticker_list = WATCHLISTS.get(watchlist, WATCHLISTS.get("default", []))
        source_label = f"{watchlist.capitalize()} Watchlist"

    total_scanned = len(ticker_list)
    matches = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_30w_curl_tradingview, t, max_weeks_ago): t for t in ticker_list}
        for fut in as_completed(futures):
            res = fut.result()
            if res:
                matches.append(res)

    # Sort matches by highest conviction rank score, then lowest weeks_ago
    matches.sort(key=lambda r: (-r["rank_score"], r["weeks_ago"], r["dist_from_sma30"]))

    return {
        "ok": True,
        "count": len(matches),
        "total_scanned": total_scanned,
        "source_label": source_label,
        "matches": matches,
    }


def format_30w_curl_telegram_message(
    matches: List[Dict[str, Any]],
    total_scanned: int = 0,
    source_label: str = "TOS Scan",
    max_items: int = 8,
) -> str:
    """
    Formats a clean, high-impact HTML alert for Telegram detailing
    stocks exhibiting TradingView 30W MA Curl Up arrows.
    """
    try:
        now_cst = datetime.now(ZoneInfo("America/Chicago"))
    except Exception:
        now_cst = datetime.now()
    date_str = now_cst.strftime("%b %d, %Y")
    time_str = now_cst.strftime("%I:%M %p CT")

    lines = [
        "📈 <b>STOCKPULSE 30W MA CURL UP ARROWS</b>",
        f"📅 <i>{date_str} · {time_str} · {source_label}</i>",
        f"🎯 <b>{len(matches)}</b> qualifying setups found out of <b>{total_scanned}</b> scanned",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if not matches:
        lines.extend([
            "<i>No tickers currently qualify for fresh 30W MA Curl Arrows in this scan.</i>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "🔒 <i>Monitor watchlists for upcoming weekly closes.</i>"
        ])
        return "\n".join(lines)

    # Group into Fresh (0-3w), Surge, and Constructive Base
    fresh = [m for m in matches if m["category"] == "FRESH"]
    surge = [m for m in matches if m["category"] == "SURGE"]
    base = [m for m in matches if m["category"] in ("BASE", "ADVANCING")]

    # Present top picks
    top_picks = matches[:max_items]

    for idx, item in enumerate(top_picks, 1):
        tk = item["ticker"]
        p = item["price"]
        sma = item["sma30"]
        dist = item["dist_from_sma30"]
        slope = item["slope"]
        w_ago = item["weeks_ago"]
        arr_date = item["arrow_date"]
        vol_r = item["vol_ratio"]
        badge = item["category_badge"]
        entry = item["price"]
        stop = item["stop_loss"]
        t1 = item["target1"]
        risk = item["risk_pct"]
        reward = item["reward_pct"]
        rr = item["rr"]
        options = item["options"]

        ago_text = "✨ <b>THIS WEEK</b>" if w_ago == 0 else f"<b>{w_ago}w ago</b> ({arr_date})"
        vol_text = f"🔥 {vol_r:.1f}x Vol" if item["is_vol_surge"] else f"{vol_r:.1f}x Vol"

        lines.extend([
            f"<b>#{idx} {badge}: {tk}</b> · <b>${p:.2f}</b>",
            f"• <b>30W SMA</b>: ${sma:.2f} (<b>{dist:+.1f}%</b>) | Slope: <b>{slope:+.2f}</b>",
            f"• <b>Curl Arrow</b>: {ago_text} · {vol_text}",
            f"• <b>Entry</b>: ${entry:.2f} | <b>Stop</b>: ${stop:.2f} (-{risk:.1f}%)",
            f"• <b>Target 1</b>: ${t1:.2f} (+{reward:.1f}%) | <b>R:R</b>: {rr:.1f}×",
            f"• 💡 <b>Options</b>: <code>{options}</code>",
            "",
        ])

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"<i>Showing top {len(top_picks)} of {len(matches)} setups. 30W SMA acts as dynamic trailing support.</i>",
        "🔒 <i>Disciplined risk management: Invalidation triggers if weekly bar closes below 30W SMA.</i>"
    ])

    return "\n".join(lines)


def dispatch_30w_curl_telegram(
    tickers: Optional[List[str]] = None,
    watchlist: str = "tos_email",
    days: int = 1,
    subjects: str = "",
    max_weeks_ago: int = 8,
    send_msg: bool = True,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Executes 30W MA Curl scan and dispatches the alert list to Telegram.
    """
    # Telegram credentials
    try:
        from backend.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        token = (bot_token or TELEGRAM_BOT_TOKEN or "").strip()
        cid = (chat_id or TELEGRAM_CHAT_ID or "").strip()
    except Exception:
        token = (bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
        cid = (chat_id or os.getenv("TELEGRAM_CHAT_ID", "")).strip()

    scan_res = scan_30w_curl_candidates(
        tickers=tickers,
        watchlist=watchlist,
        days=days,
        subjects=subjects,
        max_weeks_ago=max_weeks_ago,
    )

    matches = scan_res.get("matches", [])
    total_scanned = scan_res.get("total_scanned", 0)
    source_label = scan_res.get("source_label", "TOS Scan")

    message_text = format_30w_curl_telegram_message(
        matches=matches,
        total_scanned=total_scanned,
        source_label=source_label,
    )

    sent = False
    send_err = None

    if send_msg and token and cid:
        try:
            sent = send_telegram(token, cid, message_text)
            if sent:
                logger.info(f"30W Curl Telegram alert sent: {len(matches)} matches from {total_scanned} tickers")
            else:
                send_err = "Telegram API rejected message"
        except Exception as e:
            send_err = str(e)
            logger.error(f"Error sending 30W Curl Telegram alert: {e}")
    elif send_msg and (not token or not cid):
        send_err = "Telegram credentials not configured"

    return {
        "ok": True,
        "sent": sent,
        "error": send_err,
        "count": len(matches),
        "total_scanned": total_scanned,
        "source_label": source_label,
        "matches": matches,
        "message": message_text,
    }
