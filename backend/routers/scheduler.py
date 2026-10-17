"""
GET  /api/scheduler/status          — DB rows + active jobs
POST /api/scheduler/run-pre         — trigger pre-earnings job now (for testing)
POST /api/scheduler/start-polling   — start EPS polling now
POST /api/scheduler/stop-polling    — stop EPS polling
"""
from datetime import date
from fastapi import APIRouter

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


@router.get("/status")
def scheduler_status(query_date: str = None):
    from backend.db.earnings_tracker import get_all_for_date
    from backend.services.scheduler import scheduler

    date_str = query_date or str(date.today())
    rows     = get_all_for_date(date_str)

    jobs = []
    for job in scheduler.get_jobs():
        nxt = job.next_run_time
        jobs.append({
            "id":           job.id,
            "next_run_cst": str(nxt) if nxt else None,
        })

    return {
        "date":    date_str,
        "tickers": rows,
        "jobs":    jobs,
        "polling_active": scheduler.get_job("eps_poll") is not None,
    }


@router.post("/run-pre")
async def trigger_pre_earnings():
    """Manually trigger the pre-earnings discovery + alert job."""
    from backend.services.scheduler import pre_earnings_job
    await pre_earnings_job()
    return {"status": "pre_earnings_job completed"}


@router.post("/start-polling")
def trigger_start_polling():
    from backend.services.scheduler import start_eps_polling
    start_eps_polling()
    return {"status": "polling started"}


@router.post("/stop-polling")
def trigger_stop_polling():
    from backend.services.scheduler import stop_eps_polling
    stop_eps_polling()
    return {"status": "polling stopped"}


@router.post("/run-momentum")
def trigger_momentum_scan():
    """Manually trigger the 8:45 AM momentum scan (for testing)."""
    from backend.services.scheduler import momentum_scan_job
    momentum_scan_job()
    return {"status": "momentum scan completed"}


@router.post("/run-news")
def trigger_news_summary():
    """Manually trigger the morning Good/Bad news digest."""
    from backend.services.scheduler import news_summary_job
    news_summary_job()
    return {"status": "news summary completed"}


@router.post("/run-news-post")
def trigger_post_market_news_summary():
    """Manually trigger the post-market Good/Bad news digest."""
    from backend.services.scheduler import post_market_news_summary_job
    post_market_news_summary_job()
    return {"status": "post-market news summary completed"}


@router.post("/run-sweeps")
def trigger_sweep_digest():
    """Manually trigger the post-market sweep setup digest."""
    from backend.services.scheduler import sweep_digest_job
    sweep_digest_job()
    return {"status": "sweep digest completed"}


@router.post("/run-breakouts")
def trigger_breakout_digest():
    """Manually trigger the Default 50 & Momentum 50 breakout scanner & Telegram alert."""
    from backend.services.scheduler import breakout_digest_job
    res = breakout_digest_job()
    return {"status": "breakout alert completed", "result": res}


@router.post("/run-exceptional")
@router.post("/run-exceptional-scan")
def trigger_exceptional_swing_digest():
    """Manually trigger the EOD Multi-Timeframe Exceptional Scanner (Default + Momentum + Gmail TOS) alert."""
    from backend.services.scheduler import eod_exceptional_scan_job
    res = eod_exceptional_scan_job()
    return {"status": "eod_exceptional_scan completed", "result": res}


@router.post("/run-v3-refresh")
def trigger_backend_v3_refresh():
    """Manually trigger the backend V3 refresh, outside the market-window gate."""
    from backend.services.scheduler import backend_v3_refresh_job
    backend_v3_refresh_job(force=True)
    return {"status": "backend V3 refresh completed"}


@router.post("/run-holdings")
def trigger_holdings_summary():
    """Manually trigger the post-market holdings swing summary."""
    from backend.services.scheduler import holdings_summary_job
    holdings_summary_job()
    return {"status": "holdings summary completed"}


@router.post("/run-default50-scan")
def trigger_default50_scan():
    """Manually trigger the 8:00 AM Default 50 pre-market scan."""
    from backend.services.scheduler import default50_premarket_scan_job
    res = default50_premarket_scan_job()
    return {
        "status": "default50_premarket_scan completed",
        "valid_count": res.get("valid_count", 0),
        "scanned_count": res.get("scanned_count", 0),
    }


@router.post("/run-default50-near-entry")
def trigger_default50_near_entry():
    """Manually trigger the 8:30 AM Default 50 Near Entry check & alert."""
    from backend.services.scheduler import default50_near_entry_alert_job
    res = default50_near_entry_alert_job()
    return {
        "status": "default50_near_entry_alert completed",
        "near_count": res.get("count", 0),
        "items": [
            {
                "ticker": item.get("ticker"),
                "direction": item.get("direction"),
                "current_price": item.get("current_price"),
                "entry": item.get("entry"),
                "stop_loss": item.get("stop_loss"),
                "target1": item.get("target1"),
                "target2": item.get("target2"),
                "diff_pct": item.get("diff_pct"),
                "scenario": item.get("scenario_label"),
            }
            for item in res.get("items", [])
        ],
    }


