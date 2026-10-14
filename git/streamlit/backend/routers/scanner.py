"""
GET /api/scanner/stream?watchlist=default
Server-Sent Events — yields one JSON result per ticker as it completes.
Final message: {"done": true, "total": N}
"""
import json
import asyncio
import math
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from fastapi import APIRouter, Query, UploadFile, File, Body
from fastapi.responses import StreamingResponse

from backend.services.scanner import WATCHLISTS, scan_single, get_short_squeeze_tickers
from backend.services.best_pick import analyze_and_rank_stocks, dispatch_triad_telegram_alert
from backend.services.curl_scanner import scan_30w_curl_candidates, dispatch_30w_curl_telegram
from backend.services.gmail_watchlist import (
    poll_and_store, fetch_today_watchlist, get_watchlist_status
)

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


@router.get("/watchlists")
def get_watchlists(subjects: str = Query(""), days: int = Query(1)):
    res = {k: len(v) for k, v in WATCHLISTS.items()}
    try:
        res["tos_email"] = len(fetch_today_watchlist(subjects=subjects, days=days))
    except Exception:
        res["tos_email"] = 0
    return res


@router.get("/tos-email/status")
def tos_email_status(subjects: str = Query(""), days: int = Query(1)):
    """Return status, scan categories, and cached tickers from ThinkOrSwim Gmail alerts."""
    try:
        return get_watchlist_status(subjects=subjects, days=days)
    except Exception as e:
        return {"status": "error", "error": str(e), "count": 0, "tickers": []}


@router.post("/tos-email/refresh")
def tos_email_refresh(subjects: str = Query(""), days: int = Query(1)):
    """Force poll Gmail IMAP for new TOS scan emails and return updated tickers."""
    try:
        new_count = poll_and_store()
        tickers = fetch_today_watchlist(subjects=subjects, days=days)
        status = get_watchlist_status(subjects=subjects, days=days)
        return {
            "status": "ok",
            "new_emails": new_count,
            "count": len(tickers),
            "tickers": tickers,
            "days": days,
            "filter": subjects,
            "available_scans": status.get("available_scans", []),
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "count": 0, "tickers": []}


@router.post("/v3-refresh")
def v3_refresh(payload: dict = Body(...)):
    """Re-evaluate ONLY the V3 day-trading engine for a list of tickers.

    Designed to be polled every ~5 min during V3 trade windows
    (09:50–11:00 / 13:30–15:30 ET) so Day Trading setups stay live
    without re-running the full scan_single pipeline.

    Request:  {"tickers": ["MU", "SPY", ...], "notify": {"SPY": "Rank 1"}}
    Response: {"results": {"MU": {dt3_setup, ...}}, "count": <int>}
    """
    raw = payload.get("tickers") or []
    tickers = [t.strip().upper() for t in raw
               if isinstance(t, str) and t.strip()]
    tickers = list(dict.fromkeys(tickers))[:200]
    if not tickers:
        return {"results": {}, "count": 0}

    notify_in = payload.get("notify") or {}
    notify: dict[str, str] = {}
    if isinstance(notify_in, dict):
        for k, v in notify_in.items():
            if isinstance(k, str) and isinstance(v, str):
                notify[k.strip().upper()] = v.strip()
    elif isinstance(notify_in, list):
        for k in notify_in:
            if isinstance(k, str):
                notify[k.strip().upper()] = "Rank 1"

    from concurrent.futures import as_completed
    from day_trading.v3 import analyze as _v3_analyze
    from backend.services.scanner import _v3_alert_once

    def _one(tk: str) -> tuple[str, dict]:
        try:
            r = _v3_analyze(tk)
            sig = r.get("signal") or {}
            lvl = r.get("levels") or {}
            tgts = sig.get("targets") or []
            setup = sig.get("setup")
            if not setup:
                setup = "no_setup" if lvl else "error"
            return tk, {
                "dt3_setup":     setup,
                "dt3_side":      sig.get("side"),
                "dt3_grade":     sig.get("grade"),
                "dt3_level":     sig.get("level"),
                "dt3_level_val": sig.get("level_val"),
                "dt3_entry":     sig.get("entry"),
                "dt3_stop":      sig.get("stop"),
                "dt3_t1":        tgts[0] if len(tgts) >= 1 else None,
                "dt3_t2":        tgts[1] if len(tgts) >= 2 else None,
                "dt3_rr":        sig.get("rr"),
                "dt3_rationale": sig.get("rationale") or r.get("error"),
                "dt3_pdh":       lvl.get("pdh"),
                "dt3_pdl":       lvl.get("pdl"),
                "dt3_pwh":       lvl.get("pwh"),
                "dt3_pwl":       lvl.get("pwl"),
                "dt3_as_of":     r.get("as_of"),
            }
        except Exception as e:
            return tk, {
                "dt3_setup":     "error",
                "dt3_rationale": f"{type(e).__name__}: {str(e)[:120]}",
            }

    out: dict = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_one, tk): tk for tk in tickers}
        for fut in as_completed(futures):
            try:
                tk, data = fut.result(timeout=8.0)
                out[tk] = data
                tier = notify.get(tk)
                if tier:
                    _v3_alert_once(tk, data, tier=tier)
            except Exception:
                continue

    return {"results": out, "count": len(out), "notify_count": len(notify)}



