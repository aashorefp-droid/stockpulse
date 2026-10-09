"""
Earnings scheduler — APScheduler jobs for automated pre/post earnings alerts.

Schedule (all times CST / America/Chicago):
  08:30  pre_earnings_job    — discover today's reporters, send pre-earnings Telegram,
                               store tickers in SQLite
  15:00  start_eps_polling   — kick off 1-minute EPS poll interval job
  18:00  stop_eps_polling    — shut down the interval job

EPS poll logic (runs every 1 min, 3–6 PM CST):
  For each unnotified ticker: try fast EPS sources (Yahoo quoteSummary → Finviz).
  Once EPS confirmed, send post-earnings Telegram and mark DB record done.
"""
import logging
import sys
import os
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.db.earnings_tracker import (
    init_db, init_watchlist, upsert_ticker, mark_pre_notified,
    mark_post_notified, get_pending_post, get_watchlist, today_str,
)
from backend.services.earnings import (
    find_earnings_reporters,
    get_full_earnings_analysis,
    get_earnings_trade_for_date,
    get_earnings_dates_yf,
    get_eps_fast,
)
from backend.services.telegram_svc import send_telegram

logger = logging.getLogger(__name__)
CST = ZoneInfo("America/Chicago")

# ── Telegram credentials from root config ─────────────────────────────────────
try:
    _ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
    sys.path.insert(0, os.path.abspath(_ROOT))
    from config import (
        TELEGRAM_BOT_TOKEN,
        TELEGRAM_CHAT_ID,
        TELEGRAM_GROUP_CHAT_ID,
        TELEGRAM_MESSAGE_THREAD_ID,
        TELEGRAM_MACRO_MESSAGE_THREAD_ID,
        TELEGRAM_SPY_INTRADAY_MESSAGE_THREAD_ID,
        TELEGRAM_SWING_MESSAGE_THREAD_ID,
    )  # type: ignore
except ImportError:
    try:
        from backend.config import (
            TELEGRAM_BOT_TOKEN,
            TELEGRAM_CHAT_ID,
            TELEGRAM_GROUP_CHAT_ID,
            TELEGRAM_MESSAGE_THREAD_ID,
            TELEGRAM_MACRO_MESSAGE_THREAD_ID,
            TELEGRAM_SPY_INTRADAY_MESSAGE_THREAD_ID,
            TELEGRAM_SWING_MESSAGE_THREAD_ID,
        )
    except ImportError:
        TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
        TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
        TELEGRAM_GROUP_CHAT_ID = os.getenv("TELEGRAM_GROUP_CHAT_ID", "")
        TELEGRAM_MESSAGE_THREAD_ID = os.getenv("TELEGRAM_MESSAGE_THREAD_ID", "")
        TELEGRAM_MACRO_MESSAGE_THREAD_ID = os.getenv("TELEGRAM_MACRO_MESSAGE_THREAD_ID", "")
        TELEGRAM_SPY_INTRADAY_MESSAGE_THREAD_ID = os.getenv("TELEGRAM_SPY_INTRADAY_MESSAGE_THREAD_ID", "")
        TELEGRAM_SWING_MESSAGE_THREAD_ID = os.getenv("TELEGRAM_SWING_MESSAGE_THREAD_ID", "")

# ── Scheduler instance ────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler(timezone="America/Chicago")


def _telegram_target(thread_id: str | None = None) -> tuple[str, str | None]:
    target_chat = TELEGRAM_GROUP_CHAT_ID or TELEGRAM_CHAT_ID
    if thread_id and str(thread_id).strip():
        return target_chat, str(thread_id).strip()
    return target_chat, str(TELEGRAM_MESSAGE_THREAD_ID).strip() or None


def _env_enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


_MACRO_ALERTS_ENABLED = _env_enabled("MACRO_ALERTS_ENABLED", "1")
_MACRO_STATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "db", "macro_state.json",
)
_MACRO_ALERT_COOLDOWN_SEC = 300  # 5 min between alerts of any kind

_SCHEDULER_STATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "db", "scheduler_state.json",
)
_SPY_V4_SUMMARY_ENABLED = _env_enabled("SPY_V4_SUMMARY_ENABLED", "1")
_SWEEP_DIGEST_ENABLED = _env_enabled("SWEEP_DIGEST_ENABLED", "1")
_BACKEND_V3_REFRESH_ENABLED = _env_enabled("BACKEND_V3_REFRESH_ENABLED", "0")
_BACKEND_V3_REFRESH_WATCHLISTS = [
    w.strip().lower()
    for w in os.getenv("BACKEND_V3_REFRESH_WATCHLISTS", "holdings,earnings").split(",")
    if w.strip()
]
_BACKEND_V3_REFRESH_MAX_WORKERS = max(1, int(os.getenv("BACKEND_V3_REFRESH_MAX_WORKERS", "6")))
_BACKEND_V3_MESSAGE_THREAD_ID = os.getenv("BACKEND_V3_MESSAGE_THREAD_ID", "").strip()


def _load_scheduler_state() -> dict:
    try:
        if os.path.exists(_SCHEDULER_STATE_PATH):
            with open(_SCHEDULER_STATE_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_scheduler_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_SCHEDULER_STATE_PATH), exist_ok=True)
        tmp = f"{_SCHEDULER_STATE_PATH}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, _SCHEDULER_STATE_PATH)
    except Exception as exc:
        logger.warning("[scheduler] state write failed: %s", exc)


def _daily_job_sent(key: str, day: str | None = None) -> bool:
    day = day or datetime.now(CST).date().isoformat()
    return _load_scheduler_state().get(key) == day


def _mark_daily_job_sent(key: str, day: str | None = None) -> None:
    day = day or datetime.now(CST).date().isoformat()
    state = _load_scheduler_state()
    state[key] = day
    _save_scheduler_state(state)


# ── Message formatters ────────────────────────────────────────────────────────

def _fmt_pre(ticker: str, analysis: dict) -> str:
    d     = analysis.get("direction", {})
    stats = analysis.get("stats", {})
    em    = analysis.get("expected_move", {})
    rev   = analysis.get("revisions", {})
    sq    = d.get("squeeze_setup")

    lines = [f"<b>📊 PRE-EARNINGS: {ticker}</b>"]

    next_e = analysis.get("next_earnings") or analysis.get("last_reported")
    lines.append(f"Earnings: <b>{next_e or 'Today'}</b>  |  Price: ${analysis.get('current_price', 0):.2f}")

    # EPS Estimate from analyst consensus
    eps_est    = rev.get("est_current")
    n_analysts = rev.get("analyst_count")
    if eps_est is not None:
        est_str = f"EPS Est: <b>${eps_est:.2f}</b>"
        if n_analysts:
            est_str += f"  ({n_analysts} analysts)"
        lines.append(est_str)

    dir_str = d.get("direction", "—")
    conf    = d.get("confidence", "")
    score   = d.get("score", 0)
    lines.append(f"Direction: <b>{dir_str}</b> ({conf}, score {score:+d})")

    if em and not em.get("error"):
        lines.append(
            f"Options move: ±{em.get('expected_move_pct', '—')}%"
            f"  |  IV skew: {em.get('iv_skew', '—')}%"
        )
    lines.append(f"Est move (blended): ±{analysis.get('estimated_move', '—')}%")

    if stats:
        lines.append(
            f"Hist avg: ±{stats.get('avg_abs_move', '—')}%"
            f"  |  Beat rate: {stats.get('beat_rate', '—')}%"
            f"  |  Bull rate: {stats.get('bull_rate', '—')}%"
        )

    if sq and sq.get("score", 0) >= 2:
        lines.append(f"🔥 SQUEEZE: {sq.get('label', '')} (score {sq.get('score')})")

    return "\n".join(lines)


def _fmt_post(ticker: str, trade: dict, eps: dict) -> str:
    eps_actual   = trade.get("eps_actual")   or eps.get("eps_actual")
    eps_estimate = trade.get("eps_estimate") or eps.get("eps_estimate")
    surp         = trade.get("eps_surprise") or eps.get("surprise_pct")
    beat         = (surp or 0) > 0
    gap          = trade.get("gap_pct") or 0
    day          = trade.get("day_pct") or 0
    price        = trade.get("current_price")
    direction    = trade.get("direction", "—")
    entry        = trade.get("entry")
    exit_p       = trade.get("exit")
    vol          = trade.get("vol_ratio")
    source       = eps.get("source", "")

    beat_icon = "✅ BEAT" if beat else "❌ MISS"

    # EPS line: Actual $X.XX  vs Est $X.XX  → +X.X% BEAT
    if eps_actual is not None:
        eps_line = f"EPS: <b>${eps_actual:.2f}</b>"
        if eps_estimate is not None:
            eps_line += f"  vs Est ${eps_estimate:.2f}"
        if surp is not None:
            sign = "+" if surp > 0 else ""
            eps_line += f"  →  {sign}{surp:.1f}%  {beat_icon}"
    elif surp is not None:
        sign = "+" if surp > 0 else ""
        eps_line = f"EPS Surprise: {sign}{surp:.1f}%  {beat_icon}"
    else:
        eps_line = "EPS: pending"

    result_icon = "✅" if beat else "❌"
    lines = [
        f"<b>{result_icon} POST-EARNINGS: {ticker}</b>",
        f"Date: {trade.get('date', today_str())}  |  Source: {source or '—'}",
        eps_line,
    ]

    # Price movement (may not be available for AMC reporters until next day)
    if gap or day:
        lines.append(f"Gap: {gap:+.1f}%  |  Day: {day:+.1f}%")
    elif price:
        lines.append(f"Last price: ${price:.2f}")

    if entry and exit_p:
        pnl = trade.get("pnl_pct") or 0
        lines.append(f"Entry: ${entry:.2f}  →  Exit: ${exit_p:.2f}  |  PnL: {pnl:+.1f}%")
    if vol:
        lines.append(f"Vol: {vol:.2f}×")

    return "\n".join(lines)