@router.post("/run-paper-exit-monitor")
def trigger_paper_exit_monitor(force: bool = False):
    """Manually trigger the intraday paper trading exit monitor."""
    from backend.services.scheduler import paper_exit_monitor_job
    res = paper_exit_monitor_job(force=force)
    return {
        "status": "paper_exit_monitor completed",
        "result": res,
    }


@router.post("/run-triad")
def trigger_triad_alert():
    """Manually trigger the 7:30 PM CST Triad Best Picks + 30W Curl after-market alert."""
    from backend.services.scheduler import triad_best_picks_alert_job
    res = triad_best_picks_alert_job()
    return {"status": "triad_best_picks_alert completed", "result": res}


@router.post("/run-tos-poll")
def trigger_tos_email_poll():
    """Manually trigger the 7:15 PM CST ThinkOrSwim email poll + 30W Curl alert."""
    from backend.services.scheduler import tos_email_poll_job
    res = tos_email_poll_job()
    return {"status": "tos_email_poll completed", "new_messages": res}


@router.post("/run-macro-watch")
def trigger_macro_regime_watch():
    """Manually trigger the macro regime watch job."""
    from backend.services.scheduler import macro_regime_watch_job
    macro_regime_watch_job()
    return {"status": "macro_regime_watch completed"}


@router.post("/run-macro-hourly")
def trigger_macro_hourly_checkin():
    """Manually trigger the hourly macro regime check-in."""
    from backend.services.scheduler import macro_hourly_checkin_job
    macro_hourly_checkin_job()
    return {"status": "macro_hourly_checkin completed"}


@router.post("/run-gamma")
def trigger_gamma_jobs():
    """Manually trigger SPY and sector gamma calculation jobs."""
    from backend.services.scheduler import spy_gamma_job, sector_gamma_job
    spy_gamma_job()
    sector_gamma_job()
    return {"status": "gamma jobs completed"}


@router.get("/v4-tickers")
def get_v4_tickers_endpoint():
    """Return active tickers configured for 8:00 AM & 8:35 AM V4 Telegram alerts."""
    from backend.services.scheduler import get_v4_alert_tickers, V4_POPULAR_OPTIONS
    return {
        "tickers": get_v4_alert_tickers(),
        "popular_options": V4_POPULAR_OPTIONS,
    }


@router.post("/v4-tickers")
def update_v4_tickers_endpoint(payload: dict):
    """Update active tickers configured for 8:00 AM & 8:35 AM V4 Telegram alerts."""
    from backend.services.scheduler import (
        set_v4_alert_tickers,
        V4_POPULAR_OPTIONS,
        spy_v4_summary_job,
        spy_v4_post_open_outcome_job,
    )
    raw_tickers = payload.get("tickers") or []
    if isinstance(raw_tickers, str):
        raw_tickers = [t.strip() for t in raw_tickers.split(",") if t.strip()]
    saved = set_v4_alert_tickers(raw_tickers)

    alert_res = None
    if payload.get("send_playbook"):
        alert_res = spy_v4_summary_job(tickers=saved)
    elif payload.get("send_post_open"):
        alert_res = spy_v4_post_open_outcome_job(tickers=saved)

    return {
        "status": "updated",
        "tickers": saved,
        "popular_options": V4_POPULAR_OPTIONS,
        "alert_result": alert_res,
    }


@router.post("/run-spy-v4")
@router.post("/run-v4-spy")
@router.post("/run-v4")
def trigger_spy_v4_summary(ticker: str = None, tickers: str = None):
    """Manually trigger the Day Trading V4 plan Telegram alert (for configured tickers or specific ticker)."""
    from backend.services.scheduler import spy_v4_summary_job
    target = None
    if ticker:
        target = [t.strip().upper() for t in ticker.split(",") if t.strip()]
    elif tickers:
        target = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    res = spy_v4_summary_job(tickers=target)
    return {"status": "v4_summary completed", "result": res}


@router.post("/run-spy-post-open")
@router.post("/run-v4-post-open")
def trigger_spy_post_open_outcome(ticker: str = None, tickers: str = None):
    """Manually trigger the 8:35 AM 5-min post-open outcome validation alert."""
    from backend.services.scheduler import spy_v4_post_open_outcome_job
    target = None
    if ticker:
        target = [t.strip().upper() for t in ticker.split(",") if t.strip()]
    elif tickers:
        target = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    res = spy_v4_post_open_outcome_job(tickers=target)
    return {"status": "v4_post_open_outcome completed", "result": res}



