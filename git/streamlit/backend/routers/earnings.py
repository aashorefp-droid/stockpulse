"""
GET /api/earnings/post-analysis          — batch post-earnings analysis by date
GET /api/earnings/{ticker}               — full analysis + direction score
GET /api/earnings/{ticker}/backtest      — historical earnings-day backtest
"""
from typing import Optional
from datetime import date as date_cls
from fastapi import APIRouter, HTTPException
from backend.services.earnings import (
    get_full_earnings_analysis,
    run_earnings_backtest,
    get_post_earnings_batch,
)

router = APIRouter(prefix="/api/earnings", tags=["earnings"])


# NOTE: /post-analysis must be registered BEFORE /{ticker} to avoid being
# captured by the parameterized route.
@router.get("/post-analysis")
async def post_earnings_analysis(
    date: Optional[str] = None,
    tickers: Optional[str] = None,
):
    target_date = date or str(date_cls.today())
    custom = [t.strip() for t in tickers.split(",") if t.strip()] if tickers else None
    results = get_post_earnings_batch(target_date, custom)
    return {"date": target_date, "results": results}


@router.get("/{ticker}")
async def earnings_analysis(ticker: str):
    ticker = ticker.upper().strip()
    result = get_full_earnings_analysis(ticker)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/{ticker}/backtest")
async def earnings_backtest(ticker: str):
    ticker = ticker.upper().strip()
    result = run_earnings_backtest(ticker)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result