# ── Job: 8:30 AM CST — pre-earnings discovery ─────────────────────────────────

async def pre_earnings_job():
    today = today_str()
    logger.info(f"[scheduler] pre_earnings_job started for {today}")

    try:
        tickers = find_earnings_reporters(today)
    except Exception as e:
        logger.error(f"[scheduler] find_earnings_reporters failed: {e}")
        tickers = []

    # Also include any watchlist tickers reporting today
    try:
        from backend.services.earnings import _check_ticker_for_date
        for wt in get_watchlist():
            if wt not in tickers and _check_ticker_for_date(wt, today):
                tickers.append(wt)
    except Exception as e:
        logger.warning(f"[scheduler] watchlist merge failed: {e}")

    if not tickers:
        send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                      f"📭 <b>No earnings reporters</b> found in watchlist for {today}")
        logger.info("[scheduler] No reporters found")
        return

    # Store all tickers in DB (eps_estimate filled in per-ticker below)
    for t in tickers:
        upsert_ticker(t, today)

    # Summary header
    send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                  f"📅 <b>Earnings Today — {today}</b>\n"
                  f"Found: {', '.join(tickers)}\nSending pre-earnings analysis…")

    # Per-ticker pre-earnings alert
    for ticker in tickers:
        try:
            analysis = get_full_earnings_analysis(ticker)
            if analysis.get("error"):
                logger.warning(f"[scheduler] analysis error for {ticker}: {analysis['error']}")
                continue

            # Save eps_estimate to DB so poll can use it later
            eps_est = analysis.get("revisions", {}).get("est_current")
            if eps_est is not None:
                upsert_ticker(ticker, today, eps_estimate=eps_est)

            msg = _fmt_pre(ticker, analysis)
            ok  = send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)

            pre_drift = None
            dir_info  = analysis.get("direction", {})
            factors   = dir_info.get("factors", [])
            for f in factors:
                if "drift" in f.get("name", "").lower():
                    try:
                        pre_drift = float(f["value"].replace("%", "").replace("+", ""))
                    except Exception:
                        pass
                    break

            if ok:
                mark_pre_notified(ticker, today, pre_drift=pre_drift)
                logger.info(f"[scheduler] pre-earnings sent for {ticker}")
        except Exception as e:
            logger.error(f"[scheduler] pre-earnings error for {ticker}: {e}")


# ── Job: 3 PM–6 PM CST — per-minute EPS poll ─────────────────────────────────

async def poll_for_eps():
    """
    1-min poll: check fast EPS sources, send post-earnings Telegram once confirmed.
    Sends as soon as EPS actual is available — doesn't wait for next-day price data.
    For AMC reporters the gap/day will be 0; for BMO they'll be populated once market closed.
    """
    import yfinance as yf
    from zoneinfo import ZoneInfo

    today   = today_str()
    pending = get_pending_post(today)

    if not pending:
        logger.debug("[scheduler] poll: no pending tickers")
        return

    logger.info(f"[scheduler] polling EPS for: {[r['ticker'] for r in pending]}")

    # Determine if market is closed (after 4 PM ET) — only then do we have closing prices
    now_et        = datetime.now(ZoneInfo("America/New_York"))
    market_closed = now_et.hour >= 16

    for row in pending:
        ticker       = row["ticker"]
        eps_est_db   = row.get("eps_estimate")   # saved at 8:30 AM
        try:
            # ── Step 1: fast EPS sources (Yahoo quoteSummary → Benzinga → Finviz) ──
            eps = get_eps_fast(ticker, today) or {}

            # ── Step 2: fall back to yfinance earnings_dates if fast failed ─────────
            if not eps or eps.get("eps_actual") is None:
                dates = get_earnings_dates_yf(ticker)
                found = next(
                    (e for e in reversed(dates)
                     if abs((datetime.strptime(e["date"], "%Y-%m-%d").date()
                             - datetime.strptime(today, "%Y-%m-%d").date()).days) <= 1
                     and e.get("eps_actual") is not None),
                    None,
                )
                if found:
                    eps = {
                        "eps_actual":   found.get("eps_actual"),
                        "eps_estimate": found.get("eps_estimate"),
                        "surprise_pct": found.get("surprise_pct"),
                        "source":       "yfinance",
                    }

            if not eps or eps.get("eps_actual") is None:
                logger.debug(f"[scheduler] {ticker}: no EPS data yet")
                continue

            # ── Back-fill eps_estimate from DB if fast source didn't return one ─────
            if eps.get("eps_estimate") is None and eps_est_db is not None:
                eps["eps_estimate"] = eps_est_db
                # Recalculate surprise with the saved estimate
                if eps.get("surprise_pct") is None and eps_est_db != 0:
                    actual = eps["eps_actual"]
                    eps["surprise_pct"] = round(
                        (actual - eps_est_db) / abs(eps_est_db) * 100, 1
                    )

            # ── Step 3: get price movement (only if market closed) ────────────────
            gap_pct = day_pct = current_price = None
            if market_closed:
                try:
                    hist = yf.Ticker(ticker).history(period="3d", interval="1d")
                    if len(hist) >= 2:
                        prev_close    = float(hist["Close"].iloc[-2])
                        today_open    = float(hist["Open"].iloc[-1])
                        today_close   = float(hist["Close"].iloc[-1])
                        gap_pct       = round((today_open  - prev_close) / prev_close * 100, 2)
                        day_pct       = round((today_close - prev_close) / prev_close * 100, 2)
                        current_price = today_close
                except Exception:
                    pass
            else:
                # Pre-close: fetch after-hours last price if available
                try:
                    fi = yf.Ticker(ticker).fast_info
                    current_price = float(getattr(fi, "last_price", None) or 0) or None
                except Exception:
                    pass

            trade = {
                "ticker":        ticker,
                "date":          today,
                "direction":     ("LONG" if (day_pct or 0) >= 0 else "SHORT") if day_pct is not None else "—",
                "gap_pct":       gap_pct or 0,
                "day_pct":       day_pct or 0,
                "pnl_pct":       day_pct or 0,
                "current_price": current_price,
                "eps_actual":    eps.get("eps_actual"),
                "eps_estimate":  eps.get("eps_estimate"),
                "eps_surprise":  eps.get("surprise_pct"),
                "beat":          (eps.get("surprise_pct") or 0) > 0,
            }

            msg = _fmt_post(ticker, trade, eps)
            ok  = send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)

            if ok:
                mark_post_notified(
                    ticker, today,
                    eps_actual   = eps.get("eps_actual"),
                    surprise_pct = eps.get("surprise_pct"),
                    eps_beat     = trade["beat"],
                    gap_pct      = gap_pct or 0,
                    day_pct      = day_pct or 0,
                    direction    = trade["direction"],
                    pnl_pct      = day_pct or 0,
                    vol_ratio    = None,
                    reason       = f"via {eps.get('source', 'fast lookup')}",
                )
                logger.info(
                    f"[scheduler] post-earnings sent for {ticker}: "
                    f"EPS={eps.get('eps_actual')}, surprise={eps.get('surprise_pct')}%"
                )

        except Exception as e:
            logger.error(f"[scheduler] poll error for {ticker}: {e}")


def start_eps_polling():
    """3:00 PM CST — add the 1-minute polling interval job."""
    if not scheduler.get_job("eps_poll"):
        scheduler.add_job(
            poll_for_eps,
            "interval",
            minutes=1,
            id="eps_poll",
            max_instances=1,
        )
        send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                      f"⏱ EPS polling started (every 1 min) for {today_str()}")
        logger.info("[scheduler] EPS polling started")


def momentum_scan_job():
    """8:45 AM CST — scan momentum watchlist (hardcoded 20 + user watchlist), send weekly targets to Telegram."""
    from backend.services.scanner import scan_single, WATCHLISTS
    from backend.db.earnings_tracker import get_watchlist
    from concurrent.futures import ThreadPoolExecutor, as_completed

    core     = WATCHLISTS.get("momentum", [])
    watchlist = get_watchlist()
    # Merge: core first, then any watchlist tickers not already in core
    seen = set(core)
    extra = [t for t in watchlist if t not in seen]
    tickers = core + extra
    logger.info(f"[scheduler] momentum scan: {len(core)} core + {len(extra)} watchlist = {len(tickers)} total")
    logger.info(f"[scheduler] momentum scan started for {len(tickers)} tickers")

    results = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(scan_single, t): t for t in tickers}
        for fut in as_completed(futures):
            try:
                r = fut.result()
                if not r.get("error") and r.get("verdict") not in ("NEUTRAL",):
                    results.append(r)
            except Exception:
                pass

    if not results:
        send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                      f"📊 <b>Momentum Scan {today_str()}</b>\nNo strong signals found today.")
        return

    # Sort: score descending (strongest signals first)
    results.sort(key=lambda x: -(x.get("score") or 0))

    lines = [f"📊 <b>Momentum Watchlist — {today_str()}</b>",
             f"Signals: {len(results)} | Sorted by score\n"]

    for r in results:
        ticker  = r["ticker"]
        price   = r.get("price", 0)
        verdict = r.get("verdict", "—")
        score   = r.get("score", 0)
        entry   = r.get("entry")
        stop    = r.get("stop_loss")
        t1      = r.get("target1")
        rr      = r.get("rr_t1")
        weekly  = r.get("weekly_bias", {})
        w_bias  = weekly.get("bias", "—") if isinstance(weekly, dict) else str(weekly)
        opt_sum = r.get("opt_summary")

        icon = "🟢" if "BULL" in (verdict or "") else "🔴"
        line = f"{icon} <b>{ticker}</b> ${price:.2f} | {verdict} ({score:+d})"
        line += f"\n   Weekly: {w_bias}"
        if entry and stop and t1:
            line += f"\n   Entry: ${entry:.2f}  Stop: ${stop:.2f}  T1: ${t1:.2f}"
            if rr:
                line += f"  R:R {rr:.1f}×"
        if opt_sum:
            line += f"\n   {opt_sum}"
        lines.append(line)

    send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, "\n".join(lines))
    logger.info(f"[scheduler] momentum scan sent: {len(results)} signals")


