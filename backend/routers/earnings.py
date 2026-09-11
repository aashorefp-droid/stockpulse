"""
GET /api/earnings/post-analysis          — batch post-earnings analysis by date
GET /api/earnings/{ticker}               — full analysis + direction score
GET /api/earnings/{ticker}/backtest      — historical earnings-day backtest
"""
from typing import Optional
from datetime import date as date_cls
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.services.earnings import (
    get_full_earnings_analysis,
    run_earnings_backtest,
    get_post_earnings_batch,
)

router = APIRouter(prefix="/api/earnings", tags=["earnings"])


class PlaceholderSeedBody(BaseModel):
    start_date: Optional[str] = None
    days: int = 7
    tickers: Optional[list[str] | str] = None
    source: str = "placeholder"
    replace: bool = False
    keep_after_days: int = 2


class PlaceholderDateRow(BaseModel):
    date: str
    tickers: Optional[list[str] | str] = None
    ticker: Optional[str] = None


class PlaceholderSaveBody(PlaceholderSeedBody):
    source: str = "scanner-weekly"
    replace: bool = True
    rows: Optional[list[PlaceholderDateRow]] = None


class PlaceholderPurgeBody(BaseModel):
    keep_after_days: int = 2
    today: Optional[str] = None


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


@router.get("/placeholders")
async def earnings_placeholders(
    start_date: Optional[str] = None,
    days: int = 7,
):
    from backend.db.earnings_placeholder_store import list_placeholders
    return list_placeholders(start_date=start_date, days=days)


@router.post("/placeholders/seed")
async def seed_earnings_placeholders(body: PlaceholderSeedBody | None = None):
    from backend.db.earnings_placeholder_store import seed_weekly_placeholders
    payload = body or PlaceholderSeedBody()
    return seed_weekly_placeholders(
        start_date=payload.start_date,
        days=payload.days,
        tickers=payload.tickers,
        source=payload.source,
        replace=payload.replace,
        keep_after_days=payload.keep_after_days,
    )


@router.post("/placeholders/save")
async def save_earnings_placeholders(body: PlaceholderSaveBody):
    from backend.db.earnings_placeholder_store import (
        save_dated_placeholders,
        seed_weekly_placeholders,
    )

    if body.rows:
        rows = [row.dict() for row in body.rows]
        return save_dated_placeholders(
            rows,
            source=body.source,
            replace=body.replace,
            keep_after_days=body.keep_after_days,
        )
    return seed_weekly_placeholders(
        start_date=body.start_date,
        days=body.days,
        tickers=body.tickers,
        source=body.source,
        replace=body.replace,
        keep_after_days=body.keep_after_days,
    )


@router.post("/placeholders/purge")
async def purge_earnings_placeholders(body: PlaceholderPurgeBody | None = None):
    from backend.db.earnings_placeholder_store import purge_past_placeholders

    payload = body or PlaceholderPurgeBody()
    return purge_past_placeholders(
        keep_after_days=payload.keep_after_days,
        today=payload.today,
    )


@router.delete("/placeholders")
async def delete_earnings_placeholders(
    start_date: Optional[str] = None,
    days: int = 7,
):
    from backend.db.earnings_placeholder_store import delete_placeholders

    return delete_placeholders(start_date=start_date, days=days)


@router.get("/{ticker}")
async def earnings_analysis(ticker: str, detail: str = "auto"):
    ticker = ticker.upper().strip()
    detail = detail.lower().strip()
    if detail not in {"auto", "full"}:
        detail = "auto"
    result = get_full_earnings_analysis(ticker, detail=detail)
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