@router.get("/stage2-curl")
def get_stage2_curl_stocks(
    watchlist: str = Query("default"),
    tickers: str = Query(""),
    fresh_only: bool = Query(False),
    subjects: str = Query(""),
    days: int = Query(1),
):
    """Return stocks meeting 30W MA curl up and volume surge."""
    if tickers:
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    elif watchlist in ("tos_email", "tos", "gmail", "telegram"):
        ticker_list = fetch_today_watchlist(subjects=subjects, days=days)
    else:
        ticker_list = WATCHLISTS.get(watchlist, WATCHLISTS["default"])

    matches = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(scan_single, t): t for t in ticker_list}
        for future in as_completed(futures):
            try:
                res = future.result()
                if res and not res.get("error"):
                    if fresh_only:
                        if res.get("is_fresh_stage2") or (res.get("stage2_status") == "FRESH"):
                            matches.append(res)
                    else:
                        if res.get("stage2_curl_surge") or res.get("is_30w_curl"):
                            matches.append(res)
            except Exception:
                pass

    if fresh_only:
        matches.sort(key=lambda x: (x.get("weeks_curling", 99), abs(x.get("dist_from_sma30") or 0)))
    else:
        matches.sort(key=lambda x: x.get("sma30_slope", 0), reverse=True)

    return {
        "count": len(matches),
        "total_scanned": len(ticker_list),
        "items": matches,
    }