def stop_eps_polling():
    """6:00 PM CST — remove the polling job."""
    job = scheduler.get_job("eps_poll")
    if job:
        job.remove()
        send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                      f"🛑 EPS polling stopped for {today_str()}")
        logger.info("[scheduler] EPS polling stopped")


# ── Default 50 Pre-Market & 8:30 AM Near-Entry Jobs ───────────────────────────

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
_DEFAULT50_CACHE_FILE = os.path.join(_CACHE_DIR, "default50_premarket_cache.json")
_DEFAULT50_SCAN_CACHE: dict = {}


def default50_premarket_scan_job() -> dict:
    """
    8:00 AM CST (Mon-Fri) — Scan all 50 tickers in Default Watchlist.
    Caches trade setups (entry, stop, targets, direction, grade, options)
    ready for 8:30 AM market open Near Entry evaluation.
    """
    global _DEFAULT50_SCAN_CACHE
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from backend.services.scanner import scan_single, WATCHLISTS

    tickers = WATCHLISTS.get("default", [])
    logger.info(f"[scheduler] Default 50 pre-market scan started for {len(tickers)} tickers at 8:00 AM CT")

    valid_setups = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(scan_single, t): t for t in tickers}
        for fut in as_completed(futures):
            try:
                r = fut.result()
                if not r.get("error") and r.get("entry") is not None and r.get("price", 0) > 0:
                    valid_setups.append(r)
            except Exception as e:
                logger.warning(f"[scheduler] Error scanning ticker: {e}")

    # Sort setups by score descending
    valid_setups.sort(key=lambda x: -(x.get("score") or 0))

    long_count = sum(1 for r in valid_setups if r.get("direction") == "LONG" and r.get("verdict") != "NEUTRAL")
    short_count = sum(1 for r in valid_setups if r.get("direction") == "SHORT" and r.get("verdict") != "NEUTRAL")
    neutral_count = sum(1 for r in valid_setups if r.get("verdict") == "NEUTRAL")

    now_dt = datetime.now(CST)
    _DEFAULT50_SCAN_CACHE = {
        "date": today_str(),
        "timestamp": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "scanned_count": len(tickers),
        "valid_count": len(valid_setups),
        "items": valid_setups,
    }

    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_DEFAULT50_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_DEFAULT50_SCAN_CACHE, f, indent=2)
    except Exception as e:
        logger.warning(f"[scheduler] Failed writing Default 50 cache to disk: {e}")

    # Dispatch pre-market Telegram confirmation
    now_str = now_dt.strftime("%I:%M %p CT")
    msg = (
        f"🌅 <b>StockPulse Default 50 Pre-Market Scan ({now_str})</b>\n"
        f"Scanned: {len(tickers)} tickers · {len(valid_setups)} valid setups ready\n"
        f"🟢 Long: {long_count}  |  🔴 Short: {short_count}  |  ⚪ Neutral: {neutral_count}\n\n"
        f"🔒 <i>Setups locked with Entry, Stop, and Targets. Standing by for 8:30 AM CT market open to evaluate Near Entry tickers.</i>"
    )
    send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)
    logger.info(f"[scheduler] Default 50 pre-market scan complete: {len(valid_setups)} setups saved")

    # Also auto-generate and persist the full trade plan for top setups so /plan page is immediately ready
    try:
        from backend.services.plan_service import generate_intraday_plan
        plan_tickers = [s["ticker"] for s in valid_setups[:25]] or tickers[:25]
        generate_intraday_plan(plan_tickers, today_str())
        logger.info(f"[scheduler] Auto-generated and persisted trade plan for {len(plan_tickers)} tickers at 8:00 AM CT")
    except Exception as pe:
        logger.warning(f"[scheduler] Auto-generating trade plan on scan error: {pe}")

    return _DEFAULT50_SCAN_CACHE


def _fetch_default50_open_prices(tickers: list[str]) -> dict[str, dict]:
    """
    Fetch open/current price snapshot for tickers at 8:30 AM.
    Uses Alpaca latest bars batch first, falls back to yfinance.
    """
    prices: dict[str, dict] = {}
    remaining = list(tickers)

    # Strategy 1: Alpaca batch bars/latest
    try:
        from backend.config import ALPACA_API_KEY, ALPACA_API_SECRET, ALPACA_DATA_BASE
        import requests
        syms = ",".join(remaining)
        r = requests.get(
            f"{ALPACA_DATA_BASE}/v2/stocks/bars/latest",
            params={"symbols": syms, "feed": "iex"},
            headers={
                "APCA-API-KEY-ID": ALPACA_API_KEY,
                "APCA-API-SECRET-KEY": ALPACA_API_SECRET,
            },
            timeout=8,
        )
        if r.status_code == 200:
            bars = r.json().get("bars", {})
            for sym, b in bars.items():
                if b and b.get("c"):
                    prices[sym] = {
                        "price": round(float(b.get("c")), 2),
                        "open": round(float(b.get("o") or b.get("c")), 2),
                        "high": round(float(b.get("h") or b.get("c")), 2),
                        "low": round(float(b.get("l") or b.get("c")), 2),
                        "source": "Alpaca",
                    }
                    if sym in remaining:
                        remaining.remove(sym)
    except Exception as e:
        logger.warning(f"[scheduler] Alpaca batch open fetch error: {e}")

    # Strategy 2: yfinance fallback for any missing tickers
    if remaining:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import yfinance as yf

        def _get_yf_price(t: str):
            try:
                tk = yf.Ticker(t)
                fi = tk.fast_info
                p = fi.get("last_price") or fi.get("regular_market_price")
                op = fi.get("open") or fi.get("regular_market_open") or p
                if p:
                    return t, {"price": round(float(p), 2), "open": round(float(op), 2), "source": "yfinance"}
            except Exception:
                pass
            return t, None

        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(_get_yf_price, t): t for t in remaining}
            for fut in as_completed(futs):
                t, res = fut.result()
                if res:
                    prices[t] = res

    return prices


