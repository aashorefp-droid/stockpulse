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