@router.get("/stream")
async def stream_scan(
    watchlist: str = Query("default"),
    tickers:   str = Query(""),          # comma-separated custom list
    as_of:     Optional[str] = Query(None),  # backtest date YYYY-MM-DD
    subjects:  str = Query(""),          # comma-separated TOS scan/subject filter
    days:      int = Query(1),           # number of days back (default: 1 = same day)
):
    loop = asyncio.get_event_loop()
    if tickers:
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    elif watchlist in ("tos_email", "tos", "gmail", "telegram"):
        ticker_list = await loop.run_in_executor(None, fetch_today_watchlist, subjects, days)
    elif watchlist == "short_squeeze":
        ticker_list = await loop.run_in_executor(None, get_short_squeeze_tickers)
    else:
        ticker_list = WATCHLISTS.get(watchlist, WATCHLISTS["default"])
    tickers = ticker_list

    async def generate():
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        async def _scan_all():
            futures = [loop.run_in_executor(None, scan_single, t, as_of) for t in tickers]
            for coro in asyncio.as_completed(futures):
                result = await coro
                await queue.put(result)
            await queue.put(None)  # sentinel — done

        asyncio.create_task(_scan_all())

        count = 0
        while True:
            result = await queue.get()
            if result is None:
                break
            count += 1
            yield f"data: {json.dumps(_clean_nans(result))}\n\n"

        yield f"data: {json.dumps({'done': True, 'total': count})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _clean_nans(obj):
    if isinstance(obj, dict):
        return {k: _clean_nans(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_clean_nans(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


@router.post("/rank")
def rank_scan_results(payload: Dict[str, Any] = Body(...)):
    """
    Ranks stocks based on the user's swing trade algorithm.
    Payload can contain:
      - "items": list of scan result dictionaries, OR
      - "csv_text": raw CSV string
    """
    try:
        if "csv_text" in payload and payload["csv_text"]:
            return _clean_nans(analyze_and_rank_stocks(payload["csv_text"]))

        items = payload.get("items") or payload.get("rows") or []
        if not items:
            return {
                "best_pick": None,
                "best_picks": {"strength": None, "emerging": None, "weakness": None},
                "ranked": [],
                "total_scanned": 0,
                "strict_passed_count": 0,
                "is_strict": False,
            }

        # Filter out items that are only errors without ticker or price
        valid_items = [it for it in items if not it.get("error") and it.get("ticker")]
        if not valid_items:
            valid_items = items

        df = pd.DataFrame(valid_items)
        return _clean_nans(analyze_and_rank_stocks(df))
    except Exception as e:
        return {
            "error": str(e),
            "best_pick": None,
            "best_picks": {"strength": None, "emerging": None, "weakness": None},
            "ranked": [],
            "total_scanned": len(payload.get("items", [])),
            "strict_passed_count": 0,
            "is_strict": False,
        }


@router.post("/rank-csv")
async def rank_uploaded_csv(file: UploadFile = File(...)):
    """
    Accepts an uploaded CSV file, runs the swing trade ranking algorithm,
    and returns the best pick + ranked table.
    """
    try:
        content = await file.read()
        csv_text = content.decode("utf-8", errors="replace")
        return _clean_nans(analyze_and_rank_stocks(csv_text))
    except Exception as e:
        return {
            "error": str(e),
            "best_pick": None,
            "best_picks": {"strength": None, "emerging": None, "weakness": None},
            "ranked": [],
            "total_scanned": 0,
            "strict_passed_count": 0,
            "is_strict": False,
        }


@router.post("/triad-alert")
def send_triad_alert(
    payload: Dict[str, Any] = Body(default={}),
    watchlist: str = Query("tos_email"),
    subjects: str = Query(""),
    days: int = Query(1),
    send_telegram: bool = Query(True),
):
    """
    Evaluates scan results and dispatches the 3-Category Swing Best Picks Alert (Strength, Emerging, Weakness)
    to Telegram.
    Can be invoked with:
      - Empty body: automatically scans watchlist (TOS email alerts or default)
      - Body with `items`: evaluates already scanned items without re-fetching
    """
    try:
        items = payload.get("items") or payload.get("rows")
        wl = payload.get("watchlist") or watchlist
        subs = payload.get("subjects") if payload.get("subjects") is not None else subjects
        d = payload.get("days") if payload.get("days") is not None else days
        send_msg = payload.get("send_telegram") if payload.get("send_telegram") is not None else send_telegram

        curls = payload.get("curl_matches") or payload.get("curls")
        res = dispatch_triad_telegram_alert(
            items_or_df=items,
            watchlist=wl,
            subjects=subs,
            days=d,
            send_msg=send_msg,
            curl_matches=curls,
        )
        return _clean_nans(res)
    except Exception as e:
        return {"ok": False, "error": str(e), "sent": False}


@router.get("/30w-curl")
def get_30w_curl_candidates(
    watchlist: str = Query("tos_email"),
    days: int = Query(1),
    subjects: str = Query(""),
    max_weeks_ago: int = Query(8),
    tickers: Optional[str] = Query(None),
):
    """
    Scans candidates for 30-Week MA Curl Up signals matching TradingView (30W Curl Arrows) indicator.
    """
    try:
        t_list = [t.strip().upper() for t in tickers.split(",") if t.strip()] if tickers else None
        res = scan_30w_curl_candidates(
            tickers=t_list,
            watchlist=watchlist,
            days=days,
            subjects=subjects,
            max_weeks_ago=max_weeks_ago,
        )
        return _clean_nans(res)
    except Exception as e:
        return {"ok": False, "error": str(e), "matches": [], "total_scanned": 0}


@router.post("/30w-curl/alert")
def send_30w_curl_alert(
    payload: Dict[str, Any] = Body(default={}),
    watchlist: str = Query("tos_email"),
    subjects: str = Query(""),
    days: int = Query(1),
    max_weeks_ago: int = Query(8),
    send_telegram: bool = Query(True),
):
    """
    Scans candidates for 30-Week MA Curl Up signals and dispatches the formatted
    TradingView Curl list to Telegram.
    """
    try:
        raw_tickers = payload.get("tickers")
        t_list = None
        if raw_tickers:
            if isinstance(raw_tickers, list):
                t_list = [str(t).strip().upper() for t in raw_tickers if str(t).strip()]
            elif isinstance(raw_tickers, str):
                t_list = [t.strip().upper() for t in raw_tickers.split(",") if t.strip()]

        wl = payload.get("watchlist") or watchlist
        subs = payload.get("subjects") if payload.get("subjects") is not None else subjects
        d = payload.get("days") if payload.get("days") is not None else days
        mw = payload.get("max_weeks_ago") if payload.get("max_weeks_ago") is not None else max_weeks_ago
        send_msg = payload.get("send_telegram") if payload.get("send_telegram") is not None else send_telegram

        res = dispatch_30w_curl_telegram(
            tickers=t_list,
            watchlist=wl,
            days=d,
            subjects=subs,
            max_weeks_ago=mw,
            send_msg=send_msg,
        )
        return _clean_nans(res)
    except Exception as e:
        return {"ok": False, "error": str(e), "sent": False}


@router.post("/exceptional-alert")
def send_exceptional_alert(
    payload: Dict[str, Any] = Body(default={}),
    send_telegram: bool = Query(True),
    days_gmail: int = Query(2),
):
    """
    Executes the End-of-Day Multi-Timeframe Strategy Scan across Default 50 + Momentum + Gmail TOS alerts
    and dispatches high-conviction Exceptional tickers (Grade S/A, MTF Rank 1, 30W Curls) to Telegram.
    """
    try:
        from backend.services.exceptional_scanner import dispatch_exceptional_telegram_alert
        raw_tickers = payload.get("tickers")
        t_list = None
        if raw_tickers:
            if isinstance(raw_tickers, list):
                t_list = [str(t).strip().upper() for t in raw_tickers if str(t).strip()]
            elif isinstance(raw_tickers, str):
                t_list = [t.strip().upper() for t in raw_tickers.split(",") if t.strip()]

        send_msg = payload.get("send_telegram") if payload.get("send_telegram") is not None else send_telegram
        as_of = payload.get("as_of")

        res = dispatch_exceptional_telegram_alert(
            send_msg=send_msg,
            universe=t_list,
            as_of=as_of,
        )
        return _clean_nans(res)
    except Exception as e:
        return {"ok": False, "error": str(e), "sent": False}


@router.get("/exceptional")
def get_exceptional_scan(
    days_gmail: int = Query(2),
    as_of: Optional[str] = Query(None),
):
    """
    Executes the Multi-Timeframe Strategy Scan across Default 50 + Momentum + Gmail TOS alerts
    and returns categorized Exceptional Bullish, Exceptional Bearish, and 30W Stage 2 Curl setups.
    """
    try:
        from backend.services.exceptional_scanner import scan_exceptional_tickers, get_exceptional_scan_universe
        universe = get_exceptional_scan_universe(days_gmail=days_gmail)
        res = scan_exceptional_tickers(universe=universe, as_of=as_of)
        return _clean_nans(res)
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "exceptional_bull": [],
            "exceptional_bear": [],
            "stage2_curls": [],
            "total_scanned": 0,
        }