def default50_near_entry_alert_job() -> dict:
    """
    8:30:15 AM CST (Mon-Fri) — Evaluate market open prices for Default 50 setups.
    Identifies tickers opening Near Entry (within ±0.75% of entry on safe side of stop)
    and sends actionable trade alerts with Entry, Stop, T1, T2 to Telegram.
    """
    global _DEFAULT50_SCAN_CACHE
    logger.info("[scheduler] 8:30 AM Default 50 Near Entry check triggered")

    # Ensure we have pre-market setups from 8:00 AM
    if not _DEFAULT50_SCAN_CACHE or not _DEFAULT50_SCAN_CACHE.get("items"):
        if os.path.exists(_DEFAULT50_CACHE_FILE):
            try:
                with open(_DEFAULT50_CACHE_FILE, "r", encoding="utf-8") as f:
                    _DEFAULT50_SCAN_CACHE = json.load(f)
            except Exception:
                pass

    if not _DEFAULT50_SCAN_CACHE or not _DEFAULT50_SCAN_CACHE.get("items"):
        logger.info("[scheduler] No cached setups found; running Default 50 scan now")
        default50_premarket_scan_job()

    setups = _DEFAULT50_SCAN_CACHE.get("items", [])
    if not setups:
        send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                      f"⚠️ <b>Default 50 Alert ({today_str()})</b>\nCould not fetch setups to evaluate open prices.")
        return {"count": 0, "items": []}

    tickers = [s["ticker"] for s in setups]
    open_prices = _fetch_default50_open_prices(tickers)

    near_entries = []
    for s in setups:
        ticker = s["ticker"]
        verdict = s.get("verdict", "NEUTRAL")
        direction = s.get("direction", "LONG")
        entry = s.get("entry")
        stop = s.get("stop_loss")
        t1 = s.get("target1")
        t2 = s.get("target2")

        if verdict in ("NEUTRAL",) or entry is None or stop is None or entry <= 0:
            continue

        p_info = open_prices.get(ticker)
        if not p_info or not p_info.get("price"):
            current_p = s.get("price")
            open_p = current_p
        else:
            current_p = p_info["price"]
            open_p = p_info.get("open", current_p)

        if not current_p or current_p <= 0:
            continue

        diff_pct = round((current_p - entry) / entry * 100, 2)

        is_near = False
        scenario_label = ""

        if direction == "LONG":
            if 0.0 <= diff_pct <= 0.75:
                is_near = True
                scenario_label = "✅ OPENS NEAR ENTRY"
            elif -0.75 <= diff_pct < 0.0 and current_p > stop:
                is_near = True
                scenario_label = "⚡ NEAR ENTRY (PULLBACK)"
        else:  # SHORT
            if -0.75 <= diff_pct <= 0.0:
                is_near = True
                scenario_label = "✅ OPENS NEAR ENTRY"
            elif 0.0 < diff_pct <= 0.75 and current_p < stop:
                is_near = True
                scenario_label = "⚡ NEAR ENTRY (PULLBACK)"

        if is_near:
            near_entries.append({
                **s,
                "current_price": current_p,
                "open_price": open_p,
                "diff_pct": diff_pct,
                "scenario_label": scenario_label,
            })

    # Filter & Prioritize Best Stocks with Good R/R (R:R >= 1.5x and Grade S/A/B)
    good_rr_entries = [
        item for item in near_entries
        if (item.get("rr_t1") or 0) >= 1.5 and item.get("entry_grade") in ("S", "A", "B", "B-")
    ]

    # If we have tickers with R/R >= 1.5x and solid grade, focus on them; otherwise fallback to all near entries
    target_entries = good_rr_entries if good_rr_entries else near_entries

    # Sort primarily by Best R/R descending, then by Score descending
    target_entries.sort(key=lambda x: (
        -(x.get("rr_t1") or 0),
        -(x.get("score") or 0),
        abs(x.get("diff_pct") or 0),
    ))

    now_str = datetime.now(CST).strftime("%I:%M %p CT")
    if not target_entries:
        msg = (
            f"🎯 <b>8:30 AM Market Open — Near Entry Report</b>\n"
            f"Default 50 Watchlist · {now_str}\n\n"
            f"Scanned {len(setups)} setups at open.\n"
            f"No tickers opened near entry today."
        )
        send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)
        logger.info("[scheduler] 8:30 AM Near Entry check: 0 matches found")
        return {"count": 0, "items": []}

    is_filtered_good_rr = bool(good_rr_entries)
    title_suffix = "TOP R/R ≥ 1.5×" if is_filtered_good_rr else "NEAR ENTRY"
    header_msg = (
        f"🎯 <b>8:30 AM Market Open — {title_suffix} ALERTS</b>\n"
        f"Default 50 Watchlist · {now_str}\n"
        f"Found <b>{len(target_entries)}</b> setups near entry (ranked by highest R/R first).\n"
        f"<i>Sending top individual trade cards below:</i>"
    )
    send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, header_msg)

    import time
    cards_sent = 0
    # Send up to top 6 best setups as individual clean cards
    for item in target_entries[:6]:
        tk = item["ticker"]
        dirn = item.get("direction", "LONG")
        icon = "🟢" if dirn == "LONG" else "🔴"
        verdict = item.get("verdict", "—")
        score = item.get("score", 0)
        grade = item.get("entry_grade", "—")
        wr = item.get("expected_wr")
        cp = item["current_price"]
        entry = item.get("entry", 0)
        stop = item.get("stop_loss", 0)
        t1 = item.get("target1", 0)
        t2 = item.get("target2", 0)
        rr = item.get("rr_t1")
        diff = item["diff_pct"]
        sc_label = item["scenario_label"]
        opt_strat = item.get("opt_strategy")
        opt_sum = item.get("opt_summary")

        # Dynamic ATM Strike calculation
        if cp >= 200:
            step = 5.0
        elif cp >= 50:
            step = 2.5
        elif cp >= 20:
            step = 1.0
        else:
            step = 0.5
        atm_strike = round(round(cp / step) * step, 2)
        atm_str = f"${atm_strike:.0f}" if atm_strike.is_integer() else f"${atm_strike:.2f}"

        # Upcoming Friday expiry date
        now_cst = datetime.now(CST)
        days_to_fri = (4 - now_cst.weekday()) % 7
        if days_to_fri == 0 and now_cst.hour >= 15:
            days_to_fri = 7
        exp_fri = (now_cst + timedelta(days=days_to_fri)).strftime("%b %d")

        opt_contract = f"Buy {atm_str} Call (Exp {exp_fri})" if dirn == "LONG" else f"Buy {atm_str} Put (Exp {exp_fri})"

        sign = "+" if diff > 0 else ""
        stop_dist_pct = abs((stop - cp) / cp * 100) if cp > 0 else 0
        t1_dist_pct = abs((t1 - cp) / cp * 100) if cp > 0 else 0
        t2_dist_pct = abs((t2 - cp) / cp * 100) if cp > 0 else 0
        wr_str = f" · {wr:.0f}% exp WR" if wr else ""

        card_lines = [
            f"{icon} <b>{tk}</b> · <b>${cp:.2f}</b> ({dirn})",
            f"━━━━━━━━━━━━━━━━━━━",
            f"📊 <b>Setup</b>: {verdict} (Score: {score:+d} · Grade: {grade}{wr_str})",
            f"🎯 <b>Status</b>: {sc_label} ({sign}{diff:.2f}% from entry)",
            f"",
            f"📍 <b>Trade Levels</b>:",
            f"• Entry: <b>${entry:.2f}</b>",
            f"• Stop Loss: <b>${stop:.2f}</b> (-{stop_dist_pct:.1f}%)",
            f"• Target 1: <b>${t1:.2f}</b> (+{t1_dist_pct:.1f}%)",
        ]
        if t2:
            card_lines.append(f"• Target 2: <b>${t2:.2f}</b> (+{t2_dist_pct:.1f}%)")
        if rr:
            card_lines.append(f"• Risk/Reward: <b>{rr:.1f}×</b>")

        card_lines.append("")
        card_lines.append("💡 <b>Options Contract</b>:")
        card_lines.append(f"• ATM Strike: <b>{opt_contract}</b>")

        # Include detailed spread strikes if available
        if opt_sum:
            clean_sum = opt_sum
            if ":" in clean_sum:
                clean_sum = clean_sum.split(":", 1)[1].strip()
            card_lines.append(f"• Spread: <i>{clean_sum}</i>")
        elif opt_strat:
            card_lines.append(f"• Strategy: <i>{opt_strat}</i>")

        # Automatically enter into Paper Trading engine
        paper_trade_id = None
        try:
            from alpaca_paper import PaperTrader
            pt = PaperTrader()
            try:
                open_res = pt.open_trade(
                    ticker=tk,
                    direction=dirn,
                    entry_price=cp or entry,
                    stop_price=stop,
                    t1_price=t1,
                    t2_price=t2 or t1,
                    trade_date=today_str(),
                    scenario=sc_label,
                    confidence=str(score),
                )
                if isinstance(open_res, dict) and open_res.get("id"):
                    paper_trade_id = open_res["id"]
                elif isinstance(open_res, int):
                    paper_trade_id = open_res
                logger.info(f"[scheduler] Auto-opened paper trade for {tk}: {open_res}")
            finally:
                pt.close()
        except Exception as pe:
            logger.warning(f"[scheduler] Auto-logging paper trade failed for {tk}: {pe}")

        card_lines.append("")
        if paper_trade_id:
            card_lines.append(f"📝 <b>Paper Trading</b>: Active (Trade #{paper_trade_id})")
        else:
            card_lines.append(f"📝 <b>Paper Trading</b>: Auto-logged in Paper DB")

        card_msg = "\n".join(card_lines)
        send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, card_msg)
        cards_sent += 1
        time.sleep(0.3)

    logger.info(f"[scheduler] 8:30 AM Near Entry alert sent {cards_sent} separate ticker cards and logged to paper trading")

    # Also auto-evaluate and lock the 8:30 AM playbook snapshot to disk
    try:
        from backend.services.plan_service import get_persisted_plan, check_open_prices, save_locked_830_cache
        cached = get_persisted_plan()
        if cached.get("plan_data") and cached["plan_data"].get("rows"):
            check_res = check_open_prices(cached["plan_data"]["rows"], today_str())
            if check_res and check_res.get("rows"):
                save_locked_830_cache(check_res)
                logger.info(f"[scheduler] Auto-locked 8:30 AM open check playbook for {len(check_res['rows'])} tickers")
    except Exception as lock_err:
        logger.warning(f"[scheduler] Auto-locking 8:30 AM open check playbook failed: {lock_err}")

    return {"count": len(target_entries), "cards_sent": cards_sent, "items": target_entries}


