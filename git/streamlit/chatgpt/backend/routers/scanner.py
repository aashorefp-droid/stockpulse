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

from backend.services.scanner import (
    WATCHLISTS, scan_single, get_short_squeeze_tickers, get_telegram_watchlist,
)

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


@router.get("/watchlists")
def get_watchlists():
    return {k: len(v) for k, v in WATCHLISTS.items()}


@router.get("/seasonality")
async def seasonality(ticker: str = Query(...)):
    """On-demand current-month + 12-month seasonality (cached per month).

    Kept off the scan hot path — the UI calls this only when the
    seasonality modal is opened.
    """
    sym = (ticker or "").strip().upper()
    if not sym:
        return {"available": False, "reason": "no ticker"}
    loop = asyncio.get_event_loop()

    def _do():
        from backend.services.scanner import _seasonality_cached
        return _seasonality_cached(sym)

    return await loop.run_in_executor(None, _do)


@router.post("/telegram/refresh")
async def refresh_telegram_watchlist():
    """Force an immediate TOS-scan Gmail pull (don't wait for the 7:15 PM job)."""
    loop = asyncio.get_event_loop()

    def _do():
        from backend.services.gmail_watchlist import (
            poll_and_store, fetch_today_watchlist,
        )
        new = poll_and_store()
        tickers = fetch_today_watchlist()
        return {"new": new, "count": len(tickers), "tickers": tickers}

    return await loop.run_in_executor(None, _do)


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
    elif watchlist == "telegram":
        loop = asyncio.get_event_loop()
        ticker_list = await loop.run_in_executor(None, get_telegram_watchlist)
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
