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
    """Manually trigger the post-market exceptional/V3 scanner digest."""
    from backend.services.scheduler import breakout_digest_job
    breakout_digest_job()
    return {"status": "exceptional/V3 digest completed"}


@router.post("/run-exceptional")
def trigger_exceptional_swing_digest():
    """Manually trigger the post-market exceptional/V3 scanner digest."""
    from backend.services.scheduler import exceptional_swing_digest_job
    exceptional_swing_digest_job()
    return {"status": "exceptional/V3 digest completed"}


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

