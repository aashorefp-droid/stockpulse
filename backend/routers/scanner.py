"""
GET /api/scanner/stream?watchlist=default
Server-Sent Events — yields one JSON result per ticker as it completes.
Final message: {"done": true, "total": N}
"""
import json
import asyncio
from typing import Optional
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from backend.services.scanner import WATCHLISTS, scan_single, get_short_squeeze_tickers

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


from concurrent.futures import ThreadPoolExecutor, as_completed

@router.get("/watchlists")
def get_watchlists():
    return {k: len(v) for k, v in WATCHLISTS.items()}


@router.get("/stage2-curl")
def get_stage2_curl_stocks(
    watchlist: str = Query("default"),
    tickers: str = Query(""),
    fresh_only: bool = Query(False),
):
    """Return stocks meeting 30W MA curl up and volume surge."""
    if tickers:
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
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
    tickers:  str  = Query(""),          # comma-separated custom list
    as_of:    Optional[str] = Query(None),  # backtest date YYYY-MM-DD
):
    if tickers:
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    elif watchlist == "short_squeeze":
        loop = asyncio.get_event_loop()
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
            yield f"data: {json.dumps(result)}\n\n"

        yield f"data: {json.dumps({'done': True, 'total': count})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
