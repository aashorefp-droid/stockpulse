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
from datetime import date, datetime
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
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  # type: ignore
except ImportError:
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Scheduler instance ────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler(timezone="America/Chicago")


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


def news_summary_job():
    """8:00 AM CST — scan every watchlist, Telegram a Good/Bad news digest."""
    from backend.services.scanner import (
        scan_single, WATCHLISTS, get_telegram_watchlist, ETF_SECTORS,
    )
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Known ETF universe: explicit ETF watchlist + sector/index ETF map.
    etf_set = set(WATCHLISTS.get("etfs", [])) | set(ETF_SECTORS.keys())

    # Union of every static watchlist + the TOS/Gmail watchlist, deduped.
    tickers: list[str] = []
    seen: set = set()
    for lst in list(WATCHLISTS.values()) + [get_telegram_watchlist()]:
        for t in lst:
            if t not in seen:
                seen.add(t)
                tickers.append(t)

    logger.info(f"[scheduler] news summary: scanning {len(tickers)} unique tickers")

    import html

    good: list[dict] = []
    bad:  list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(scan_single, t): t for t in tickers}
        for fut in as_completed(futures):
            try:
                r = fut.result()
            except Exception:
                continue
            if r.get("error"):
                continue
            label = r.get("news")
            if label not in ("Good", "Bad"):
                continue
            g = r.get("news_good", 0) or 0
            b = r.get("news_bad", 0) or 0
            rec = {
                "ticker":  r["ticker"],
                "price":   r.get("price"),
                "verdict": r.get("verdict", "—"),
                "g": g, "b": b, "net": g - b,
                "is_etf":  r["ticker"].upper() in etf_set,
                "headlines": r.get("news_headlines") or [],
            }
            (good if label == "Good" else bad).append(rec)

    good.sort(key=lambda x: -x["net"])   # most net-positive first
    bad.sort(key=lambda x:  x["net"])    # most net-negative first

    def _detail(rows: list[dict], n: int = 3) -> str:
        if not rows:
            return "—"
        blocks = []
        for x in rows[:n]:
            net = x["net"]
            px = f"${x['price']:.2f}" if isinstance(x.get("price"), (int, float)) else ""
            head = (
                f"<b>{html.escape(x['ticker'])}</b> {px} · "
                f"{html.escape(str(x['verdict']))} · "
                f"+{x['g']}/-{x['b']} (Σ{'+' if net >= 0 else ''}{net})"
            )
            hls = []
            for hd in x["headlines"][:3]:
                ic = "🟢" if hd.get("s") == "Good" else "🔴" if hd.get("s") == "Bad" else "⚪"
                src = f" — {html.escape(hd['src'])}" if hd.get("src") else ""
                hls.append(f"   {ic} {html.escape(hd.get('h', ''))}{src}")
            blocks.append(head + ("\n" + "\n".join(hls) if hls else ""))
        return "\n\n".join(blocks)

    def _split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
        return ([x for x in rows if not x["is_etf"]],
                [x for x in rows if x["is_etf"]])

    g_stk, g_etf = _split(good)
    b_stk, b_etf = _split(bad)

    msg = (
        f"📰 <b>News Digest — {today_str()}</b>\n"
        f"Scanned {len(tickers)} tickers · top 3 each\n\n"
        f"🟢 <b>Good — Stocks ({len(g_stk)})</b>\n{_detail(g_stk)}\n\n"
        f"🟢 <b>Good — ETFs ({len(g_etf)})</b>\n{_detail(g_etf)}\n\n"
        f"🔴 <b>Bad — Stocks ({len(b_stk)})</b>\n{_detail(b_stk)}\n\n"
        f"🔴 <b>Bad — ETFs ({len(b_etf)})</b>\n{_detail(b_etf)}"
    )
    send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)
    logger.info(
        f"[scheduler] news summary sent: {len(good)} good / {len(bad)} bad "
        f"(top 3 each with details)"
    )


def telegram_watchlist_job():
    """7:15 PM CST — pull the day's tickers from the TOS scan email (Gmail)."""
    try:
        from backend.services.gmail_watchlist import (
            poll_and_store, fetch_today_watchlist,
        )
        n = poll_and_store()
        tickers = fetch_today_watchlist()
        logger.info(
            f"[scheduler] TOS/Gmail watchlist pull: {n} new email(s), "
            f"{len(tickers)} ticker(s) active"
        )
    except Exception as e:
        logger.error(f"[scheduler] TOS/Gmail watchlist pull failed: {e}")


def sector_gamma_job():
    """Every 4h on trading days — recompute all sector GEX so the per-sector
    daily streak is recorded even when no one has the UI open."""
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
    """Every 4h on trading days — recompute SPY GEX so the daily sign is
    recorded (and the consecutive-day streak advances) even when nobody has
    the UI open. Without this, SPY's streak pins at ±1 because its sign is
    only written opportunistically on UI hits (sectors already have a job)."""
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


def stop_eps_polling():
    """6:00 PM CST — remove the polling job."""
    job = scheduler.get_job("eps_poll")
    if job:
        job.remove()
        send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                      f"🛑 EPS polling stopped for {today_str()}")
        logger.info("[scheduler] EPS polling stopped")


# ── Scheduler setup ───────────────────────────────────────────────────────────

def setup_scheduler():
    """
    Register all cron jobs and initialise the database.
    Call once at FastAPI startup.
    """
    init_db()
    init_watchlist()  # creates watchlist table if not exists

    scheduler.add_job(
        pre_earnings_job,
        CronTrigger(hour=8, minute=30, timezone=CST),
        id="pre_earnings",
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        news_summary_job,
        CronTrigger(hour=8, minute=0, timezone=CST),
        id="news_summary",
        replace_existing=True,
        misfire_grace_time=600,
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
        telegram_watchlist_job,
        CronTrigger(hour=19, minute=15, timezone=CST),  # 7:15 PM CST
        id="telegram_watchlist",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Sector GEX every 4h on trading days (Mon–Fri) so each sector's daily
    # streak is recorded regardless of UI usage. Fires 00:05/04:05/08:05/
    # 12:05/16:05/20:05 CST — at least one per 4h every trading day.
    scheduler.add_job(
        sector_gamma_job,
        CronTrigger(day_of_week="mon-fri", hour="*/4", minute=5, timezone=CST),
        id="sector_gamma",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # SPY GEX on the same cadence so its daily sign is recorded regardless of
    # UI usage. minute=2 (before sectors at :05) keeps the SPY write first.
    scheduler.add_job(
        spy_gamma_job,
        CronTrigger(day_of_week="mon-fri", hour="*/4", minute=2, timezone=CST),
        id="spy_gamma",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Prime the store at startup so the watchlist isn't empty before the
    # first 7:15 PM CST pull.
    try:
        telegram_watchlist_job()
    except Exception as e:
        logger.warning(f"[scheduler] startup TOS/Gmail prime failed: {e}")

    logger.info(
        "[scheduler] registered: news_summary@8:00CST, pre_earnings@8:30CST, "
        "momentum@8:45CST, polling 15:00–18:00 CST, tos_gmail_watchlist@19:15CST, "
        "spy_gamma + sector_gamma every 4h Mon–Fri"
    )