def paper_exit_monitor_job(force: bool = False) -> dict:
    """
    Intraday Paper Trading Monitor (runs every 5 mins from 8:35 AM to 3:00 PM CST, Mon-Fri).
    Fetches current market prices for open paper trades, evaluates stop loss and profit targets,
    and automatically executes exits with Telegram notifications.
    """
    now_cst = datetime.now(CST)
    if not force:
        # Check regular market hours: 8:35 AM to 3:05 PM CST
        if now_cst.hour < 8 or (now_cst.hour == 8 and now_cst.minute < 35):
            return {"status": "market_not_open_yet"}
        if now_cst.hour > 15 or (now_cst.hour == 15 and now_cst.minute > 5):
            return {"status": "market_closed"}

    try:
        from alpaca_paper import PaperTrader
        pt = PaperTrader()
        try:
            # Re-attach GTC exit orders on Alpaca if previous DAY orders expired
            try:
                re_attached = pt.ensure_active_orders_for_open_positions()
                if re_attached:
                    logger.info(f"[scheduler] Re-attached GTC exit orders on Alpaca: {re_attached}")
            except Exception as re_err:
                logger.warning(f"[scheduler] Error checking active Alpaca orders: {re_err}")

            open_trades = pt.get_open_trades()
            if not open_trades:
                return {"status": "no_open_trades", "count": 0}

            tickers = list({t["ticker"] for t in open_trades})
            prices = _fetch_default50_open_prices(tickers)

            exited_trades = []
            for tr in open_trades:
                tk = tr["ticker"]
                p_info = prices.get(tk)
                if not p_info or not p_info.get("price"):
                    continue
                cur_price = p_info["price"]

                exits = pt.check_exits(tk, cur_price)
                if exits:
                    for ex in exits:
                        exited_trades.append(ex)
                        outcome = ex.get("outcome", "FLAT")
                        pnl_dol = ex.get("pnl_dollars", 0.0)
                        pnl_pct = ex.get("pnl_pct", 0.0)
                        reason = ex.get("reason", "EXIT")

                        icon = "🎯" if outcome == "WIN" else "🛑"
                        sign = "+" if pnl_dol >= 0 else ""

                        msg = (
                            f"{icon} <b>Paper Trade Exit: {tk}</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"• Outcome: <b>{outcome}</b> ({reason})\n"
                            f"• Exit Price: <b>${cur_price:.2f}</b>\n"
                            f"• Realized P&L: <b>{sign}${pnl_dol:.2f}</b> ({sign}{pnl_pct:.1f}%)\n"
                            f"• Status: Position Closed &amp; Archived"
                        )
                        send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)
                        logger.info(f"[scheduler] Paper exit executed for {tk}: {ex}")

            return {"status": "ok", "checked": len(open_trades), "exited": len(exited_trades)}
        finally:
            pt.close()
    except Exception as e:
        logger.warning(f"[scheduler] Paper exit monitor error: {e}")
        return {"status": "error", "error": str(e)}


# ── ThinkOrSwim (TOS) Scan Email Watchlist Job ────────────────────────────────

def tos_email_poll_job() -> int:
    """7:15 PM CST — poll Gmail IMAP for ThinkOrSwim scan alert emails."""
    try:
        from backend.services.gmail_watchlist import poll_and_store, fetch_today_watchlist
        new_count = poll_and_store()
        tickers = fetch_today_watchlist()
        logger.info(f"[scheduler] 7:15 PM TOS scan email poll finished: {new_count} new messages, {len(tickers)} today's tickers")
        if tickers and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            curl_extra = ""
            try:
                from backend.services.curl_scanner import scan_30w_curl_candidates
                c_res = scan_30w_curl_candidates(tickers=tickers[:50], max_weeks_ago=8)
                c_matches = c_res.get("matches", [])
                if c_matches:
                    c_tks = [m["ticker"] for m in c_matches]
                    curl_extra = f"\n🌀 <b>30W MA Curl Setups ({len(c_tks)})</b>:\n<code>{', '.join(c_tks)}</code>\n"
            except Exception as e:
                logger.warning(f"[scheduler] TOS email poll 30W curl check failed: {e}")

            send_telegram(
                TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                f"📧 <b>ThinkOrSwim Scan Watchlist ({today_str()})</b>\n"
                f"Fetched <b>{len(tickers)}</b> tickers from TOS email alerts:\n"
                f"<code>{', '.join(tickers)}</code>\n"
                f"{curl_extra}"
            )
        return new_count
    except Exception as e:
        logger.warning(f"[scheduler] TOS scan email poll failed: {e}")
        return 0


def triad_best_picks_alert_job() -> dict:
    """
    7:30 PM CST (Mon-Fri) — Dispatch the 3-Category Swing Best Picks Alert to Telegram.
    Scans today's ThinkOrSwim scan alerts (from the 7:15 PM poll) or Default 50.
    Evaluates 52W ATH momentum (Strength), tight VCP coils (Emerging), and dip support (Weakness),
    along with qualifying 30-Week MA Curl Up tickers.
    """
    try:
        from backend.services.best_pick import dispatch_triad_telegram_alert
        logger.info("[scheduler] 7:30 PM CST Triad Best Picks Alert Job started")
        res = dispatch_triad_telegram_alert(watchlist="tos_email", days=1, send_msg=True)
        curl_cnt = res.get("curl_count", 0)
        logger.info(f"[scheduler] Triad Best Picks Alert Job completed: sent={res.get('sent')} scanned={res.get('total_scanned')} curls={curl_cnt}")
        return res
    except Exception as e:
        logger.error(f"[scheduler] Triad Best Picks Alert Job error: {e}")
        return {"ok": False, "error": str(e)}


def eod_exceptional_scan_job() -> dict:
    """
    4:15 PM CST (Mon-Fri) — Dispatch the End-of-Day Multi-Timeframe Strategy Exceptional Scans to Telegram.
    Scans the complete combined universe: Default 50 + Momentum + ThinkOrSwim (TOS) Gmail alerts.
    Filters and formats Grade S/A setups, 30W Stage 2 Curls, Volume Profile confluence, and Options.
    """
    try:
        from backend.services.exceptional_scanner import dispatch_exceptional_telegram_alert
        logger.info("[scheduler] End-of-Day Exceptional Scan Job started (Default 50 + Momentum + Gmail TOS)...")
        res = dispatch_exceptional_telegram_alert(send_msg=True)
        bull_cnt = res.get("exceptional_bull_count", 0)
        bear_cnt = res.get("exceptional_bear_count", 0)
        curl_cnt = res.get("stage2_curl_count", 0)
        logger.info(
            f"[scheduler] EOD Exceptional Scan Job completed: sent={res.get('sent')} "
            f"scanned={res.get('total_scanned')} bull={bull_cnt} bear={bear_cnt} curls={curl_cnt}"
        )
        return res
    except Exception as e:
        logger.error(f"[scheduler] EOD Exceptional Scan Job error: {e}")
        return {"ok": False, "error": str(e)}


# Alias for backwards compatibility with scheduler router
exceptional_swing_digest_job = eod_exceptional_scan_job


def breakout_digest_job() -> dict:
    """
    Scans Default 50 & Momentum 50 watchlists for breakout alerts and dispatches to Telegram.
    """
    try:
        from backend.services.breakout_scanner import dispatch_breakout_telegram_alert
        logger.info("[scheduler] Breakout Scan Job started (Default 50 + Momentum 50)...")
        res = dispatch_breakout_telegram_alert(send_msg=True)
        logger.info(
            f"[scheduler] Breakout Scan Job completed: sent={res.get('sent')} "
            f"scanned={res.get('total_scanned')} matches={res.get('total_matches')}"
        )
        return res
    except Exception as e:
        logger.error(f"[scheduler] Breakout Scan Job error: {e}")
        return {"ok": False, "error": str(e)}


breakout_alert_job = breakout_digest_job


# ── Macro & Gamma scheduler jobs ──────────────────────────────────────────────

def sector_gamma_job():
    """After open/close on trading days: recompute all sector GEX so the
    per-sector daily streak is recorded even when no one has the UI open."""
    try:
        from backend.services.gex import compute_sector_gex
        res = compute_sector_gex()
        secs = res.get("sectors", [])
        avail = [s for s in secs if s.get("available")]
        longg = sum(1 for s in avail if s.get("regime") == "Long Gamma")
        shortg = sum(1 for s in avail if s.get("regime") == "Short Gamma")
        logger.info(
            f"[scheduler] sector gamma: {len(avail)}/{len(secs)} available "
            f"({longg} long, {shortg} short) — streaks recorded"
        )
    except Exception as e:
        logger.error(f"[scheduler] sector gamma job failed: {e}")


def spy_gamma_job():
    """After open/close on trading days: recompute SPY GEX so the daily sign is
    recorded (and the consecutive-day streak advances) even when nobody has
    the UI open. Without this, SPY's streak pins at ±1 because its sign is
    only written opportunistically on UI hits."""
    try:
        from backend.services.gex import compute_spy_gex
        g = compute_spy_gex()
        if g.get("available"):
            logger.info(
                f"[scheduler] SPY gamma: {g.get('regime')} "
                f"net={g.get('net_gex')} streak={g.get('streak')} "
                f"store={g.get('store')} — sign recorded"
            )
        else:
            logger.warning(
                f"[scheduler] SPY gamma unavailable: "
                f"{g.get('reason', 'unknown')} — sign NOT recorded today"
            )
    except Exception as e:
        logger.error(f"[scheduler] SPY gamma job failed: {e}")


def _compute_day_verdict(gex_regime, btd_state, btd_zone, risk_score):
    """Return (label, reason). Exact mirror of MarketRisk.tsx dayVerdict()."""
    # Sell triggers — any single warning sign wins
    sell_reasons: list[str] = []
    if gex_regime in ("Short Gamma", "Near Flip"):
        sell_reasons.append(f"γ {gex_regime}")
    if btd_state == "DISARMED":
        sell_reasons.append("BTD DISARMED")
    if (risk_score or 0) >= 3:
        sell_reasons.append(f"Risk {risk_score} (HIGH)")
    if sell_reasons:
        return "Day to Sell", "Sell bias — " + " · ".join(sell_reasons)

    pullback_or_trigger = (
        btd_state == "TRIGGER"
        or (btd_state == "ARMED"      and btd_zone == "dip 20–50EMA")
        or (btd_state == "ARMED-DEEP" and btd_zone == "deep dip <50EMA")
    )
    is_extended = btd_state == "ARMED" and btd_zone == "extended >20EMA"

    if gex_regime == "Long Gamma" and pullback_or_trigger and (risk_score or 0) <= 2:
        suffix = " · half size — deeper risk" if btd_state == "ARMED-DEEP" else ""
        return "Day to Buy", (
            f"Buy bias — γ Long Gamma · BTD {btd_state}/{btd_zone or '?'} "
            f"· Risk {risk_score}/MOD or better{suffix}"
        )
    if gex_regime == "Long Gamma" and is_extended and (risk_score or 0) <= 2:
        return "Wait for pullback", (
            f"Environment OK (γ Long Gamma · Risk {risk_score}) but BTD ARMED "
            "· extended >20EMA — no entry trigger. Wait for price to pull "
            "back to 20EMA."
        )
    return "Sideline", (
        f"Mixed — γ:{gex_regime or '?'} · BTD:{btd_state or '?'} "
        f"· Risk:{risk_score}"
    )


