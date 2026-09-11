"""
Earnings watchlist tracker.

GET  /api/tracker/watchlist              — list all tickers
POST /api/tracker/watchlist              — add tickers {"tickers": ["AAPL","MSFT"]}
DELETE /api/tracker/watchlist/{ticker}   — remove one ticker
DELETE /api/tracker/watchlist            — remove all

GET  /api/tracker/week                   — week schedule for all watchlist tickers
GET  /api/tracker/today                  — today's DB rows (pre/post alert status)
"""
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/tracker", tags=["tracker"])

ET = ZoneInfo("America/New_York")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_timing(ticker: str) -> str:
    """BMO / AMC detection via yfinance earningsTimestamp in ticker.info."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        ts = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
        if ts:
            dt = datetime.fromtimestamp(ts, tz=ET)
            return "BMO" if dt.hour < 12 else "AMC"
    except Exception:
        pass
    return "Unknown"


def _next_earnings(ticker: str) -> Optional[str]:
    try:
        from backend.services.earnings import get_next_earnings_date
        return get_next_earnings_date(ticker)
    except Exception:
        return None


def _quick_prediction(ticker: str) -> dict:
    """
    Lightweight direction prediction — skips options chain and backtest.
    Returns direction, confidence, score, and top factors.
    """
    try:
        import yfinance as yf
        from backend.services.earnings import (
            get_earnings_dates_yf, enrich_with_price_moves,
            compute_history_stats, get_estimate_revisions,
            get_direction_score, get_news_sentiment,
        )
        from backend.services.analysis import get_fundamentals

        tk   = yf.Ticker(ticker)
        hist = tk.history(period="1y", interval="1d")
        if hist.empty:
            return {"error": "no data"}
        hist.columns = [c.lower() for c in hist.columns]
        current_price = float(hist["close"].iloc[-1])

        earnings_raw  = get_earnings_dates_yf(ticker)
        earnings_hist = enrich_with_price_moves(ticker, earnings_raw)
        fundamentals  = get_fundamentals(ticker) or {}
        revisions     = get_estimate_revisions(ticker)
        news          = get_news_sentiment(ticker)

        direction = get_direction_score(
            ticker, current_price, hist,
            earnings_hist, fundamentals,
            expected_move={},          # skip options chain
            revisions=revisions,
            news_sentiment=news,
        )
        return {
            "direction":  direction.get("direction", "NEUTRAL"),
            "confidence": direction.get("confidence", "LOW"),
            "score":      direction.get("score", 0),
            "squeeze":    bool(direction.get("squeeze_setup")),
        }
    except Exception as e:
        return {"error": str(e)[:60]}


def _week_bounds():
    today = date.today()
    start = today - timedelta(days=today.weekday())   # Monday
    end   = start + timedelta(days=6)                 # Sunday
    return start, end


# ── Watchlist CRUD ────────────────────────────────────────────────────────────

class AddTickersBody(BaseModel):
    tickers: list[str]


@router.get("/watchlist")
def list_watchlist():
    from backend.db.earnings_tracker import get_watchlist, init_watchlist
    init_watchlist()
    return {"tickers": get_watchlist()}


@router.post("/watchlist")
def add_tickers(body: AddTickersBody):
    from backend.db.earnings_tracker import add_watchlist_tickers, init_watchlist
    init_watchlist()
    cleaned = [t.strip().upper() for t in body.tickers if t.strip()]
    add_watchlist_tickers(cleaned)
    return {"added": cleaned}


@router.delete("/watchlist/{ticker}")
def delete_ticker(ticker: str):
    from backend.db.earnings_tracker import remove_watchlist_ticker
    remove_watchlist_ticker(ticker.upper())
    return {"removed": ticker.upper()}


@router.delete("/watchlist")
def delete_all():
    from backend.db.earnings_tracker import clear_watchlist
    clear_watchlist()
    return {"status": "cleared"}


# ── Week schedule ─────────────────────────────────────────────────────────────

@router.get("/week")
def week_schedule():
    """
    Return the current week's earnings schedule for all watchlist tickers,
    including a lightweight pre-earnings direction prediction for each.
    Predictions run in parallel to keep response time reasonable.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from backend.db.earnings_tracker import get_watchlist
    tickers = get_watchlist()
    if not tickers:
        return {"week_start": str(_week_bounds()[0]), "week_end": str(_week_bounds()[1]), "schedule": []}

    today = date.today()
    week_start, week_end = _week_bounds()

    # Fetch earnings dates in parallel
    def _build_entry(ticker: str) -> dict:
        next_date = _next_earnings(ticker)
        entry: dict = {
            "ticker":        ticker,
            "earnings_date": next_date,
            "timing":        "Unknown",
            "day_of_week":   None,
            "is_today":      False,
            "in_week":       False,
            "prediction":    None,
        }
        if next_date:
            ed = datetime.strptime(next_date, "%Y-%m-%d").date()
            entry["day_of_week"] = ed.strftime("%A")
            entry["is_today"]    = ed == today
            entry["in_week"]     = week_start <= ed <= week_end
            if entry["in_week"]:
                entry["timing"] = _detect_timing(ticker)
        entry["prediction"] = _quick_prediction(ticker)
        return entry

    rows = []
    with ThreadPoolExecutor(max_workers=min(len(tickers), 8)) as pool:
        futures = {pool.submit(_build_entry, t): t for t in tickers}
        for fut in as_completed(futures):
            try:
                rows.append(fut.result())
            except Exception:
                rows.append({"ticker": futures[fut], "earnings_date": None,
                             "in_week": False, "prediction": None})

    rows.sort(key=lambda x: x["earnings_date"] or "9999-99-99")
    return {
        "week_start": str(week_start),
        "week_end":   str(week_end),
        "schedule":   rows,
    }


# ── Today's alert status ──────────────────────────────────────────────────────

@router.get("/today")
def today_status():
    from backend.db.earnings_tracker import get_all_for_date, today_str
    return {"date": today_str(), "tickers": get_all_for_date(today_str())}