def _load_macro_state() -> dict:
    try:
        if os.path.exists(_MACRO_STATE_PATH):
            import json
            with open(_MACRO_STATE_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception as e:
        logger.warning(f"[macro_watch] state read failed: {e}")
    return {}


def _save_macro_state(state: dict) -> None:
    try:
        import json
        os.makedirs(os.path.dirname(_MACRO_STATE_PATH), exist_ok=True)
        with open(_MACRO_STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except Exception as e:
        logger.warning(f"[macro_watch] state write failed: {e}")


def _verdict_emoji(label: str) -> str:
    return {
        "Day to Buy": "📈",
        "Wait for pullback": "⏳",
        "Sideline": "⏸",
        "Day to Sell": "📉",
    }.get(label, "📊")


def macro_regime_watch_job():
    """Poll the macro snapshot every 5 min during market hours; Telegram on
    verdict transitions and γ regime flips. State persisted across restarts."""
    if not _MACRO_ALERTS_ENABLED:
        return
    import html
    from datetime import datetime, timezone

    try:
        from backend.routers.macro import macro_snapshot
        snap = macro_snapshot()
    except Exception as e:
        logger.warning(f"[macro_watch] snapshot failed: {e}")
        return

    gex   = snap.get("gex") or {}
    btd   = snap.get("btd") or {}
    risk  = snap.get("risk") or {}
    gex_avail   = gex.get("available") is True
    gex_regime  = gex.get("regime") if gex_avail else None
    btd_state   = btd.get("btd_state")
    btd_zone    = btd.get("btd_zone")
    risk_score  = int(risk.get("score") or 0)
    risk_label  = risk.get("label") or "?"
    verdict, reason = _compute_day_verdict(
        gex_regime, btd_state, btd_zone, risk_score
    )

    state = _load_macro_state()
    prev_verdict = state.get("verdict")
    prev_gamma   = state.get("gex_regime")
    now_iso      = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Detect transitions
    transitions: list[tuple[str, str, str]] = []
    if prev_verdict and verdict != prev_verdict:
        transitions.append(("Verdict", prev_verdict, verdict))
    if prev_gamma and gex_regime and gex_regime != prev_gamma:
        transitions.append(("γ regime", prev_gamma, gex_regime))

    if not transitions:
        # First run — seed state without alerting
        if not prev_verdict:
            state.update(
                verdict=verdict, gex_regime=gex_regime,
                btd_state=btd_state, btd_zone=btd_zone,
                risk_score=risk_score, last_changed_at=now_iso,
            )
            _save_macro_state(state)
        return

    # Cooldown — avoid whipsaw storms on Near-Flip boundaries
    last_alert_iso = state.get("last_alerted_at")
    if last_alert_iso:
        try:
            last = datetime.fromisoformat(last_alert_iso)
            age_s = (datetime.now(timezone.utc) - last).total_seconds()
            if age_s < _MACRO_ALERT_COOLDOWN_SEC:
                logger.info(
                    f"[macro_watch] transition detected but within "
                    f"{int(age_s)}s cooldown — suppressing"
                )
                state.update(
                    verdict=verdict, gex_regime=gex_regime,
                    btd_state=btd_state, btd_zone=btd_zone,
                    risk_score=risk_score, last_changed_at=now_iso,
                )
                _save_macro_state(state)
                return
        except Exception:
            pass

    # Build alert
    spy_chg_1d = ""
    vix_now    = ""
    for it in snap.get("items", []):
        if it.get("ticker") == "SPY":
            spy_chg_1d = f"SPY {it.get('chg_1d', 0):+.1f}% 1d · 5d {it.get('chg_5d', 0):+.1f}%"
        elif it.get("ticker") == "^VIX":
            vix_now = f"VIX {it.get('price', 0):.1f} ({it.get('chg_1d', 0):+.1f}% 1d)"

    e_prev = _verdict_emoji(prev_verdict or "")
    e_curr = _verdict_emoji(verdict)
    lines = [f"<b>🚨 MARKET REGIME CHANGE</b>"]
    for kind, before, after in transitions:
        lines.append(
            f"<b>{html.escape(kind)}:</b> {html.escape(before)} → "
            f"<b>{html.escape(after)}</b>"
        )
    lines.append("")
    lines.append(f"{e_curr} <b>{html.escape(verdict)}</b>")
    lines.append(f"<i>{html.escape(reason)}</i>")
    if spy_chg_1d or vix_now:
        ctx = " · ".join(x for x in (spy_chg_1d, vix_now, risk_label) if x)
        lines.append(f"\n{html.escape(ctx)}")
    msg = "\n".join(lines)

    try:
        chat_id, thread_id = _telegram_target(TELEGRAM_MACRO_MESSAGE_THREAD_ID)
        send_telegram(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            msg,
            message_thread_id=thread_id,
        )
        logger.info(
            f"[macro_watch] alerted: {prev_verdict} → {verdict} "
            f"(γ {prev_gamma} → {gex_regime})"
        )
    except Exception as e:
        logger.warning(f"[macro_watch] telegram send failed: {e}")

    state.update(
        verdict=verdict, gex_regime=gex_regime,
        btd_state=btd_state, btd_zone=btd_zone,
        risk_score=risk_score,
        last_changed_at=now_iso, last_alerted_at=now_iso,
    )
    _save_macro_state(state)


# ── V4 & V3 Day Trading Scheduler Jobs ────────────────────────────────────────

def spy_v4_summary_job():
    """Send the SPY Day Trading V4 plan to Telegram."""
    if not _SPY_V4_SUMMARY_ENABLED:
        logger.info("[scheduler] SPY V4 summary skipped: SPY_V4_SUMMARY_ENABLED=0")
        return

    import html

    try:
        from backend.services.scanner import scan_single

        try:
            row = scan_single("SPY", None, None, "daytrading")
        except TypeError:
            row = scan_single("SPY")

        if not row.get("dt4_setup") or row.get("error"):
            try:
                from day_trading.v4 import analyze as _dt4_direct
                _direct = _dt4_direct("SPY")
                if _direct.get("signal"):
                    _sig = _direct["signal"]
                    _lvl = _direct.get("levels") or {}
                    row.update({
                        "dt4_enabled": True,
                        "dt4_setup": _sig.get("setup"),
                        "dt4_context": _sig.get("context"),
                        "dt4_side": _sig.get("side"),
                        "dt4_bias": _sig.get("bias"),
                        "dt4_grade": _sig.get("grade"),
                        "dt4_level": _sig.get("level"),
                        "dt4_level_val": _sig.get("level_val"),
                        "dt4_entry": _sig.get("entry"),
                        "dt4_stop": _sig.get("stop"),
                        "dt4_t1": _sig.get("t1"),
                        "dt4_t2": _sig.get("t2"),
                        "dt4_rr": _sig.get("rr"),
                        "dt4_trigger": _sig.get("trigger"),
                        "dt4_invalidation": _sig.get("invalidation"),
                        "dt4_target_plan": _sig.get("target_plan"),
                        "dt4_exit_plan": _sig.get("exit_plan"),
                        "dt4_note": _sig.get("note"),
                        "dt4_pdh": _lvl.get("pdh"),
                        "dt4_pdl": _lvl.get("pdl"),
                        "dt4_pwh": _lvl.get("pwh"),
                        "dt4_pwl": _lvl.get("pwl"),
                        "dt4_atr": _lvl.get("atr"),
                        "price": _direct.get("price") or row.get("price"),
                    })
            except Exception as _e:
                logger.warning(f"[scheduler] SPY direct V4 fallback failed: {_e}")

        chat_id, thread_id = _telegram_target(TELEGRAM_SPY_INTRADAY_MESSAGE_THREAD_ID)
        if row.get("error") and not row.get("dt4_setup"):
            msg = (
                f"<b>SPY Day Trading V4 — {today_str()}</b>\n"
                f"Unavailable: {html.escape(str(row.get('error') or 'unknown error'))}"
            )
            send_telegram(
                TELEGRAM_BOT_TOKEN,
                chat_id,
                msg,
                message_thread_id=thread_id,
            )
            logger.warning("[scheduler] SPY V4 summary unavailable: %s", row.get("error"))
            return

        def _money(value) -> str:
            return f"${float(value):.2f}" if isinstance(value, (int, float)) else "—"

        def _rr(value) -> str:
            return f"{float(value):.2f}x" if isinstance(value, (int, float)) else "—"

        setup = str(row.get("dt4_setup") or "—")
        setup_text = html.escape(setup.replace("_", " "))
        side = str(row.get("dt4_side") or "—")
        side_label = "Long" if side == "long" else "Short" if side == "short" else "Plan"
        grade = html.escape(str(row.get("dt4_grade") or "—"))
        bias = html.escape(str(row.get("dt4_bias") or "—"))
        context = html.escape(str(row.get("dt4_context") or "—"))
        range_wait = setup == "range_wait"

        if range_wait:
            level_lines = [
                f"Support: PDL {_money(row.get('dt4_pdl'))} / PWL {_money(row.get('dt4_pwl'))}",
                f"Resistance: PDH {_money(row.get('dt4_pdh'))} / PWH {_money(row.get('dt4_pwh'))}",
                "Entry: wait for reclaim/reject confirmation",
                "Risk: define after trigger",
                "Target: VWAP/mid, then opposite edge",
            ]
        else:
            level = html.escape(str(row.get("dt4_level") or "—"))
            level_lines = [
                f"Level: {level} {_money(row.get('dt4_level_val'))}",
                f"Watch/Entry: {_money(row.get('dt4_entry'))}",
                f"Stop: {_money(row.get('dt4_stop'))}",
                f"T1: {_money(row.get('dt4_t1'))} / T2: {_money(row.get('dt4_t2'))}",
                f"R:R: {_rr(row.get('dt4_rr'))}",
            ]

        trigger = html.escape(str(row.get("dt4_trigger") or "—"))[:320]
        invalidation = html.escape(str(row.get("dt4_invalidation") or "—"))[:260]
        target_plan = html.escape(str(row.get("dt4_target_plan") or "—"))[:260]
        exit_plan = html.escape(str(row.get("dt4_exit_plan") or "—"))[:260]
        note = html.escape(str(row.get("dt4_note") or ""))[:260]

        msg = (
            f"<b>SPY Day Trading V4 — {today_str()}</b>\n"
            f"{side_label} | {setup_text} | Grade {grade}\n"
            f"Bias: {bias} | Context: {context}\n"
            f"Price: {_money(row.get('price'))} | ATR: {_money(row.get('dt4_atr'))}\n"
            f"PDH {_money(row.get('dt4_pdh'))} | PDL {_money(row.get('dt4_pdl'))} | "
            f"PWH {_money(row.get('dt4_pwh'))} | PWL {_money(row.get('dt4_pwl'))}\n\n"
            + "\n".join(level_lines)
            + f"\n\nTrigger: {trigger}\n"
            f"Invalidation: {invalidation}\n"
            f"Target plan: {target_plan}\n"
            f"Exit plan: {exit_plan}"
        )
        if note:
            msg += f"\nNote: {note}"

        sent = send_telegram(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            msg,
            message_thread_id=thread_id,
        )
        logger.info("[scheduler] SPY V4 summary sent=%s setup=%s side=%s", sent, setup, side)
    except Exception as e:
        logger.error(f"[scheduler] SPY V4 summary failed: {e}")


def sweep_digest_job():
    """Post-market: send V4/V3 sweep reclaim/reject setups from saved scans."""
    if not _SWEEP_DIGEST_ENABLED:
        logger.info("[scheduler] sweep digest skipped: SWEEP_DIGEST_ENABLED=0")
        return

    import html

    try:
        from backend.services.scanner_snapshot import (
            configured_watchlists,
            load_snapshot,
            refresh_snapshots,
        )

        watchlists = configured_watchlists()
        snapshots = [load_snapshot(w) for w in watchlists]
        if not any(s.get("available") and s.get("results") for s in snapshots):
            refreshed = refresh_snapshots(watchlists)
            snapshots = refreshed.get("watchlists", [])

        seen: set[str] = set()
        longs: list[dict] = []
        shorts: list[dict] = []

        for snap in snapshots:
            for row in snap.get("results") or []:
                ticker = str(row.get("ticker") or "").upper()
                if not ticker or ticker in seen or row.get("error"):
                    continue
                seen.add(ticker)

                dt4_setup = row.get("dt4_setup")
                dt3_setup = row.get("dt3_setup")
                dt3_side = row.get("dt3_side")

                is_long = dt4_setup == "sweep_reclaim_long" or (
                    dt3_setup == "sweep_reclaim" and dt3_side == "long"
                )
                is_short = dt4_setup == "sweep_reject_short" or (
                    dt3_setup == "sweep_reclaim" and dt3_side == "short"
                )
                if not is_long and not is_short:
                    continue

                rec = {
                    "ticker": ticker,
                    "price": row.get("price"),
                    "sector": row.get("sector"),
                    "verdict": row.get("verdict"),
                    "setup": dt4_setup or dt3_setup,
                    "grade": row.get("dt4_grade") or row.get("dt3_grade"),
                    "level": row.get("dt4_level") or row.get("dt3_level"),
                    "entry": row.get("dt4_entry") or row.get("dt3_entry"),
                    "stop": row.get("dt4_stop") or row.get("dt3_stop"),
                    "t1": row.get("dt4_t1") or row.get("dt3_t1"),
                    "rr": row.get("dt4_rr") or row.get("dt3_rr"),
                    "trigger": row.get("dt4_trigger") or row.get("dt3_rationale"),
                }
                (longs if is_long else shorts).append(rec)

        def _money(value) -> str:
            return f"${float(value):.2f}" if isinstance(value, (int, float)) else "—"

        def _line(row: dict) -> str:
            parts = [
                f"<b>{html.escape(row['ticker'])}</b>",
                _money(row.get("price")),
                html.escape(str(row.get("grade") or "")),
                html.escape(str(row.get("level") or "")),
            ]
            head = " ".join(p for p in parts if p and p != "—")
            setup = html.escape(str(row.get("setup") or "").replace("_", " "))
            risk = (
                f"watch {_money(row.get('entry'))} / stop {_money(row.get('stop'))} / "
                f"T1 {_money(row.get('t1'))}"
            )
            rr = row.get("rr")
            if isinstance(rr, (int, float)):
                risk += f" / R:R {rr:.2f}x"
            trigger = html.escape(str(row.get("trigger") or ""))[:180]
            return f"{head}\n   {setup} — {risk}\n   {trigger}"

        def _block(title: str, rows: list[dict]) -> str:
            if not rows:
                return f"<b>{title} (0)</b>\n—"
            rows.sort(key=lambda r: (-(r.get("rr") or 0), str(r.get("ticker") or "")))
            return f"<b>{title} ({len(rows)})</b>\n" + "\n\n".join(_line(r) for r in rows[:12])

        msg = (
            f"🎯 <b>Post-Market Sweep Setups — {today_str()}</b>\n"
            f"Watchlists: {html.escape(', '.join(watchlists))}\n\n"
            f"{_block('Sweep Reclaim Long', longs)}\n\n"
            f"{_block('Sweep Reclaim Short', shorts)}"
        )
        chat_id, thread_id = _telegram_target(TELEGRAM_SWING_MESSAGE_THREAD_ID)
        send_telegram(
            TELEGRAM_BOT_TOKEN,
            chat_id,
            msg,
            message_thread_id=thread_id,
        )
        logger.info(
            "[scheduler] sweep digest sent: %s long / %s short from %s tickers",
            len(longs),
            len(shorts),
            len(seen),
        )
    except Exception as e:
        logger.error(f"[scheduler] sweep digest failed: {e}")


def _backend_v3_in_trade_window(now_et: datetime | None = None) -> bool:
    from datetime import datetime
    now_et = now_et or datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return False
    minutes = now_et.hour * 60 + now_et.minute
    morning_start = 9 * 60 + 50
    morning_end = 11 * 60
    afternoon_start = 13 * 60 + 30
    afternoon_end = 15 * 60 + 30
    return (
        morning_start <= minutes <= morning_end
        or afternoon_start <= minutes <= afternoon_end
    )


def _backend_v3_tickers() -> dict[str, list[str]]:
    from backend.services.scanner import WATCHLISTS
    by_ticker: dict[str, list[str]] = {}

    def add(source: str, tickers: list[str]) -> None:
        label = source.strip().lower()
        for raw in tickers:
            ticker = str(raw or "").strip().upper()
            if not ticker:
                continue
            by_ticker.setdefault(ticker, [])
            if label not in by_ticker[ticker]:
                by_ticker[ticker].append(label)

    for watchlist in _BACKEND_V3_REFRESH_WATCHLISTS:
        try:
            add(watchlist, list(WATCHLISTS.get(watchlist, [])))
        except Exception as exc:
            logger.warning("[scheduler] backend v3 %s watchlist failed: %s", watchlist, str(exc)[:120])
    return by_ticker


def backend_v3_refresh_job(force: bool = False):
    """Server-side V3 scan so Telegram alerts do not depend on the UI being open."""
    if not _BACKEND_V3_REFRESH_ENABLED and not force:
        logger.info("[scheduler] backend v3 refresh skipped: BACKEND_V3_REFRESH_ENABLED=0")
        return
    if not force and not _backend_v3_in_trade_window():
        logger.debug("[scheduler] backend v3 refresh skipped: outside V3 trade window")
        return

    import html
    from concurrent.futures import ThreadPoolExecutor, as_completed

    try:
        from day_trading.v3 import analyze as _v3_analyze

        by_ticker = _backend_v3_tickers()
        tickers = list(by_ticker)
        if not tickers:
            logger.info("[scheduler] backend v3 refresh skipped: no tickers from %s", _BACKEND_V3_REFRESH_WATCHLISTS)
            return

        def _one(ticker: str) -> tuple[str, dict]:
            try:
                result = _v3_analyze(ticker)
                sig = result.get("signal") or {}
                lvl = result.get("levels") or {}
                targets = sig.get("targets") or []
                setup = sig.get("setup") or ("no_setup" if lvl else "error")
                return ticker, {
                    "dt3_setup": setup,
                    "dt3_side": sig.get("side"),
                    "dt3_grade": sig.get("grade"),
                    "dt3_level": sig.get("level"),
                    "dt3_level_val": sig.get("level_val"),
                    "dt3_entry": sig.get("entry"),
                    "dt3_stop": sig.get("stop"),
                    "dt3_t1": targets[0] if len(targets) >= 1 else None,
                    "dt3_t2": targets[1] if len(targets) >= 2 else None,
                    "dt3_rr": sig.get("rr"),
                    "dt3_rationale": sig.get("rationale") or result.get("error"),
                    "dt3_as_of": result.get("as_of"),
                }
            except Exception as exc:
                return ticker, {
                    "dt3_setup": "error",
                    "dt3_rationale": f"{type(exc).__name__}: {str(exc)[:120]}",
                }

        def _money(value) -> str:
            return f"${float(value):.2f}" if isinstance(value, (int, float)) else "—"

        def _rr(value) -> str:
            return f"{float(value):.2f}x" if isinstance(value, (int, float)) else "—"

        scanned = 0
        sent = 0
        workers = max(1, min(len(tickers), _BACKEND_V3_REFRESH_MAX_WORKERS))
        chat_id, thread_id = _telegram_target(_BACKEND_V3_MESSAGE_THREAD_ID or TELEGRAM_SWING_MESSAGE_THREAD_ID)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_one, ticker): ticker for ticker in tickers}
            for fut in as_completed(futures):
                ticker, row = fut.result()
                scanned += 1
                setup = str(row.get("dt3_setup") or "")
                side = str(row.get("dt3_side") or "")
                if setup not in {"sweep_reclaim", "break_retest"} or side not in {"long", "short"}:
                    continue

                state_key = f"backend_v3:{ticker}:{setup}:{side}"
                if _daily_job_sent(state_key):
                    continue

                sources = ", ".join(s.upper() for s in by_ticker.get(ticker, []))
                title = f"<b>V3 Backend Alert — {html.escape(ticker)}</b>"
                msg = (
                    f"{title}\n"
                    f"<i>{html.escape(sources or 'WATCHLIST')} | {html.escape(setup.replace('_', '+'))} | {html.escape(side)}</i>\n"
                    f"Grade {html.escape(str(row.get('dt3_grade') or '—'))} | "
                    f"Level {html.escape(str(row.get('dt3_level') or '—'))} {_money(row.get('dt3_level_val'))}\n"
                    f"Entry {_money(row.get('dt3_entry'))} / Stop {_money(row.get('dt3_stop'))}\n"
                    f"T1 {_money(row.get('dt3_t1'))} / T2 {_money(row.get('dt3_t2'))} / R:R {_rr(row.get('dt3_rr'))}"
                )
                rationale = html.escape(str(row.get("dt3_rationale") or "")[:220])
                if rationale:
                    msg += f"\n\n<i>{rationale}</i>"
                if row.get("dt3_as_of"):
                    msg += f"\nAs of: {html.escape(str(row.get('dt3_as_of')))}"

                if send_telegram(TELEGRAM_BOT_TOKEN, chat_id, msg, message_thread_id=thread_id):
                    _mark_daily_job_sent(state_key)
                    sent += 1

        logger.info(
            "[scheduler] backend v3 refresh completed: scanned=%s sent=%s watchlists=%s force=%s",
            scanned,
            sent,
            ",".join(_BACKEND_V3_REFRESH_WATCHLISTS),
            force,
        )
    except Exception as exc:
        logger.error(f"[scheduler] backend v3 refresh failed: {exc}")


# ── Scheduler setup ───────────────────────────────────────────────────────────

def setup_scheduler():
    """
    Register all cron jobs and initialise the database.
    Call once at FastAPI startup.
    """
    init_db()
    init_watchlist()  # creates watchlist table if not exists

    scheduler.add_job(
        default50_premarket_scan_job,
        CronTrigger(hour=8, minute=0, day_of_week="mon-fri", timezone=CST),
        id="default50_premarket_scan",
        replace_existing=True,
        misfire_grace_time=600,
    )

    scheduler.add_job(
        default50_near_entry_alert_job,
        CronTrigger(hour=8, minute=30, second=15, day_of_week="mon-fri", timezone=CST),
        id="default50_near_entry_alert",
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        paper_exit_monitor_job,
        CronTrigger(hour="8-15", minute="*/5", day_of_week="mon-fri", timezone=CST),
        id="paper_exit_monitor",
        replace_existing=True,
        misfire_grace_time=120,
    )

    scheduler.add_job(
        pre_earnings_job,
        CronTrigger(hour=8, minute=30, timezone=CST),
        id="pre_earnings",
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        momentum_scan_job,
        CronTrigger(hour=8, minute=45, timezone=CST),
        id="momentum_scan",
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        start_eps_polling,
        CronTrigger(hour=15, minute=0, timezone=CST),
        id="start_polling",
        replace_existing=True,
    )

    scheduler.add_job(
        stop_eps_polling,
        CronTrigger(hour=18, minute=0, timezone=CST),
        id="stop_polling",
        replace_existing=True,
    )

    scheduler.add_job(
        tos_email_poll_job,
        CronTrigger(hour=19, minute=15, timezone=CST),
        id="tos_email_poll",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        triad_best_picks_alert_job,
        CronTrigger(hour=19, minute=30, day_of_week="mon-fri", timezone=CST),
        id="triad_best_picks_alert",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # 4:15 PM CST (Mon-Fri) — EOD Market Close Exceptional Scans (Default + Momentum + Gmail TOS)
    scheduler.add_job(
        eod_exceptional_scan_job,
        CronTrigger(hour=16, minute=15, day_of_week="mon-fri", timezone=CST),
        id="eod_exceptional_scan",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # 7:45 PM CST (Mon-Fri) — Evening Post-TOS Exceptional Scans
    scheduler.add_job(
        eod_exceptional_scan_job,
        CronTrigger(hour=19, minute=45, day_of_week="mon-fri", timezone=CST),
        id="eod_exceptional_scan_evening",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # 9:00 AM CST (Mon-Fri) — Morning Market Open Breakouts Alert (Default 50 + Momentum 50)
    scheduler.add_job(
        breakout_digest_job,
        CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone=CST),
        id="breakout_alert_morning",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # 3:30 PM CST (Mon-Fri) — Post-Market Close Breakout Alert (Default 50 + Momentum 50)
    scheduler.add_job(
        breakout_digest_job,
        CronTrigger(hour=15, minute=30, day_of_week="mon-fri", timezone=CST),
        id="breakout_alert_post_market",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Macro regime watcher — poll every 5m during market hours, alert on verdict/gamma transitions
    if _MACRO_ALERTS_ENABLED:
        scheduler.add_job(
            macro_regime_watch_job,
            CronTrigger(day_of_week="mon-fri", hour="8-15", minute="*/5", timezone=CST),
            id="macro_regime_watch",
            replace_existing=True,
            misfire_grace_time=300,
        )

    # Gamma refreshes twice on trading days: after open and after close.
    # SPY runs a few minutes before sectors so provider calls are staggered.
    scheduler.add_job(
        sector_gamma_job,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=40, timezone=CST),
        id="sector_gamma_open",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        sector_gamma_job,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=10, timezone=CST),
        id="sector_gamma_close",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        spy_gamma_job,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=37, timezone=CST),
        id="spy_gamma_open",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        spy_gamma_job,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=7, timezone=CST),
        id="spy_gamma_close",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # SPY Day Trading V4 plan summaries: morning open plan & post-market review
    if _SPY_V4_SUMMARY_ENABLED:
        scheduler.add_job(
            spy_v4_summary_job,
            CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=CST),
            id="spy_v4_summary_morning",
            replace_existing=True,
            misfire_grace_time=600,
        )
        scheduler.add_job(
            spy_v4_summary_job,
            CronTrigger(day_of_week="mon-fri", hour=15, minute=40, timezone=CST),
            id="spy_v4_summary_post_market",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    # Post-market V4/V3 sweep setups digest
    if _SWEEP_DIGEST_ENABLED:
        scheduler.add_job(
            sweep_digest_job,
            CronTrigger(day_of_week="mon-fri", hour=15, minute=40, timezone=CST),
            id="sweep_digest_close",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    # Intraday V3 backend refresh during trade windows
    if _BACKEND_V3_REFRESH_ENABLED:
        scheduler.add_job(
            backend_v3_refresh_job,
            CronTrigger(day_of_week="mon-fri", hour="8-10,12-14", minute="*/5", timezone=CST),
            id="backend_v3_refresh",
            replace_existing=True,
            misfire_grace_time=300,
            max_instances=1,
        )

    _macro_w = "macro_regime_watch every 5m Mon-Fri 8-15:55CST" if _MACRO_ALERTS_ENABLED else "macro_regime_watch DISABLED"
    _spy_v4_w = "spy_v4_summary@8:00&15:40CST" if _SPY_V4_SUMMARY_ENABLED else "spy_v4_summary DISABLED"
    logger.info(
        f"[scheduler] registered: default50_scan@8:00CST, {_spy_v4_w}, "
        f"default50_near_entry@8:30CST, paper_exit_monitor@*/5m, "
        f"pre_earnings@8:30CST, momentum@8:45CST, breakout_morning@9:00CST, "
        f"breakout_post_market@15:30CST, polling 15:00–18:00 CST, "
        f"eod_exceptional_scan@16:15CST, tos_email_poll@19:15CST, "
        f"triad_best_picks@19:30CST, eod_exceptional_evening@19:45CST, "
        f"spy_gamma + sector_gamma after open and close, {_macro_w}"
    )
